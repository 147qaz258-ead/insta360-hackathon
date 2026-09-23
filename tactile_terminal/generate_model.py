# -*- coding: utf-8 -*-
"""
通用视觉触觉转换终端 - 参数化 3D 打印模型生成器
====================================================
纯 Python 零依赖，直接运行即可重新生成全部零件。

输出 (dist/):
  main_body.stl      外壳(含顶部框架、按钮/扬声器开孔、点阵沉台)  倒置打印
  tactile_panel.stl  触觉点阵面板(方形点, 仅高度变化)              正向打印
  buttons.stl        三个大按钮(开始/重复/模式, 带触觉区分点)      正向打印
  bottom_cover.stl   底盖                                          正向打印
  accent_ring.stl    底部蓝色装饰环(用蓝色 PLA 打印)               正向打印
  tactile_terminal.3mf  整机装配(仅用于查看整体, 不要直接打印)
  tactile_panel_left/right.stl  面板左右拼块(320 宽超出 256 打印盘)
  main_body/bottom_cover/accent_ring_{fl,fr,bl,br}.stl
                     超大件四分块(200x150, 适配打印盘, 胶水拼合)

单位: mm。所有尺寸见下方 PARAMS。
"""

import math
import os
import struct
import zipfile

from heightfield import build_grid

# ======================== 参数 ========================
P = dict(
    body_L=400.0,        # 整机长 X
    body_W=300.0,        # 整机宽 Y
    body_H=47.0,         # 整机高 Z (原35, 用户要求加高1/3)
    corner_R=12.0,       # 外壳圆角
    wall=2.5,            # 侧壁厚
    accent_h=3.0,        # 底部蓝色装饰环高度
    accent_t=2.0,        # 装饰环壁厚

    panel_W=320.0,       # 触觉面板 X
    panel_D=220.0,       # 触觉面板 Y
    panel_base=3.0,      # 面板基板厚度
    panel_cy=27.0,       # 面板中心 Y(前侧留控制区)
    pin=5.0,             # 方点边长
    pitch=6.0,           # 点距(点5 + 间隙1)
    pin_min=1.0,         # 最低点高
    pin_max=5.0,         # 最高点高
    pin_step=1.0,        # 高度量化步进 -> 1/2/3/4/5 五档

    frame_h=4.0,         # 顶部框架厚 (z H-4..H)
    ledge=3.0,           # 面板沉台托边宽度
    pocket_drop=4.0,     # 沉台深度(面板坐落的台阶)

    btn_W=56.0, btn_D=34.0, btn_H=5.0, btn_R=8.0,   # 按钮
    btn_xs=(-120.0, -40.0, 40.0), btn_cy=-116.0,     # 三个按钮中心
    grille_x0=108.0, grille_x1=188.0,                # 扬声器孔区
    grille_y0=-136.0, grille_y1=-96.0,
    grille_bar=2.0, grille_gap=3.0,

    cover_t=2.0,         # 底盖厚
    arc_seg=12,          # 每圆角分段数
)
DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")

# ======================== 网格基础 ========================
class Mesh:
    def __init__(self):
        self.tris = []  # [(p1,p2,p3), ...]
    def tri(self, a, b, c):
        self.tris.append((a, b, c))
    def quad(self, a, b, c, d):
        self.tri(a, b, c); self.tri(a, c, d)
    def extend(self, other):
        self.tris.extend(other.tris)

def add_box(m, x0, y0, z0, x1, y1, z1):
    v = [(x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),
         (x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)]
    m.quad(v[0],v[3],v[2],v[1])          # bottom
    m.quad(v[4],v[5],v[6],v[7])          # top
    m.quad(v[0],v[1],v[5],v[4])          # front
    m.quad(v[1],v[2],v[6],v[5])
    m.quad(v[2],v[3],v[7],v[6])
    m.quad(v[3],v[0],v[4],v[7])

def rounded_rect_outline(cx, cy, w, h, r, seg):
    """逆时针圆角矩形轮廓, 每角 seg 段。"""
    pts = []
    corners = [(cx+w/2-r, cy+h/2-r, 0.0),
               (cx-w/2+r, cy+h/2-r, 90.0),
               (cx-w/2+r, cy-h/2+r, 180.0),
               (cx+w/2-r, cy-h/2+r, 270.0)]
    for ccx, ccy, a0 in corners:
        for i in range(seg):
            a = math.radians(a0 + 90.0*i/seg)
            pts.append((ccx + r*math.cos(a), ccy + r*math.sin(a)))
    return pts

