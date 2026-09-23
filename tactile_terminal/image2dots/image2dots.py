# -*- coding: utf-8 -*-
"""
image2dots —— 图片 -> 触觉点阵转换器(项目核心管线)
==================================================
流程: 图片 -> 灰度/对比度 -> 裁剪 -> 点阵栅格化 -> 中值去噪
      -> 4级高度量化 -> 规则方点高度场 -> 严格流形 STL/3MF

与"照片直接转STL"的区别(参考图的毛病):
  - 点严格落在网格上, 方形, 同尺寸同材料
  - 去噪+量化, 无碎点无噪声, 只有 4 个高度层级
  - 顶部天花板等无效区域强制压平为背景

运行: python image2dots.py  (参数见下方 P)
输出: dist/bold_stage.stl + .3mf + bold_stage_preview.png(高度图预览)
"""

import os
import sys

from PIL import Image, ImageOps, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from generate_model import write_stl, write_3mf
from heightfield import build_grid

P = dict(
    img=r"D:\xwechat_files\wxid_n7vvnrc1ackg22_22f3\temp\RWTemp\2026-09\25bd27c8a0cab2d2866c5a229969a47a\8a634fc43b045151c626ff911c7a5f50.jpg",
    out="bold_stage",
    board_w=240,             # 板宽 mm
    pin=2.0, gap=1.0,        # 点边长 / 间隙 mm  (pitch=3, 80列, 点粗壮耐触摸)
    base=3.0,                # 底板厚
    levels=(1.0, 3.0, 5.0, 8.0),   # BG/L1/L2/L3 点凸出高度 mm
    crop_top=0.23,           # 裁掉顶部天花板
    crop_bottom=0.0,
    # 分带处理: (行起, 行止, 量化分位, 中值次数)  灯光带/大屏带/地面带
    bands=((0.00, 0.20, (25, 55, 82), 1),
           (0.20, 0.58, (40, 70, 90), 0),
           (0.58, 1.01, (50, 78, 93), 1)),
    # AI语义覆写: 大屏标题用文字引擎重绘(照片缩小后文字不可读)
    # (文字, 字体, 字号px, 行位置=行占比, 层级索引)
    texts=(("BOLD MAKER 2026", "arialbd.ttf", 7, 0.25, 3),
           ("智能影像挑战赛", "msyh.ttc", 7, 0.41, 3)),
    text_zone=(0.20, 0.55, 4),   # 文字背景"屏幕"区: (行起,行止,边距列) 先压平到L1
)
DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")

