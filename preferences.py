import bpy
import json
import os
from bpy_extras.io_utils import ExportHelper, ImportHelper
from .state import (
    S, tag_all, save_vimender_config, load_vimender_config,
    save_vimender_state, load_vimender_state,
    save_arrow_keys_layout, load_arrow_keys_layout,
    get_preferences, _keymaps_initialized
)

KEY_ITEMS = [
    (chr(c), chr(c), "") for c in range(ord('A'), ord('Z') + 1)
] + [
    ('NUMPAD_0', 'Numpad 0', ''), ('NUMPAD_1', 'Numpad 1', ''), ('NUMPAD_2', 'Numpad 2', ''),
    ('NUMPAD_3', 'Numpad 3', ''), ('NUMPAD_4', 'Numpad 4', ''), ('NUMPAD_5', 'Numpad 5', ''),
    ('NUMPAD_6', 'Numpad 6', ''), ('NUMPAD_7', 'Numpad 7', ''), ('NUMPAD_8', 'Numpad 8', ''),
    ('NUMPAD_9', 'Numpad 9', ''), ('NUMPAD_PERIOD', 'Numpad .', ''), ('NUMPAD_COMMA', 'Numpad ,', ''),
    ('NUMPAD_ENTER', 'Numpad Enter', ''), ('NUMPAD_PLUS', 'Numpad +', ''),
    ('NUMPAD_MINUS', 'Numpad -', ''), ('NUMPAD_ASTERIX', 'Numpad *', ''),
    ('NUMPAD_SLASH', 'Numpad /', '')
] + [
    (f'F{i}', f'F{i}', '') for i in range(1, 13)
] + [
    ('SPACE', 'Space', ''), ('TAB', 'Tab', ''), ('RET', 'Enter', ''),
    ('ESC', 'Escape', ''), ('BACK_SPACE', 'Backspace', ''), ('DELETE', 'Delete', ''),
    ('PAGE_UP', 'Page Up', ''), ('PAGE_DOWN', 'Page Down', ''),
    ('HOME', 'Home', ''), ('END', 'End', ''),
    ('UP_ARROW', 'Up Arrow', ''), ('DOWN_ARROW', 'Down Arrow', ''),
    ('LEFT_ARROW', 'Left Arrow', ''), ('RIGHT_ARROW', 'Right Arrow', ''),
    ('COMMA', 'Comma (,)', ''), ('PERIOD', 'Period (.)', ''),
    ('MINUS', 'Minus (-)', ''), ('EQUAL', 'Equal (=)', ''),
    ('SLASH', 'Slash (/)', ''), ('BACK_SLASH', 'Backslash (\\)', ''),
    ('SEMI_COLON', 'Semicolon (;)', ''), ('QUOTE', "Quote (')", ''),
    ('ACCENT_GRAVE', 'Grave (`)', '')
] + [
    (str(i), str(i), '') for i in range(10)
] + [
    ('NONE', 'None', '')
]


KEY_PREFIXES = (
    ("search_mode", "Mode Search (Alt+Space)", "SPACE", False, False, True),
    ("preview", "Preview Mode Toggle (Ctrl+Y)", "Y", True, False, False),
    ("nav", "Navigation Mode Toggle (Ctrl+B)", "B", True, False, False),
    ("cursor", "Cursor Mode Toggle (Ctrl+Num5)", "NUMPAD_5", True, False, False),
    ("walk", "Walk Mode Toggle (Alt+Q)", "Q", False, False, True),
    ("tablet", "Tablet Mode Toggle (Ctrl+W)", "W", True, False, False),
    ("orbit_pan", "Orbit / Pan Mode Toggle (U)", "U", False, False, False),
    ("editor_pie", "Editor Pie Menu", "NONE", False, False, False),
    ("search_menu", "General Search (Space)", "SPACE", False, False, False),
    ("search_object", "Search Objects / Cursor (Ctrl+Space)", "SPACE", True, False, False),
    ("fuzzy_search", "Data Search (AltGr+Space)", "SPACE", True, False, True),
    ("hint_select", "Hint Click Select (F)", "F", False, False, False),
    ("box_select", "Box Hint Select (C)", "C", False, False, False),
    ("bevel", "Bevel Tool (B)", "B", False, False, False),
    ("persp_ortho", "Persp / Ortho View (Y / Asterisk)", "Y", False, False, False),
    ("view_selected", "Focus Selected View (Numpad . / I)", "NUMPAD_PERIOD", False, False, False),
)


