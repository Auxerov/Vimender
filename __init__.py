bl_info = {
    "name": "Vimender",
    "author": "Auxerov",
    "version": (1, 3, 0),
    "blender": (5, 0, 0),
    "location": "F3 > Vim Mode",
    "description": "Keyboard controller for Blender with modal navigation, hints, walk mode, and tablet mode",
    "category": "Interface",
}

import importlib

if "state" in locals():
    importlib.reload(state)
    importlib.reload(mode_walk)
    importlib.reload(mode_tablet)
    importlib.reload(mode_cursor)
    importlib.reload(mode_nav)
    importlib.reload(mode_preview)
    importlib.reload(mode_pen)
    importlib.reload(mode_hint)
    importlib.reload(ui)
    importlib.reload(preferences)
    importlib.reload(operators)
else:
    from . import state
    from . import mode_walk
    from . import mode_tablet
    from . import mode_cursor
    from . import mode_nav
    from . import mode_preview
    from . import mode_pen
    from . import mode_hint
    from . import ui
    from . import preferences
    from . import operators

import bpy
from .state import S, load_vimender_state, get_preferences, close_x11_display
from .ui import (
    patch_view_pie, unpatch_view_pie, restore_modal_keymaps, shutdown,
    VIM_MT_editor_more, VIEW3D_MT_vim_view_pie
)
from .preferences import (
    VimenderPreferences, WM_OT_vim_export_keymap,
    WM_OT_vim_import_keymap, WM_OT_vim_reset_keymap,
    WM_OT_vim_check_update, WM_OT_vim_update_addon,
    WM_OT_vim_update_dialog, repair_corrupted_keymaps
)
from .operators import (
    WM_OT_vim_mode_toggle, WM_OT_vim_nudge, WM_OT_vim_set_editor,
    WM_OT_vim_tablet_select_modifier,
    WM_OT_vim_search_object, WM_OT_vim_search_mode, WM_OT_vim_fuzzy_search,
    WM_OT_vim_general_search, WM_OT_vim_save_prompt, WM_OT_vim_save_action,
    WM_OT_vim_close_search, register_search_key, unregister_search_key
)

classes = (
    VimenderPreferences,
    WM_OT_vim_mode_toggle,
    WM_OT_vim_nudge,
    WM_OT_vim_set_editor,
    WM_OT_vim_tablet_select_modifier,
    WM_OT_vim_search_object,
    WM_OT_vim_search_mode,
    WM_OT_vim_fuzzy_search,
    WM_OT_vim_general_search,
    WM_OT_vim_save_prompt,
    WM_OT_vim_save_action,
    WM_OT_vim_close_search,
    WM_OT_vim_export_keymap,
    WM_OT_vim_import_keymap,
    WM_OT_vim_reset_keymap,
    WM_OT_vim_check_update,
    WM_OT_vim_update_addon,
    WM_OT_vim_update_dialog,
    VIM_MT_editor_more,
    VIEW3D_MT_vim_view_pie,
)


def _auto_start():
    if bpy.app.background:
        return None
    try:
        if load_vimender_state() and not S.active:
            bpy.ops.wm.vim_mode_toggle('INVOKE_DEFAULT')
    except Exception as e:
        print(f"[Vimender] Auto-start error: {e}")
    return None


@bpy.app.handlers.persistent
def _on_load_pre(dummy):
    if bpy.app.background:
        return
    shutdown()


@bpy.app.handlers.persistent
def _on_load_post(dummy):
    if bpy.app.background:
        return
    shutdown()
    register_search_key()
    if load_vimender_state():
        bpy.app.timers.register(_auto_start, first_interval=0.1)


def _window_menu_draw(self, context):
    self.layout.separator()
    self.layout.operator(WM_OT_vim_mode_toggle.bl_idname, text="Vimender Mode",
                         icon='CHECKBOX_HLT' if S.active else 'CHECKBOX_DEHLT')
    self.layout.operator(WM_OT_vim_check_update.bl_idname, text="Vimender: Check for Updates", icon='FILE_REFRESH')
    self.layout.operator(WM_OT_vim_update_addon.bl_idname, text="Vimender: Update Now", icon='IMPORT')


def _view3d_menu_draw(self, context):
    self.layout.separator()
    self.layout.operator(WM_OT_vim_update_dialog.bl_idname, text="Vimender: Check Updates...", icon='URL')


def register():
    for c in classes:
        bpy.utils.register_class(c)
    patch_view_pie()
    register_search_key()
    try:
        prefs = get_preferences()
        if prefs:
            repair_corrupted_keymaps(prefs)
    except Exception:
        pass
    bpy.types.TOPBAR_MT_window.append(_window_menu_draw)
    try:
        bpy.types.VIEW3D_MT_view.append(_view3d_menu_draw)
    except Exception:
        pass
    if _on_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_on_load_pre)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    if not bpy.app.background and load_vimender_state():
        bpy.app.timers.register(_auto_start, first_interval=0.1)


def unregister():
    unpatch_view_pie()
    shutdown()
    restore_modal_keymaps()
    close_x11_display()
    unregister_search_key()
    try:
        bpy.types.TOPBAR_MT_window.remove(_window_menu_draw)
    except Exception:
        pass
    try:
        bpy.types.VIEW3D_MT_view.remove(_view3d_menu_draw)
    except Exception:
        pass
    if _on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load_pre)
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass


if __name__ == "__main__":
    register()
