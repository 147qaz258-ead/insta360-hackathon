from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
from pathlib import Path
import socket
import threading
import time
from urllib.parse import urlparse
import uuid

from PIL import Image

from agent.review_shots import capture_page_screenshot
from agent.v5_loop import run_tactile_agent_loop_v5
from multimodal import MultimodalProviderError, Qwen3VLApiProvider
from tactile.frame import TactileFrame
from tactile.v4_frame import COLS, ROWS, TactileFrameV2, publish_tactile_frame


FrameFactory = Callable[[str], TactileFrame]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _image_size_from_data_url(image_data_url: str) -> tuple[int, int]:
    """Read the source size once so content_rect stays a mechanical contain."""
    header, _, encoded = image_data_url.partition(",")
    if ";base64" not in header:
        raise ValueError("image_data_url must use base64 encoding")
    try:
        raw = base64.b64decode(encoded)
        with Image.open(io.BytesIO(raw)) as image:
            return int(image.width), int(image.height)
    except (OSError, ValueError, binascii.Error) as exc:
        raise ValueError("image_data_url is not a readable image") from exc


def _candidate_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for entry in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            ip = entry[4][0]
            if ip and not ip.startswith("127."):
                addresses.add(ip)
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        ip = probe.getsockname()[0]
        if ip and not ip.startswith("127."):
            addresses.add(ip)
        probe.close()
    except OSError:
        pass
    return sorted(addresses)


def _empty_frame() -> TactileFrameV2:
    candidate = {
        "version": 2,
        "device_id": "rdk_hdmi",
        "cols": COLS,
        "rows": ROWS,
        "height_encoding": "uint8",
        "height_rows": [[0] * COLS for _ in range(ROWS)],
        "region_rows": [[0] * COLS for _ in range(ROWS)],
        "regions": [],
        "scene_summary": "等待图片",
        "audio_overview": "请先上传一张图片生成触觉表面。",
        "self_review": {"state": "initial"},
    }
    return publish_tactile_frame(candidate, 0)