def add_prism(m, outline, z0, z1):
    """凸轮廓直拉伸体(扇形封顶)。"""
    n = len(outline)
    cx = sum(p[0] for p in outline)/n
    cy = sum(p[1] for p in outline)/n
    bot = [(x, y, z0) for x, y in outline]
    top = [(x, y, z1) for x, y in outline]
    cb, ct = (cx, cy, z0), (cx, cy, z1)
    for i in range(n):
        j = (i+1) % n
        m.quad(bot[i], bot[j], top[j], top[i])
        m.tri(cb, bot[j], bot[i])
        m.tri(ct, top[i], top[j])

def add_cylinder(m, cx, cy, r, z0, z1, seg=16):
    out = [(cx+r*math.cos(2*math.pi*i/seg), cy+r*math.sin(2*math.pi*i/seg))
           for i in range(seg)]
    add_prism(m, out, z0, z1)

# ======================== 网格裁切(切块打印) ========================
def clip_outline(pts, rect):
    """凸轮廓被矩形 (x0,y0,x1,y1) 裁剪 (Sutherland-Hodgman)。"""
    x0, y0, x1, y1 = rect
    def half(pts, axis, val, keep_greater):
        if not pts:
            return pts
        out = []
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i+1) % n]
            da = (a[axis]-val) if keep_greater else (val-a[axis])
            db = (b[axis]-val) if keep_greater else (val-b[axis])
            ina, inb = da >= -1e-9, db >= -1e-9
            if ina:
                out.append(a)
            if ina != inb:
                t = da/(da-db)
                q = [0.0, 0.0]
                q[axis] = val
                q[1-axis] = a[1-axis] + t*(b[1-axis]-a[1-axis])
                q = tuple(q)
                if not out or abs(q[0]-out[-1][0]) > 1e-7 \
                        or abs(q[1]-out[-1][1]) > 1e-7:
                    out.append(q)
        if len(out) > 1 and abs(out[0][0]-out[-1][0]) < 1e-7 \
                and abs(out[0][1]-out[-1][1]) < 1e-7:
            out.pop()
        return out
    pts = half(pts, 0, x0, True)
    pts = half(pts, 0, x1, False)
    pts = half(pts, 1, y0, True)
    pts = half(pts, 1, y1, False)
    return pts

def _bridge(m, O, I, flip):
    """三角化同一平面上两个凸环之间的环形面。flip=False 时法线朝 +z。"""
    no, ni = len(O), len(I)
    cx = sum(p[0] for p in I)/ni
    cy = sum(p[1] for p in I)/ni
    def ang(p):
        return math.atan2(p[1]-cy, p[0]-cx)
    kO = min(range(no), key=lambda k: ang(O[k]))
    kI = min(range(ni), key=lambda k: ang(I[k]))
    O = O[kO:] + O[:kO] + [O[kO]]
    I = I[kI:] + I[:kI] + [I[kI]]
    aO = [ang(p) for p in O]
    aI = [ang(p) for p in I]
    for k in range(1, len(aO)):
        while aO[k] < aO[k-1] - 1e-12:
            aO[k] += 2*math.pi
    for k in range(1, len(aI)):
        while aI[k] < aI[k-1] - 1e-12:
            aI[k] += 2*math.pi
    i = j = 0
    while i < no or j < ni:
        if j >= ni or (i < no and aO[i+1] <= aI[j+1] + 1e-12):
            a, b, c = O[i], O[i+1], I[j]
            i += 1
        else:
            a, b, c = O[i], I[j+1], I[j]
            j += 1
        ux, uy = b[0]-a[0], b[1]-a[1]
        vx, vy = c[0]-a[0], c[1]-a[1]
        if abs(ux*vy-uy*vx) < 1e-9:
            continue
        m.tri(a, c, b) if flip else m.tri(a, b, c)

def add_ring2(m, out_o, out_i, z0, z1):
    """两个凸轮廓(点数可不同)之间的环壳。"""
    ob = [(x, y, z0) for x, y in out_o]; ot = [(x, y, z1) for x, y in out_o]
    ib = [(x, y, z0) for x, y in out_i]; it = [(x, y, z1) for x, y in out_i]
    for i in range(len(ob)):
        j = (i+1) % len(ob)
        m.quad(ob[i], ob[j], ot[j], ot[i])   # 外壁
    for i in range(len(ib)):
        j = (i+1) % len(ib)
        m.quad(ib[j], ib[i], it[i], it[j])   # 内壁
    _bridge(m, ot, it, False)                # 顶环
    _bridge(m, ob, ib, True)                 # 底环

