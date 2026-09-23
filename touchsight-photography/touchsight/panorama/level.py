"""全景地平线校正（L2b 兜底）：把"相机拿歪了"的全景图扶正。

原理：地平线（水平面）在全景球面上是一个大圆。相机水平时，它在等距柱状投影里
是赤道直线；相机倾斜 i° 时，它变成正弦曲线 φ(λ) = arctan(tan(i)·cos(λ−λ0))。
用 Canny 边缘对 (i, λ0) 网格搜索拟合这条曲线，再做球面旋转把它转回赤道。

定位：保守兜底。只在找到显著地平线（置信度高）时才校正，否则保持原图——
宁可漏校，不可误校。室内复杂穹顶结构会干扰拟合（置信度低时自动跳过）。
上位替代是陀螺仪元数据（L2a，.insp 内嵌姿态，确定解零误判），相机在场时接入。

这是经典 ISP 几何校正（边缘/曲线拟合），不做任何目标检测与内容识别。
"""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

_MAX_TILT_DEG = 60.0
_TILT_STEP = 1.5
_PHASE_STEP = 4.0
_WORK_W = 720
# 置信度阈值：最优曲线得分需明显高于全部候选的平均水平，否则认为找不到地平线
_MIN_CONFIDENCE = 2.2
# 小于该倾角视为已水平，不校正（避免重采样画质损失）
_MIN_TILT_APPLY = 1.5


def _read_bgr(path: Path) -> np.ndarray | None:
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _write_bgr(path: Path, img: np.ndarray) -> None:
    ok, buf = cv2.imencode(path.suffix or ".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise OSError(f"imencode failed: {path}")
    buf.tofile(str(path))


def _horizon_rows(tilt_deg: float, phase_deg: float, w: int, h: int) -> np.ndarray:
    """给定倾角 i 与相位 λ0，返回每个经度列上地平线所在的像素行。"""
    lam = (np.arange(w) + 0.5) / w * 360.0 - 180.0
    phi = np.degrees(np.arctan(math.tan(math.radians(tilt_deg)) *
                               np.cos(np.radians(lam - phase_deg))))
    return (90.0 - phi) / 180.0 * h


def estimate_horizon(pano_bgr: np.ndarray) -> dict:
    """拟合地平线。返回 {tilt_deg, phase_deg, confidence, score}；找不到时 confidence 低。"""
    h0, w0 = pano_bgr.shape[:2]
    w = _WORK_W
    h = int(w * h0 / w0)
    gray = cv2.cvtColor(cv2.resize(pano_bgr, (w, h)), cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 160).astype(np.float32) / 255.0
    # 地平线不会在天顶/地底，只在中纬度带内搜索
    edges[: int(h * 0.15), :] = 0
    edges[int(h * 0.85):, :] = 0

    best = {"tilt_deg": 0.0, "phase_deg": 0.0, "score": -1.0}
    scores = []
    rows_idx = np.arange(w)
    t = _TILT_STEP
    while t <= _MAX_TILT_DEG + 1e-9:
        p = 0.0
        while p < 360.0 - 1e-9:
            rows = _horizon_rows(t, p, w, h).astype(np.int32)
            s = float(edges[rows, rows_idx].sum())
            scores.append(s)
            if s > best["score"]:
                best = {"tilt_deg": t, "phase_deg": p, "score": s}
            p += _PHASE_STEP
        t += _TILT_STEP

    mean = float(np.mean(scores)) or 1.0
    best["confidence"] = best["score"] / mean

    # 局部细化（±2° 倾角、±5° 相位）
    if best["confidence"] >= _MIN_CONFIDENCE:
        t0, p0 = best["tilt_deg"], best["phase_deg"]
        t = max(_TILT_STEP, t0 - 2)
        while t <= min(_MAX_TILT_DEG, t0 + 2) + 1e-9:
            p = p0 - 5
            while p <= p0 + 5 + 1e-9:
                rows = _horizon_rows(t, p % 360.0, w, h).astype(np.int32)
                s = float(edges[rows, rows_idx].sum())
                if s > best["score"]:
                    best.update(tilt_deg=t, phase_deg=p % 360.0, score=s)
                p += 1.0
            t += 0.25
        best["confidence"] = best["score"] / mean
    return best


def _rotation_to_up(tilt_deg: float, phase_deg: float) -> np.ndarray:
    """把地平线大圆的极轴旋转到球面北极（世界竖直方向）的旋转矩阵（Rodrigues）。"""
    c = math.tan(math.radians(tilt_deg))
    a = c * math.cos(math.radians(phase_deg))
    b = c * math.sin(math.radians(phase_deg))
    u = np.array([-a, 1.0, -b])
    u /= np.linalg.norm(u)
    up = np.array([0.0, 1.0, 0.0])
    axis = np.cross(u, up)
    n = np.linalg.norm(axis)
    if n < 1e-9:
        return np.eye(3)
    axis /= n
    theta = math.acos(float(np.clip(np.dot(u, up), -1.0, 1.0)))
    kx, ky, kz = axis
    K = np.array([[0, -kz, ky], [kz, 0, -kx], [-ky, kx, 0]])
    return np.eye(3) + math.sin(theta) * K + (1 - math.cos(theta)) * (K @ K)


def rotate_equirect(img: np.ndarray, rot: np.ndarray) -> np.ndarray:
    """球面旋转等距柱状全景图（反向映射 + 经度环绕 + 双线性采样）。"""
    h, w = img.shape[:2]
    xs = (np.arange(w) + 0.5) / w * 360.0 - 180.0
    ys = 90.0 - (np.arange(h) + 0.5) / h * 180.0
    lam, phi = np.meshgrid(np.radians(xs), np.radians(ys))
    dirs = np.stack([np.cos(phi) * np.cos(lam), np.sin(phi), np.cos(phi) * np.sin(lam)], axis=-1)
    src = dirs @ rot  # rot.T 作用等价于取逆旋转
    src_lam = np.degrees(np.arctan2(src[..., 2], src[..., 0]))
    src_phi = np.degrees(np.arcsin(np.clip(src[..., 1], -1.0, 1.0)))
    map_x = ((src_lam + 180.0) / 360.0 * w - 0.5).astype(np.float32)
    map_y = ((90.0 - src_phi) / 180.0 * h - 0.5).astype(np.float32)
    return cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)


