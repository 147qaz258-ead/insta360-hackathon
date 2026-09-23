# -*- coding: utf-8 -*-
"""
手持式智能视觉辅助终端 - 参数化 3D 打印模型生成器
====================================================
形态: 横向主屏 + 左侧单手握把 + 顶部摄像头头 + 握把按键
复用 tactile_terminal/generate_model.py 的网格工具, 纯 Python 零依赖。

坐标约定(装配态): 屏幕朝 +Z(躺平建模), Y+ 为机身顶部(摄像头), X- 为左侧(握把)。
所有零件单独导出时 z 平移到 0 起, 逐个打印; 3MF 仅查看装配, 不要直接打印!

输出 (dist/):
  front_shell.stl    前壳(侧壁+前脸+屏幕窗+螺柱+各种缺口)   倒置打印(前脸朝床)
  back_shell.stl     后壳(平板盖, 粘在内壁凸边上)            正向打印
  grip_left.stl      左握把(分层曲面+插入舌, 胶粘)           正向打印
  camera_head.stl    顶部摄像头舱(镜头窗朝 -Z, 颈部插入机身)  正向打印
  buttons.stl        拍摄键+模式键(带触觉区分凸点)           正向打印
  handheld_terminal.3mf  整机装配(仅查看)

!! 屏尺寸警告: P 字典前 8 项是用户实测值(106.5x113, 显示区宽101.5),
   与"7寸 800x480"标称不符(7寸显示区应约152x91)——更像 5 寸屏。
   装机前务必用卡尺复核这 8 项, 改完重跑本脚本即可。
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "tactile_terminal"))
from generate_model import (Mesh, add_box, add_prism, add_cylinder, add_ring2,
                            rounded_rect_outline, write_stl, write_3mf)

# ======================== 参数 ========================
P = dict(
    # ---- 屏幕模组(!!实测占位, 与7寸标称不符, 装机前复核) ----
    pcb_W=106.5,         # 屏模组整板宽 X
    pcb_H=113.0,         # 屏模组整板高 Y
    pcb_t=10.0,          # 屏模组总厚(含驱动板折叠/排线)
    disp_W=101.6,        # 有效显示区宽
    disp_H=61.0,         # 有效显示区高(按800x480比例推算, 未实测)
    disp_cy=22.0,        # 显示区中心相对整板中心的 Y 偏移(显示区靠上, 驱动条在底)
    hole_dx=49.75,       # 安装孔相对中心 ±X (106.5/2 - 3.5)
    hole_dy=53.0,        # 安装孔相对中心 ±Y (113/2 - 3.5)

    # ---- 机身 ----
    side_clr=1.5,        # PCB 与内壁单边间隙
    wall=2.5,            # 侧壁厚
    corner_R=10.0,       # 机身圆角
    face_t=2.4,          # 前脸厚
    wall_z1=17.0,        # 侧壁顶(=前脸内面)
    lip_t=2.5,           # 后壳凸边高(z 0..lip_t)
    lip_w=1.5,           # 后壳凸边宽
    cover_t=2.0,         # 后壳厚(坐在凸边上)
    post_r=3.0,          # PCB 支撑柱半径(实心, 屏贴面固定)
    post_h=2.5,          # 支撑柱高(从凸边顶面起)

    # ---- 握把(左手) ----
    grip_len=68.0,       # 握把超出左侧壁长度
    grip_cy=-8.0,        # 握把中心 Y(略偏下半部)
    grip_r=11.0,         # 握把轮廓圆角
    tongue_len=14.0,     # 插入舌伸入机身长度
    tongue_w=40.0,       # 插入舌宽(Y)
    tongue_z0=5.0, tongue_z1=16.5,   # 插入舌 Z 范围

    # ---- 摄像头舱 ----
    pod_W=46.0,          # 舱宽 X
    pod_H=22.0,          # 舱高(超出顶边)
    pod_back=2.4,        # 舱背板(-Z)超出机背距离
    pod_front=2.4,       # 舱盖(+Z)超出前脸距离
    pod_wall=2.0,
    pod_R=8.0,
    lens_r=7.5,          # 镜头窗半径(朝 -Z)
    neck_w=30.0,         # 颈部宽(X), 插入机身
    neck_depth=12.0,     # 颈部插入深度(Y)
    neck_z0=5.0, neck_z1=16.5,

    # ---- 按键(握把顶面, 拇指区) ----
    btn_scan_W=18.0, btn_scan_D=14.0, btn_H=3.0,
    btn_mode_r=6.0,
    btn_scan_dx=-18.0,   # 相对握把根部的 X 偏移
    btn_mode_dx=-40.0,

    # ---- 底部走线缺口 ----
    cable_w=44.0,        # 底壁缺口宽(HDMI/USB/电源集中出线)
    cable_cx=0.0,

    arc_seg=12,
)
DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")

# 派生尺寸
W = P["pcb_W"] + 2 * (P["wall"] + P["side_clr"])     # 机身宽 113.5
H = P["pcb_H"] + 2 * (P["wall"] + P["side_clr"])     # 机身高 120.0
R = P["corner_R"]
w = P["wall"]
T = P["wall_z1"] + P["face_t"]                       # 整机厚 19.4
Z_LIP = P["lip_t"]                                   # 2.5
Z_COV0, Z_COV1 = Z_LIP, Z_LIP + P["cover_t"]         # 后壳 2.5..4.5
Z_POST1 = Z_COV1 + P["post_h"]                       # 柱顶 7.0


# ======================== 墙体分段(带缺口) ========================
def arc_band_outline(cx, cy, r_out, r_in, a0, a1, seg):
    """90度环形扇区轮廓(外墙角)。角度度制。"""
    pts = []
    for i in range(seg + 1):
        a = math.radians(a0 + (a1 - a0) * i / seg)
        pts.append((cx + r_out * math.cos(a), cy + r_out * math.sin(a)))
    for i in range(seg + 1):
        a = math.radians(a1 + (a0 - a1) * i / seg)
        pts.append((cx + r_in * math.cos(a), cy + r_in * math.sin(a)))
    return pts

def complement(span, gaps):
    """[-s, s] 去掉 gaps 区间后的剩余段。"""
    segs, cur = [], -span
    for g0, g1 in sorted(gaps):
        g0, g1 = max(g0, -span), min(g1, span)
        if g0 - cur > 0.5:
            segs.append((cur, g0))
        cur = max(cur, g1)
    if span - cur > 0.5:
        segs.append((cur, span))
    return segs

def wall_boxes(side, gaps):
    """单侧直墙(避开缺口)。z 0..wall_z1。"""
    z0, z1 = 0.0, P["wall_z1"]
    s = (W / 2 - R) if side in ("top", "bottom") else (H / 2 - R)
    out = []
    for a, b in complement(s, gaps):
        if side == "top":
            out.append((a, H / 2 - w, z0, b, H / 2, z1))
        elif side == "bottom":
            out.append((a, -H / 2, z0, b, -H / 2 + w, z1))
        elif side == "left":
            out.append((-W / 2, a, z0, -W / 2 + w, b, z1))
        else:
            out.append((W / 2 - w, a, z0, W / 2, b, z1))
    return out


# ======================== 零件 ========================
def build_front_shell():
    m = Mesh()
    s = P["arc_seg"]
    # 四个圆角墙角
    for cx, cy, a0 in ((W/2-R, H/2-R, 0), (-(W/2-R), H/2-R, 90),
                       (-(W/2-R), -(H/2-R), 180), (W/2-R, -(H/2-R), 270)):
        add_prism(m, arc_band_outline(cx, cy, R, R - w, a0, a0 + 90, s),
                  0.0, P["wall_z1"])
    # 四面直墙(带缺口): 左=握把舌口, 顶=摄像头颈口, 底=走线口
    gc, gw = P["grip_cy"], P["tongue_w"] / 2 + 1.0
    boxes = wall_boxes("left", [(gc - gw, gc + gw)])
    boxes += wall_boxes("top", [(-P["neck_w"] / 2 - 1, P["neck_w"] / 2 + 1)])
    boxes += wall_boxes("bottom", [(P["cable_cx"] - P["cable_w"] / 2,
                                    P["cable_cx"] + P["cable_w"] / 2)])
    boxes += wall_boxes("right", [])
    # 后壳凸边(内缩环, z 0..lip_t)
    add_ring2(m, rounded_rect_outline(0, 0, W - 2 * w, H - 2 * w, R - w, s),
              rounded_rect_outline(0, 0, W - 2 * w - 2 * P["lip_w"],
                                   H - 2 * w - 2 * P["lip_w"],
                                   R - w - P["lip_w"], s),
              0.0, Z_LIP)
    # 前脸(带屏幕窗的环, z wall_z1..T)
    win = rounded_rect_outline(0, P["disp_cy"], P["disp_W"] + 1.0,
                               P["disp_H"] + 1.0, 3.0, s)
    add_ring2(m, rounded_rect_outline(0, 0, W, H, R, s), win,
              P["wall_z1"], T)
    # PCB 支撑柱(实心, 屏贴柱顶, 胶/泡棉固定)
    for px in (-P["hole_dx"], P["hole_dx"]):
        for py in (-P["hole_dy"], P["hole_dy"]):
            add_cylinder(m, px, py, P["post_r"], Z_COV1, Z_POST1)
    for b in boxes:
        add_box(m, *b)
    return m

def build_back_shell():
    m = Mesh()
    add_prism(m, rounded_rect_outline(0, 0, W - 2 * w + 2.2, H - 2 * w + 2.2,
                                      R - w + 1.1, P["arc_seg"]),
              Z_COV0, Z_COV1)
    return m

def build_grip():
    """分层圆角棱柱叠出手掌握持曲面 + 插入舌。"""
    m = Mesh()
    x0 = -W / 2 - P["grip_len"]          # 握把尖端
    x1 = -W / 2 + 2.0                    # 探入侧壁 2mm(装配视觉贴合)
    # (z0, z1, 半宽yh): 两端窄中间鼓, 前后鼓出机身
    layers = [(-4.0, 2.0, 15.0), (2.0, 8.0, 19.0), (8.0, 14.0, 21.5),
              (14.0, 20.0, 21.0), (20.0, T + 4.0, 18.0)]
    for z0, z1, yh in layers:
        r = min(P["grip_r"], yh - 1.0)
        out = rounded_rect_outline((x0 + x1) / 2, P["grip_cy"], x1 - x0,
                                   2 * yh, r, P["arc_seg"])
        add_prism(m, out, z0, z1)
    # 插入舌(穿左侧壁缺口进腔体, 胶粘)
    add_box(m, -W / 2 - 2.0, P["grip_cy"] - P["tongue_w"] / 2, P["tongue_z0"],
            -W / 2 + P["tongue_len"], P["grip_cy"] + P["tongue_w"] / 2,
            P["tongue_z1"])
    return m

def build_camera_head():
    """顶部摄像头舱: 镜头窗朝 -Z, 颈部插入机身顶壁缺口。"""
    m = Mesh()
    s = P["arc_seg"]
    y_top = H / 2
    cy = y_top + 2.0 + P["pod_H"] / 2            # 舱中心 Y(压顶边2mm)
    z0, z1 = -P["pod_back"], T + P["pod_front"]  # 舱 Z 范围
    out = rounded_rect_outline(0, cy, P["pod_W"], P["pod_H"], P["pod_R"], s)
    # 背板带镜头窗(z0..z0+2)
    lens_y = cy + 2.0
    win = [(lens_r_c * math.cos(2 * math.pi * i / 24) + 0.0,
            lens_r_c * math.sin(2 * math.pi * i / 24) + lens_y)
           for i in range(24)
           for lens_r_c in [P["lens_r"]]]
    add_ring2(m, out, win, z0, z0 + 2.0)
    # 周壁
    add_ring2(m, out,
              rounded_rect_outline(0, cy, P["pod_W"] - 2 * P["pod_wall"],
                                   P["pod_H"] - 2 * P["pod_wall"],
                                   P["pod_R"] - P["pod_wall"], s),
              z0 + 2.0, z1 - 1.0)
    # 舱盖
    add_prism(m, out, z1 - 1.0, z1)
    # 颈部(插入机身)
    add_box(m, -P["neck_w"] / 2, y_top - P["neck_depth"], P["neck_z0"],
            P["neck_w"] / 2, y_top + 2.0, P["neck_z1"])
    return m

def build_buttons(z0):
    """拍摄键(大, 1凸点) + 模式键(小圆, 2凸点)。"""
    m = Mesh()
    gx = -W / 2
    # 拍摄键: 圆角矩形
    bx, by = gx + P["btn_scan_dx"], P["grip_cy"] + 2.0
    add_prism(m, rounded_rect_outline(bx, by, P["btn_scan_W"],
                                      P["btn_scan_D"], 4.0, P["arc_seg"]),
              z0, z0 + P["btn_H"])
    add_cylinder(m, bx, by, 1.6, z0 + P["btn_H"], z0 + P["btn_H"] + 1.2)
    # 模式键: 圆形
    mx = gx + P["btn_mode_dx"]
    add_cylinder(m, mx, by, P["btn_mode_r"], z0, z0 + P["btn_H"])
    for d in (-4.0, 4.0):
        add_cylinder(m, mx + d, by, 1.6, z0 + P["btn_H"],
                     z0 + P["btn_H"] + 1.2)
    return m


# ======================== 输出 ========================
def shift_z(mesh, dz):
    if abs(dz) < 1e-9:
        return mesh
    m = Mesh()
    for a, b, c in mesh.tris:
        m.tri((a[0], a[1], a[2] + dz), (b[0], b[1], b[2] + dz),
              (c[0], c[1], c[2] + dz))
    return m

def main():
    if P["pcb_W"] < 140:
        print("!! 注意: 当前屏尺寸与7寸标称不符(更像5寸), 装机前请复核 P 前8项")
    os.makedirs(DIST, exist_ok=True)
    asm = {
        "front_shell": build_front_shell(),
        "back_shell": build_back_shell(),
        "grip_left": build_grip(),
        "camera_head": build_camera_head(),
        "buttons": build_buttons(T + 4.0),
    }
    standalone = {
        "front_shell": (asm["front_shell"], 0.0, "倒置打印(前脸朝床)"),
        "back_shell": (asm["back_shell"], -Z_COV0, "正向打印"),
        "grip_left": (asm["grip_left"], 4.0, "正向打印"),
        "camera_head": (asm["camera_head"], P["pod_back"], "正向打印(镜头窗朝床)"),
        "buttons": (asm["buttons"], -(T + 4.0), "正向打印"),
    }
    for name, (mesh, dz, note) in standalone.items():
        path = os.path.join(DIST, name + ".stl")
        write_stl(shift_z(mesh, dz), path, name)
        xs = [p[i] for t in mesh.tris for p in t for i in (0,)]
        ys = [p[1] for t in mesh.tris for p in t]
        print(f"{name:13s} tris={len(mesh.tris):6d}  "
              f"{max(xs)-min(xs):6.1f} x {max(ys)-min(ys):6.1f} mm  {note}")
    mf = os.path.join(DIST, "handheld_terminal.3mf")
    write_3mf(asm, mf)
    print(f"3MF assembly -> {mf}")
    print(f"机身 {W:.1f} x {H:.1f} x {T:.1f} mm, 全部零件 < 256 打印盘")

if __name__ == "__main__":
    main()
