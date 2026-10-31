import bpy
import json
import os
import subprocess
import urllib.request
import zipfile
import shutil
import tempfile
import sys
import importlib
from bpy_extras.io_utils import ExportHelper, ImportHelper
from .state import (
    S, tag_all, save_vimender_config, load_vimender_config,
    save_vimender_state, load_vimender_state,
    save_arrow_keys_layout, load_arrow_keys_layout,
    get_preferences, _keymaps_initialized
)

def get_addon_directory():
    """
    Returns the real directory path of the Vimender addon, resolving symlinks.
    """
    return os.path.dirname(os.path.realpath(os.path.abspath(__file__)))


_cached_installed_commit = None


def get_installed_git_commit(addon_dir=None):
    """
    Returns the current local commit hash or version without blocking.
    Checks version.json first, then .git, with in-memory caching.
    """
    global _cached_installed_commit
    if _cached_installed_commit is not None:
        return _cached_installed_commit

    if addon_dir is None:
        addon_dir = get_addon_directory()


    try:
        vfile = os.path.join(addon_dir, "version.json")
        if os.path.exists(vfile):
            with open(vfile, "r", encoding="utf-8") as f:
                d = json.load(f)
                val = d.get("commit", "")
                if val:
                    _cached_installed_commit = val
                    return val
    except Exception:
        pass


    try:
        git_dir = os.path.join(addon_dir, ".git")
        if os.path.exists(git_dir):
            res = subprocess.run(
                ["git", "-C", addon_dir, "rev-parse", "HEAD"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2
            )
            if res.returncode == 0 and res.stdout.strip():
                _cached_installed_commit = res.stdout.strip()
                return _cached_installed_commit
    except Exception:
        pass

    _cached_installed_commit = "1ef78b3"
    return _cached_installed_commit


def get_remote_git_commit():
    """
    Queries GitHub for the latest commit SHA and description on the main branch.
    First tries 'git ls-remote' (fast and rate-limit free), then falls back to GitHub REST API.
    """

    try:
        res = subprocess.run(
            ["git", "ls-remote", "https://github.com/Auxerov/Vimender.git", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=6
        )
        if res.returncode == 0 and res.stdout.strip():
            sha = res.stdout.strip().split()[0]
            return sha, "Latest commit on origin/main"
    except Exception:
        pass


    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/Auxerov/Vimender/commits/main",
            headers={"User-Agent": "Vimender-Blender-Addon"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            sha = data.get("sha", "")
            msg = data.get("commit", {}).get("message", "").splitlines()[0]
            return sha, msg
    except Exception as e:
        return "", f"Check failed: {e}"


def perform_addon_update():
    """
    Direct Updater from GitHub:
    Downloads main.zip from https://github.com/Auxerov/Vimender/archive/refs/heads/main.zip,
    extracts into addon folder, updates version.json, and hot-reloads all modules.
    No git clone required!
    """
    global _cached_installed_commit
    addon_dir = get_addon_directory()


    try:
        req = urllib.request.Request(
            "https://github.com/Auxerov/Vimender/archive/refs/heads/main.zip",
            headers={"User-Agent": "Vimender-Blender-Addon"}
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            zip_bytes = resp.read()

        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = os.path.join(tmp_dir, "vimender_update.zip")
            with open(zip_path, "wb") as f:
                f.write(zip_bytes)

            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(tmp_dir)

            extracted_subdirs = [
                os.path.join(tmp_dir, d) for d in os.listdir(tmp_dir)
                if os.path.isdir(os.path.join(tmp_dir, d)) and d != "__MACOSX"
            ]
            if not extracted_subdirs:
                return False, "Failed to locate extracted files in downloaded archive"

            source_dir = extracted_subdirs[0]


            for item in os.listdir(source_dir):
                if item in ("touchview", ".git"):
                    continue
                s = os.path.join(source_dir, item)
                d = os.path.join(addon_dir, item)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)


            remote_sha, msg = get_remote_git_commit()
            if remote_sha:
                _cached_installed_commit = remote_sha
                vfile = os.path.join(addon_dir, "version.json")
                try:
                    with open(vfile, "w", encoding="utf-8") as f:
                        json.dump({"commit": remote_sha, "message": msg}, f, indent=2)
                except Exception:
                    pass

        return True, f"Updated successfully from GitHub! ({remote_sha[:7] if remote_sha else 'Latest'})"
    except Exception as e:
        print(f"[Vimender] Zip download failed, checking git fallback: {e}")


    git_dir = os.path.join(addon_dir, ".git")
    if os.path.exists(git_dir):
        try:
            fetch_res = subprocess.run(
                ["git", "-C", addon_dir, "fetch", "origin", "main"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15
            )
            if fetch_res.returncode == 0:
                reset_res = subprocess.run(
                    ["git", "-C", addon_dir, "reset", "--hard", "origin/main"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10
                )
                if reset_res.returncode == 0:
                    _cached_installed_commit = None
                    new_sha = get_installed_git_commit(addon_dir)
                    return True, f"Git update successful (Commit: {new_sha[:7]})"
        except Exception as e:
            return False, f"Git update error: {e}"

    return False, "Failed to update from GitHub. Please check internet connection."


def reload_vimender_submodules():
    """
    Reloads all Vimender Python modules in sys.modules so changes take effect immediately.
    """
    addon_pkg = __package__ or __name__.partition('.')[0] or "Vimender"
    submodules = [
        "state", "preferences", "mode_walk", "mode_tablet",
        "mode_cursor", "mode_nav", "mode_preview", "mode_pen", "mode_hint",
        "bool_tool", "ui", "operators"
    ]
    reloaded = []
    for mod_name in submodules:
        full_name = f"{addon_pkg}.{mod_name}"
        if full_name in sys.modules:
            try:
                importlib.reload(sys.modules[full_name])
                reloaded.append(mod_name)
            except Exception as e:
                print(f"[Vimender] Module reload error for {full_name}: {e}")
    tag_all()
    return reloaded


class WM_OT_vim_check_update(bpy.types.Operator):
    bl_idname = "wm.vim_check_update"
    bl_label = "Vimender: Check for Updates"
    bl_description = "Check GitHub repository for newer Vimender versions"

    def execute(self, context):
        prefs = get_preferences(context)
        if not prefs:
            self.report({'ERROR'}, "Vimender preferences not found")
            return {'CANCELLED'}

        local_sha = get_installed_git_commit()
        if local_sha:
            prefs.installed_commit = local_sha

        remote_sha, note = get_remote_git_commit()
        if not remote_sha:
            prefs.update_status = f"Check failed: {note}"
            self.report({'WARNING'}, f"Could not check remote updates: {note}")
            return {'CANCELLED'}

        prefs.latest_commit = remote_sha
        prefs.latest_commit_msg = note

        if local_sha and local_sha.lower() == remote_sha.lower():
            prefs.update_status = "Vimender is up to date!"
            prefs.update_available = False
            self.report({'INFO'}, "Vimender is up to date!")
        else:
            prefs.update_status = f"Update available: {remote_sha[:7]}"
            prefs.update_available = True
            self.report({'INFO'}, f"Update available: {remote_sha[:7]} ({note})")

        return {'FINISHED'}


class WM_OT_vim_update_addon(bpy.types.Operator):
    bl_idname = "wm.vim_update_addon"
    bl_label = "Vimender: Update to Latest Version"
    bl_description = "Pull and apply the latest Vimender release from GitHub"

    def execute(self, context):
        prefs = get_preferences(context)
        self.report({'INFO'}, "Updating Vimender directly from GitHub...")
        success, msg = perform_addon_update()
        if not success:
            if prefs:
                prefs.update_status = f"Update error: {msg}"
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}

        reloaded = reload_vimender_submodules()
        new_sha = get_installed_git_commit()
        if prefs:
            if new_sha:
                prefs.installed_commit = new_sha
            prefs.update_status = "Updated successfully! Modules reloaded."
            prefs.update_available = False

        self.report({'INFO'}, f"Vimender updated successfully! ({len(reloaded)} modules reloaded)")
        return {'FINISHED'}


class WM_OT_vim_update_dialog(bpy.types.Operator):
    bl_idname = "wm.vim_update_dialog"
    bl_label = "Vimender Update Dialog"
    bl_description = "Open the Vimender updater dialog"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        prefs = get_preferences(context)

        box = layout.box()
        box.label(text="Vimender GitHub Updater", icon='URL')

        col = box.column(align=True)
        curr_sha = get_installed_git_commit()
        if curr_sha:
            col.label(text=f"Current Commit: {curr_sha[:7]}", icon='CHECKMARK')
        else:
            col.label(text="Version: 1.3.0", icon='INFO')

        latest_sha = getattr(prefs, "latest_commit", "")
        if latest_sha:
            msg = getattr(prefs, "latest_commit_msg", "")
            suffix = f" ({msg})" if msg else ""
            col.label(text=f"Latest on GitHub: {latest_sha[:7]}{suffix}", icon='IMPORT')

        status_str = getattr(prefs, "update_status", "Ready to check")
        col.label(text=f"Status: {status_str}", icon='INFO')
        col.label(text="Repo: https://github.com/Auxerov/Vimender.git")

        layout.separator()
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("wm.vim_check_update", text="Check for Updates", icon='FILE_REFRESH')
        row.operator("wm.vim_update_addon", text="Update Now", icon='IMPORT')

    def execute(self, context):
        return {'FINISHED'}


def ensure_updater_classes_registered():
    for op_cls in (WM_OT_vim_check_update, WM_OT_vim_update_addon, WM_OT_vim_update_dialog):
        sub_id = op_cls.bl_idname.partition('.')[2]
        if not hasattr(bpy.ops.wm, sub_id):
            try:
                bpy.utils.register_class(op_cls)
            except Exception:
                pass


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
    ('GRLESS', 'Less Than (<)', ''),
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
    ("search_mode", "Mode Search (AltGr+<)", "GRLESS", True, False, True),
    ("preview", "Preview Mode Toggle (Ctrl+Y)", "Y", True, False, False),
    ("pen", "Pen Mode Toggle (Ctrl+Alt+P)", "P", True, False, True),
    ("nav", "Navigation Mode Toggle (Ctrl+B)", "B", True, False, False),
    ("cursor", "Cursor Mode Toggle (Ctrl+Num5)", "NUMPAD_5", True, False, False),
    ("walk", "Walk Mode Toggle (Alt+Q)", "Q", False, False, True),
    ("tablet", "Tablet Mode Toggle (Ctrl+W)", "W", True, False, False),
    ("orbit_pan", "Orbit / Pan Mode Toggle (U)", "U", False, False, False),
    ("editor_pie", "Editor Pie Menu", "NONE", False, False, False),
    ("search_menu", "General Search (<)", "GRLESS", False, False, False),
    ("search_object", "Search Objects / Cursor (Ctrl+<)", "GRLESS", True, False, False),
    ("fuzzy_search", "Data Search (Shift+AltGr+<)", "GRLESS", True, True, True),
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
    for prefix in ("search_menu", "search_object", "search_mode", "fuzzy_search"):
        if getattr(prefs, f"key_{prefix}", "") == "SPACE":
            setattr(prefs, f"key_{prefix}", "GRLESS")
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

    key_pen: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='P', update=_on_keymap_change)
    ctrl_pen: bpy.props.BoolProperty(name="Ctrl", default=True, update=_on_keymap_change)
    shift_pen: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_pen: bpy.props.BoolProperty(name="Alt", default=True, update=_on_keymap_change)

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

    key_search_mode: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='GRLESS', update=_on_keymap_change)
    ctrl_search_mode: bpy.props.BoolProperty(name="Ctrl", default=True, update=_on_keymap_change)
    shift_search_mode: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_search_mode: bpy.props.BoolProperty(name="Alt", default=True, update=_on_keymap_change)

    key_search_menu: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='GRLESS', update=_on_keymap_change)
    ctrl_search_menu: bpy.props.BoolProperty(name="Ctrl", default=False, update=_on_keymap_change)
    shift_search_menu: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_search_menu: bpy.props.BoolProperty(name="Alt", default=False, update=_on_keymap_change)

    key_search_object: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='GRLESS', update=_on_keymap_change)
    ctrl_search_object: bpy.props.BoolProperty(name="Ctrl", default=True, update=_on_keymap_change)
    shift_search_object: bpy.props.BoolProperty(name="Shift", default=False, update=_on_keymap_change)
    alt_search_object: bpy.props.BoolProperty(name="Alt", default=False, update=_on_keymap_change)

    key_fuzzy_search: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='GRLESS', update=_on_keymap_change)
    ctrl_fuzzy_search: bpy.props.BoolProperty(name="Ctrl", default=True, update=_on_keymap_change)
    shift_fuzzy_search: bpy.props.BoolProperty(name="Shift", default=True, update=_on_keymap_change)
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

    def _get_stylus_pan_button(self):
        return str(load_vimender_config().get('stylus_pan_button', 'BTN_STYLUS')).upper()

    def _set_stylus_pan_button(self, val):
        save_vimender_config({'stylus_pan_button': str(val).upper()})

    stylus_pan_button: bpy.props.EnumProperty(
        name="Stylus Pan Button",
        description="Physical barrel button used for Viewport Pan",
        items=[
            ('BTN_STYLUS', "Upper Barrel Button (Default)", "Hardware BTN_STYLUS / code 331 pans the 3D viewport"),
            ('BTN_STYLUS2', "Lower Barrel Button", "Hardware BTN_STYLUS2 / code 332 pans the 3D viewport"),
            ('BOTH', "Either Barrel Button", "Allow either button to Pan"),
        ],
        default='BTN_STYLUS',
        get=_get_stylus_pan_button,
        set=_set_stylus_pan_button,
    )

    def _get_stylus_pen(self):
        return bool(load_vimender_config().get('stylus_pen', False))

    def _set_stylus_pen(self, val):
        save_vimender_config({'stylus_pen': bool(val)})

    stylus_pen: bpy.props.BoolProperty(
        name="Stylus Pen",
        description="Enable Stylus Pen mode support (automatically enables Emulate 3 Button Mouse in Tablet Mode)",
        default=False,
        get=_get_stylus_pen,
        set=_set_stylus_pen,
    )

    def _get_stylus_pan_sensitivity(self):
        return float(load_vimender_config().get('stylus_pan_sensitivity', 1.75))

    def _set_stylus_pan_sensitivity(self, val):
        save_vimender_config({'stylus_pan_sensitivity': round(float(val), 2)})

    stylus_pan_sensitivity: bpy.props.FloatProperty(
        name="Stylus Pan Sensitivity",
        description="Speed / sensitivity multiplier for Stylus barrel button viewport pan",
        default=1.75,
        min=0.2,
        max=5.0,
        step=10,
        precision=2,
        get=_get_stylus_pan_sensitivity,
        set=_set_stylus_pan_sensitivity,
    )

    def _get_pen_ui_scale(self):
        return float(load_vimender_config().get('pen_ui_scale', 1.25))

    def _set_pen_ui_scale(self, val):
        val = round(float(val), 2)
        save_vimender_config({'pen_ui_scale': val})
        if getattr(S, 'pen_mode', False) and getattr(S, 'pen_prev_ui_scale', None) is not None:
            try:
                base = S.pen_prev_ui_scale
                bpy.context.preferences.view.ui_scale = round(base * max(1.0, val), 2)
            except Exception:
                pass

    pen_ui_scale: bpy.props.FloatProperty(
        name="Pen Mode UI Button Scale",
        description="Scale multiplier applied to UI buttons and Properties panel when entering Pen Mode (e.g. 1.25 = 25% bigger)",
        default=1.25,
        min=1.05,
        max=2.0,
        step=5,
        precision=2,
        get=_get_pen_ui_scale,
        set=_set_pen_ui_scale,
    )

    def _get_pen_orbit_sens(self):
        return float(load_vimender_config().get('pen_orbit_sensitivity', 1.0))

    def _set_pen_orbit_sens(self, val):
        val = round(float(val), 2)
        save_vimender_config({'pen_orbit_sensitivity': val})
        try:
            if getattr(S, 'pen_mode', False) or getattr(S, 'active_mode', None) == 'PEN':
                ctx = getattr(bpy, 'context', None)
                if ctx and hasattr(ctx, 'preferences'):
                    inputs = ctx.preferences.inputs
                    base_tt = getattr(S, 'pen_saved_turntable_sens', None)
                    if base_tt is not None:
                        inputs.view_rotate_sensitivity_turntable = base_tt * val
                    base_tb = getattr(S, 'pen_saved_trackball_sens', None)
                    if base_tb is not None:
                        inputs.view_rotate_sensitivity_trackball = base_tb * val
        except Exception:
            pass

    pen_orbit_sensitivity: bpy.props.FloatProperty(
        name="Pen Orbit Sensitivity",
        description="Speed multiplier for Middle Click Viewport Orbit in Pen Mode",
        default=1.0,
        min=0.2,
        max=5.0,
        step=10,
        precision=2,
        get=_get_pen_orbit_sens,
        set=_set_pen_orbit_sens,
    )

    def _get_pen_pan_sens(self):
        cfg = load_vimender_config()
        return float(cfg.get('pen_pan_sensitivity', cfg.get('stylus_pan_sensitivity', 1.75)))

    def _set_pen_pan_sens(self, val):
        val = round(float(val), 2)
        save_vimender_config({'pen_pan_sensitivity': val, 'stylus_pan_sensitivity': val})

    pen_pan_sensitivity: bpy.props.FloatProperty(
        name="Pen Pan Sensitivity",
        description="Speed multiplier for Shift + Middle Click and Stylus Barrel Pan in Pen Mode",
        default=1.75,
        min=0.2,
        max=5.0,
        step=10,
        precision=2,
        get=_get_pen_pan_sens,
        set=_set_pen_pan_sens,
    )

    update_status: bpy.props.StringProperty(
        name="Update Status",
        default="Ready to check",
        description="Current status of the updater",
    )
    latest_commit: bpy.props.StringProperty(
        name="Latest Commit",
        default="",
        description="Latest remote commit hash from GitHub",
    )
    latest_commit_msg: bpy.props.StringProperty(
        name="Latest Commit Note",
        default="",
        description="Latest remote commit description or status note",
    )
    installed_commit: bpy.props.StringProperty(
        name="Installed Commit",
        default="",
        description="Currently active commit hash",
    )
    update_available: bpy.props.BoolProperty(
        name="Update Available",
        default=False,
        description="Indicates whether a newer commit is available on GitHub",
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
            ensure_updater_classes_registered()
            box_up = layout.box()
            header_row = box_up.row(align=True)
            header_row.label(text="Vimender Updates (GitHub)", icon='URL')


            btn_row = box_up.row(align=True)
            btn_row.scale_y = 1.35
            btn_row.operator("wm.vim_check_update", text="Check for Updates", icon='FILE_REFRESH')
            btn_row.operator("wm.vim_update_addon", text="Update Now", icon='IMPORT')

            info_col = box_up.column(align=True)
            curr_sha = get_installed_git_commit()
            if curr_sha:
                info_col.label(text=f"Current Commit: {curr_sha[:7]}", icon='CHECKMARK')
            else:
                info_col.label(text="Version: 1.3.0", icon='INFO')

            latest_sha = getattr(self, "latest_commit", "")
            if latest_sha:
                msg = getattr(self, "latest_commit_msg", "")
                suffix = f" ({msg})" if msg else ""
                info_col.label(text=f"Latest on GitHub: {latest_sha[:7]}{suffix}", icon='IMPORT')

            status_str = getattr(self, "update_status", "Ready to check")
            info_col.label(text=f"Status: {status_str}", icon='INFO')
            info_col.label(text="Repo: https://github.com/Auxerov/Vimender.git")
        except Exception as e:
            box_up = layout.box()
            box_up.label(text=f"Updater note: {e}", icon='INFO')

        try:

            top_box = layout.box()
            top_row = top_box.row(align=True)
            top_row.operator("wm.vim_export_keymap", text="Export Keymap", icon='EXPORT')
            top_row.operator("wm.vim_import_keymap", text="Import Keymap", icon='IMPORT')
            top_row.operator("wm.vim_reset_keymap", text="Reset Defaults", icon='LOOP_BACK')


            box0 = layout.box()
            box0.label(text="Input Devices & Layout", icon='KEYBOARD')
            box0.prop(self, "arrow_keys_layout", text="Arrow Keys Layout (Laptop / No Numpad)")
            box0.prop(self, "stylus_pen", text="Stylus Pen (Emulate 3 Button Mouse in Tablet Mode)")
            box0.prop(self, "stylus_pan_button", text="Stylus Pan Button")
            box0.prop(self, "pen_orbit_sensitivity", text="Pen Middle-Click Sensitivity (Orbit)", slider=True)
            box0.prop(self, "pen_pan_sensitivity", text="Pen Shift+Middle-Click Sensitivity (Pan)", slider=True)
            box0.prop(self, "pen_ui_scale", text="Pen Mode UI Scale (Properties & Buttons)", slider=True)


            box = layout.box()
            box.label(text="Mode Switching Shortcuts", icon='ARROW_LEFTRIGHT')
            self._draw_shortcut(box, "Mode Search (AltGr+<)", "search_mode")
            self._draw_shortcut(box, "Preview Mode Toggle (Ctrl+Y)", "preview")
            self._draw_shortcut(box, "Pen Mode Toggle (Ctrl+Alt+P)", "pen")
            self._draw_shortcut(box, "Navigation Mode Toggle (Ctrl+B)", "nav")
            self._draw_shortcut(box, "Cursor Mode Toggle (Ctrl+Num5)", "cursor")
            self._draw_shortcut(box, "Walk Mode Toggle (Alt+Q)", "walk")
            self._draw_shortcut(box, "Tablet Mode Toggle (Ctrl+W)", "tablet")
            self._draw_shortcut(box, "Orbit / Pan Mode Toggle (U)", "orbit_pan")


            box2 = layout.box()
            box2.label(text="Tool & General Shortcuts", icon='EVENT_F')
            self._draw_shortcut(box2, "General Search (<)", "search_menu")
            self._draw_shortcut(box2, "Search Objects / Cursor (Ctrl+<)", "search_object")
            self._draw_shortcut(box2, "Data Search (Shift+AltGr+<)", "fuzzy_search")
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
            "version": (1, 0, 0),
            "keymaps": {},
            "arrow_keys_layout": bool(getattr(prefs, 'arrow_keys_layout', False)),
            "stylus_pen": bool(getattr(prefs, 'stylus_pen', False)),
            "stylus_pan_sensitivity": float(getattr(prefs, 'stylus_pan_sensitivity', 1.75)),
            "pen_orbit_sensitivity": float(getattr(prefs, 'pen_orbit_sensitivity', 1.0)),
            "pen_pan_sensitivity": float(getattr(prefs, 'pen_pan_sensitivity', 1.75)),
            "pen_ui_scale": float(getattr(prefs, 'pen_ui_scale', 1.25)),
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
            if "stylus_pen" in data:
                prefs.stylus_pen = bool(data["stylus_pen"])
            if "stylus_pan_sensitivity" in data:
                prefs.stylus_pan_sensitivity = float(data["stylus_pan_sensitivity"])
            if "pen_orbit_sensitivity" in data:
                prefs.pen_orbit_sensitivity = float(data["pen_orbit_sensitivity"])
            if "pen_pan_sensitivity" in data:
                prefs.pen_pan_sensitivity = float(data["pen_pan_sensitivity"])
            if "pen_ui_scale" in data:
                prefs.pen_ui_scale = float(data["pen_ui_scale"])
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





