import bpy
import bmesh
from bpy.props import IntProperty, EnumProperty
from bpy.types import Operator, Panel, PropertyGroup
from bpy.utils import register_class, unregister_class


# ---------------------------------------------------------------------------
# Island detection
# ---------------------------------------------------------------------------


def get_uv_islands(bm, uv_layer, use_sync):
    """Return list of islands; each island is a list of BMLoops.
    Only islands belonging to UV-selected faces are included.

    Blender 4, sync off: BMLoopUV.select exists and is the correct signal.
    Blender 4, sync on:  UV selection mirrors face selection; use face.select.
    Blender 5:           BMLoopUV.select was removed; always use face.select.
    """
    visited = set()
    islands = []

    # Detect Blender version by probing BMLoopUV for .select
    test_loop = next((l for f in bm.faces for l in f.loops), None)
    has_loop_select = (test_loop is not None and
                       hasattr(test_loop[uv_layer], "select"))

    def face_uv_selected(face):
        if has_loop_select and not use_sync:
            return all(loop[uv_layer].select for loop in face.loops)
        return face.select

    def grow_island(start_face):
        island_loops = []
        stack = [start_face]
        while stack:
            face = stack.pop()
            if face.index in visited:
                continue
            visited.add(face.index)
            for loop in face.loops:
                island_loops.append(loop)
            for edge in face.edges:
                for linked_face in edge.link_faces:
                    if linked_face.index in visited:
                        continue
                    if not face_uv_selected(linked_face):
                        continue
                    for loop in face.loops:
                        if loop.edge != edge:
                            continue
                        for other_loop in linked_face.loops:
                            if other_loop.edge != edge:
                                continue
                            uv_a0 = tuple(round(v, 6) for v in loop[uv_layer].uv)
                            uv_a1 = tuple(round(v, 6) for v in loop.link_loop_next[uv_layer].uv)
                            uv_b0 = tuple(round(v, 6) for v in other_loop[uv_layer].uv)
                            uv_b1 = tuple(round(v, 6) for v in other_loop.link_loop_next[uv_layer].uv)
                            if {uv_a0, uv_a1} & {uv_b0, uv_b1}:
                                stack.append(linked_face)
        return island_loops

    for face in bm.faces:
        if face.index not in visited and face_uv_selected(face):
            island = grow_island(face)
            if island:
                islands.append(island)

    return islands


# ---------------------------------------------------------------------------
# Core snapping logic
# ---------------------------------------------------------------------------

def snap_islands_to_pixel(image_width, image_height, snap_mode='CORNER'):
    """
    For every UV island in every selected mesh object,
    snap the island's bounding-box center to the nearest pixel corner
    or pixel center depending on snap_mode ('CORNER' or 'CENTER').
    Works in both Edit Mode and Object Mode.
    Returns the total number of islands processed.
    """
    objects = [obj for obj in bpy.context.selected_objects
               if obj.type == 'MESH' and obj.mode == 'EDIT']

    total_found = 0
    total_moved = 0

    for obj in objects:
        me = obj.data
        in_edit = (obj.mode == 'EDIT')

        if in_edit:
            bm = bmesh.from_edit_mesh(me)
        else:
            bm = bmesh.new()
            bm.from_mesh(me)

        bm.faces.ensure_lookup_table()
        uv_layer = bm.loops.layers.uv.verify()
        use_sync = bpy.context.tool_settings.use_uv_select_sync
        islands = get_uv_islands(bm, uv_layer, use_sync)
        total_found += len(islands)

        for island_loops in islands:
            us = [loop[uv_layer].uv.x for loop in island_loops]
            vs = [loop[uv_layer].uv.y for loop in island_loops]
            cx = (min(us) + max(us)) / 2.0
            cy = (min(vs) + max(vs)) / 2.0

            snapped_cx = round(cx * image_width)  / image_width
            snapped_cy = round(cy * image_height) / image_height

            if snap_mode == 'CENTER':
                snapped_cx += 0.5 / image_width
                snapped_cy += 0.5 / image_height

            dx = snapped_cx - cx
            dy = snapped_cy - cy

            if abs(dx) > 1e-9 or abs(dy) > 1e-9:
                for loop in island_loops:
                    loop[uv_layer].uv.x += dx
                    loop[uv_layer].uv.y += dy
                total_moved += 1

        if in_edit:
            bmesh.update_edit_mesh(me)
        else:
            bm.to_mesh(me)
            bm.free()
            me.update()

    return total_found, total_moved


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class UVPixelSnapProperties(PropertyGroup):
    image_width: IntProperty(
        name="Width",
        description="Image width in pixels",
        default=1024,
        min=1,
        max=65536,
    )
    image_height: IntProperty(
        name="Height",
        description="Image height in pixels",
        default=1024,
        min=1,
        max=65536,
    )
    snap_mode: EnumProperty(
        name="Snap To",
        description="Where to align the island's bounding-box center",
        items=[
            ('CORNER', "Pixel Corner",
             "Snap to the nearest intersection of pixel edges (texel corner)"),
            ('CENTER', "Pixel Center",
             "Snap to the center of the nearest pixel (texel center)"),
        ],
        default='CORNER',
    )


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

