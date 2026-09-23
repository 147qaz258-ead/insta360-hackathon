"""路演 Dashboard 后端：Flask + SSE，把 Agent 闭环全过程实时推到网页。

路由:
  GET  /                      页面
  GET  /api/panos             input 目录全景图列表
  POST /api/upload            上传全景图到 input/
  POST /api/run               启动一次拍摄构图 {pano, intent, mock}
  POST /api/answer            回答 Agent 的 ask_user 提问 {run_id, answer}
  GET  /api/events            SSE 事件流（所有 run 的实时事件）
  GET  /api/runs              历史 run 列表（含最终片与决策摘要）
  GET  /api/run/<id>/trace    某次 run 的完整 trace.json
  GET  /img/<relpath>         安全地提供 runs/ 与 input/ 下的图片
  POST /api/watch             监听 input 目录，新照片自动触发 {intent}
  POST /api/batch             多时刻批次：先选最佳时刻再构图 {panos, intent, mock}
  POST /api/asr               语音转文字（multipart 音频文件 → {text}）
  POST /api/tts               文字转语音（{text} → wav 音频）
"""
from __future__ import annotations

import json
import queue
import shutil
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, request, send_file, send_from_directory
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename

from touchsight.agent.loop import PhotographyAgent
from touchsight.agent.tools import Toolset
from touchsight.capture.acquisition import BatchCollector, MultiWatcher, prepare_pano
from touchsight.config import PROJECT_ROOT, settings
from touchsight.panorama.views import PanoramaViewGenerator
from touchsight.storage.runs import RunSession
from touchsight.vlm.providers import ChatResult, MockProvider, ToolCall, make_provider

app = Flask(__name__, static_folder="static", static_url_path="/static")

ALLOWED_ROOTS = [settings.runs_dir.resolve(), settings.input_dir.resolve()]
MAX_UPLOAD_BYTES = 256 * 1024 * 1024

# ---------------- 事件总线 ----------------
subscribers: list[queue.Queue] = []
active = {"run_id": None, "status": "idle", "intent": ""}
_pending_questions: dict[str, dict] = {}
lock = threading.Lock()


def broadcast(event: dict):
    event = _web_event(event)
    for q in list(subscribers):
        try:
            q.put_nowait(event)
        except queue.Full:
            pass


def _web_event(event: dict) -> dict:
    """把事件里的本地绝对路径转成 /img 相对 URL。"""
    data = event.get("data")

    def conv(v):
        if isinstance(v, str):
            p = Path(v)
            if p.is_absolute() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                return to_img_url(p)
        if isinstance(v, list):
            return [conv(x) for x in v]
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        return v

    return {**event, "data": conv(data), "ts": time.strftime("%H:%M:%S")}


def to_img_url(p: Path) -> str:
    p = p.resolve()
    for root in ALLOWED_ROOTS:
        try:
            rel = p.relative_to(root)
            return f"/img/{root.name}/{rel.as_posix()}"
        except ValueError:
            continue
    return ""


# ---------------- 运行管理 ----------------
def _mock_script() -> list[ChatResult]:
    """演示模式脚本：仅走通工具链路。旁白不描述任何画面内容——
    画面理解与评审理由只有真实 VLM 模式才会产出，演示模式一律明确标注。"""
    return [
        ChatResult(text="【演示模式】我先观察整个 360° 场景。", tool_calls=[ToolCall("m1", "get_overview_views", {})]),
        ChatResult(text="【演示模式】在三个方向各取一个构图作为候选（真实模式由 VLM 根据画面内容与用户意图自主选择取景参数与构图理由）。",
                   tool_calls=[
                       ToolCall("m2", "render_view", {"yaw": 0, "pitch": 0, "fov": 70, "aspect_ratio": "3:2", "reason": "【演示模式】候选一：正前方标准视角"}),
                       ToolCall("m3", "render_view", {"yaw": 120, "pitch": 0, "fov": 70, "aspect_ratio": "3:2", "reason": "【演示模式】候选二：120° 方向"}),
                       ToolCall("m4", "render_view", {"yaw": 240, "pitch": 0, "fov": 70, "aspect_ratio": "16:9", "reason": "【演示模式】候选三：240° 方向"}),
                   ]),
        ChatResult(text="【演示模式】把三张候选放在一起比较（真实模式此处为 VLM 图片编辑式评审，逐张指出优劣）。",
                   tool_calls=[ToolCall("m5", "inspect_images", {"image_ids": ["candidate_01", "candidate_02", "candidate_03"], "question": "演示模式：占位比较"})]),
        ChatResult(text="【演示模式】选定 candidate_01。",
                   tool_calls=[ToolCall("m6", "save_image", {"candidate_id": "candidate_01", "reason": "【演示模式 · 非真实评审】此选定理由为占位文本，仅用于演示“取景→比较→保存”的闭环流程；真实模式下此处是 VLM 基于实际画面内容写出的评审理由。"})]),
    ]


