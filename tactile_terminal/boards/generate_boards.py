# -*- coding: utf-8 -*-
"""
触觉展示板生成器(独立于产品外壳的"触摸面板")
=============================================
与产品一致的点阵语言: 150x150 底板上 37x37 个方点(3mm点+1mm间隙),
椅子/桌面+杯子/门/楼梯/路径 全部由点的高度"画"出来。
单色单材料, 信息只通过高度表达。

三块内容板 x 两个高度版本:
  chair       椅子   (俯视: 椅面L3 + 四腿L2 + 靠背L2)
  table_cup   桌面+杯子 (桌面L1 + 杯身L3环 + 杯柄L2)
  door_path   门/楼梯/路径 (路径L1 + 门L3 + 楼梯逐级)

版本(高度均为"点凸出于底板"的高度):
  practical  实用版: 底板3mm, 背景点1 L1=2 L2=3.5 L3=5   (总高8mm)
  demo       展示版: 底板5mm, 背景点2 L1=10 L2=20 L3=30  (总高35mm)

网格: 1mm 高度场整体成网(heightfield.build_grid), 严格流形, 切片器无警告。
输出: boards/dist/{name}_{version}.stl + .3mf
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from generate_model import write_stl, write_3mf
from heightfield import build_grid

DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")

BOARD = 150          # 板边长 mm
DOTS = 37            # 37x37 点阵
PIN = 3              # 点边长 mm
PITCH = 4            # 点距 mm (37*4 = 148, 余 2mm 边距)

VERSIONS = {
    "practical": dict(base=3.0, BG=1.0, L1=2.0,  L2=3.5,  L3=5.0),
    "demo":      dict(base=5.0, BG=2.0, L1=10.0, L2=20.0, L3=30.0),
}

# ---------------- 点阵内容定义(37x37, 值为点凸出高度mm) ----------------
def grid():
    return [[0.0]*DOTS for _ in range(DOTS)]

def fill(g, r0, c0, r1, c1, h):
    for r in range(r0, r1+1):
        for c in range(c0, c1+1):
            g[r][c] = h

def dots_chair(v):
    g = grid()
    fill(g, 0, 0, DOTS-1, DOTS-1, v["BG"])            # 背景点
    for r0, c0 in ((11,11),(11,22),(22,11),(22,22)):  # 四腿 L2 (略出椅面角)
        fill(g, r0, c0, r0+2, c0+2, v["L2"])
    fill(g, 12, 12, 23, 23, v["L3"])                  # 椅面 12x12 L3
    fill(g, 24, 12, 27, 23, v["L2"])                  # 靠背 L2
    return g

def dots_table_cup(v):
    g = grid()
    fill(g, 0, 0, DOTS-1, DOTS-1, v["BG"])
    fill(g, 6, 2, 30, 34, v["L1"])                    # 桌面
    fill(g, 13, 13, 21, 21, v["L3"])                  # 杯身外环
    fill(g, 15, 15, 19, 19, v["L1"])                  # 杯内(露出桌面)
    fill(g, 15, 22, 19, 24, v["L2"])                  # 杯柄
    return g

def dots_door_path(v):
    g = grid()
    fill(g, 0, 0, DOTS-1, DOTS-1, v["BG"])
    for k in range(4):                                # 楼梯: 4级逐级升高
        fill(g, 3+4*k, 3, 6+4*k, 12, v["L3"]*(k+1)/4.0)
    fill(g, 2, 24, 26, 27, v["L1"])                   # 路径
    fill(g, 27, 21, 34, 31, v["L3"])                  # 门
    return g

BOARDS = {
    "chair": dots_chair,
    "table_cup": dots_table_cup,
    "door_path": dots_door_path,
}

# ---------------- 点阵 -> 1mm 高度场 ----------------
def raster(dots, base):
    span = DOTS*PITCH                      # 148, 两侧各留 1mm 边
    n = BOARD
    top = [[base]*n for _ in range(n)]
    for r in range(DOTS):
        for c in range(DOTS):
            x0 = 1 + c*PITCH
            y0 = 1 + r*PITCH
            h = base + dots[r][c]
            for yy in range(y0, y0+PIN):
                for xx in range(x0, x0+PIN):
                    top[yy][xx] = h
    return top

def main():
    os.makedirs(DIST, exist_ok=True)
    for name, fn in BOARDS.items():
        for ver, v in VERSIONS.items():
            mesh = build_grid(raster(fn(v), v["base"]))
            tag = f"{name}_{ver}"
            stl = os.path.join(DIST, tag + ".stl")
            write_stl(mesh, stl, tag)
            write_3mf({tag: mesh}, os.path.join(DIST, tag + ".3mf"))
            print(f"{tag:22s} tris={len(mesh.tris):7d}")

if __name__ == "__main__":
    main()