def save_keymaps_to_config(prefs):
    if not prefs:
        return
    km = {}
    for prefix, label, def_k, def_c, def_s, def_a in KEY_PREFIXES:
        km[prefix] = {
            "key": getattr(prefs, f"key_{prefix}", def_k),
            "ctrl": getattr(prefs, f"ctrl_{prefix}", def_c),
            "shift": getattr(prefs, f"shift_{prefix}", def_s),
            "alt": getattr(prefs, f"alt_{prefix}", def_a),
        }
    save_vimender_config({'keymaps': km})


def repair_corrupted_keymaps(prefs):
    if not prefs:
        return False
    if getattr(prefs, 'key_nav', '') == 'A' and getattr(prefs, 'key_preview', '') == 'X':
        for prefix, label, def_k, def_c, def_s, def_a in KEY_PREFIXES:
            try:
                setattr(prefs, f"key_{prefix}", def_k)
                setattr(prefs, f"ctrl_{prefix}", def_c)
                setattr(prefs, f"shift_{prefix}", def_s)
                setattr(prefs, f"alt_{prefix}", def_a)
            except Exception:
                pass
        save_keymaps_to_config(prefs)
        return True
    return False


def load_keymaps_from_config(prefs):
    if not prefs:
        return
    cfg = load_vimender_config()
    km = cfg.get('keymaps', {})
    if not km:
        repair_corrupted_keymaps(prefs)
        return
    for prefix, label, def_k, def_c, def_s, def_a in KEY_PREFIXES:
        entry = km.get(prefix)
        if entry and isinstance(entry, dict):
            if "key" in entry:
                setattr(prefs, f"key_{prefix}", entry["key"])
            if "ctrl" in entry:
                setattr(prefs, f"ctrl_{prefix}", bool(entry["ctrl"]))
            if "shift" in entry:
                setattr(prefs, f"shift_{prefix}", bool(entry["shift"]))
            if "alt" in entry:
                setattr(prefs, f"alt_{prefix}", bool(entry["alt"]))
    repair_corrupted_keymaps(prefs)


def _on_keymap_change(self, context):
    try:
        save_keymaps_to_config(self)
    except Exception as e:
        print(f"[Vimender] Keymap save error: {e}")


class VimenderPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__ or __name__.partition('.')[0] or "Vimender"

    key_preview: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='Y', update=_on_keymap_change)
    ctrl_preview: bpy.props.BoolProperty(name="Ctrl", default=True, update=_on_keymap_change)
    shift_preview: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_preview: bpy.props.BoolProperty(name="Alt", default=False, update=_on_keymap_change)

    key_nav: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='B', update=_on_keymap_change)
    ctrl_nav: bpy.props.BoolProperty(name="Ctrl", default=True, update=_on_keymap_change)
    shift_nav: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_nav: bpy.props.BoolProperty(name="Alt", default=False, update=_on_keymap_change)

    key_cursor: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='NUMPAD_5', update=_on_keymap_change)
    ctrl_cursor: bpy.props.BoolProperty(name="Ctrl", default=True, update=_on_keymap_change)
    shift_cursor: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_cursor: bpy.props.BoolProperty(name="Alt", default=False, update=_on_keymap_change)

    key_walk: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='Q', update=_on_keymap_change)
    ctrl_walk: bpy.props.BoolProperty(name="Ctrl", default=False, update=_on_keymap_change)
    shift_walk: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_walk: bpy.props.BoolProperty(name="Alt", default=True, update=_on_keymap_change)

    key_tablet: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='W', update=_on_keymap_change)
    ctrl_tablet: bpy.props.BoolProperty(name="Ctrl", default=True, update=_on_keymap_change)
    shift_tablet: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_tablet: bpy.props.BoolProperty(name="Alt", default=False, update=_on_keymap_change)

    key_orbit_pan: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='U', update=_on_keymap_change)
    ctrl_orbit_pan: bpy.props.BoolProperty(name="Ctrl", default=False, update=_on_keymap_change)
    shift_orbit_pan: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_orbit_pan: bpy.props.BoolProperty(name="Alt", default=False, update=_on_keymap_change)

    key_editor_pie: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='NONE', update=_on_keymap_change)
    ctrl_editor_pie: bpy.props.BoolProperty(name="Ctrl", default=False, update=_on_keymap_change)
    shift_editor_pie: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_editor_pie: bpy.props.BoolProperty(name="Alt", default=False, update=_on_keymap_change)

    key_search_mode: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='SPACE', update=_on_keymap_change)
    ctrl_search_mode: bpy.props.BoolProperty(name="Ctrl", default=False, update=_on_keymap_change)
    shift_search_mode: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_search_mode: bpy.props.BoolProperty(name="Alt", default=True, update=_on_keymap_change)

    key_search_menu: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='SPACE', update=_on_keymap_change)
    ctrl_search_menu: bpy.props.BoolProperty(name="Ctrl", default=False, update=_on_keymap_change)
    shift_search_menu: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_search_menu: bpy.props.BoolProperty(name="Alt", default=False, update=_on_keymap_change)

    key_search_object: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='SPACE', update=_on_keymap_change)
    ctrl_search_object: bpy.props.BoolProperty(name="Ctrl", default=True, update=_on_keymap_change)
    shift_search_object: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_search_object: bpy.props.BoolProperty(name="Alt", default=False, update=_on_keymap_change)

    key_fuzzy_search: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='SPACE', update=_on_keymap_change)
    ctrl_fuzzy_search: bpy.props.BoolProperty(name="Ctrl", default=True, update=_on_keymap_change)
    shift_fuzzy_search: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_fuzzy_search: bpy.props.BoolProperty(name="Alt", default=True, update=_on_keymap_change)

    key_hint_select: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='F', update=_on_keymap_change)
    ctrl_hint_select: bpy.props.BoolProperty(name="Ctrl", default=False, update=_on_keymap_change)
    shift_hint_select: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_hint_select: bpy.props.BoolProperty(name="Alt", default=False, update=_on_keymap_change)

    key_box_select: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='C', update=_on_keymap_change)
    ctrl_box_select: bpy.props.BoolProperty(name="Ctrl", default=False, update=_on_keymap_change)
    shift_box_select: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_box_select: bpy.props.BoolProperty(name="Alt", default=False, update=_on_keymap_change)

    key_bevel: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='B', update=_on_keymap_change)
    ctrl_bevel: bpy.props.BoolProperty(name="Ctrl", default=False, update=_on_keymap_change)
    shift_bevel: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_bevel: bpy.props.BoolProperty(name="Alt", default=False, update=_on_keymap_change)

    key_persp_ortho: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='Y', update=_on_keymap_change)
    ctrl_persp_ortho: bpy.props.BoolProperty(name="Ctrl", default=False, update=_on_keymap_change)
    shift_persp_ortho: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_persp_ortho: bpy.props.BoolProperty(name="Alt", default=False, update=_on_keymap_change)

    key_view_selected: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='NUMPAD_PERIOD', update=_on_keymap_change)
    ctrl_view_selected: bpy.props.BoolProperty(name="Ctrl", default=False, update=_on_keymap_change)
    shift_view_selected: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_view_selected: bpy.props.BoolProperty(name="Alt", default=False, update=_on_keymap_change)

    def _get_auto_start(self):
        return load_vimender_state()

    def _set_auto_start(self, val):
        save_vimender_state(val)

    auto_start: bpy.props.BoolProperty(
        name="Auto-Start Vimender on Launch",
        description="Automatically activate Vimender when Blender starts or opens a project",
        default=False,
        get=_get_auto_start,
        set=_set_auto_start
    )

    def _get_arrow_keys_layout(self):
        return load_arrow_keys_layout()

    def _set_arrow_keys_layout(self, val):
        save_arrow_keys_layout(val)

    arrow_keys_layout: bpy.props.BoolProperty(
        name="Arrow Keys Layout",
        description="Enable Arrow Keys Layout for laptops and keyboards without a numpad",
        default=False,
        get=_get_arrow_keys_layout,
        set=_set_arrow_keys_layout
    )


    def _draw_shortcut(self, layout, label, prefix):
        row = layout.row(align=True)
        row.label(text=label)
        sub = row.row(align=True)
        sub.prop(self, f"key_{prefix}", text="")
        sub.prop(self, f"ctrl_{prefix}", text="Ctrl", toggle=True)
        sub.prop(self, f"shift_{prefix}", text="Shift", toggle=True)
        sub.prop(self, f"alt_{prefix}", text="Alt", toggle=True)

    def draw(self, context, layout=None):
        if layout is None:
            layout = getattr(self, "layout", None)
        if layout is None:
            return
        try:
            top_box = layout.box()
            top_row = top_box.row(align=True)
            top_row.operator("wm.vim_export_keymap", text="Export Keymap", icon='EXPORT')
            top_row.operator("wm.vim_import_keymap", text="Import Keymap", icon='IMPORT')
            top_row.operator("wm.vim_reset_keymap", text="Reset Defaults", icon='LOOP_BACK')

            box0 = layout.box()
            box0.label(text="Input Devices & Layout", icon='KEYBOARD')
            box0.prop(self, "arrow_keys_layout", text="Arrow Keys Layout (Laptop / No Numpad)")

            box = layout.box()
            box.label(text="Mode Switching Shortcuts", icon='ARROW_LEFTRIGHT')
            self._draw_shortcut(box, "Mode Search (Alt+Space)", "search_mode")
            self._draw_shortcut(box, "Preview Mode Toggle (Ctrl+Y)", "preview")
            self._draw_shortcut(box, "Navigation Mode Toggle (Ctrl+B)", "nav")
            self._draw_shortcut(box, "Cursor Mode Toggle (Ctrl+Num5)", "cursor")
            self._draw_shortcut(box, "Walk Mode Toggle (Alt+Q)", "walk")
            self._draw_shortcut(box, "Tablet Mode Toggle (Ctrl+W)", "tablet")
            self._draw_shortcut(box, "Orbit / Pan Mode Toggle (U)", "orbit_pan")

            box2 = layout.box()
            box2.label(text="Tool & General Shortcuts", icon='EVENT_F')
            self._draw_shortcut(box2, "General Search (Space)", "search_menu")
            self._draw_shortcut(box2, "Search Objects / Cursor (Ctrl+Space)", "search_object")
            self._draw_shortcut(box2, "Data Search (AltGr+Space)", "fuzzy_search")
            self._draw_shortcut(box2, "Hint Click Select (F)", "hint_select")
            self._draw_shortcut(box2, "Box Hint Select (C)", "box_select")
            self._draw_shortcut(box2, "Bevel Tool (B)", "bevel")
            self._draw_shortcut(box2, "Persp / Ortho View (Y / Asterisk)", "persp_ortho")
            self._draw_shortcut(box2, "Focus Selected View (Numpad . / I)", "view_selected")

            box3 = layout.box()
            box3.label(text="General Settings", icon='PREFERENCES')
            box3.prop(self, "auto_start", text="Auto-Start Vimender on Launch")
        except Exception as e:
            layout.label(text=f"Vimender Preferences error: {e}", icon='ERROR')
            print(f"[Vimender] Preferences draw error: {e}")