class UV_OT_island_pixel_snap(Operator):
    bl_idname = "uv.island_pixel_snap"
    bl_label = "Snap Islands to Pixel"
    bl_description = (
        "Move each UV island so its bounding-box center lands "
        "on the nearest pixel corner of the given image resolution"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.uv_pixel_snap

        # Condition 1: at least one mesh object selected
        selected = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if not selected:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}

        # Condition 2: at least one selected mesh is in Edit Mode
        if not any(obj.mode == 'EDIT' for obj in selected):
            self.report({'WARNING'}, "No selected mesh is in Edit Mode.")
            return {'CANCELLED'}

        # Condition 3: UV islands selected — checked inside snap via found count
        found, moved = snap_islands_to_pixel(props.image_width, props.image_height, props.snap_mode)

        if found == 0:
            self.report({'WARNING'}, "No UV islands selected.")
            return {'CANCELLED'}

        if moved == 0:
            self.report({'INFO'}, f"All {found} island(s) already snapped — nothing to move.")
            return {'FINISHED'}

        self.report(
            {'INFO'},
            f"Snapped {moved} of {found} UV island(s) to {props.image_width}×{props.image_height} pixel grid."
        )
        return {'FINISHED'}


# Preset operator: set both dims to a power-of-two value
class UV_OT_pixel_snap_preset(Operator):
    bl_idname = "uv.pixel_snap_preset"
    bl_label = "Apply Preset"
    bl_description = "Set both width and height to this preset value"

    size: IntProperty(default=1024)

    def execute(self, context):
        props = context.scene.uv_pixel_snap
        props.image_width  = self.size
        props.image_height = self.size
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Panel  (UV Editor > Sidebar > UV Island Pixel Snap)
# ---------------------------------------------------------------------------

class UV_PT_island_pixel_snap(Panel):
    bl_label = "UV Island Pixel Snap"
    bl_space_type  = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category    = "UV Island Pixel Snap"

    def draw(self, context):
        layout = self.layout
        props = context.scene.uv_pixel_snap

        # Image size inputs
        box = layout.box()
        box.label(text="Image Size", icon='IMAGE_DATA')

        row = box.row(align=True)
        row.prop(props, "image_width",  text="W")
        row.prop(props, "image_height", text="H")

        # Quick presets
        col = box.column(align=True)
        col.label(text="Presets:")
        row = col.row(align=True)
        for size in (256, 512, 1024, 2048, 4096):
            op = row.operator("uv.pixel_snap_preset", text=str(size))
            op.size = size

        layout.separator()

        # Snap mode
        box2 = layout.box()
        box2.label(text="Snap Mode", icon='SNAP_GRID')
        box2.row().prop(props, "snap_mode", expand=True)

        layout.separator()
        layout.operator(
            "uv.island_pixel_snap",
            text="Snap Islands to Pixel Grid",
            icon='SNAP_GRID',
        )

        # Info text
        col = layout.column()
        col.scale_y = 0.75
        col.label(text="Snaps bbox center of each")
        col.label(text="island across selected objects.")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    UVPixelSnapProperties,
    UV_OT_island_pixel_snap,
    UV_OT_pixel_snap_preset,
    UV_PT_island_pixel_snap,
)


def register():
    for cls in classes:
        register_class(cls)
    bpy.types.Scene.uv_pixel_snap = bpy.props.PointerProperty(type=UVPixelSnapProperties)


def unregister():
    for cls in reversed(classes):
        unregister_class(cls)
    del bpy.types.Scene.uv_pixel_snap


if __name__ == "__main__":
    register()