def run_agent(pano_path: Path, intent: str, use_mock: bool = False, pre_events: list[dict] | None = None) -> dict:
    with lock:
        active.update(run_id=None, status="running", intent=intent)
    provider = MockProvider(_mock_script()) if use_mock else make_provider(settings)
    q_holder: list[queue.Queue] = []

    def listener(ev):
        broadcast(ev)

    ask_event = threading.Event()
    ask_state = {"answer": ""}

    def web_ask(question: str) -> str:
        run_id = active.get("run_id") or "pending"
        _pending_questions[run_id] = {"question": question, "event": ask_event, "state": ask_state}
        broadcast({"type": "ask_user_pending", "run_id": run_id, "data": {"question": question}})
        ask_event.wait(timeout=300)
        _pending_questions.pop(run_id, None)
        return ask_state["answer"] or "（用户未回答，请自行判断）"

    session = RunSession(settings.runs_dir, intent=intent, listeners=[listener])
    with lock:
        active["run_id"] = session.run_id
    for ev in pre_events or []:
        session.log(ev["type"], ev["data"])
    session.save_panorama(pano_path)
    broadcast({"type": "panorama_saved", "run_id": session.run_id,
               "data": {"pano": to_img_url(session.run_dir / f"original_panorama{pano_path.suffix.lower()}")}})
    generator = PanoramaViewGenerator(pano_path)
    toolset = Toolset(generator, session, ask_user_fn=web_ask)
    agent = PhotographyAgent(provider, toolset, session, max_steps=settings.max_agent_steps)
    try:
        result = agent.run(intent)
        status = "done"
    except Exception as e:
        result = {"run_id": session.run_id, "run_dir": str(session.run_dir), "final": None, "summary": f"运行出错: {e}"}
        status = "error"
    if status == "done" and not use_mock and result.get("final"):
        # 评审 -> 画质参数闭环：评语中可修复的画质问题用影石 MediaSDK 重出成片
        try:
            from touchsight.enhance import enhance_final
            cid = next((c for c, p in toolset.candidates.items()
                        if toolset.final_candidate and p == toolset.final_candidate), None)
            spec = toolset.candidate_meta.get(cid, {}) if cid else {}
            enhanced = enhance_final(provider, session.run_dir, pano_path, spec,
                                     result.get("summary", ""))
            if enhanced:
                ev_data = {"path": str(enhanced["path"]), "params": enhanced["params"],
                           "via": enhanced["via"], "reason": enhanced["reason"]}
                session.log("final_enhanced", ev_data)
                broadcast({"type": "final_enhanced", "run_id": session.run_id, "data": ev_data})
        except Exception as e:
            print(f"[enhance] 画质增强失败（不影响成片）: {e}")
    with lock:
        active["status"] = status
    broadcast({"type": "run_finished", "run_id": session.run_id,
               "data": {"status": status, "final": to_img_url(Path(result["final"])) if result.get("final") else None,
                        "summary": result.get("summary", "")}})
    return result