def load_dot_luminance(p):
    from PIL import ImageChops
    im = Image.open(p["img"]).convert("L")
    w, h = im.size
    im = im.crop((0, int(h*p["crop_top"]), w, int(h*(1-p["crop_bottom"]))))
    # 局部特征增强: 原图 - 高斯背景 = 文字/灯光等局部结构, 再与原图混合
    blur = im.filter(ImageFilter.GaussianBlur(radius=im.size[0]//60))
    hp = ImageChops.subtract(im, blur, scale=1.0, offset=128)
    im = ImageOps.autocontrast(Image.blend(im, hp, 0.65), cutoff=1)
    cols = int(p["board_w"] / (p["pin"] + p["gap"]))
    rows = cols * im.size[1] // im.size[0]
    im = im.resize((cols, rows), Image.LANCZOS)
    px = list(im.getdata())
    return [px[r*cols:(r+1)*cols] for r in range(rows)]

def median3(g, rounds):
    rows, cols = len(g), len(g[0])
    for _ in range(rounds):
        out = [row[:] for row in g]
        for r in range(rows):
            for c in range(cols):
                vals = sorted(g[rr][cc]
                              for rr in range(max(0,r-1), min(rows,r+2))
                              for cc in range(max(0,c-1), min(cols,c+2)))
                out[r][c] = vals[len(vals)//2]
        g = out
    return g

def quantize_bands(g, p):
    rows = len(g)
    lv = p["levels"]
    out = [row[:] for row in g]
    all_thr = []
    for f0, f1, pct, med in p["bands"]:
        r0, r1 = int(rows*f0), min(rows, int(rows*f1))
        band = median3([row[:] for row in g[r0:r1]], med)
        flat = sorted(v for row in band for v in row)
        n = len(flat)
        t = [flat[min(n-1, n*q//100)] for q in pct]
        all_thr.append(t)
        for r in range(r0, r1):
            for c, v in enumerate(band[r-r0]):
                k = 0
                while k < 3 and v > t[k]:
                    k += 1
                out[r][c] = lv[k]
    return out, all_thr

def overlay_text(dots, p):
    from PIL import ImageFont, ImageDraw
    rows, cols = len(dots), len(dots[0])
    lv = p["levels"]
    if "text_zone" in p:
        f0, f1, mgn = p["text_zone"]
        for r in range(int(rows*f0), int(rows*f1)):
            for c in range(mgn, cols-mgn):
                dots[r][c] = lv[1]
    for text, font_name, size, fy, lk in p["texts"]:
        try:
            font = ImageFont.truetype(font_name, size)
        except OSError:
            try:
                font = ImageFont.truetype("C:/Windows/Fonts/" + font_name, size)
            except OSError:
                font = ImageFont.load_default()
        dr = ImageDraw.Draw(Image.new("L", (cols, rows), 0))
        while size > 4:
            bb = dr.textbbox((0, 0), text, font=font)
            if bb[2]-bb[0] <= cols-8:
                break
            size -= 1
            try:
                font = ImageFont.truetype(font_name, size)
            except OSError:
                font = ImageFont.truetype("C:/Windows/Fonts/" + font_name, size)
        im = Image.new("L", (cols, rows), 0)
        ImageDraw.Draw(im).text((cols//2, int(rows*fy)), text,
                                font=font, fill=255, anchor="ma")
        print(f"text '{text}' -> font size {size}")
        q = im.load()
        for r in range(rows):
            for c in range(cols):
                if q[c, r] > 96:
                    dots[r][c] = lv[lk]
    return dots

def raster(dots, p):
    pitch = p["pin"] + p["gap"]
    rows, cols = len(dots), len(dots[0])
    pin, base = int(p["pin"]), int(p["pin"]+p["gap"])
    w, d = cols*int(pitch), rows*int(pitch)
    top = [[p["base"]]*w for _ in range(d)]
    for r in range(rows):
        for c in range(cols):
            h = p["base"] + dots[r][c]
            for yy in range(r*int(pitch), r*int(pitch)+pin):
                for xx in range(c*int(pitch), c*int(pitch)+pin):
                    top[yy][xx] = h
    return top, w, d

def save_preview(dots, p, path):
    rows, cols = len(dots), len(dots[0])
    lv = p["levels"]
    s = 10
    im = Image.new("L", (cols*s, rows*s))
    px = im.load()
    lo, hi = lv[0], lv[-1]
    for r in range(rows):
        for c in range(cols):
            v = int(40 + 215*(dots[r][c]-lo)/(hi-lo))
            for yy in range(r*s, r*s+s-1):
                for xx in range(c*s, c*s+s-1):
                    px[xx, yy] = v
    im.save(path)

def main():
    os.makedirs(DIST, exist_ok=True)
    g = load_dot_luminance(P)
    dots, thr = quantize_bands(g, P)
    dots = overlay_text(dots, P)
    rows, cols = len(dots), len(dots[0])
    from collections import Counter
    dist = Counter(v for row in dots for v in row)
    print(f"grid {cols}x{rows}  band thresholds={thr}")
    print("level distribution:", dict(sorted(dist.items())))
    top, w, d = raster(dots, P)
    mesh = build_grid(top)
    stl = os.path.join(DIST, P["out"] + ".stl")
    write_stl(mesh, stl, P["out"])
    write_3mf({P["out"]: mesh}, os.path.join(DIST, P["out"] + ".3mf"))
    save_preview(dots, P, os.path.join(DIST, P["out"] + "_preview.png"))
    print(f"board {w}x{d}mm  tris={len(mesh.tris)}  -> {stl}")

if __name__ == "__main__":
    main()
