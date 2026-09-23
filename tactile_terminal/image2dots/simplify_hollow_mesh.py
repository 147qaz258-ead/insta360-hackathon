import bpy
import math
import sys


src, dst = sys.argv[sys.argv.index("--") + 1 :]
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.wm.stl_import(filepath=src)
obj = bpy.context.active_object

before = len(obj.data.polygons)
mod = obj.modifiers.new(name="Planar simplify", type="DECIMATE")
mod.decimate_type = "DISSOLVE"
mod.angle_limit = math.radians(0.01)
mod.use_dissolve_boundaries = True
mod.delimit = set()
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
bpy.ops.object.modifier_apply(modifier=mod.name)

bpy.ops.object.select_all(action="DESELECT")
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
bpy.ops.wm.stl_export(filepath=dst, export_selected_objects=True, apply_modifiers=True)
print(f"SIMPLIFIED {before} -> {len(obj.data.polygons)} triangles")
