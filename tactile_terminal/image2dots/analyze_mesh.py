import bpy
import bmesh
import json
import os
import sys


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :]
    src, out = argv

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.wm.stl_import(filepath=src)
    obj = bpy.context.active_object
    mesh = obj.data

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.normal_update()
    boundary = sum(1 for e in bm.edges if e.is_boundary)
    nonmanifold = sum(1 for e in bm.edges if not e.is_manifold)
    volume = abs(bm.calc_volume(signed=True))
    parts = len(list(bm.verts.layers.int.keys()))  # overwritten below

    # Count connected face components without relying on optional add-ons.
    unseen = set(bm.faces)
    components = 0
    while unseen:
        components += 1
        stack = [unseen.pop()]
        while stack:
            face = stack.pop()
            for edge in face.edges:
                for linked in edge.link_faces:
                    if linked in unseen:
                        unseen.remove(linked)
                        stack.append(linked)

    coords = [obj.matrix_world @ v.co for v in mesh.vertices]
    mins = [min(v[i] for v in coords) for i in range(3)]
    maxs = [max(v[i] for v in coords) for i in range(3)]
    z_values = sorted({round(v.z, 6) for v in coords})

    report = {
        "source": os.path.abspath(src),
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "triangles": len(mesh.loop_triangles),
        "faces": len(mesh.polygons),
        "boundary_edges": boundary,
        "nonmanifold_edges": nonmanifold,
        "connected_components": components,
        "watertight": boundary == 0 and nonmanifold == 0,
        "bounds_min_mm": mins,
        "bounds_max_mm": maxs,
        "dimensions_mm": [maxs[i] - mins[i] for i in range(3)],
        "volume_mm3": volume,
        "volume_cm3": volume / 1000.0,
        "petg_mass_g_at_1_27": volume / 1000.0 * 1.27,
        "unique_z_mm": z_values,
    }
    bm.free()
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