class RuntimeStore:
    def __init__(self, state_path: Path | None = None) -> None:
        self.condition = threading.Condition()
        self.state_path = state_path
        self.frame = _empty_frame()
        self.current_image_data_url: str | None = None
        self.current_run_id: str | None = None
        self.motion_state = "IDLE"
        self.source = "system:empty"
        self.updated_at = _utc_now()
        self.revision = 1
        self.runs: dict[str, dict] = {}
        self.conversations: dict[str, list[dict[str, str]]] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.review_candidates: dict[str, dict] = {}
        self.review_shots: dict[tuple[str, str], bytes] = {}
        self._restore()

    def _restore(self) -> None:
        if self.state_path is None or not self.state_path.is_file():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            saved = payload.get("frame") or {}
            candidate = {
                "version": saved.get("version"),
                "device_id": saved.get("device_id"),
                "cols": saved.get("cols"),
                "rows": saved.get("rows"),
                "height_encoding": saved.get("height_encoding"),
                "height_rows": saved.get("height_rows"),
                "region_rows": saved.get("region_rows"),
                "regions": saved.get("regions"),
                "scene_summary": saved.get("scene_summary"),
                "audio_overview": saved.get("audio_overview"),
                "self_review": saved.get("self_review") or {},
            }
            restored = publish_tactile_frame(candidate, int(saved.get("frame_id", 0)))
            expected_checksum = str(saved.get("checksum") or "")
            if expected_checksum and restored.checksum != expected_checksum:
                raise ValueError("saved frame checksum mismatch")
            restored.created_at = str(saved.get("created_at") or restored.created_at)
            self.frame = restored
            self.current_image_data_url = payload.get("current_image_data_url")
            self.current_run_id = payload.get("current_run_id")
            self.motion_state = str(payload.get("motion_state") or "READY")
            self.source = str(payload.get("source") or "runtime:restored")
            self.updated_at = str(payload.get("updated_at") or _utc_now())
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            print(f"[runtime] ignored invalid persisted state: {exc}")

    def _persist_locked(self) -> None:
        if self.state_path is None or self.frame.frame_id <= 0:
            return
        payload = {
            "frame": self.frame.to_dict(),
            "current_image_data_url": self.current_image_data_url,
            "current_run_id": self.current_run_id,
            "motion_state": self.motion_state,
            "source": self.source,
            "updated_at": self.updated_at,
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.state_path)
        except OSError as exc:
            print(f"[runtime] could not persist current frame: {exc}")

    def runtime_snapshot(self) -> dict:
        with self.condition:
            return {
                "frame": self.frame.to_dict(),
                "source": self.source,
                "updated_at": self.updated_at,
                "revision": self.revision,
                "motion_state": self.motion_state,
                "current_run_id": self.current_run_id,
                "current_run_available": bool(
                    self.current_run_id and self.current_run_id in self.runs
                ),
            }

    def run_snapshot(self, run_id: str) -> dict | None:
        with self.condition:
            run = self.runs.get(run_id)
            if run is None:
                return None
            return json.loads(json.dumps(run, ensure_ascii=False))

    def create_run(self) -> str:
        run_id = uuid.uuid4().hex
        with self.condition:
            now = _utc_now()
            for previous_id, previous in self.runs.items():
                if previous.get("state") not in {"READY", "ERROR", "CANCELLED"}:
                    event = self.cancel_events.get(previous_id)
                    if event is not None:
                        event.set()
            self.runs[run_id] = {
                "run_id": run_id,
                "state": "QUEUED",
                "candidate": 0,
                "max_candidates": 3,
                "message": "运行已创建",
                "created_at": now,
                "updated_at": now,
                "revision": 1,
                "timeline": [],
                "error": None,
                "frame_id": None,
                "checksum": None,
            }
            self.cancel_events[run_id] = threading.Event()
            self.conversations[run_id] = []
            self.condition.notify_all()
        return run_id

    def run_cancelled(self, run_id: str) -> bool:
        event = self.cancel_events.get(run_id)
        return bool(event and event.is_set())

    def register_review_candidate(self, payload: dict) -> str:
        token = uuid.uuid4().hex
        with self.condition:
            self.review_candidates[token] = payload
            if len(self.review_candidates) > 24:
                for stale in list(self.review_candidates)[: len(self.review_candidates) - 24]:
                    self.review_candidates.pop(stale, None)
        return token

    def review_candidate_snapshot(self, token: str) -> dict | None:
        with self.condition:
            payload = self.review_candidates.get(token)
            return json.loads(json.dumps(payload, ensure_ascii=False)) if payload else None

    def store_review_shot(self, token: str, mode: str, png: bytes) -> None:
        with self.condition:
            self.review_shots[(token, mode)] = png
            if len(self.review_shots) > 48:
                for stale in list(self.review_shots)[: len(self.review_shots) - 48]:
                    self.review_shots.pop(stale, None)

    def review_shot_bytes(self, token: str, mode: str) -> bytes | None:
        with self.condition:
            return self.review_shots.get((token, mode))

    def update_run(self, run_id: str, event: dict) -> None:
        with self.condition:
            run = self.runs.get(run_id)
            if run is None:
                return
            for key in ("state", "candidate", "message", "validation", "metrics", "critique", "frame_id", "checksum"):
                if key in event:
                    run[key] = event[key]
            run["updated_at"] = _utc_now()
            run["revision"] += 1
            run["timeline"] = (run["timeline"] + [{"at": run["updated_at"], **event}])[-40:]
            self.condition.notify_all()

    def finish_run(self, run_id: str, result: dict) -> None:
        with self.condition:
            run = self.runs[run_id]
            run["trace"] = result.get("trace", [])
            run["candidate_count"] = result.get("candidate_count", 0)
            run["updated_at"] = _utc_now()
            run["revision"] += 1
            if result.get("ok"):
                frame: TactileFrameV2 = result["frame"]
                run.update({
                    "state": "READY",
                    "message": "触觉表面已经升起，可以触摸探索",
                    "frame_id": frame.frame_id,
                    "checksum": frame.checksum,
                    "error": None,
                })
            elif result.get("cancelled"):
                run.update({
                    "state": "CANCELLED",
                    "message": str(result.get("error") or "运行已被取消"),
                    "error": str(result.get("error") or "运行已被取消"),
                })
            else:
                run.update({
                    "state": "ERROR",
                    "message": str(result.get("error") or "智能体运行失败"),
                    "error": str(result.get("error") or "智能体运行失败"),
                    "last_validation": result.get("last_validation"),
                })
            self.condition.notify_all()

    def runtime_event(self, *, state: str, source: str, run_id: str | None = None) -> None:
        with self.condition:
            self.motion_state = state
            self.source = source
            if run_id is not None:
                self.current_run_id = run_id
            self.updated_at = _utc_now()
            self.revision += 1
            self._persist_locked()
            self.condition.notify_all()

    def publish(self, run_id: str, image_data_url: str, frame: TactileFrameV2) -> None:
        with self.condition:
            self.frame = frame
            self.current_image_data_url = image_data_url
            self.current_run_id = run_id
            self.motion_state = "RISING"
            self.source = f"agent:{run_id}"
            self.updated_at = _utc_now()
            self.revision += 1
            self._persist_locked()
            self.condition.notify_all()


