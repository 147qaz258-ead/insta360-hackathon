"""生成带已知地标的合成 360° 等距柱状全景图，用于离线验证投影与管线。

地标布局（yaw 向右为正，0°=图像中心）：
  yaw=0°    : 红色圆形标记 "A0"（正对初始方向）
  yaw=60°   : 绿色矩形标记 "B60"
  yaw=120°  : 蓝色人形标记 "C120"
  yaw=180°  : 黄色高塔标记 "D180"（接缝处，验证环绕）
  yaw=-120° : 紫色方块 "E240"
  yaw=-60°  : 青色三角 "F300"
  pitch=+90 : 天顶白色太阳
  pitch=-90 : 底部深灰地面
"""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 4096, 2048


def uv(yaw: float, pitch: float) -> tuple[int, int]:
    u = int((yaw / 360.0 + 0.5) * W) % W
    v = int((0.5 - pitch / 180.0) * H)
    return u, v


def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("arial.ttf", "msyh.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def generate(out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 背景：天空->地平线->地面 垂直渐变
    img = np.zeros((H, W, 3), dtype=np.uint8)
    for v in range(H):
        t = v / H
        if t < 0.5:  # sky: 亮蓝 -> 浅蓝
            k = t / 0.5
            color = (int(230 - 60 * k), int(180 - 30 * k), int(120 - 20 * k))
        else:  # ground: 草绿 -> 深绿
            k = (t - 0.5) / 0.5
            color = (int(60 + 20 * k), int(140 - 60 * k), int(50 - 20 * k))
        img[v, :] = color

    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)
    font = _font(90)

    def label(yaw, pitch, text, color, shape="circle", size=70):
        u, v = uv(yaw, pitch)
        if shape == "circle":
            draw.ellipse([u - size, v - size, u + size, v + size], fill=color)
        elif shape == "rect":
            draw.rectangle([u - size, v - size, u + size, v + size], fill=color)
        elif shape == "person":
            draw.ellipse([u - size // 2, v - size * 2, u + size // 2, v - size], fill=color)  # head
            draw.rectangle([u - size // 2, v - size, u + size // 2, v + size], fill=color)  # body
        elif shape == "tower":
            draw.polygon([(u - size // 3, v + size * 3), (u + size // 3, v + size * 3), (u, v - size * 2)], fill=color)
        elif shape == "triangle":
            draw.polygon([(u, v - size), (u - size, v + size), (u + size, v + size)], fill=color)
        draw.text((u, v - size - 110), text, fill=(255, 255, 255), font=font, anchor="mm")

    label(0, 0, "A0", (220, 40, 40), "circle")
    label(60, 0, "B60", (40, 180, 40), "rect")
    label(120, -5, "C120", (40, 80, 220), "person")
    label(180, 0, "D180", (240, 200, 30), "tower")
    label(-120, 0, "E240", (160, 60, 200), "rect")
    label(-60, 0, "F300", (30, 200, 200), "triangle")
    label(0, 80, "SUN", (255, 255, 240), "circle", size=120)

    # 一条"道路"：从底部中央延伸到 yaw=0 地平线
    for v in range(H // 2, H):
        width = int(30 + 250 * (v - H / 2) / (H / 2))
        u, _ = uv(0, 0)
        draw.rectangle([u - width, v, u + width, v], fill=(150, 140, 130))

    final = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".jpg", final, [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert ok
    buf.tofile(str(out_path))
    return out_path


if __name__ == "__main__":
    import sys
    out = generate(sys.argv[1] if len(sys.argv) > 1 else "input/test_pano.jpg")
    print(f"generated: {out}")
