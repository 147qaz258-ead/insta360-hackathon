"""成片画质增强闭环（L3b）：评审批评 -> 影石 MediaSDK 画质参数 -> 重出成片。

主链路：VLM 把最终评审中提到的画质问题（偏暗/发灰/噪点/偏色/不够锐）
翻译成 MediaSDK 拼接管线的画质参数，用保留的原始 .insp 重新拼接全景，
再按最终成片的视角参数重渲染——影石 ISP 级画质，非后处理近似。
兜底：无 .insp 原件时（如上传的 jpg），用 cv2 在成片上做等价调整。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np

from touchsight.capture.acquisition import stitch_insp
from touchsight.panorama.views import PanoramaViewGenerator, ViewSpec
from touchsight.vlm.providers import VisionModelProvider, text_content

PARAM_RANGES = {
    "exposure": (-100, 100), "highlights": (-100, 100), "shadows": (-100, 100),
    "contrast": (-100, 100), "brightness": (-100, 100), "blackpoint": (-100, 100),
    "saturation": (-100, 100), "vibrance": (-100, 100), "warmth": (-100, 100),
    "tint": (-100, 100), "definition": (0, 100),
}
SWITCHES = ("enable_colorplus", "enable_denoise")

ENHANCE_PROMPT = """你是摄影后期工程师。下面是一段摄影评审对最终照片的评语。
如果评语中提到可以通过影石 MediaSDK 画质参数修复的问题，请输出对应参数；
如果没有可修复的画质问题（评语只是构图/内容赞美），输出空 params。

可修复问题 -> 参数映射（均为整数）：
- 偏暗/欠曝 -> exposure 或 brightness 正值（[-100,100]）
- 过曝/死白 -> exposure 或 highlights 负值
- 发灰/不够通透 -> contrast 正值、blackpoint 小正值
- 暗部死黑 -> shadows 正值
- 颜色寡淡 -> saturation 或 vibrance 正值
- 偏色（偏暖/偏冷 -> warmth 负/正；偏绿/偏品 -> tint 正/负）
- 不够锐利/肉 -> definition（[0,100]）
- 噪点 -> enable_denoise: true
- 色彩整体提升 -> enable_colorplus: true
幅度克制：|值| 一般不超过 30，严重问题最多 60。只输出有依据的参数。

评语：「{critique}」

严格用以下 JSON 回复（不要输出其他内容）：
{{
  "params": {{"exposure": 0, "contrast": 0, "enable_denoise": false}},
  "reason": "<中文一句话：做了什么调整，依据评语哪句>"
}}"""


def params_from_critique(provider: VisionModelProvider, critique: str) -> dict:
    """评审文本 -> {合法参数}（白名单 + 范围 clamp）。无问题/解析失败返回 {}。"""
    result = provider.chat(
        [{"role": "user", "content": [text_content(ENHANCE_PROMPT.format(critique=critique[:1200]))]}],
        json_mode=True,
    )
    try:
        data = json.loads(result.text)
    except json.JSONDecodeError:
        return {}
    raw = data.get("params") or {}
    params: dict = {}
    for k, v in raw.items():
        if k in PARAM_RANGES:
            lo, hi = PARAM_RANGES[k]
            try:
                params[k] = int(max(lo, min(hi, int(v))))
            except (TypeError, ValueError):
                continue
        elif k in SWITCHES and bool(v):
            params[k] = True
    params = {k: v for k, v in params.items() if v is True or v != 0}
    if params:
        params["_reason"] = data.get("reason", "")
    return params


def _apply_params_cv2(img: np.ndarray, params: dict) -> np.ndarray:
    """无 .insp 时的 cv2 等价调整（近似，非影石 ISP）。"""
    out = img.astype(np.float32)
    if "exposure" in params:
        out *= 1 + params["exposure"] / 200.0
    if "brightness" in params:
        out += params["brightness"] / 100.0 * 48.0
    if "contrast" in params:
        a = 1 + params["contrast"] / 100.0
        out = (out - 128.0) * a + 128.0
    if "warmth" in params:
        w = params["warmth"] * 0.3
        out[..., 0] -= w  # B
        out[..., 2] += w  # R
    if "tint" in params:
        out[..., 1] -= params["tint"] * 0.3  # G
    out = np.clip(out, 0, 255)
    if "saturation" in params or "vibrance" in params:
        s = params.get("saturation", 0) + params.get("vibrance", 0) * 0.6
        hsv = cv2.cvtColor(out.astype(np.uint8), cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[..., 1] = np.clip(hsv[..., 1] * (1 + s / 100.0), 0, 255)
        out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)
    out8 = out.astype(np.uint8)
    if params.get("enable_denoise"):
        out8 = cv2.fastNlMeansDenoisingColored(out8, None, 7, 7, 5, 15)
    if "definition" in params and params["definition"] > 0:
        blur = cv2.GaussianBlur(out8, (0, 0), 3)
        amt = params["definition"] / 100.0 * 1.2
        out8 = cv2.addWeighted(out8, 1 + amt, blur, -amt, 0)
    return out8


def enhance_final(
    provider: VisionModelProvider,
    run_dir: Path,
    pano_path: Path,
    view_spec: dict,
    critique: str,
) -> dict | None:
    """主入口。返回 {"path", "params", "via", "reason"}；无参数可调返回 None。"""
    params = params_from_critique(provider, critique)
    if not params:
        return None
    reason = params.pop("_reason", "")
    dst = run_dir / "final_enhanced.jpg"

    raw_insp = pano_path.parent / "_raw" / f"{pano_path.stem}.insp"
    if raw_insp.exists():
        enhanced_pano = run_dir / "enhanced_panorama.jpg"
        if stitch_insp(raw_insp, enhanced_pano, params=params):
            gen = PanoramaViewGenerator(enhanced_pano)
            spec = ViewSpec(
                view_id="final_enhanced",
                yaw=float(view_spec.get("yaw", 0)), pitch=float(view_spec.get("pitch", 0)),
                roll=float(view_spec.get("roll", 0)), fov=float(view_spec.get("fov", 60)),
                width=int(view_spec.get("width", 1200)), height=int(view_spec.get("height", 800)),
            )
            out = gen.render_to_file(spec, run_dir)  # -> run_dir/final_enhanced.jpg
            return {"path": out, "params": params, "via": "mediasdk", "reason": reason}

    final_src = run_dir / "final.jpg"
    if not final_src.exists():
        return None
    data = np.fromfile(str(final_src), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        return None
    out = _apply_params_cv2(img, params)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        return None
    buf.tofile(str(dst))
    return {"path": dst, "params": params, "via": "cv2", "reason": reason}
