# -*- coding: utf-8 -*-
"""
高度场 -> 严格流形三角网格(通用模块, 支持矩形网格)
top: 二维数组 top[y][x] = 该 1mm 格柱顶面高度(z>=0), 底面在 z=0。
角点高度对齐缝合, 任意高度差/墙角组合均严格闭合。
"""

class HFMesh:
    def __init__(self):
        self.tris = []
    def tri(self, a, b, c):
        self.tris.append((a, b, c))
    def quad(self, a, b, c, d):
        self.tri(a, b, c); self.tri(a, c, d)

def build_grid(top, res=1.0, offset=(0.0, 0.0, 0.0)):
    ny = len(top)
    nx = len(top[0])
    ox, oy, oz = offset
    m = HFMesh()

    corner = [[None]*(nx+1) for _ in range(ny+1)]
    for i in range(nx+1):
        for j in range(ny+1):
            hs = set()
            for ci, cj in ((i-1,j-1),(i,j-1),(i-1,j),(i,j)):
                if 0 <= ci < nx and 0 <= cj < ny:
                    hs.add(top[cj][ci])
            corner[j][i] = sorted(hs)

    def stitch(A, B, la, lb, flip):
        def P(p, z): return (p[0]+ox, p[1]+oy, z+oz)
        tris = []
        i = j = 0
        na, nb = len(la), len(lb)
        while i+1 < na or j+1 < nb:
            nxtA = la[i+1] if i+1 < na else None
            nxtB = lb[j+1] if j+1 < nb else None
            if nxtA is not None and (nxtB is None or nxtA < nxtB):
                tris.append((P(A,la[i]), P(B,lb[j]), P(A,nxtA))); i += 1
            elif nxtB is not None and (nxtA is None or nxtB < nxtA):
                tris.append((P(A,la[i]), P(B,lb[j]), P(B,nxtB))); j += 1
            else:
                tris.append((P(A,la[i]), P(B,lb[j]), P(B,nxtB)))
                tris.append((P(A,la[i]), P(B,nxtB), P(A,nxtA)))
                i += 1; j += 1
        for t in tris:
            if flip: m.tri(t[1], t[0], t[2])
            else:    m.tri(*t)

    def wall(A, B, cA, cB, lo, hi, flip):
        la = [lo] + [v for v in cA if lo < v < hi] + [hi]
        lb = [lo] + [v for v in cB if lo < v < hi] + [hi]
        stitch(A, B, la, lb, flip)

    for j in range(ny):
        for i in range(nx):
            x0, y0, x1, y1 = i*res, j*res, (i+1)*res, (j+1)*res
            t = top[j][i]
            m.quad((x0+ox,y0+oy,t+oz), (x1+ox,y0+oy,t+oz),
                   (x1+ox,y1+oy,t+oz), (x0+ox,y1+oy,t+oz))   # 顶面
            m.quad((x0+ox,y0+oy,oz), (x0+ox,y1+oy,oz),
                   (x1+ox,y1+oy,oz), (x1+ox,y0+oy,oz))       # 底面
            A, B = (x1,y0), (x1,y1)                          # 右(+x)
            cA, cB = corner[j][i+1], corner[j+1][i+1]
            if i+1 < nx:
                tn = top[j][i+1]
                if t > tn:   wall(A, B, cA, cB, tn, t, False)
                elif tn > t: wall(A, B, cA, cB, t, tn, True)
            else:
                wall(A, B, cA, cB, 0, t, False)
            A, B = (x1,y1), (x0,y1)                          # 上(+y)
            cA, cB = corner[j+1][i+1], corner[j+1][i]
            if j+1 < ny:
                tn = top[j+1][i]
                if t > tn:   wall(A, B, cA, cB, tn, t, False)
                elif tn > t: wall(A, B, cA, cB, t, tn, True)
            else:
                wall(A, B, cA, cB, 0, t, False)
            if i == 0:                                       # 左边界
                wall((x0,y1), (x0,y0), corner[j+1][i], corner[j][i], 0, t, False)
            if j == 0:                                       # 下边界
                wall((x0,y0), (x1,y0), corner[j][i], corner[j][i+1], 0, t, False)
    return m
