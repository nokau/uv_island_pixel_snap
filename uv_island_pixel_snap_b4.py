import bpy
import bmesh
import time
from bpy.props import IntProperty, EnumProperty, BoolProperty
from bpy.types import Operator, Panel, PropertyGroup
from bpy.utils import register_class, unregister_class


# ---------------------------------------------------------------------------
# Island detection — Blender 4
# ---------------------------------------------------------------------------

def get_uv_islands(bm, uv_layer, use_sync, uv_select_mode):
    """
    Return list of islands; each island is a list of BMLoops.
    Seeds from any face with at least one selected element, then grows
    to the full connected UV island regardless of selection state.

    Blender 4: BMLoopUV.select exists and is the most granular signal
    in Vertex mode without sync. face.select is always checked first
    as a staleness gate — clicking blank space in the 3D viewport clears
    it reliably even when UV loop flags remain stale.

    UV Sync off: uv_select_mode is a real UV Editor mode (VERTEX, EDGE,
                 FACE, ISLAND) independent from the 3D viewport.
    UV Sync on:  uv_select_mode is irrelevant; mesh Select Mode governs.
    """
    visited = set()
    islands = []

    test_loop = next((l for f in bm.faces for l in f.loops), None)
    has_loop_select = (test_loop is not None and
                       hasattr(test_loop[uv_layer], "select"))

    def face_has_selection(face):
        """
        True if this face contains any selected UV element.
        face.select is always the first gate regardless of mode —
        it clears correctly on 3D viewport deselection where UV loop
        flags may linger stale.
        """
        if not face.select:
            return False
        if use_sync:
            return True
        # In B4 with sync off, loop[uv_layer].select is the most reliable
        # signal across all UV select modes — face.select and edge.select
        # spread mesh-wide in Edge/Face modes and can't distinguish islands.
        if has_loop_select:
            return any(loop[uv_layer].select for loop in face.loops)
        # Fallback when loop flags unavailable
        if uv_select_mode in ('FACE', 'ISLAND'):
            return face.select
        if uv_select_mode == 'EDGE':
            return any(edge.select for edge in face.edges)
        return any(v.select for v in face.verts)

    def grow_island(start_face):
        """
        Flood fill to all UV-connected faces from start_face.
        Crosses a shared edge only if UV coords match on both sides
        (i.e. the edge is not a UV seam).
        """
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
                    loop_here  = next((l for l in face.loops
                                       if l.edge == edge), None)
                    loop_there = next((l for l in linked_face.loops
                                       if l.edge == edge), None)
                    if loop_here is None or loop_there is None:
                        continue
                    uvs_here  = {tuple(round(v, 6) for v in loop_here[uv_layer].uv),
                                 tuple(round(v, 6) for v in loop_here.link_loop_next[uv_layer].uv)}
                    uvs_there = {tuple(round(v, 6) for v in loop_there[uv_layer].uv),
                                 tuple(round(v, 6) for v in loop_there.link_loop_next[uv_layer].uv)}
                    if uvs_here & uvs_there:
                        stack.append(linked_face)
        return island_loops

    for face in bm.faces:
        if face.index not in visited and face_has_selection(face):
            island = grow_island(face)
            if island:
                islands.append(island)

    return islands


# ---------------------------------------------------------------------------
# Overlap grouping (convex hull + Separating Axis Theorem)
# ---------------------------------------------------------------------------

def convex_hull_2d(points):
    pts = list({(round(p[0], 9), round(p[1], 9)) for p in points})
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    pts.sort()
    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def sat_overlap(hull_a, hull_b):
    def project(hull, axis):
        dots = [p[0] * axis[0] + p[1] * axis[1] for p in hull]
        return min(dots), max(dots)

    def axes(hull):
        for i in range(len(hull)):
            edge = (hull[(i + 1) % len(hull)][0] - hull[i][0],
                    hull[(i + 1) % len(hull)][1] - hull[i][1])
            yield (-edge[1], edge[0])

    if len(hull_a) < 3 or len(hull_b) < 3:
        ax = [p[0] for p in hull_a]; ay = [p[1] for p in hull_a]
        bx = [p[0] for p in hull_b]; by = [p[1] for p in hull_b]
        if not ax or not bx:
            return False
        return (min(ax) <= max(bx) and max(ax) >= min(bx) and
                min(ay) <= max(by) and max(ay) >= min(by))

    for axis in list(axes(hull_a)) + list(axes(hull_b)):
        mn_a, mx_a = project(hull_a, axis)
        mn_b, mx_b = project(hull_b, axis)
        if mx_a < mn_b or mx_b < mn_a:
            return False
    return True


