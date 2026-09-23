"""Rebuild bold_stage as a true hollow tactile sandwich with modeled grid ribs.

The visible height field is recovered directly from the source STL.  No source
image is required and no Boolean operation is used.
"""

import json
import math
import os
import struct
import sys
import zipfile


NX, NY = 240, 123
DZ = 0.2
BASE_TOP = 3.0
BOTTOM_SKIN = 0.6
TOP_SKIN = 0.8
RIB_PITCH = 20
RIB_WIDTH_CELLS = 1
PETG_DENSITY = 1.27


def read_heightfield(stl_path):
    top = [[None] * NX for _ in range(NY)]
    with open(stl_path, "rb") as f:
        f.read(80)
        count = struct.unpack("<I", f.read(4))[0]
        for _ in range(count):
            row = struct.unpack("<12fH", f.read(50))
            nx, ny, nz = row[:3]
            pts = (row[3:6], row[6:9], row[9:12])
            zs = [p[2] for p in pts]
            if nz < 0.9 or max(zs) - min(zs) > 1e-5 or zs[0] <= 0:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            if max(xs) - min(xs) > 1.001 or max(ys) - min(ys) > 1.001:
                continue
            x = int(round(min(xs)))
            y = int(round(min(ys)))
            if 0 <= x < NX and 0 <= y < NY:
                z = round(zs[0], 5)
                if top[y][x] is None or z > top[y][x]:
                    top[y][x] = z
    missing = [(x, y) for y in range(NY) for x in range(NX) if top[y][x] is None]
    if missing:
        raise RuntimeError(f"heightfield recovery failed: {len(missing)} cells missing")
    return top


def build_occupancy(top):
    max_h = max(max(row) for row in top)
    nz = int(round(max_h / DZ))
    occ = bytearray(NX * NY * nz)

    def idx(x, y, k):
        return (k * NY + y) * NX + x

    def fill(x, y, z0, z1):
        k0 = int(round(z0 / DZ))
        k1 = int(round(z1 / DZ))
        for k in range(k0, k1):
            occ[idx(x, y, k)] = 1

    carrier_bottom = BASE_TOP - TOP_SKIN
    for y in range(NY):
        for x in range(NX):
            # Flat closed bottom skin.
            fill(x, y, 0.0, BOTTOM_SKIN)
            # Continuous carrier skin below the untouched tactile surface.
            fill(x, y, carrier_bottom, BASE_TOP)
            # Preserve every tactile column above the original z=3 plane.
            if top[y][x] > BASE_TOP:
                fill(x, y, BASE_TOP, top[y][x])

    # Closed outer wall and explicit orthogonal ribs.  The maximum unsupported
    # span is 19 mm, below the requested 20–30 mm spacing.
    for y in range(NY):
        for x in range(NX):
            perimeter = x < RIB_WIDTH_CELLS or x >= NX - RIB_WIDTH_CELLS \
                or y < RIB_WIDTH_CELLS or y >= NY - RIB_WIDTH_CELLS
            rib_x = x % RIB_PITCH < RIB_WIDTH_CELLS
            rib_y = y % RIB_PITCH < RIB_WIDTH_CELLS
            if perimeter or rib_x or rib_y:
                fill(x, y, BOTTOM_SKIN, carrier_bottom)
    return occ, nz