class WM_OT_vim_export_keymap(bpy.types.Operator, ExportHelper):
    bl_idname = "wm.vim_export_keymap"
    bl_label = "Export Vimender Keymap"
    bl_description = "Export Vimender shortcuts to a JSON file"

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(
        default="*.json",
        options={'HIDDEN'},
        maxlen=255,
    )

    filepath: bpy.props.StringProperty(
        name="File Path",
        description="File path for exporting keymaps",
        maxlen=1024,
        default="vimender_keymap.json",
        subtype='FILE_PATH',
    )

    def execute(self, context):
        prefs = get_preferences(context)
        if not prefs:
            self.report({'ERROR'}, "Vimender preferences could not be found")
            return {'CANCELLED'}
        data = {
            "addon": "Vimender",
            "version": (1, 3, 0),
            "keymaps": {},
            "arrow_keys_layout": bool(getattr(prefs, 'arrow_keys_layout', False)),
            "auto_start": bool(getattr(prefs, 'auto_start', False)),
        }
        for prefix, label, def_k, def_c, def_s, def_a in KEY_PREFIXES:
            data["keymaps"][prefix] = {
                "key": getattr(prefs, f"key_{prefix}", def_k),
                "ctrl": getattr(prefs, f"ctrl_{prefix}", def_c),
                "shift": getattr(prefs, f"shift_{prefix}", def_s),
                "alt": getattr(prefs, f"alt_{prefix}", def_a),
            }
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            self.report({'INFO'}, f"Vimender: Keymaps exported to {os.path.basename(self.filepath)}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Export failed: {e}")
            return {'CANCELLED'}