def clamp_box(b, rect):
    x0, y0, x1, y1 = b
    rx0, ry0, rx1, ry1 = rect
    x0, x1 = max(x0, rx0), min(x1, rx1)
    y0, y1 = max(y0, ry0), min(y1, ry1)
    if x1-x0 < 0.05 or y1-y0 < 0.05:
        return None
    return (x0, y0, x1, y1)

def mesh_from_prims(prims, rect=None):
    """由图元列表建网格; 给 rect=(x0,y0,x1,y1) 时按矩形精确裁剪。"""
    m = Mesh()
    for prim in prims:
        kind = prim[0]
        if kind == "ring":
            _, out_o, out_i, z0, z1 = prim
            if rect is not None:
                out_o = clip_outline(out_o, rect)
                # 内环裁剪向内微偏: 避免内外环在矩形角点重合产生零厚度尖点
                d = 0.02
                ri = (rect[0]+d, rect[1]+d, rect[2]-d, rect[3]-d)
                out_i = clip_outline(out_i, ri)
                if len(out_o) < 3 or len(out_i) < 3:
                    continue
            add_ring2(m, out_o, out_i, z0, z1)
        elif kind == "prism":
            _, out, z0, z1 = prim
            if rect is not None:
                out = clip_outline(out, rect)
                if len(out) < 3:
                    continue
            add_prism(m, out, z0, z1)
        else:
            _, x0, y0, z0, x1, y1, z1 = prim
            if rect is not None:
                r = clamp_box((x0, y0, x1, y1), rect)
                if r is None:
                    continue
                x0, y0, x1, y1 = r
            add_box(m, x0, y0, z0, x1, y1, z1)
    return m

# ======================== 地形示例 ========================
def terrain(nx, ny):
    """纯高度示例: 双峰 + 山谷, 归一化并量化到 pin_min..pin_max。"""
    def raw(x, y):
        return (0.60*math.exp(-((x-70)**2+(y-35)**2)/4500.0)
              + 0.55*math.exp(-((x+80)**2+(y+45)**2)/6000.0)
              + 0.40*math.exp(-((x+5)**2+(y-65)**2)/2800.0)
              - 0.50*math.exp(-((x-15)**2+(y+55)**2)/3800.0)
              + 0.15*math.sin(x/55.0)*math.cos(y/48.0))
    vals = []
    for j in range(ny):
        for i in range(nx):
            vals.append(raw(i, j))
    lo, hi = min(vals), max(vals)
    span = (hi-lo) or 1.0
    out = []
    for v in vals:
        t = (v-lo)/span
        h = P["pin_min"] + t*(P["pin_max"]-P["pin_min"])
        h = round(h/P["pin_step"])*P["pin_step"]
        out.append(max(P["pin_min"], min(P["pin_max"], h)))
    return out

# ======================== 零件 ========================
def main_body_prims():
    """外壳图元: 侧壁环 + 顶部框架梁 + 扬声器格栅 + 面板托边。"""
    L, W, H = P["body_L"], P["body_W"], P["body_H"]
    R, w, s = P["corner_R"], P["wall"], P["arc_seg"]
    z_frame0 = H - P["frame_h"]                    # 43
    prims = [("ring",
              rounded_rect_outline(0, 0, L, W, R, s),
              rounded_rect_outline(0, 0, L-2*w, W-2*w, R-w, s),
              P["accent_h"], H)]                   # 侧壁环: 装饰环之上到顶
    ix, iy = (L-2*w)/2, (W-2*w)/2                  # 内壁半尺寸 197.5, 147.5
    hx, hy = P["panel_W"]/2+1, P["panel_D"]/2+1    # 面板孔半尺寸 161, 111
    cy = P["panel_cy"]
    y0h, y1h = cy-hy, cy+hy                        # 孔范围 -84..138
    # 顶部框架 (z_frame0..H), 绕开面板孔与扬声器槽
    z0, z1 = z_frame0, H
    gx0, gx1 = P["grille_x0"], P["grille_x1"]
    gy0, gy1 = P["grille_y0"], P["grille_y1"]
    boxes = [
        (-ix, y1h, z0, ix, iy, z1),                # 后梁
        (-ix, y0h, z0, -hx, y1h, z1),              # 左梁
        (hx, y0h, z0, ix, y1h, z1),                # 右梁
        (-ix, -iy, z0, gx0, y0h, z1),              # 前梁(左段)
        (gx1, -iy, z0, ix, y0h, z1),               # 前梁(右段)
        (gx0, -iy, z0, gx1, gy0, z1),              # 孔区前段
        (gx0, gy1, z0, gx1, y0h, z1),              # 孔区后段
    ]
    x = gx0 + 2.0
    while x + P["grille_bar"] <= gx1 - 1.0:        # 格栅竖条
        boxes.append((x, gy0, z0, x+P["grille_bar"], gy1, z1))
        x += P["grille_bar"] + P["grille_gap"]
    # 面板沉台托边 (ledge), 向内突出, 顶面 z=H-3 与面板基板底面贴合
    lz0 = z_frame0 - 3.0                           # 40..44
    lz1 = z_frame0 + 1.0
    lg = P["ledge"]
    boxes += [
        (-hx, y1h-lg, lz0, hx, y1h, lz1),          # 后托边
        (-hx, y0h, lz0, hx, y0h+lg, lz1),          # 前托边
        (-hx, y0h+lg, lz0, -hx+lg, y1h-lg, lz1),   # 左托边
        (hx-lg, y0h+lg, lz0, hx, y1h-lg, lz1),     # 右托边
    ]
    prims += [("box",) + b for b in boxes]
    return prims