# ---------------- 路由 ----------------
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/panos")
def panos():
    settings.input_dir.mkdir(exist_ok=True)
    files = sorted(
        [p for p in settings.input_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}],
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    return jsonify([{"name": p.name, "url": to_img_url(p), "size_kb": p.stat().st_size // 1024} for p in files])


@app.errorhandler(413)
def upload_too_large(error):
    return jsonify({"error": "文件太大，请上传不超过 256 MB 的照片。"}), 413


@app.post("/api/upload")
def upload():
    request.max_content_length = MAX_UPLOAD_BYTES + 64 * 1024
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "请选择要上传的影石原片或全景照片。"}), 400
    source_name = Path(f.filename.replace("\\", "/")).name
    suffix = Path(source_name).suffix.lower()
    if suffix not in {".insp", ".jpg", ".jpeg", ".png"}:
        return jsonify({"error": "支持影石 INSP 原片和 JPG / JPEG / PNG 全景照片；DNG 请先用 Insta360 Studio 导出为全景 JPG，视频文件暂不支持。"}), 415
    stem = secure_filename(Path(source_name).stem)[:100] or "panorama"
    name = f"{stem}_{uuid4().hex}{suffix}"
    settings.input_dir.mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=".upload_", dir=settings.input_dir) as tmp:
            src = Path(tmp) / name
            f.save(src)
            size = src.stat().st_size
            if size > MAX_UPLOAD_BYTES:
                return upload_too_large(None)
            if not size:
                return jsonify({"error": "照片文件为空，请重新选择原片。"}), 400
            if suffix != ".insp":
                with Image.open(src) as image:
                    if image.format not in {"JPEG", "PNG"}:
                        return jsonify({"error": "文件内容不是 JPG / PNG 照片，请勿仅修改扩展名。"}), 415
                    image.verify()
            broadcast({"type": "import_progress", "run_id": "",
                       "data": {"name": source_name, "stage": "stitching" if suffix == ".insp" else "copying"}})
            prepared, meta = prepare_pano(src, Path(tmp) / "prepared")
            if prepared is None:
                message = "INSP 拼接失败：请确认上传的是完整影石照片原片，并检查 MediaSDK 是否可用。"
                broadcast({"type": "import_progress", "run_id": "",
                           "data": {"name": source_name, "stage": "fail", "error": message}})
                return jsonify({"error": message}), 422
            with Image.open(prepared) as image:
                image.load()
            if suffix == ".insp":
                raw_dir = settings.input_dir / "_raw"
                raw_dir.mkdir(exist_ok=True)
                shutil.move(str(prepared.parent / "_raw" / name), str(raw_dir / name))
            dst = settings.input_dir / prepared.name
            shutil.move(str(prepared), str(dst))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        app.logger.exception("Uploaded photo could not be imported")
        message = "无法读取或处理照片，请检查文件是否完整、磁盘是否可写，或用 Insta360 Studio 导出全景 JPG 后重试。"
        broadcast({"type": "import_progress", "run_id": "",
                   "data": {"name": source_name, "stage": "fail", "error": message}})
        return jsonify({"error": message}), 422
    level = (meta.get("level") or {}).get("reason", "")
    broadcast({"type": "import_progress", "run_id": "",
               "data": {"name": source_name, "stage": "ok", "pano": to_img_url(dst),
                        "pano_name": dst.name, "level": level}})
    return jsonify({"name": dst.name, "url": to_img_url(dst), "source_name": source_name,
                    "stitched": suffix == ".insp", "level": level})


@app.post("/api/run")
def start_run():
    body = request.get_json(force=True)
    pano = settings.input_dir / Path(body.get("pano", "")).name
    intent = body.get("intent") or "为这个场景拍一张最好的照片"
    use_mock = bool(body.get("mock"))
    if not pano.exists():
        return jsonify({"error": f"全景图不存在: {pano.name}"}), 404
    if active.get("status") == "running":
        return jsonify({"error": "已有任务运行中"}), 409
    threading.Thread(target=run_agent, args=(pano, intent, use_mock), daemon=True).start()
    return jsonify({"ok": True})


@app.post("/api/answer")
def answer():
    body = request.get_json(force=True)
    pending = _pending_questions.get(body.get("run_id", ""))
    if not pending:
        for v in _pending_questions.values():
            pending = v
            break
    if not pending:
        return jsonify({"error": "没有待回答的问题"}), 404
    pending["state"]["answer"] = body.get("answer", "")
    pending["event"].set()
    return jsonify({"ok": True})


@app.get("/api/events")
def events():
    q = queue.Queue(maxsize=500)
    subscribers.append(q)

    def stream():
        try:
            yield f"data: {json.dumps({'type': 'hello', 'data': active}, ensure_ascii=False)}\n\n"
            while True:
                ev = q.get()
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        finally:
            if q in subscribers:
                subscribers.remove(q)

    return app.response_class(stream(), mimetype="text/event-stream")