def level_panorama(path: Path, out_path: Path | None = None, max_rounds: int = 2) -> dict:
    """检测并按需扶正全景图（多轮迭代逼近）。返回处理记录（是否校正、角度、置信度）。"""
    img = _read_bgr(path)
    if img is None:
        return {"leveled": False, "reason": "无法读取图片"}
    passes = []
    cur = img
    for _ in range(max_rounds):
        est = estimate_horizon(cur)
        tilt, phase, conf = est["tilt_deg"], est["phase_deg"], est["confidence"]
        if conf < _MIN_CONFIDENCE:
            passes.append({"skipped": f"未找到可信地平线（置信度 {conf:.1f}），保持原图"})
            break
        if tilt < _MIN_TILT_APPLY:
            passes.append({"skipped": f"已基本水平（倾斜 {tilt:.1f}°）"})
            break
        rot = _rotation_to_up(tilt, phase)
        cur = rotate_equirect(cur, rot)
        passes.append({"tilt_deg": round(tilt, 2), "phase_deg": round(phase, 2),
                       "confidence": round(conf, 2)})
    applied = [p for p in passes if "tilt_deg" in p]
    if not applied:
        rec = {"leveled": False, "passes": passes}
        rec["reason"] = passes[-1].get("skipped", "未校正") if passes else "未校正"
        return rec
    dst = out_path or path
    _write_bgr(dst, cur)
    desc = " + ".join(f"{p['tilt_deg']:.1f}°@{p['phase_deg']:.0f}°" for p in applied)
    return {"leveled": True, "passes": passes, "reason": f"已扶正（{len(applied)} 轮）：{desc}"}