class WM_OT_vim_import_keymap(bpy.types.Operator, ImportHelper):
    bl_idname = "wm.vim_import_keymap"
    bl_label = "Import Vimender Keymap"
    bl_description = "Import Vimender shortcuts from a JSON file"

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(
        default="*.json",
        options={'HIDDEN'},
        maxlen=255,
    )

    filepath: bpy.props.StringProperty(
        name="File Path",
        description="File path for importing keymaps",
        maxlen=1024,
        default="",
        subtype='FILE_PATH',
    )

    def execute(self, context):
        if not os.path.exists(self.filepath):
            self.report({'ERROR'}, "Specified keymap file does not exist")
            return {'CANCELLED'}
        prefs = get_preferences(context)
        if not prefs:
            self.report({'ERROR'}, "Vimender preferences could not be found")
            return {'CANCELLED'}
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            km = data.get("keymaps", data)
            for prefix, label, def_k, def_c, def_s, def_a in KEY_PREFIXES:
                if prefix in km and isinstance(km[prefix], dict):
                    entry = km[prefix]
                    if "key" in entry:
                        setattr(prefs, f"key_{prefix}", entry["key"])
                    if "ctrl" in entry:
                        setattr(prefs, f"ctrl_{prefix}", bool(entry["ctrl"]))
                    if "shift" in entry:
                        setattr(prefs, f"shift_{prefix}", bool(entry["shift"]))
                    if "alt" in entry:
                        setattr(prefs, f"alt_{prefix}", bool(entry["alt"]))
            if "arrow_keys_layout" in data:
                prefs.arrow_keys_layout = bool(data["arrow_keys_layout"])
            if "auto_start" in data:
                prefs.auto_start = bool(data["auto_start"])
            save_keymaps_to_config(prefs)
            tag_all()
            self.report({'INFO'}, f"Vimender: Keymaps imported from {os.path.basename(self.filepath)}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Import failed: {e}")
            return {'CANCELLED'}


class WM_OT_vim_reset_keymap(bpy.types.Operator):
    bl_idname = "wm.vim_reset_keymap"
    bl_label = "Reset Keymaps to Defaults"
    bl_description = "Reset all Vimender shortcuts to factory defaults"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        prefs = get_preferences(context)
        if not prefs:
            self.report({'ERROR'}, "Vimender preferences could not be found")
            return {'CANCELLED'}
        for prefix, label, def_k, def_c, def_s, def_a in KEY_PREFIXES:
            setattr(prefs, f"key_{prefix}", def_k)
            setattr(prefs, f"ctrl_{prefix}", def_c)
            setattr(prefs, f"shift_{prefix}", def_s)
            setattr(prefs, f"alt_{prefix}", def_a)
        save_keymaps_to_config(prefs)
        tag_all()
        self.report({'INFO'}, "Vimender: Keymaps reset to defaults")
        return {'FINISHED'}