@app.get("/api/runs")
def runs():
    out = []
    for d in sorted(settings.runs_dir.glob("run_*"), reverse=True):
        trace_file = d / "trace.json"
        if not trace_file.exists():
            continue
        try:
            trace = json.loads(trace_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        intent = next((e["data"].get("intent") for e in trace if e["type"] == "user_intent"), "")
        final_ev = next((e for e in reversed(trace) if e["type"] == "final_decision"), None)
        final_url = ""
        if final_ev and final_ev["data"].get("final"):
            final_url = to_img_url(Path(final_ev["data"]["final"]))
        n_candidates = len(list((d / "candidates").glob("candidate_*.jpg")))
        out.append({"run_id": d.name, "intent": intent, "final": final_url,
                    "summary": (final_ev or {}).get("data", {}).get("summary", ""),
                    "candidates": n_candidates,
                    "has_final": bool(final_url)})
    return jsonify(out)


@app.get("/api/run/<run_id>/trace")
def run_trace(run_id: str):
    trace_file = settings.runs_dir / run_id / "trace.json"
    if not trace_file.exists():
        return jsonify({"error": "not found"}), 404
    trace = json.loads(trace_file.read_text(encoding="utf-8"))
    return jsonify([_web_event(e) for e in trace])


@app.get("/img/<root>/<path:rel>")
def img(root: str, rel: str):
    base = settings.runs_dir if root == settings.runs_dir.name else settings.input_dir if root == settings.input_dir.name else None
    if base is None:
        return "forbidden", 403
    path = (base / rel).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError:
        return "forbidden", 403
    if not path.exists():
        return "not found", 404
    return send_file(path)


_watch_state: dict = {"watcher": None, "thread": None, "dirs": [], "intent": "", "mock": False}


def process_batch(photos: list[Path], intent: str, use_mock: bool):
    broadcast({"type": "batch_collected", "run_id": "",
               "data": {"count": len(photos), "photos": [to_img_url(p) for p in photos],
                        "names": [p.name for p in photos]}})
    if active.get("status") == "running":
        broadcast({"type": "batch_skipped", "run_id": "", "data": {"reason": "上一批次仍在处理中"}})
        return
    chosen = photos[0]
    sel = None
    if len(photos) > 1:
        from touchsight.agent.timing import select_best_moment
        if use_mock:
            sel = {"best_path": photos[0], "reason": "【演示模式】默认选择第 1 张（真实模式由 VLM 逐张比较睁眼/表情/姿态后选出）",
                   "notes": ["（演示模式占位点评，非真实评审）" for _ in photos]}
        else:
            provider = make_provider(settings)
            try:
                sel = select_best_moment(provider, photos, intent)
            except Exception as e:
                broadcast({"type": "run_error", "run_id": "",
                           "data": {"stage": "moment_selection",
                                    "message": f"时刻评选失败，改用第 1 张: {e}"}})
        if sel:
            chosen = Path(sel["best_path"])
            broadcast({"type": "moment_selected", "run_id": "",
                       "data": {"chosen": to_img_url(chosen), "chosen_name": chosen.name,
                                "reason": sel["reason"], "notes": sel["notes"],
                                "sharpness": sel.get("sharpness", {}),
                                "photos": [to_img_url(p) for p in photos]}})
    pre_events = [
        {"type": "batch_collected",
         "data": {"count": len(photos), "names": [p.name for p in photos]}},
    ]
    if sel:
        pre_events.append({"type": "moment_selected",
                           "data": {"chosen_name": chosen.name, "reason": sel["reason"],
                                    "notes": sel["notes"]}})
    run_agent(chosen, intent, use_mock, pre_events=pre_events)


@app.post("/api/batch")
def batch_run():
    body = request.get_json(force=True)
    names = body.get("panos") or []
    intent = body.get("intent") or "为这个场景拍一张最好的照片"
    use_mock = bool(body.get("mock"))
    photos = [settings.input_dir / Path(n).name for n in names]
    missing = [p.name for p in photos if not p.exists()]
    if missing:
        return jsonify({"error": f"文件不存在: {missing}"}), 404
    if not photos:
        return jsonify({"error": "panos 为空"}), 400
    threading.Thread(target=process_batch, args=(photos, intent, use_mock), daemon=True).start()
    return jsonify({"ok": True, "count": len(photos)})


def _run_watch(dirs: list[Path], intent: str, use_mock: bool):
    """监听线程主函数：新文件 -> 逐张导入（进度上屏） -> 聚合批次 -> 选时刻 -> Agent。"""
    collector = BatchCollector(
        lambda photos: process_batch(photos, intent, use_mock),
        gap_seconds=8.0,
        on_waiting=lambda photos, secs: broadcast({
            "type": "batch_waiting", "run_id": "",
            "data": {"count": len(photos), "seconds_left": secs, "names": [p.name for p in photos]}}),
    )

    def on_new_files(files: list[Path]):
        imported: list[Path] = []
        for p in files:
            broadcast({"type": "watch_new_photo", "run_id": "",
                       "data": {"name": p.name, "dir": str(p.parent)}})
            stage = "stitching" if p.suffix.lower() == ".insp" else "copying"
            broadcast({"type": "import_progress", "run_id": "",
                       "data": {"name": p.name, "stage": stage}})
            try:
                dst, meta = prepare_pano(p, settings.input_dir)
            except Exception as e:
                broadcast({"type": "import_progress", "run_id": "",
                           "data": {"name": p.name, "stage": "fail", "error": str(e)}})
                continue
            if dst:
                level_reason = (meta.get("level") or {}).get("reason", "")
                broadcast({"type": "import_progress", "run_id": "",
                           "data": {"name": p.name, "stage": "ok", "pano": to_img_url(dst),
                                    "pano_name": dst.name, "level": level_reason}})
                imported.append(dst)
            else:
                broadcast({"type": "import_progress", "run_id": "",
                           "data": {"name": p.name, "stage": "fail",
                                    "error": "拼接失败（详见服务控制台）"}})
        if imported:
            collector.add_all(imported)

    watcher = MultiWatcher(dirs, on_new_files)
    _watch_state["watcher"] = watcher
    watcher.run()


def _start_watch(dirs: list[Path], intent: str, use_mock: bool):
    old = _watch_state.get("watcher")
    if old:
        old.stop()
    t = threading.Thread(target=_run_watch, args=(dirs, intent, use_mock), daemon=True)
    t.start()
    _watch_state.update(thread=t, dirs=[str(d) for d in dirs], intent=intent, mock=use_mock)
    broadcast({"type": "watch_started", "run_id": "",
               "data": {"dirs": [str(d) for d in dirs], "intent": intent}})


def _parse_dirs(body: dict) -> list[Path]:
    """优先 dirs 列表；兼容 dcim 单路径（支持分号分隔多个）；缺省用配置。"""
    dirs = body.get("dirs") or []
    dcim = body.get("dcim") or ""
    raw = [str(d) for d in dirs] + [s for s in str(dcim).split(";")]
    out = [Path(s.strip()) for s in raw if s and s.strip()]
    return out or list(settings.watch_dirs)


@app.get("/api/watch/status")
def watch_status():
    t = _watch_state.get("thread")
    return jsonify({
        "watching": bool(t and t.is_alive()),
        "dirs": _watch_state.get("dirs", []),
        "default_dirs": [str(d) for d in settings.watch_dirs],
        "intent": _watch_state.get("intent", ""),
    })


@app.post("/api/watch")
def watch():
    body = request.get_json(force=True)
    intent = body.get("intent") or "为这个场景拍一张最好的照片"
    use_mock = bool(body.get("mock"))
    dirs = _parse_dirs(body)
    settings.input_dir.mkdir(exist_ok=True)
    t = _watch_state.get("thread")
    if t and t.is_alive() and _watch_state.get("dirs") == [str(d) for d in dirs]:
        return jsonify({"ok": True, "msg": "已在监听中"})
    _start_watch(dirs, intent, use_mock)
    return jsonify({"ok": True, "msg": f"开始监听 {len(dirs)} 个目录", "dirs": [str(d) for d in dirs]})


# ---------------- 语音链路（P8） ----------------
@app.post("/api/asr")
def asr():
    """浏览器上传录音（wav），返回转写文本。"""
    from touchsight.voice import asr_transcribe
    f = request.files.get("audio")
    if not f:
        return jsonify({"error": "缺少 audio 文件"}), 400
    fmt = Path(f.filename or "a.wav").suffix.lstrip(".").lower() or "wav"
    try:
        text = asr_transcribe(f.read(), fmt)
    except Exception as e:
        return jsonify({"error": f"ASR 失败: {e}"}), 502
    return jsonify({"text": text})


@app.post("/api/tts")
def tts():
    """文本 → wav 音频（给浏览器播报）。"""
    from touchsight.voice import tts_speak
    text = (request.get_json(force=True) or {}).get("text", "").strip()
    if not text:
        return jsonify({"error": "text 为空"}), 400
    try:
        audio = tts_speak(text[:400])
    except Exception as e:
        return jsonify({"error": f"TTS 失败: {e}"}), 502
    return app.response_class(audio, mimetype="audio/wav")


def main():
    print("TouchSight Dashboard: http://127.0.0.1:8050")
    # 服务启动即自动监听（X5 文件传输盘 + 网络摄像头本地目录），热插拔自动恢复
    _start_watch(list(settings.watch_dirs), "为这个场景拍一张最好的照片", False)
    app.run(host="127.0.0.1", port=8050, threaded=True, debug=False)


if __name__ == "__main__":
    main()