def run_server(
    host: str,
    port: int,
    web_root: Path,
    frame_factory: FrameFactory,
    profile_info: dict,
    profiles: list | None = None,
) -> None:
    del frame_factory, profiles
    web_root = web_root.resolve()
    multimodal = Qwen3VLApiProvider()
    state_path = web_root.parent.parent / "output" / "runtime" / "current_frame.json"
    store = RuntimeStore(state_path)
    frame_counter_lock = threading.Lock()
    frame_counter = store.frame.frame_id

    network_urls = [f"http://{ip}:{port}" for ip in _candidate_ipv4_addresses()]
    runtime_info = {
        **profile_info,
        "network_urls": network_urls,
        "control_url": f"http://127.0.0.1:{port}/",
        "display_path": "/display.html",
        "multimodal": multimodal.status(),
        "architecture": "model-authoritative-tactile-frame-v5-sparse-runs",
        "review_page": "/review.html",
        "hardware_contract": {
            "device_id": "rdk_hdmi",
            "cols": COLS,
            "rows": ROWS,
            "pin_count": COLS * ROWS,
            "height_encoding": "uint8",
            "minimum_height": 0,
            "maximum_height": 255,
        },
    }

    def next_frame_id() -> int:
        nonlocal frame_counter
        with frame_counter_lock:
            frame_counter += 1
            return frame_counter

    def mark_ready_after(delay: float = 1.1) -> None:
        time.sleep(delay)
        store.runtime_event(state="READY", source=store.source)

    def mark_idle_after(delay: float = 1.1) -> None:
        time.sleep(delay)
        store.runtime_event(state="IDLE", source=store.source)

    def execute_run(run_id: str, image_data_url: str, user_instruction: str) -> None:
        store.update_run(run_id, {
            "state": "UNDERSTANDING",
            "candidate": 0,
            "message": "qwen3.8-max 正在整图理解并决定表达目的",
        })
        try:
            image_size = _image_size_from_data_url(image_data_url)

            def register_candidate(payload: dict) -> str:
                return store.register_review_candidate(payload)

            def review_shot(token: str, mode: str) -> str:
                size = {"full": (1600, 1000), "blind": (840, 520)}.get(mode, (1600, 1000))
                url = f"http://127.0.0.1:{port}/review.html?token={token}&mode={mode}"
                # Headless Chrome launches occasionally wedge (lingering
                # browser trees, profile contention); a transient browser
                # hiccup must not fail an otherwise valid agent run.
                last_error: Exception | None = None
                png: bytes | None = None
                for attempt in range(3):
                    try:
                        png = capture_page_screenshot(url, width=size[0], height=size[1])
                        break
                    except Exception as exc:  # ReviewShotError and friends
                        last_error = exc
                        time.sleep(2.0 * (attempt + 1))
                if png is None:
                    raise last_error or RuntimeError("review screenshot failed")
                store.store_review_shot(token, mode, png)
                return "data:image/png;base64," + base64.b64encode(png).decode("ascii")

            result = run_tactile_agent_loop_v5(
                provider=multimodal,
                image_data_url=image_data_url,
                image_size=image_size,
                frame_id=next_frame_id(),
                user_instruction=user_instruction,
                max_candidates=3,
                on_event=lambda event: store.update_run(run_id, event),
                register_candidate=register_candidate,
                review_shot=review_shot,
                is_cancelled=lambda: store.run_cancelled(run_id),
            )
            if result.get("ok"):
                store.runtime_event(state="LOWERING_OLD_FRAME", source=f"agent:{run_id}", run_id=run_id)
                time.sleep(0.65)
                store.publish(run_id, image_data_url, result["frame"])
                threading.Thread(target=mark_ready_after, daemon=True).start()
            store.finish_run(run_id, result)
        except Exception as exc:
            snapshot = store.run_snapshot(run_id) or {}
            store.finish_run(run_id, {
                "ok": False,
                "error": str(exc),
                "candidate_count": snapshot.get("candidate", 0),
                "trace": [],
            })

    class Handler(BaseHTTPRequestHandler):
        server_version = "TactileAgentV5/1.0"

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _sse_stream(self, snapshot_getter, event_name: str, terminal: bool = False) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                last_id = int(self.headers.get("Last-Event-ID", "-1") or "-1")
            except ValueError:
                last_id = -1
            try:
                while True:
                    payload = snapshot_getter()
                    if payload is None:
                        return
                    event_id = int(payload.get("revision", 0))
                    if event_id != last_id:
                        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                        body = f"retry: 700\nid: {event_id}\nevent: {event_name}\ndata: {data}\n\n".encode("utf-8")
                        self.wfile.write(body)
                        self.wfile.flush()
                        last_id = event_id
                        if terminal and payload.get("state") in {"READY", "ERROR", "CANCELLED"}:
                            return
                    with store.condition:
                        changed = store.condition.wait(timeout=15)
                    if not changed:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def _serve_file(self, relative: str) -> None:
            path = (web_root / relative).resolve()
            if web_root not in path.parents and path != web_root:
                self.send_error(403)
                return
            if not path.is_file():
                self.send_error(404)
                return
            body = path.read_bytes()
            content_type, _ = mimetypes.guess_type(path.name)
            self.send_response(200)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self, max_bytes: int = 30_000_000) -> dict:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > max_bytes:
                raise ValueError("invalid request body length")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("request body must be valid UTF-8 JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Last-Event-ID")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                return
            if path == "/api/info":
                self._json(runtime_info)
                return
            if path == "/api/multimodal/status":
                self._json(multimodal.status())
                return
            if path in {"/api/runtime/frame", "/api/live-frame"}:
                self._json(store.runtime_snapshot())
                return
            if path == "/api/runtime/image":
                with store.condition:
                    image_data_url = store.current_image_data_url
                if not image_data_url or ";base64," not in image_data_url[:128]:
                    self._json({"error": "no active image"}, 404)
                    return
                try:
                    header, encoded = image_data_url.split(",", 1)
                    content_type = header[5:].split(";", 1)[0]
                    body = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error):
                    self._json({"error": "persisted image is invalid"}, 500)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/runtime/events":
                self._sse_stream(store.runtime_snapshot, "runtime")
                return
            if path.startswith("/api/internal/review-candidates/"):
                token = path.rsplit("/", 1)[-1]
                payload = store.review_candidate_snapshot(token)
                if payload is None:
                    self._json({"error": "review candidate not found"}, 404)
                    return
                self._json(payload)
                return
            if path.startswith("/api/internal/review-shots/"):
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[3].endswith(".png"):
                    token = parts[2]
                    mode = parts[3][:-4]
                    png = store.review_shot_bytes(token, mode)
                    if png is None:
                        self._json({"error": "review shot not found"}, 404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(png)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(png)
                    return
                self._json({"error": "not found"}, 404)
                return
            if path.startswith("/api/agent/runs/"):
                parts = path.strip("/").split("/")
                if len(parts) in {4, 5}:
                    run_id = parts[3]
                    snapshot = store.run_snapshot(run_id)
                    if snapshot is None:
                        self._json({"error": "run not found"}, 404)
                        return
                    if len(parts) == 5 and parts[4] == "events":
                        self._sse_stream(lambda: store.run_snapshot(run_id), "run", terminal=True)
                    else:
                        self._json(snapshot)
                    return
            if path in {"", "/"}:
                self._serve_file("index.html")
                return
            self._serve_file(path.lstrip("/"))

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/agent/runs":
                try:
                    payload = self._read_json_body()
                    image_data_url = payload.get("image_data_url")
                    if not isinstance(image_data_url, str) or not image_data_url.startswith("data:image/"):
                        raise ValueError("image_data_url must be a base64 image data URL")
                    user_instruction = str(payload.get("user_instruction") or "").strip()[:2000]
                    run_id = store.create_run()
                    threading.Thread(
                        target=execute_run,
                        args=(run_id, image_data_url, user_instruction),
                        daemon=True,
                        name=f"tactile-agent-{run_id[:8]}",
                    ).start()
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
                    return
                self._json({
                    "ok": True,
                    "run_id": run_id,
                    "state": "QUEUED",
                    "events_url": f"/api/agent/runs/{run_id}/events",
                }, 202)
                return

            if path == "/api/surface/actions":
                try:
                    payload = self._read_json_body(max_bytes=50_000)
                    action = str(payload.get("action") or "").strip().casefold()
                    if action not in {"lower", "raise", "replay"}:
                        raise ValueError("action must be lower, raise or replay")
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
                    return
                if action == "lower":
                    store.runtime_event(state="RETRACTING", source="control:lower")
                    threading.Thread(target=mark_idle_after, daemon=True).start()
                else:
                    store.runtime_event(state="RISING", source=f"control:{action}")
                    threading.Thread(target=mark_ready_after, daemon=True).start()
                self._json({"ok": True, "action": action, **store.runtime_snapshot()})
                return

            if path == "/api/touch":
                try:
                    payload = self._read_json_body(max_bytes=200_000)
                    row = int(payload.get("row"))
                    col = int(payload.get("col"))
                    question = str(payload.get("question") or "").strip()[:2000]
                    snapshot = store.runtime_snapshot()
                    frame = store.frame
                    requested_frame_id = int(payload.get("frame_id", frame.frame_id))
                    if requested_frame_id != frame.frame_id:
                        self._json({"error": "stale frame_id", "current_frame_id": frame.frame_id}, 409)
                        return
                    if row < 0 or row >= ROWS or col < 0 or col >= COLS:
                        raise ValueError("row or col outside 80x48 surface")
                    region = frame.region_at(row, col)
                    height = frame.height_rows[row][col]
                    run_id = snapshot.get("current_run_id") or ""
                    if not question:
                        answer_mode = "pre_generated_region"
                        answer_warning = None
                        if region:
                            speech = str(region.get("speech") or region.get("description") or region.get("name"))
                        elif height == 0:
                            speech = "这里是没有升起的底面。"
                        else:
                            speech = "这个凸点没有关联的语义区域。"
                    else:
                        if not store.current_image_data_url:
                            raise ValueError("no active image session")
                        history = store.conversations.get(run_id, [])[-8:]
                        context_question = json.dumps({
                            "current_touch": {"row": row, "col": col, "height": height},
                            "question": question,
                            "scene_context": {
                                "scene_summary": frame.scene_summary,
                                "audio_overview": frame.audio_overview,
                                "regions": frame.regions,
                                "grid": {"cols": frame.cols, "rows": frame.rows},
                            },
                            "recent_conversation": history,
                        }, ensure_ascii=False)
                        try:
                            raw = multimodal.explain_touch(
                                image_data_url=store.current_image_data_url,
                                scene_summary=frame.scene_summary,
                                region_info=region or {"id": 0, "name": "画面整体"},
                                question=context_question,
                            )
                            speech = str(raw.get("speech") or "").strip()
                            if not speech:
                                raise MultimodalProviderError("touch explanation returned no speech")
                            answer_mode = "qwen3.8-max"
                            answer_warning = None
                            with store.condition:
                                history = store.conversations.setdefault(run_id, [])
                                history.extend([
                                    {"role": "user", "text": question},
                                    {"role": "assistant", "text": speech},
                                ])
                                store.conversations[run_id] = history[-12:]
                        except MultimodalProviderError as exc:
                            cached = ""
                            if region:
                                cached = str(region.get("speech") or region.get("description") or region.get("name") or "")
                            if not cached:
                                cached = str(frame.audio_overview or frame.scene_summary)
                            speech = cached + " 智能追问暂时不可用，请稍后重试。"
                            answer_mode = "cached_model_region_fallback"
                            answer_warning = str(exc)

                    audio_url = None
                    tts_mode = "browser-fallback"
                    audio_result = multimodal.synthesize_speech(speech)
                    if audio_result:
                        audio_bytes, content_type = audio_result
                        audio_url = f"data:{content_type};base64," + base64.b64encode(audio_bytes).decode("ascii")
                        tts_mode = "qwen-audio-3.0-tts-plus"
                except (TypeError, ValueError) as exc:
                    self._json({"error": str(exc)}, 400)
                    return
                except MultimodalProviderError as exc:
                    self._json({"error": str(exc), "stage": "touch_explain"}, 502)
                    return
                self._json({
                    "ok": True,
                    "frame_id": frame.frame_id,
                    "row": row,
                    "col": col,
                    "height": height,
                    "region": region,
                    "speech": speech,
                    "audio_url": audio_url,
                    "tts_mode": tts_mode,
                    "answer_mode": answer_mode,
                    "warning": answer_warning,
                })
                return

            if path in {"/api/understand", "/api/plan", "/api/compile"}:
                self._json({
                    "error": "该分步接口已退出 V5 产品主链，请使用 POST /api/agent/runs",
                    "replacement": "/api/agent/runs",
                }, 410)
                return
            if path == "/api/live-frame":
                self._json({"error": "V5 不允许外部覆盖权威帧"}, 403)
                return
            self._json({"error": "not found"}, 404)

        def log_message(self, format: str, *args) -> None:
            if args and "/api/live-frame" in str(args[0]):
                return
            print("[web]", format % args)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Tactile V5 control page: http://127.0.0.1:{port}/")
    print(f"Tactile V5 display page: http://127.0.0.1:{port}/display.html")
    for base in network_urls:
        print(f"LAN control: {base}/")
        print(f"LAN display: {base}/display.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()
