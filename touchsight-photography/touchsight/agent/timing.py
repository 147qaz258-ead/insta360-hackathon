"""拍摄时机选择（"时机不用看"）：VLM 对连拍批次做跨时刻比较。

哲学与 360° 构图一致：先记录，后选择。
视障用户不需要知道"什么时候拍"——相机连拍采样 N 个时刻，
Agent 比较谁睁眼、谁微笑、谁看镜头、姿态是否自然，选出最佳时刻。

清晰度前置淘汰（L1）：机测 Laplacian 方差明显低于批次最高值的时刻
直接淘汰不进 VLM 比较——模糊无法靠构图救回，修复交给源头选择。
"""
from __future__ import annotations

from pathlib import Path

from touchsight.vlm.providers import VisionModelProvider, image_content, text_content

TIMING_PROMPT = """你是一名摄影图片编辑，服务对象是一位视障用户。
用户说了拍摄意图后，相机在相近时间连续拍摄了 {n} 张 360° 全景照片（同一机位、不同时刻，编号 1~{n}）。
这些图都是等距柱状全景：水平方向是 360° 环绕，垂直方向从天到地，画面中央是相机正前方。

用户的拍摄意图：「{intent}」

请比较这 {n} 个时刻，选出最好的一张，评判重点：
1. 人物状态：是否睁眼、表情是否自然、是否面向镜头方向、姿态是否好看（合照尤其重要）
2. 场景变化：有没有人走动造成的残影/遮挡、光线变化、画面元素是否完整
3. 与意图的匹配度

每张时刻附有机测清晰度指数（1.00 = 本批最清晰）。明显模糊的时刻已在机测阶段淘汰，
若你观察到的画面与指数矛盾（如指数高但局部运动模糊），以你的观察为准并在点评中说明。

严格用以下 JSON 格式回复（不要输出其他内容）：
{{
  "best": <编号, 1~{n}>,
  "reason": "<中文，一句话说明为什么这个时刻最好>",
  "notes": ["<每张一句话点评>", "..."]
}}"""

# 机测清晰度低于最高值 * SHARP_KEEP_RATIO 的时刻直接淘汰
SHARP_KEEP_RATIO = 0.45


def sharpness_score(path: Path) -> float:
    """Laplacian 方差，在缩放到 1024 宽的中央水平带计算（避开全景两极拉伸区）。"""
    import cv2
    import numpy as np
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    h, w = img.shape
    if w > 1024:
        img = cv2.resize(img, (1024, int(h * 1024 / w)))
    band = img[int(img.shape[0] * 0.25):int(img.shape[0] * 0.75), :]
    return float(cv2.Laplacian(band, cv2.CV_64F).var())


def _full_notes(n: int, alive: list[int], alive_notes: list[str], scores: list[float], max_s: float) -> list[str]:
    """把 VLM 对存活时刻的点评映射回原始编号，被淘汰的标注原因。"""
    import re
    out = []
    for i in range(n):
        if i in alive:
            j = alive.index(i)
            note = alive_notes[j] if j < len(alive_notes) else ""
            out.append(re.sub(r"^\s*时刻\s*\d+\s*[：:]\s*", "", note))
        else:
            out.append(f"机测清晰度仅为本批最高值的 {scores[i] / max_s:.0%}（运动模糊），已淘汰")
    return out


def select_best_moment(
    provider: VisionModelProvider,
    pano_paths: list[Path],
    intent: str,
) -> dict:
    """返回 {"best_index": int(0基), "best_path": Path, "reason": str, "notes": list[str]}"""
    n = len(pano_paths)
    if n == 1:
        return {"best_index": 0, "best_path": pano_paths[0], "reason": "仅一张照片", "notes": []}

    scores = [sharpness_score(p) for p in pano_paths]
    max_s = max(scores) or 1.0
    alive = [i for i in range(n) if scores[i] >= SHARP_KEEP_RATIO * max_s]
    if not alive:
        alive = [scores.index(max_s)]
    sharpness = {p.name: round(s / max_s, 3) for p, s in zip(pano_paths, scores)}

    if len(alive) == 1:
        i = alive[0]
        return {"best_index": i, "best_path": pano_paths[i],
                "reason": f"仅时刻{i + 1}机测清晰度达标，其余时刻因运动模糊淘汰",
                "notes": _full_notes(n, alive, ["机测清晰度最高，直接选定"], scores, max_s),
                "sharpness": sharpness}

    m = len(alive)
    content: list[dict] = [text_content(TIMING_PROMPT.format(n=m, intent=intent))]
    for j, i in enumerate(alive, 1):
        content.append(text_content(f"【时刻 {j}】（机测清晰度指数 {scores[i] / max_s:.2f}）"))
        content.append(image_content(pano_paths[i], detail="low"))

    result = provider.chat([{"role": "user", "content": content}], json_mode=True)
    import json
    try:
        data = json.loads(result.text)
        best = int(data.get("best", 1))
        best = max(1, min(m, best))
        best_index = alive[best - 1]
        return {
            "best_index": best_index,
            "best_path": pano_paths[best_index],
            "reason": data.get("reason", ""),
            "notes": _full_notes(n, alive, data.get("notes", []), scores, max_s),
            "sharpness": sharpness,
        }
    except (json.JSONDecodeError, ValueError, TypeError):
        return {"best_index": 0, "best_path": pano_paths[0],
                "reason": f"时刻选择解析失败，默认取第1张。原始回复：{result.text[:200]}", "notes": [],
                "sharpness": sharpness}
