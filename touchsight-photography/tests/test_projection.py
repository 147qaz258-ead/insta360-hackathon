"""验证全景->透视投影：各地标应出现在对应取景方向的画面中心。"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from touchsight.panorama.views import PanoramaViewGenerator, ViewSpec, imread_unicode
from scripts.make_test_pano import generate

TMP = Path(__file__).resolve().parent / "_tmp"


def color_mask(img, bgr, tol=60):
    diff = np.abs(img.astype(int) - np.array(bgr)).sum(axis=2)
    return diff < tol


def centroid_near_center(img, bgr, name, max_offset_ratio=0.12, dy_ratio=None):
    mask = color_mask(img, bgr)
    ys, xs = np.nonzero(mask)
    assert len(xs) > 50, f"{name}: 未找到目标颜色 {bgr}，只有 {len(xs)} 像素"
    cx, cy = xs.mean(), ys.mean()
    h, w = img.shape[:2]
    dx, dy = abs(cx - w / 2) / w, abs(cy - h / 2) / h
    dy_lim = dy_ratio if dy_ratio is not None else max_offset_ratio + 0.05
    status = "OK " if (dx < max_offset_ratio and dy < dy_lim) else "FAIL"
    print(f"  [{status}] {name}: 质心=({cx:.0f},{cy:.0f}) 中心=({w/2:.0f},{h/2:.0f}) 偏移=({dx:.3f},{dy:.3f})")
    return status == "OK "


def main():
    TMP.mkdir(exist_ok=True)
    pano_path = generate(TMP / "test_pano.jpg")
    gen = PanoramaViewGenerator(pano_path)
    print(f"全景图: {gen.pano_width}x{gen.pano_height}")

    ok = True
    # yaw=0 看 A0 红圆
    img = gen.render_to_file(ViewSpec("t1", yaw=0, pitch=0, fov=80), TMP)
    ok &= centroid_near_center(imread_unicode(img), (40, 40, 220), "A0红圆 @yaw0")
    # yaw=60 看 B60 绿块
    img = gen.render_to_file(ViewSpec("t2", yaw=60, pitch=0, fov=80), TMP)
    ok &= centroid_near_center(imread_unicode(img), (40, 180, 40), "B60绿块 @yaw60")
    # yaw=120 看 C120 蓝人
    img = gen.render_to_file(ViewSpec("t3", yaw=120, pitch=0, fov=80), TMP)
    ok &= centroid_near_center(imread_unicode(img), (220, 80, 40), "C120蓝人 @yaw120")
    # yaw=180 看 D180 黄塔（接缝环绕；塔形质心天然偏下，主要验水平居中）
    img = gen.render_to_file(ViewSpec("t4", yaw=180, pitch=0, fov=80), TMP)
    ok &= centroid_near_center(imread_unicode(img), (30, 200, 240), "D180黄塔 @yaw180接缝", dy_ratio=0.25)
    # yaw=-120 看 E240 紫块
    img = gen.render_to_file(ViewSpec("t5", yaw=-120, pitch=0, fov=80), TMP)
    ok &= centroid_near_center(imread_unicode(img), (200, 60, 160), "E240紫块 @yaw-120")
    # pitch=+80 正对 SUN（极地拉伸区，验水平居中与大致纵向位置）
    img = gen.render_to_file(ViewSpec("t6", yaw=0, pitch=80, fov=60), TMP)
    ok &= centroid_near_center(imread_unicode(img), (240, 255, 255), "SUN @pitch+80", dy_ratio=0.25)

    # overview 生成完整性
    entries = gen.generate_overview(TMP / "overview")
    assert len(entries) == 8, f"overview 应生成 8 张视图，实际 {len(entries)}"
    assert all(Path(e["path"]).exists() for e in entries)
    print(f"  [OK ] overview 生成 {len(entries)} 张视图 + views.json")

    print("\n全部通过" if ok else "\n存在失败项！")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
