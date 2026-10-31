bl_info = {
    "name": "Vimender",
    "author": "Auxerov",
    "version": (1, 0, 5),
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
    importlib.reload(bool_tool)
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
    from . import bool_tool
    from . import ui
    from . import preferences
    from . import operators

import bpy
from .state import S, load_vimender_state, get_preferences, close_x11_display, set_active_mode, tag_all
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
    WM_OT_vim_mode_toggle, WM_OT_vim_pen_mode_toggle, WM_OT_vim_pen_settings, WM_OT_vim_nudge, WM_OT_vim_set_editor,
    WM_OT_vim_tablet_select_modifier,
    WM_OT_vim_search_object, WM_OT_vim_search_mode, WM_OT_vim_fuzzy_search,
    WM_OT_vim_general_search, WM_OT_vim_save_prompt, WM_OT_vim_save_action,
    WM_OT_vim_close_search, register_search_key, unregister_search_key
)

classes = (
    VimenderPreferences,
    WM_OT_vim_mode_toggle,
    WM_OT_vim_pen_mode_toggle,
    WM_OT_vim_pen_settings,
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


@bpy.app.handlers.persistent
def _on_depsgraph_update(scene, depsgraph=None):
    if getattr(S, 'search_active', False):
        S.search_active = False


@bpy.app.handlers.persistent
def _on_undo_post(scene):
    if getattr(S, 'search_active', False):
        S.search_active = False


def _get_vim_pen_mode(self):
    return bool(getattr(S, 'pen_mode', False))


def _set_vim_pen_mode(self, val):
    cur = bool(getattr(S, 'pen_mode', False))
    if val == cur:
        return
    ctx = bpy.context
    if val:
        if not S.active:
            try:
                bpy.ops.wm.vim_mode_toggle('INVOKE_DEFAULT')
            except Exception as e:
                print(f"[Vimender] Failed to activate Vimender: {e}")
        win = getattr(ctx, 'window', None)
        target_a = getattr(ctx, 'area', None) or (
            next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None) if win and getattr(win, 'screen', None) else None
        )
        set_active_mode('PEN', ctx, target_area=target_a)
    else:
        set_active_mode('PREVIEW', ctx)
    tag_all()


def _edit_menu_draw(self, context):
    wm = getattr(context, 'window_manager', None)
    if wm and hasattr(wm, 'vim_pen_mode'):
        self.layout.separator()
        self.layout.prop(wm, 'vim_pen_mode', text="Vimender: Pen Mode")
        self.layout.operator(WM_OT_vim_pen_settings.bl_idname, text="Vimender: Pen Settings...", icon='PREFERENCES')


def _window_menu_draw(self, context):
    self.layout.separator()
    self.layout.operator(WM_OT_vim_mode_toggle.bl_idname, text="Vimender Mode",
                         icon='CHECKBOX_HLT' if S.active else 'CHECKBOX_DEHLT')
    wm = getattr(context, 'window_manager', None)
    if wm and hasattr(wm, 'vim_pen_mode'):
        self.layout.prop(wm, 'vim_pen_mode', text="Vimender: Pen Mode")
        self.layout.operator(WM_OT_vim_pen_settings.bl_idname, text="Vimender: Pen Settings...", icon='PREFERENCES')
    self.layout.operator(WM_OT_vim_check_update.bl_idname, text="Vimender: Check for Updates", icon='FILE_REFRESH')
    self.layout.operator(WM_OT_vim_update_addon.bl_idname, text="Vimender: Update Now", icon='IMPORT')


def _view3d_menu_draw(self, context):
    self.layout.separator()
    self.layout.operator(WM_OT_vim_pen_settings.bl_idname, text="Vimender: Pen Mode Sensitivity...", icon='PREFERENCES')
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
    bpy.types.WindowManager.vim_pen_mode = bpy.props.BoolProperty(
        name="Vimender: Pen Mode",
        description="Toggle Vimender Pen Mode (Stylus drawing with 2-finger hand pinch-zoom)",
        get=_get_vim_pen_mode,
        set=_set_vim_pen_mode,
    )
    bpy.types.TOPBAR_MT_window.append(_window_menu_draw)
    try:
        bpy.types.TOPBAR_MT_edit.append(_edit_menu_draw)
    except Exception:
        pass
    try:
        bpy.types.VIEW3D_MT_view.append(_view3d_menu_draw)
    except Exception:
        pass
    if _on_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_on_load_pre)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    if _on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
    if _on_undo_post not in bpy.app.handlers.undo_post:
        bpy.app.handlers.undo_post.append(_on_undo_post)
    if not bpy.app.background and load_vimender_state():
        bpy.app.timers.register(_auto_start, first_interval=0.1)


def unregister():
    unpatch_view_pie()
    shutdown()
    restore_modal_keymaps()
    close_x11_display()
    unregister_search_key()
    try:
        bpy.types.TOPBAR_MT_edit.remove(_edit_menu_draw)
    except Exception:
        pass
    try:
        del bpy.types.WindowManager.vim_pen_mode
    except Exception:
        pass
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
    if _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    if _on_undo_post in bpy.app.handlers.undo_post:
        bpy.app.handlers.undo_post.remove(_on_undo_post)
    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass


if __name__ == "__main__":
    register()
