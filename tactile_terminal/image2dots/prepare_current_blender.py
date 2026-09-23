import bpy
import os


OUT_DIR = r"E:\C_Projects\影石黑客松\tactile_terminal\image2dots\dist"
BACKUP_BLEND = os.path.join(OUT_DIR, "bold_stage_original_recovered.blend")
OUTPUT_BLEND = os.path.join(OUT_DIR, "bold_stage_Bambu_H2S_触觉保真.blend")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if len(meshes) != 1:
        raise RuntimeError(f"Expected exactly one mesh object, found {len(meshes)}")
    obj = meshes[0]

    # Preserve the exact unsaved scene before adding metadata.
    bpy.ops.wm.save_as_mainfile(filepath=BACKUP_BLEND, check_existing=False)

    dims = tuple(round(float(v), 6) for v in obj.dimensions)
    expected = (240.0, 123.0, 11.0)
    if any(abs(a - b) > 1e-4 for a, b in zip(dims, expected)):
        raise RuntimeError(f"Unexpected dimensions {dims}; expected {expected}")

    obj.name = "bold_stage_Bambu_H2S_触觉保真"
    obj.data.name = "bold_stage_heightfield_surface_preserved"
    obj["source_top_surface_preserved"] = True
    obj["dimensions_mm"] = "240 x 123 x 11"
    obj["mesh_watertight"] = True
    obj["mesh_volume_cm3"] = 135.264
    obj["petg_solid_mass_g_at_1_27"] = 171.8
    obj["optimization_strategy"] = "Bambu Studio shell + 8% Gyroid; exterior mesh unchanged"
    obj["wall_loops"] = 2
    obj["top_shell_layers"] = 3
    obj["bottom_shell_layers"] = 3
    obj["sparse_infill"] = "8% Gyroid"
    obj["outer_brim_mm"] = 8.0
    obj["geometric_40pct_conflict"] = (
        "2mm full bottom (59.040cm3) + preserved tactile columns above z=3 "
        "(46.704cm3) already equals 78.2% of original volume"
    )

    scene = bpy.context.scene
    scene["打印方案"] = "H2S / PETG / 0.20mm / 2墙 / 顶底各3层 / 8% Gyroid / 8mm Brim"
    scene["说明文件"] = os.path.join(OUT_DIR, "轻量化诊断与打印说明.md")
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "MILLIMETERS"

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND, check_existing=False)
    print(f"BACKUP={BACKUP_BLEND}")
    print(f"OUTPUT={OUTPUT_BLEND}")
    print(f"OBJECT={obj.name}")
    print(f"DIMS={dims}")


main()
