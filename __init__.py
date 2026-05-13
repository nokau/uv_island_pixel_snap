import bpy

# ---------------------------------------------------------------------------
# Version router
# ---------------------------------------------------------------------------
# Loads the Blender 5 module for Blender 5.0 and above,
# and the Blender 4 module for anything older.
# The two modules are fully self-contained — this file only
# decides which one to hand control to.

def _get_module():
    if bpy.app.version >= (5, 0, 0):
        from . import uv_island_pixel_snap_b5 as snap
    else:
        from . import uv_island_pixel_snap_b4 as snap
    return snap


def register():
    _get_module().register()


def unregister():
    _get_module().unregister()