def group_overlapping_islands_multi(islands, uv_layers):
    n = len(islands)
    if n == 0:
        return []

    hulls = []
    for isl, uv_layer in zip(islands, uv_layers):
        pts = [(loop[uv_layer].uv.x, loop[uv_layer].uv.y) for loop in isl]
        hulls.append(convex_hull_2d(pts))

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        parent[find(i)] = find(j)

    for i in range(n):
        for j in range(i + 1, n):
            if sat_overlap(hulls[i], hulls[j]):
                union(i, j)

    groups = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(i)

    return list(groups.values())


# ---------------------------------------------------------------------------
# Selection write-back — Blender 4
# ---------------------------------------------------------------------------

def apply_uv_selection(bm, uv_layer, loops, use_sync, uv_select_mode, select):
    """
    Blender 4, no sync: write BMLoopUV.select and select_edge directly.
    Writing loop flags keeps UV selection contained in the UV editor
    and does not bleed into 3D viewport face selection.
    Blender 4, sync on: face.select is the only meaningful signal.
    """
    loops = list(loops)
    if not loops:
        return

    has_loop_select = hasattr(loops[0][uv_layer], "select")

    if has_loop_select and not use_sync:
        for loop in loops:
            loop[uv_layer].select      = select
            loop[uv_layer].select_edge = select
    else:
        faces = {loop.face for loop in loops}
        for face in faces:
            face.select = select


# ---------------------------------------------------------------------------
# Core snapping logic
# ---------------------------------------------------------------------------

def snap_islands_to_pixel(image_width, image_height, snap_mode='CORNER',
                           merge_overlapping=False, axis_mode='BOTH'):
    """
    Snap selected UV islands to the nearest pixel corner or center.
    axis_mode controls which axes are snapped: 'BOTH', 'X', or 'Y'.
    When merge_overlapping is True, overlapping islands are grouped and
    snapped together using a shared bounding box center.
    After snapping, deselects islands that didn't move.
    If nothing moved, selection is unchanged.
    Returns (total_found, total_moved).
    """
    objects = [obj for obj in bpy.context.selected_objects
               if obj.type == 'MESH' and obj.mode == 'EDIT']

    use_sync       = bpy.context.tool_settings.use_uv_select_sync
    uv_select_mode = bpy.context.tool_settings.uv_select_mode

    # --- Pass 1: collect all islands across all objects ----------------------
    all_islands = []
    bm_map = {}

    for obj in objects:
        me = obj.data
        bm = bmesh.from_edit_mesh(me)
        bm.faces.ensure_lookup_table()
        uv_layer = bm.loops.layers.uv.verify()
        bm_map[obj.name] = (bm, uv_layer, me)

        islands = get_uv_islands(bm, uv_layer, use_sync, uv_select_mode)
        for isl in islands:
            all_islands.append((isl, bm, uv_layer, me))

    if not all_islands:
        return 0, 0

    # --- Pass 2: group by overlap --------------------------------------------
    if merge_overlapping and len(all_islands) > 1:
        island_loops_list = [entry[0] for entry in all_islands]
        uv_layers_list    = [entry[2] for entry in all_islands]
        groups_indices = group_overlapping_islands_multi(
            island_loops_list, uv_layers_list
        )
        groups = [[all_islands[i] for i in grp] for grp in groups_indices]
    else:
        groups = [[entry] for entry in all_islands]

    total_found = len(groups)
    total_moved = 0
    moved_groups   = []
    unmoved_groups = []

    # --- Pass 3: snap each group ---------------------------------------------
    for group in groups:
        us = [loop[uv_layer].uv.x
              for (isl, bm, uv_layer, me) in group for loop in isl]
        vs = [loop[uv_layer].uv.y
              for (isl, bm, uv_layer, me) in group for loop in isl]
        cx = (min(us) + max(us)) / 2.0
        cy = (min(vs) + max(vs)) / 2.0

        snapped_cx = round(cx * image_width)  / image_width
        snapped_cy = round(cy * image_height) / image_height

        if snap_mode == 'CENTER':
            snapped_cx = (round(cx * image_width  - 0.5) + 0.5) / image_width
            snapped_cy = (round(cy * image_height - 0.5) + 0.5) / image_height

        dx = snapped_cx - cx if axis_mode != 'Y' else 0.0
        dy = snapped_cy - cy if axis_mode != 'X' else 0.0

        if abs(dx) > 1e-9 or abs(dy) > 1e-9:
            for (isl, bm, uv_layer, me) in group:
                for loop in isl:
                    loop[uv_layer].uv.x += dx
                    loop[uv_layer].uv.y += dy
            moved_groups.append(group)
            total_moved += 1
        else:
            unmoved_groups.append(group)

    # --- Pass 4: update selection --------------------------------------------
    if moved_groups:
        for group in unmoved_groups:
            for (isl, bm, uv_layer, me) in group:
                apply_uv_selection(bm, uv_layer, isl, use_sync, uv_select_mode, False)

    # --- Pass 5: flush all bmeshes back to meshes ----------------------------
    for bm, uv_layer, me in bm_map.values():
        # Blender 4: none of the B5 UV sync methods exist here.
        # select_flush spreads selection incorrectly so we skip it entirely.
        bmesh.update_edit_mesh(me)

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
    merge_overlapping: BoolProperty(
        name="Merge Overlapping",
        description=(
            "Treat selected overlapping islands as one unit — they share a combined bounding box center and move by the same offset"
        ),
        default=False,
    )


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