def build_main_body():
    return mesh_from_prims(main_body_prims())

def build_panel():
    m = Mesh()
    W, D, cy = P["panel_W"], P["panel_D"], P["panel_cy"]
    base_z0 = P["body_H"] - P["pocket_drop"] + 1.0             # 44: 坐在托边上
    add_box(m, -W/2, cy-D/2, base_z0, W/2, cy+D/2, base_z0+P["panel_base"])
    pin, pitch = P["pin"], P["pitch"]
    nx = int((W - pin) // pitch) + 1                           # 53
    ny = int((D - pin) // pitch) + 1                           # 36
    heights = terrain(nx, ny)
    ox = -(nx-1)*pitch/2 - pin/2
    oy = cy - (ny-1)*pitch/2 - pin/2
    zb = base_z0 + P["panel_base"]
    for j in range(ny):
        for i in range(nx):
            h = heights[j*nx+i]
            add_box(m, ox+i*pitch, oy+j*pitch, zb,
                    ox+i*pitch+pin, oy+j*pitch+pin, zb+h)
    return m

def build_panel_tiles():
    """产品面板拆分件: 320x220 超出 256 打印盘, 拆左右两块(可拼合)。
    独立打印件, z 从 0 起, 底板 3mm + 点 1..5mm。"""
    pin, pitch = P["pin"], P["pitch"]
    nx = int((P["panel_W"] - pin) // pitch) + 1          # 53
    ny = int((P["panel_D"] - pin) // pitch) + 1          # 36
    heights = terrain(nx, ny)
    base = P["panel_base"]                               # 3
    tiles = {}
    for tag, i0, i1 in (("left", 0, 27), ("right", 27, nx)):
        cols = i1 - i0
        w, d = cols*int(pitch), ny*int(pitch)
        top = [[base]*w for _ in range(d)]
        for j in range(ny):
            for i in range(i0, i1):
                h = base + heights[j*nx+i]
                for yy in range(j*int(pitch), j*int(pitch)+int(pin)):
                    for xx in range((i-i0)*int(pitch), (i-i0)*int(pitch)+int(pin)):
                        top[yy][xx] = h
        tiles[f"tactile_panel_{tag}"] = build_grid(top)
    return tiles

def build_buttons(z0=None):
    """z0=None 时为装配位置(机身顶面); 单独打印传 0.0。"""
    if z0 is None:
        z0 = P["body_H"]
    m = Mesh()
    for k, bx in enumerate(P["btn_xs"]):
        out = rounded_rect_outline(bx, P["btn_cy"], P["btn_W"], P["btn_D"],
                                   P["btn_R"], P["arc_seg"])
        add_prism(m, out, z0, z0+P["btn_H"])
        zt = z0 + P["btn_H"]
        n_dot = k + 1                                          # 触觉区分: 1/2/3 凸点
        for d in range(n_dot):
            dx = bx + (d - (n_dot-1)/2.0)*8.0
            add_cylinder(m, dx, P["btn_cy"], 1.6, zt, zt+1.2)
    return m

def bottom_cover_prims():
    return [("prism",
             rounded_rect_outline(0, 0, P["body_L"]-2*P["wall"]-0.4,
                                  P["body_W"]-2*P["wall"]-0.4,
                                  P["corner_R"]-P["wall"], P["arc_seg"]),
             P["accent_h"], P["accent_h"]+P["cover_t"])]

def build_bottom_cover():
    return mesh_from_prims(bottom_cover_prims())

def accent_ring_prims():
    return [("ring",
             rounded_rect_outline(0, 0, P["body_L"], P["body_W"],
                                  P["corner_R"], P["arc_seg"]),
             rounded_rect_outline(0, 0, P["body_L"]-2*P["accent_t"],
                                  P["body_W"]-2*P["accent_t"],
                                  P["corner_R"]-P["accent_t"], P["arc_seg"]),
             0.0, P["accent_h"])]

def build_accent_ring():
    return mesh_from_prims(accent_ring_prims())

# ======================== 输出 ========================
def write_stl(mesh, path, name="part"):
    with open(path, "wb") as f:
        f.write(b"\0"*80)
        f.write(struct.pack("<I", len(mesh.tris)))
        for a, b, c in mesh.tris:
            ux, uy, uz = b[0]-a[0], b[1]-a[1], b[2]-a[2]
            vx, vy, vz = c[0]-a[0], c[1]-a[1], c[2]-a[2]
            nx, ny, nz = uy*vz-uz*vy, uz*vx-ux*vz, ux*vy-uy*vx
            ln = math.sqrt(nx*nx+ny*ny+nz*nz) or 1.0
            f.write(struct.pack("<3f", nx/ln, ny/ln, nz/ln))
            for p in (a, b, c):
                f.write(struct.pack("<3f", *p))
            f.write(struct.pack("<H", 0))

def write_3mf(parts, path):
    objs, items = [], []
    for pid, (name, mesh) in enumerate(parts.items(), start=1):
        vidx, verts, tris = {}, [], []
        for t in mesh.tris:
            idx = []
            for p in t:
                key = (round(p[0], 4), round(p[1], 4), round(p[2], 4))
                if key not in vidx:
                    vidx[key] = len(verts)
                    verts.append(key)
                idx.append(vidx[key])
            tris.append(tuple(idx))
        vxml = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in verts)
        txml = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris)
        objs.append(f'<object id="{pid}" type="model" name="{name}">'
                    f'<mesh><vertices>{vxml}</vertices>'
                    f'<triangles>{txml}</triangles></mesh></object>')
        items.append(f'<item objectid="{pid}"/>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<model unit="millimeter" xml:lang="zh-CN" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<resources>{"".join(objs)}</resources>'
             f'<build>{"".join(items)}</build></model>')
    ct = ('<?xml version="1.0" encoding="UTF-8"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
            '</Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)

def main():
    os.makedirs(DIST, exist_ok=True)
    parts = {
        "main_body": build_main_body(),
        "tactile_panel": build_panel(),
        "buttons": build_buttons(),
        "bottom_cover": build_bottom_cover(),
        "accent_ring": build_accent_ring(),
    }
    standalone = dict(parts)
    standalone["buttons"] = build_buttons(0.0)      # 单件打印从 z=0 起
    for name, mesh in standalone.items():
        path = os.path.join(DIST, name + ".stl")
        write_stl(mesh, path, name)
        print(f"{name:15s} tris={len(mesh.tris):7d}  -> {path}")
    for name, mesh in build_panel_tiles().items():
        write_stl(mesh, os.path.join(DIST, name + ".stl"), name)
        write_3mf({name: mesh}, os.path.join(DIST, name + ".3mf"))
        print(f"{name:15s} tris={len(mesh.tris):7d}  (打印盘适配拼块)")
    # 超大件切 4 块 (200x150, 适配 256 打印盘, 胶水拼合; 主体倒置打印)
    hx, hy = P["body_L"]/2, P["body_W"]/2
    quarters = (("fl", (-hx, -hy, 0.0, 0.0)), ("fr", (0.0, -hy, hx, 0.0)),
                ("bl", (-hx, 0.0, 0.0, hy)), ("br", (0.0, 0.0, hx, hy)))
    for part, prims in (("main_body", main_body_prims()),
                        ("bottom_cover", bottom_cover_prims()),
                        ("accent_ring", accent_ring_prims())):
        for tag, rect in quarters:
            qm = mesh_from_prims(prims, rect)
            write_stl(qm, os.path.join(DIST, f"{part}_{tag}.stl"), f"{part}_{tag}")
        print(f"{part:15s} -> 4 quarter STL")
    mf = os.path.join(DIST, "tactile_terminal.3mf")
    write_3mf(parts, mf)
    print(f"3MF assembly -> {mf}")

if __name__ == "__main__":
    main()
