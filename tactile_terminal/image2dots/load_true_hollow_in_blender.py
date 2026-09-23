import bpy
import os


STL = r"E:\C_Projects\影石黑客松\tactile_terminal\image2dots\dist\bold_stage_真中空_20mm网格筋.stl"
BLEND = r"E:\C_Projects\影石黑客松\tactile_terminal\image2dots\dist\bold_stage_真中空_20mm网格筋.blend"


for obj in list(bpy.context.scene.objects):
    if obj.type == "MESH":
        obj.hide_viewport = True
        obj.hide_render = True
        obj.select_set(False)

bpy.ops.wm.stl_import(filepath=STL)
obj = bpy.context.active_object
obj.name = "bold_stage_真中空_20mm网格筋"
obj.data.name = "tactile_heightfield_hollow_grid20"
obj["top_surface_preserved"] = True
obj["dimensions_mm"] = "240 x 123 x 11"
obj["bottom_skin_mm"] = 0.6
obj["top_carrier_skin_mm"] = 0.8
obj["internal_clear_height_mm"] = 1.6
obj["grid_rib_pitch_mm"] = 20
obj["grid_rib_width_mm"] = 1
obj["volume_cm3"] = 93.496
obj["petg_theoretical_mass_g"] = 118.74
obj["volume_reduction_percent"] = 30.88
obj["watertight"] = True
obj["nonmanifold_edges"] = 0

bpy.context.scene["结构"] = "真中空夹层：0.6mm底皮 + 20mm网格筋 + 0.8mm顶部承托层"
bpy.context.scene["打印建议"] = "0.20mm层高，2墙，5% Gyroid或0%填充，20-25mm/s桥接，PETG桥接风扇100%"
bpy.context.scene.unit_settings.system = "METRIC"
bpy.context.scene.unit_settings.length_unit = "MILLIMETERS"
bpy.ops.wm.save_as_mainfile(filepath=BLEND, check_existing=False)
print(f"LOADED={obj.name}")
print(f"DIMS={tuple(round(float(v), 3) for v in obj.dimensions)}")
print(f"SAVED={BLEND}")