class UV_OT_island_pixel_snap(Operator):
    bl_idname = "uv.island_pixel_snap"
    bl_label = "Snap Islands to Pixel"
    bl_description = "Snap UV islands to the nearest pixel of the given image resolution"
    bl_options = {'REGISTER', 'UNDO'}

    axis_mode: EnumProperty(
        name="Axis",
        description="Which axis to snap",
        items=[
            ('BOTH', "Both", "Snap both axis — moves islands horizontally and vertically"),
            ('X',    "X",    "Snap horizontally — moves islands left or right"),
            ('Y',    "Y",    "Snap vertically — moves islands up or down"),
        ],
        default='BOTH',
    )

    @classmethod
    def description(cls, context, properties):
        return {
            'BOTH': "Snap both axis — moves islands horizontally and vertically",
            'X':    "Snap horizontally — moves islands left or right",
            'Y':    "Snap vertically — moves islands up or down",
        }.get(properties.axis_mode, cls.bl_description)

    def execute(self, context):
        props = context.scene.uv_pixel_snap

        selected = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if not selected:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}

        if context.mode != 'EDIT_MESH':
            self.report({'WARNING'}, "No selected mesh is in Edit Mode.")
            return {'CANCELLED'}

        t_start = time.time()
        found, moved = snap_islands_to_pixel(
            props.image_width, props.image_height,
            props.snap_mode, props.merge_overlapping,
            self.axis_mode,
        )
        elapsed = time.time() - t_start

        if found == 0:
            self.report({'WARNING'}, "No UV islands selected.")
            return {'CANCELLED'}

        if moved == 0:
            self.report({'INFO'}, f"All {found} island(s) already snapped — nothing to move. ({elapsed:.2f}s)")
            return {'FINISHED'}

        self.report(
            {'INFO'},
            f"Snapped {moved} of {found} UV island(s) to {props.image_width}×{props.image_height} pixel grid. ({elapsed:.2f}s)"
        )
        return {'FINISHED'}


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
# Panel
# ---------------------------------------------------------------------------

class UV_PT_island_pixel_snap(Panel):
    bl_label = "UV Island Pixel Snap"
    bl_space_type  = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category    = "UV Island Pixel Snap"

    def draw(self, context):
        layout = self.layout
        props = context.scene.uv_pixel_snap

        box = layout.box()
        box.label(text="Image Size", icon='IMAGE_DATA')
        col = box.column(align=True)
        col.use_property_decorate = False
        col.prop(props, "image_width",  text="W")
        col.prop(props, "image_height", text="H")

        col = box.column(align=True)
        col.label(text="Presets:")
        row = col.row(align=True)
        for size in (256, 512, 1024, 2048, 4096):
            op = row.operator("uv.pixel_snap_preset", text=str(size))
            op.size = size

        layout.separator()

        box2 = layout.box()
        box2.label(text="Snap Mode", icon='SNAP_GRID')
        box2.row().prop(props, "snap_mode", expand=True)
        box2.prop(props, "merge_overlapping")

        layout.label(text="Snap Islands to Pixel")
        row = layout.row(align=True)
        for axis, label in (('BOTH', "Both"), ('X', "X"), ('Y', "Y")):
            op = row.operator("uv.island_pixel_snap", text=label)
            op.axis_mode = axis

        ts = context.tool_settings
        use_sync = ts.use_uv_select_sync
        is_face  = ts.mesh_select_mode[2] if use_sync else True

        # Sync off works reliably in all UV select modes in B4.
        # Sync on works reliably only in Face select mode.
        # Only show warnings when sync is on and face mode is not active.
        if use_sync:
            col = layout.column()
            col.scale_y = 0.75
            col.label(
                text="UV Sync Selection ON",
                icon='UV_SYNC_SELECT',
            )

            col2 = layout.column(align=True)
            col2.scale_y = 0.75

            row = col2.row()
            row.alert = not is_face
            row.label(
                text="Set Select Mode to Face",
                icon='CHECKMARK' if is_face else 'ERROR',
            )


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