def occupancy_to_tris(occ, nz):
    tris = []

    def at(x, y, k):
        if x < 0 or x >= NX or y < 0 or y >= NY or k < 0 or k >= nz:
            return 0
        return occ[(k * NY + y) * NX + x]

    def quad(a, b, c, d):
        tris.append((a, b, c))
        tris.append((a, c, d))

    for k in range(nz):
        z0, z1 = k * DZ, (k + 1) * DZ
        for y in range(NY):
            y0, y1 = float(y), float(y + 1)
            for x in range(NX):
                if not at(x, y, k):
                    continue
                x0, x1 = float(x), float(x + 1)
                if not at(x - 1, y, k):
                    quad((x0,y0,z0),(x0,y0,z1),(x0,y1,z1),(x0,y1,z0))
                if not at(x + 1, y, k):
                    quad((x1,y0,z0),(x1,y1,z0),(x1,y1,z1),(x1,y0,z1))
                if not at(x, y - 1, k):
                    quad((x0,y0,z0),(x1,y0,z0),(x1,y0,z1),(x0,y0,z1))
                if not at(x, y + 1, k):
                    quad((x0,y1,z0),(x0,y1,z1),(x1,y1,z1),(x1,y1,z0))
                if not at(x, y, k - 1):
                    quad((x0,y0,z0),(x0,y1,z0),(x1,y1,z0),(x1,y0,z0))
                if not at(x, y, k + 1):
                    quad((x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1))
    return tris


def write_stl(tris, path):
    with open(path, "wb") as f:
        f.write(b"true hollow grid tactile board".ljust(80, b"\0"))
        f.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            ux, uy, uz = b[0]-a[0], b[1]-a[1], b[2]-a[2]
            vx, vy, vz = c[0]-a[0], c[1]-a[1], c[2]-a[2]
            nx, ny, nz = uy*vz-uz*vy, uz*vx-ux*vz, ux*vy-uy*vx
            ln = math.sqrt(nx*nx + ny*ny + nz*nz) or 1.0
            f.write(struct.pack("<12fH", nx/ln, ny/ln, nz/ln, *a, *b, *c, 0))


def write_3mf(tris, path, name):
    vidx, verts, faces = {}, [], []
    for tri in tris:
        face = []
        for p in tri:
            key = tuple(round(v, 5) for v in p)
            if key not in vidx:
                vidx[key] = len(verts)
                verts.append(key)
            face.append(vidx[key])
        faces.append(tuple(face))
    vxml = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x,y,z in verts)
    txml = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a,b,c in faces)
    model = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<model unit="millimeter" xml:lang="zh-CN" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<resources><object id="1" type="model" name="{name}"><mesh>'
             f'<vertices>{vxml}</vertices><triangles>{txml}</triangles>'
             '</mesh></object></resources><build><item objectid="1"/></build></model>')
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
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ct)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("3D/3dmodel.model", model)


def main():
    src, out_dir = sys.argv[1:3]
    os.makedirs(out_dir, exist_ok=True)
    top = read_heightfield(src)
    occ, nz = build_occupancy(top)
    tris = occupancy_to_tris(occ, nz)
    stem = "bold_stage_真中空_20mm网格筋"
    stl = os.path.join(out_dir, stem + ".stl")
    mf = os.path.join(out_dir, stem + ".3mf")
    write_stl(tris, stl)
    write_3mf(tris, mf, stem)
    volume = sum(occ) * DZ
    original_volume = 135264.0
    report = {
        "dimensions_mm": [NX, NY, nz * DZ],
        "top_surface_preserved": True,
        "base_top_z_mm": BASE_TOP,
        "bottom_skin_mm": BOTTOM_SKIN,
        "top_carrier_skin_mm": TOP_SKIN,
        "internal_clear_height_mm": BASE_TOP - TOP_SKIN - BOTTOM_SKIN,
        "grid_rib_pitch_mm": RIB_PITCH,
        "grid_rib_width_mm": RIB_WIDTH_CELLS,
        "triangles": len(tris),
        "volume_mm3": volume,
        "volume_reduction_percent": (1.0 - volume / original_volume) * 100.0,
        "petg_theoretical_mass_g": volume / 1000.0 * PETG_DENSITY,
        "source_volume_mm3": original_volume,
        "source_petg_theoretical_mass_g": original_volume / 1000.0 * PETG_DENSITY,
        "stl": stl,
        "3mf": mf,
    }
    report_path = os.path.join(out_dir, stem + "_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
