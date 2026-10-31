from collections import deque
import ctypes
import json
import math
import os
import string
import sys
import time

import blf
import bmesh
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector, Matrix, Quaternion

ALPHABET = "sadgjklewmpghvniruqtyobxz"
ALPHABET = "".join(dict.fromkeys(ch for ch in ALPHABET if ch not in {'f', 'c'}))

MAX_HINTS = 3000
MIN_LABEL_LEN = 2
MAX_LABEL_LEN = 5

BORDER_PX = 3
GREEN = (0.15, 0.95, 0.30, 1.0)
CYAN = (0.20, 0.85, 1.00, 1.0)
RED = (0.95, 0.20, 0.20, 1.0)
PURPLE = (0.78, 0.22, 0.98, 1.0)
YELLOW = (0.95, 0.85, 0.15, 1.0)
ORANGE = (1.00, 0.55, 0.05, 1.0)
GREY = (0.60, 0.60, 0.60, 1.0)
WHITE = (0.98, 0.98, 0.98, 1.0)
HINT_BG = (1.0, 0.86, 0.25, 0.95)
HINT_TEXT = (0.05, 0.05, 0.05, 1.0)
HINT_TYPED = (0.85, 0.10, 0.10, 1.0)
LEADER = (0.08, 0.08, 0.08, 0.85)

PALETTE = (
    (1.00, 0.40, 0.40, 0.95),
    (0.40, 0.75, 1.00, 0.95),
    (0.50, 1.00, 0.50, 0.95),
    (1.00, 0.65, 0.25, 0.95),
    (0.85, 0.55, 1.00, 0.95),
    (0.30, 0.95, 0.90, 0.95),
    (1.00, 0.95, 0.30, 0.95),
    (1.00, 0.55, 0.85, 0.95),
)

SENS_LEVELS = (0.25, 0.4, 0.65, 1.0, 1.45, 2.1, 3.0, 4.2)
DEFAULT_SENS_IDX = 3

CURSOR_MAX_SPEED = 780.0
CURSOR_ACCEL_RATE = 14.0
CURSOR_FRICTION = 22.0
CURSOR_WRAP_MARGIN = 2.0

ORBIT_MAX_SPEED = 2.4
PAN_MAX_SPEED = 1.35
ZOOM_MAX_SPEED = 2.5
VIEW2D_PAN_SPEED = 850.0
NAV_ACCEL_RATE = 15.0
NAV_FRICTION = 18.0
CURSOR_3D_SPEED = 3.5
WALK_MOVE_SPEED = 2.5
WALK_LOOK_SPEED = 2.4

PASS_KEYS_IN = {'TEXT_EDITOR', 'CONSOLE'}

SPACE_NAMES = (
    "SpaceView3D", "SpaceImageEditor", "SpaceNodeEditor", "SpaceSequenceEditor",
    "SpaceTextEditor", "SpaceOutliner", "SpaceProperties", "SpaceConsole",
    "SpaceInfo", "SpaceFileBrowser", "SpaceDopeSheetEditor", "SpaceGraphEditor",
    "SpaceNLA", "SpaceClipEditor", "SpaceSpreadsheet",
)

LETTERS = set(string.ascii_uppercase)
MOUSE_CANCEL = {
    'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE',
    'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE',
}
MODIFIER_KEYS = {
    'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_CTRL', 'RIGHT_CTRL',
    'LEFT_ALT', 'RIGHT_ALT', 'OSKEY',
}
CONSUME_KEYS = {
    'RET', 'NUMPAD_ENTER', 'TAB', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE',
    'SIX', 'SEVEN', 'EIGHT', 'NINE', 'ZERO',
}
NUM_TO_PAD = {
    'ONE': (1, 'NUMPAD_1'),
    'TWO': (2, 'NUMPAD_2'),
    'THREE': (3, 'NUMPAD_3'),
    'FOUR': (4, 'NUMPAD_4'),
    'FIVE': (5, 'NUMPAD_5'),
    'SIX': (6, 'NUMPAD_6'),
    'SEVEN': (7, 'NUMPAD_7'),
    'EIGHT': (8, 'NUMPAD_8'),
    'NINE': (9, 'NUMPAD_9'),
    'ZERO': (0, 'NUMPAD_0'),
}
UNICODE_TO_PAD = {
    '1': (1, 'NUMPAD_1'),
    '2': (2, 'NUMPAD_2'),
    '3': (3, 'NUMPAD_3'),
    '4': (4, 'NUMPAD_4'),
    '5': (5, 'NUMPAD_5'),
    '6': (6, 'NUMPAD_6'),
    '7': (7, 'NUMPAD_7'),
    '8': (8, 'NUMPAD_8'),
    '9': (9, 'NUMPAD_9'),
    '0': (0, 'NUMPAD_0'),
}
NUMPAD_PIE_KEYS = {
    'NUMPAD_0', 'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3', 'NUMPAD_4',
    'NUMPAD_5', 'NUMPAD_6', 'NUMPAD_7', 'NUMPAD_8', 'NUMPAD_9',
}
NUDGE_DIRS = {
    'NUMPAD_8': (0, 1), 'NUMPAD_2': (0, -1),
    'NUMPAD_4': (-1, 0), 'NUMPAD_6': (1, 0),
}
ARROW_DIRS = {
    'UP_ARROW': (0, 1), 'DOWN_ARROW': (0, -1),
    'LEFT_ARROW': (-1, 0), 'RIGHT_ARROW': (1, 0),
}
ALL_DIRS = {
    'NUMPAD_8': (0, 1), 'NUMPAD_2': (0, -1),
    'NUMPAD_4': (-1, 0), 'NUMPAD_6': (1, 0),
    'UP_ARROW': (0, 1), 'DOWN_ARROW': (0, -1),
    'LEFT_ARROW': (-1, 0), 'RIGHT_ARROW': (1, 0),
    'K': (0, 1), 'J': (0, -1),
    'H': (-1, 0), 'L': (1, 0),
    'k': (0, 1), 'j': (0, -1),
    'h': (-1, 0), 'l': (1, 0),
}
PREFIX_DIRS = {
    'LEFT_ARROW': 'LEFT', 'RIGHT_ARROW': 'RIGHT', 'UP_ARROW': 'UP', 'DOWN_ARROW': 'DOWN',
    'H': 'LEFT', 'L': 'RIGHT', 'K': 'UP', 'J': 'DOWN',
}
PREFIX_TIMEOUT = 3.0
PREFIX_REPEAT_TIMEOUT = 0.8

TRANSFORM_KEYWORDS = (
    'transform', 'move', 'extrude', 'duplicate', 'bevel',
    'shrink_fatten', 'push_pull', 'translate', 'rotate', 'resize',
    'scale', 'tosphere', 'shear', 'bend', 'trackball', 'edge_slide', 'vert_slide',
    'loopcut', 'slide', 'inset', 'rip', 'knife', 'subdivide', 'smooth', 'modifier'
)


class _State:
    def __init__(self):
        self.active = False
        self.hover_ptr = 0
        self.pie_ptr = 0
        self.sens_idx = DEFAULT_SENS_IDX
        self.nav_mode = 'ORBIT'
        self.nudge_on = False
        self.prefix = False
        self.prefix_t = 0.0
        self.prefix_timeout = PREFIX_TIMEOUT
        self.mover = None
        self.smooth_box = None
        self.nav = None
        self.transform_center_win = None
        self.transform_center_3d = None
        self.last_transform_type = 'TRANSLATE'
        self.navigation_mode = False
        self.workspace_nav = False
        self.target_workspace_name = None
        self.last_target_ws = None
        self.last_ws_switch_time = 0.0
        self.cursor_mode = False
        self.cursor_plane = 'XY'
        self.right_alt_down = False
        self.search_active = False
        self.search_open_time = 0.0
        self.save_dialog_open = False
        self.save_dialog_action = 'QUIT'
        self.pie_active = False
        self.view_pie_active = False
        self.pie_open_time = 0.0
        self.pie_just_selected_time = 0.0
        self.prev_mode_state = {}
        self.smooth_cursor = None
        self.smooth_walker = None
        self.walk_mode = False
        self.walk_ptr = 0
        self.walk_prev_state = {}
        self.tablet_mode = False
        self.tablet_ptr = 0
        self.tablet_prev_state = {}
        self.tablet_active_transform = 'G'
        self.tablet_in_transform = None
        self.tablet_transform_backup = None
        self.tablet_active_axis = None
        self.tablet_touch_start = None
        self.tablet_touch_dragged = False
        self.tablet_select_modifier = None
        self.tablet_select_tool = 'TWEAK'
        self.tablet_prev_show_toolbar = {}
        self.tablet_prev_gizmos = {}
        self.tablet_prev_emulate_3_button = None
        self.preview_mode = True
        self.pen_mode = False
        self.pen_ptr = 0
        self.pen_nav = None
        self.pen_prev_ui_scale = None
        self.pen_mmb_active = False
        self.pen_mmb_is_pan = False
        self.pen_mmb_last_x = 0
        self.pen_mmb_last_y = 0
        self.pen_mmb_area_ptr = 0
        self.stylus_nav = None
        self.exit_walk_fn = None
        self.exit_tablet_fn = None
        self.exit_cursor_fn = None
        self.exit_pen_fn = None
        self.touch_nav = None
        self.clear_hint()

    def clear_hint(self):
        self.mode = None
        self.kind = None
        self.search_active = False
        self.save_dialog_open = False
        self.pie_active = False
        self.view_pie_active = False
        self.pie_open_time = 0.0
        self.pie_just_selected_time = 0.0
        self.action = 'NORMAL'
        self.box_extend = False
        self.vertex_mode = False
        self.hints = []
        self.typed = ""
        self.box_a = None
        self.box_start_hint = None
        self.box_end_hint = None
        self.hint_ptr = 0
        self.mouse = (0, 0)
        if self.mover:
            self.mover.reset()
        if self.smooth_box:
            self.smooth_box.reset()

    def toggle_nav_mode(self):
        self.nav_mode = 'PAN' if self.nav_mode == 'ORBIT' else 'ORBIT'

    def exit_all_modes(self, context=None, report_fn=None):
        set_active_mode('PREVIEW', context, report_fn)


S = _State()
_handlers = []
_shader = None
_addon_keymaps = []
_X11_LIB = None
_XTST_LIB = None
_ALTGR_KEYCODES = None
_ORIGINAL_VIEW_PIE_DRAW = None
_keymaps_initialized = False

def _node_abs_loc(n):
    x, y = n.location.x, n.location.y
    p = n.parent
    while p is not None:
        x += p.location.x
        y += p.location.y
        p = p.parent
    return x, y


def get_selection_center_2d(context, area):
    """Returns the screen (2D window) center and 3D world coordinate of selected elements."""
    if area is None:
        return None
    region = window_region(area)
    if region is None:
        return None

    if area.type == 'VIEW_3D':
        rv3d = area.spaces.active.region_3d
        if rv3d is None:
            return None
        obj = context.active_object
        mode = obj.mode if obj else 'OBJECT'
        pivot_setting = getattr(context.scene.tool_settings, 'transform_pivot_point', 'MEDIAN_POINT') if context.scene else 'MEDIAN_POINT'

        center_3d = None
        if pivot_setting == 'CURSOR':
            center_3d = context.scene.cursor.location.copy()
        elif pivot_setting == 'ACTIVE_ELEMENT' and obj:
            center_3d = obj.matrix_world.translation.copy()
        elif mode == 'OBJECT':
            sel = [o for o in context.selected_objects if o.visible_get()]
            if sel:
                if pivot_setting == 'BOUNDING_BOX_CENTER':
                    pts = []
                    for o in sel:
                        try:
                            c = sum((Vector(v) for v in o.bound_box), Vector()) / 8.0
                            pts.append(o.matrix_world @ c)
                        except Exception:
                            pts.append(o.matrix_world.translation)
                    center_3d = sum(pts, Vector()) / len(pts)
                else:
                    center_3d = sum((o.matrix_world.translation for o in sel), Vector()) / len(sel)
            elif obj:
                center_3d = obj.matrix_world.translation.copy()
        elif mode == 'EDIT' and obj and obj.type == 'MESH':
            bm = bmesh.from_edit_mesh(obj.data)
            sel_cos = [v.co for v in bm.verts if v.select and not v.hide]
            if sel_cos:
                center_3d = obj.matrix_world @ (sum(sel_cos, Vector()) / len(sel_cos))
            else:
                center_3d = obj.matrix_world.translation.copy()
        elif mode == 'POSE' and obj and obj.type == 'ARMATURE':
            sel_bones = [b for b in obj.pose.bones if b.bone.select]
            if sel_bones:
                center_3d = obj.matrix_world @ (sum((b.head for b in sel_bones), Vector()) / len(sel_bones))
            else:
                center_3d = obj.matrix_world.translation.copy()
        elif obj:
            center_3d = obj.matrix_world.translation.copy()

        if center_3d is not None:
            pt = location_3d_to_region_2d(region, rv3d, center_3d)
            if pt is not None:
                return int(round(region.x + pt[0])), int(round(region.y + pt[1])), center_3d

    elif area.type == 'NODE_EDITOR':
        space = area.spaces.active
        tree = getattr(space, "edit_tree", None)
        if tree and tree.nodes:
            sel_nodes = [n for n in tree.nodes if n.select]
            if sel_nodes:
                sysprefs = context.preferences.system
                fac = sysprefs.dpi * sysprefs.pixel_size / 72.0
                pts = []
                for n in sel_nodes:
                    x, y = _node_abs_loc(n)
                    vx = x * fac + n.dimensions.x / 2.0
                    vy = y * fac - 12.0
                    pt = region.view2d.view_to_region(vx, vy, clip=False)
                    if pt:
                        pts.append(pt)
                if pts:
                    avg_x = sum(p[0] for p in pts) / len(pts)
                    avg_y = sum(p[1] for p in pts) / len(pts)
                    return int(round(region.x + avg_x)), int(round(region.y + avg_y)), None

    return None


def window_region(area):
    for r in area.regions:
        if r.type == 'WINDOW':
            return r
    return None


def focusable_areas(win):
    return [a for a in win.screen.areas if a.type not in {'TOPBAR', 'STATUSBAR'}]


def area_at(win, mx, my):
    if not win or not getattr(win, 'screen', None):
        return None
    for a in win.screen.areas:
        if a.type in {'TOPBAR', 'STATUSBAR'}:
            continue
        if a.x <= mx < a.x + a.width and a.y <= my < a.y + a.height:
            return a
    return None


def area_by_ptr(win, ptr):
    if not ptr:
        return None
    for a in win.screen.areas:
        if a.as_pointer() == ptr:
            return a
    return None


def tag_all():
    wm = bpy.context.window_manager
    for w in wm.windows:
        for a in w.screen.areas:
            a.tag_redraw()


def ui_scale():
    return bpy.context.preferences.system.ui_scale


def make_labels(n):
    """
    Vimium-style 100% unique and prefix-free label generator.
    Labels start at 2 letters and scale up to 5 letters based on count.
    """
    if n <= 0:
        return []

    alpha = ALPHABET
    b = len(alpha)

    if n <= 20:
        eff_b = min(b, max(5, int(math.ceil(math.sqrt(n))) + 1))
    elif n <= 160:
        eff_b = min(b, 10)
    elif n <= 600:
        eff_b = min(b, 14)
    else:
        eff_b = b

    use_alpha = alpha[:eff_b]
    q = deque(a + c for a in use_alpha for c in use_alpha)

    while len(q) < n:
        prefix = q.popleft()
        if len(prefix) >= MAX_LABEL_LEN:
            q.append(prefix)
            for ch in alpha:
                if len(q) >= n:
                    break
                q.append(ch + prefix[:MAX_LABEL_LEN - 1])
            break
        for ch in use_alpha:
            q.append(prefix + ch)
            if len(q) >= n:
                break

    res = list(q)[:n]

    if len(set(res)) < len(res):
        seen = set()
        fixed = []
        for lbl in res:
            while lbl in seen:
                lbl = lbl + alpha[len(seen) % b]
            seen.add(lbl)
            fixed.append(lbl)
        res = fixed

    return res


def transform_active(win):
    if not win:
        return False
    try:
        ops = win.modal_operators
    except (AttributeError, Exception):
        return False
    for op in ops:
        op_id = (getattr(op, "bl_idname", "") or getattr(op, "name", "") or getattr(getattr(op, "bl_rna", None), "identifier", "")).lower()
        if any(k in op_id for k in TRANSFORM_KEYWORDS):
            return True
    return False


_X11_LIB = None
_XTST_LIB = None
_X11_DISPLAY = None
_FAKE_INPUT_WARNED = False


def _warn_fake_input_once(reason):
    global _FAKE_INPUT_WARNED
    if not _FAKE_INPUT_WARNED:
        _FAKE_INPUT_WARNED = True
        print(f"[Vimender] Fake key injection unavailable ({reason}); "
              f"popup-closing helpers are disabled on this system.")


def _get_x11():
    """Load libX11/libXtst once. Returns (False, False) on non-Linux platforms."""
    global _X11_LIB, _XTST_LIB
    if not sys.platform.startswith('linux'):
        return False, False
    if _X11_LIB is None:
        try:
            _X11_LIB = ctypes.cdll.LoadLibrary('libX11.so.6')
            _X11_LIB.XOpenDisplay.restype = ctypes.c_void_p
            _X11_LIB.XKeysymToKeycode.restype = ctypes.c_uint
            _X11_LIB.XStringToKeysym.restype = ctypes.c_ulong
        except Exception:
            _X11_LIB = False
    if _XTST_LIB is None:
        try:
            _XTST_LIB = ctypes.cdll.LoadLibrary('libXtst.so.6')
        except Exception:
            _XTST_LIB = False
    return _X11_LIB, _XTST_LIB


def _get_display():
    """One cached X11 Display* for the whole session (opened lazily)."""
    global _X11_DISPLAY
    if _X11_DISPLAY:
        return _X11_DISPLAY
    x11, _ = _get_x11()
    if not x11:
        return None
    try:
        disp = x11.XOpenDisplay(None)
    except Exception:
        disp = None
    if disp:
        _X11_DISPLAY = ctypes.c_void_p(disp)
    return _X11_DISPLAY


def close_x11_display():
    """Close the cached display. Call on addon unregister."""
    global _X11_DISPLAY
    if _X11_DISPLAY and _X11_LIB:
        try:
            _X11_LIB.XCloseDisplay(_X11_DISPLAY)
        except Exception:
            pass
    _X11_DISPLAY = None


def _fake_keysym(sym):
    """Press+release a keysym through XTest. Returns True on success."""
    x11, xtst = _get_x11()
    if not x11 or not xtst:
        _warn_fake_input_once("libX11/libXtst missing or non-Linux platform")
        return False
    d = _get_display()
    if d is None:
        _warn_fake_input_once("cannot open X display")
        return False
    try:
        kc = x11.XKeysymToKeycode(d, ctypes.c_ulong(sym))
        if not kc:
            return False
        xtst.XTestFakeKeyEvent(d, ctypes.c_uint(kc), True, 0)
        xtst.XTestFakeKeyEvent(d, ctypes.c_uint(kc), False, 0)
        x11.XFlush(d)
        return True
    except Exception:
        close_x11_display()
        return False


def send_modal_plus_minus(is_up):
    _fake_keysym(0xff55 if is_up else 0xff56)


def send_fake_escape():
    _fake_keysym(0xff1b)


def send_fake_numpad(num):
    _fake_keysym(0xffb0 + int(num))


def send_fake_key(key_str):
    """Send a real key press/release via X11 XTest (Linux/X11 only)."""
    special = {
        'RETURN': 0xff0d, 'ENTER': 0xff0d,
        'ESCAPE': 0xff1b, 'ESC': 0xff1b,
        'TAB': 0xff09, 'SPACE': 0x0020,
        'BACKSPACE': 0xff08, 'DELETE': 0xffff,
    }
    upper_k = key_str.upper()
    if upper_k in special:
        sym = special[upper_k]
    else:
        x11, _ = _get_x11()
        sym = 0
        if x11:
            try:
                sym = x11.XStringToKeysym(key_str.lower().encode('latin1'))
            except Exception:
                sym = 0
        if not sym and key_str:
            sym = ord(key_str.lower()[0])
    return _fake_keysym(sym) if sym else False


_ALTGR_KEYCODES = None


def _get_altgr_keycodes():
    global _ALTGR_KEYCODES
    if _ALTGR_KEYCODES is not None:
        return _ALTGR_KEYCODES
    kcs = [92, 108]
    x11, _ = _get_x11()
    d = _get_display() if x11 else None
    if d is not None:
        try:
            for sym in (0xfe03, 0xff7e, 0xffea):
                kc = x11.XKeysymToKeycode(d, ctypes.c_ulong(sym))
                if kc and kc not in kcs:
                    kcs.append(kc)
        except Exception:
            pass
    _ALTGR_KEYCODES = tuple(kcs)
    return _ALTGR_KEYCODES


def is_altgr_pressed(event=None):
    if event is not None and getattr(event, 'alt', False) and getattr(event, 'ctrl', False):
        return True
    if getattr(S, 'right_alt_down', False):
        return True
    x11, _ = _get_x11()
    d = _get_display() if x11 else None
    if d is not None:
        try:
            buf = (ctypes.c_char * 32)()
            x11.XQueryKeymap(d, buf)
            raw = buf.raw
            for kc in _get_altgr_keycodes():
                if (raw[kc >> 3] >> (kc & 7)) & 1:
                    return True
        except Exception:
            close_x11_display()
    return False


def is_view_pie_trigger(event):
    if not event:
        return False
    if event.ctrl or event.alt or event.oskey:
        return False
    if getattr(event, 'unicode', '') in {'"', 'é', '`', '~'}:
        return True
    if getattr(event, 'type', '') in {'ACCENT_GRAVE', 'TILDE'}:
        return True
    return False


def active_modal_tool(win):
    if not win:
        return None
    try:
        ops = win.modal_operators
    except (AttributeError, Exception):
        return None
    for op in ops:
        ident = (getattr(op, "bl_idname", "") or getattr(op, "name", "") or getattr(getattr(op, "bl_rna", None), "identifier", "")).lower()
        if ident and not ident.startswith("wm_ot_vim_") and not ident.startswith("wm.vim_") and "vim" not in ident:
            return op
    return None


def is_blender_modal_active(win):
    return active_modal_tool(win) is not None


def adjust_active_modal_or_modifier(context, is_up):
    step = 1 if is_up else -1
    try:
        ts = context.scene.tool_settings
        is_prop = (
            getattr(ts, 'use_proportional_edit', False) or
            getattr(ts, 'use_proportional_edit_objects', False)
        )
        if is_prop:
            factor = 1.15 if is_up else (1.0 / 1.15)
            ts.proportional_size = max(min(ts.proportional_size * factor, 10000.0), 0.001)
    except Exception:
        pass

    try:
        obj = context.active_object
        if obj and getattr(obj, 'modifiers', None):
            act_mod = getattr(obj.modifiers, 'active', None) or (obj.modifiers[-1] if len(obj.modifiers) > 0 else None)
            if act_mod:
                mtype = act_mod.type
                if mtype == 'SUBSURF':
                    act_mod.levels = max(0, min(act_mod.levels + step, 6))
                elif mtype == 'BEVEL':
                    act_mod.segments = max(1, min(act_mod.segments + step, 100))
                elif mtype == 'ARRAY':
                    act_mod.count = max(1, act_mod.count + step)
                elif mtype == 'MULTIRES':
                    act_mod.levels = max(0, min(act_mod.levels + step, 6))
                elif mtype == 'SOLIDIFY':
                    act_mod.thickness = max(0.0001, act_mod.thickness + (0.01 if is_up else -0.01))
                elif mtype == 'DECIMATE' and hasattr(act_mod, 'iterations'):
                    act_mod.iterations = max(0, act_mod.iterations + step)
                elif mtype == 'SCREW':
                    act_mod.steps = max(1, act_mod.steps + step)
                elif mtype == 'REMESH' and hasattr(act_mod, 'octree_depth'):
                    act_mod.octree_depth = max(1, min(act_mod.octree_depth + step, 12))
    except Exception:
        pass


def get_active_transform_type(win):
    try:
        ops = win.modal_operators if win else ()
    except (AttributeError, Exception):
        return getattr(S, 'last_transform_type', 'TRANSLATE')
    for op in ops:
        name = (getattr(op, "bl_idname", "") or getattr(op, "name", "") or getattr(getattr(op, "bl_rna", None), "identifier", "")).lower()
        if "rotate" in name or "trackball" in name:
            return 'ROTATE'
        if "resize" in name or "shrink_fatten" in name or "push_pull" in name or "scale" in name:
            return 'RESIZE'
        if any(k in name for k in TRANSFORM_KEYWORDS):
            return 'TRANSLATE'
    return getattr(S, 'last_transform_type', 'TRANSLATE')


def change_sens(delta):
    S.sens_idx = min(max(S.sens_idx + delta, 0), len(SENS_LEVELS) - 1)
    a = area_by_ptr(bpy.context.window, S.hover_ptr)
    if a:
        a.tag_redraw()


def release_all_held():
    """Drop every 'held key' state. Used when the window loses focus so that
    a missed RELEASE event can't leave the camera/cursor drifting forever."""
    for name in ('mover', 'smooth_cursor', 'smooth_walker', 'nav', 'smooth_box'):
        obj = getattr(S, name, None)
        if obj is not None:
            try:
                obj.reset()
            except Exception:
                pass


def _exec_view3d_op(context, win, target_area, op_func, **kwargs):
    if not target_area or target_area.type != 'VIEW_3D':
        target_area = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None)
    if target_area and target_area.type == 'VIEW_3D':
        region = window_region(target_area)
        if region:
            space = target_area.spaces.active if target_area.spaces else None
            try:
                with context.temp_override(window=win, area=target_area, region=region, space_data=space):
                    res = op_func(**kwargs)
                    if op_func == bpy.ops.view3d.view_orbit and res != {'FINISHED'}:
                        rv3d = space.region_3d if space else None
                        if rv3d:
                            rv3d.view_rotation = rv3d.view_rotation @ Quaternion((0.0, 1.0, 0.0), math.pi)
            except Exception:
                if op_func == bpy.ops.view3d.view_orbit:
                    rv3d = getattr(target_area.spaces.active, 'region_3d', None) if target_area.spaces else None
                    if rv3d:
                        rv3d.view_rotation = rv3d.view_rotation @ Quaternion((0.0, 1.0, 0.0), math.pi)
                elif op_func == bpy.ops.view3d.view_persportho:
                    rv3d = getattr(target_area.spaces.active, 'region_3d', None) if target_area.spaces else None
                    if rv3d:
                        rv3d.view_perspective = 'ORTHO' if rv3d.view_perspective == 'PERSP' else 'PERSP'
            target_area.tag_redraw()
            return True
    return False


def get_config_path():
    try:
        cfg_dir = bpy.utils.user_resource('CONFIG')
    except Exception:
        cfg_dir = os.path.expanduser('~/.config/blender')
    os.makedirs(cfg_dir, exist_ok=True)
    return os.path.join(cfg_dir, 'vimender_config.json')


def save_vimender_config(data: dict):
    try:
        p = get_config_path()
        existing = {}
        if os.path.exists(p) and os.path.getsize(p) > 0:
            with open(p, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    existing = json.loads(content)
        existing.update(data)
        tmp_p = p + '.tmp'
        with open(tmp_p, 'w', encoding='utf-8') as f:
            json.dump(existing, f, indent=2)
        os.replace(tmp_p, p)
    except Exception as e:
        print(f"[Vimender] Config save error: {e}")


def load_vimender_config() -> dict:
    try:
        p = get_config_path()
        if os.path.exists(p) and os.path.getsize(p) > 0:
            with open(p, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
    except Exception as e:
        print(f"[Vimender] Config read error: {e}")
    return {}


def save_vimender_state(enabled: bool):
    save_vimender_config({'auto_start': bool(enabled)})


def load_vimender_state() -> bool:
    return bool(load_vimender_config().get('auto_start', False))


def save_arrow_keys_layout(enabled: bool):
    save_vimender_config({'arrow_keys_layout': bool(enabled)})


def load_arrow_keys_layout() -> bool:
    return bool(load_vimender_config().get('arrow_keys_layout', False))


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


def is_edit_mode(context):
    mode = getattr(context, 'mode', '')
    if mode.startswith('EDIT'):
        return True
    obj = getattr(context, 'active_object', None)
    if obj and getattr(obj, 'mode', '') == 'EDIT':
        return True
    return False

def get_preferences(context=None):
    global _keymaps_initialized
    if context is None:
        context = bpy.context
    try:
        pkg = __package__ or __name__.partition('.')[0] or "Vimender"
        addon = context.preferences.addons.get(pkg)
        if not addon:
            addon = context.preferences.addons.get("Vimender")
        if not addon:
            for k, v in context.preferences.addons.items():
                if "vimender" in k.lower():
                    addon = v
                    break
        if addon:
            prefs = addon.preferences
            if not _keymaps_initialized and prefs:
                _keymaps_initialized = True
                try:
                    from .preferences import load_keymaps_from_config, repair_corrupted_keymaps
                    load_keymaps_from_config(prefs)
                    repair_corrupted_keymaps(prefs)
                except Exception:
                    pass
            return prefs
    except Exception:
        pass
    return None


def check_shortcut(prefs, prefix, event, fallback_key, fallback_ctrl=False, fallback_shift=False, fallback_alt=False, allow_shift_alt_extend=False):
    target_key = fallback_key
    target_ctrl = fallback_ctrl
    target_shift = fallback_shift
    target_alt = fallback_alt

    if prefs is not None:
        pk = getattr(prefs, f"key_{prefix}", fallback_key)
        if pk and pk != 'NONE':
            target_key = pk
            target_ctrl = getattr(prefs, f"ctrl_{prefix}", fallback_ctrl)
            target_shift = getattr(prefs, f"shift_{prefix}", fallback_shift)
            target_alt = getattr(prefs, f"alt_{prefix}", fallback_alt)
        elif pk == 'NONE':
            return False

    if getattr(event, 'oskey', False):
        return False

    matches_ctrl = (bool(event.ctrl) == bool(target_ctrl))
    if not matches_ctrl:
        return False

    is_altgr = is_altgr_pressed(event)
    if is_altgr and target_alt:
        matches_alt = True
    else:
        matches_alt = (bool(event.alt) == bool(target_alt))
    if not matches_alt:
        return False

    is_less = target_key in {'GRLESS', '<'}
    uni = getattr(event, 'unicode', '')
    if is_less and target_shift and uni == '>':
        matches_shift = True
    elif not allow_shift_alt_extend:
        matches_shift = (bool(event.shift) == bool(target_shift))
    else:
        matches_shift = not (target_shift and not (event.shift or uni == '>'))
    if not matches_shift:
        return False

    if is_less:
        if event.type == 'GRLESS' or uni in {'<', '>', '|'}:
            return True

    if event.type == target_key:
        return True

    if len(target_key) == 1 and target_key.isalpha():
        if (event.unicode or "").upper() == target_key:
            return True

    if target_key == fallback_key:
        if fallback_key in {'GRLESS', '<'}:
            if event.type == 'GRLESS' or uni in {'<', '>', '|'}:
                return True
        if event.type == fallback_key:
            return True
        if len(fallback_key) == 1 and fallback_key.isalpha():
            if (event.unicode or "").upper() == fallback_key:
                return True

    return False


def set_active_mode(mode_name, context=None, report_fn=None, target_area=None):
    if context is None:
        context = bpy.context

    if S.walk_mode:
        S.walk_mode = False
        S.walk_ptr = 0
        if S.smooth_walker:
            S.smooth_walker.reset()

    if S.tablet_mode:
        S.tablet_mode = False
        S.tablet_ptr = 0
        S.tablet_in_transform = None
        S.tablet_transform_backup = None
        S.tablet_active_axis = None
        S.tablet_touch_start = None
        S.tablet_touch_dragged = False
        S.tablet_select_modifier = None
        if getattr(S, 'touch_nav', None):
            S.touch_nav.reset()
        try:
            if getattr(S, 'tablet_prev_emulate_3_button', None) is not None:
                context.preferences.inputs.use_mouse_emulate_3_button = S.tablet_prev_emulate_3_button
                S.tablet_prev_emulate_3_button = None
        except Exception:
            pass
        try:
            from .mode_tablet import disable_touchview_integration, _tracker, restore_tablet_view_settings
            disable_touchview_integration(context)
            restore_tablet_view_settings(context)
            _tracker.stop()
        except Exception:
            pass

    if S.cursor_mode:
        S.cursor_mode = False
        if S.smooth_cursor:
            S.smooth_cursor.reset()

    if S.navigation_mode or S.workspace_nav:
        S.navigation_mode = False
        S.workspace_nav = False

    if getattr(S, 'pen_mode', False) or getattr(S, 'pen_prev_ui_scale', None) is not None:
        S.pen_mode = False
        S.pen_ptr = 0
        S.pen_mmb_active = False
        S.pen_mmb_is_pan = False
        S.pen_mmb_area_ptr = 0
        if getattr(S, 'pen_nav', None):
            S.pen_nav.reset()
        try:
            from .mode_tablet import _tracker, _stylus_tracker
            if not S.tablet_mode:
                _tracker.stop()
                _stylus_tracker.stop()
        except Exception:
            pass
        if getattr(S, 'pen_prev_ui_scale', None) is not None:
            ctx = context or getattr(bpy, 'context', None)
            if ctx and hasattr(ctx, 'preferences'):
                try:
                    ctx.preferences.view.ui_scale = S.pen_prev_ui_scale
                except Exception:
                    pass
            S.pen_prev_ui_scale = None

    if S.mode:
        S.clear_hint()

    S.preview_mode = False

    if mode_name == 'WALK':
        S.walk_mode = True
        if target_area:
            S.walk_ptr = target_area.as_pointer()
        if S.smooth_walker:
            S.smooth_walker.reset()
        sens_val = SENS_LEVELS[S.sens_idx]
        if report_fn:
            report_fn({'INFO'}, f"Vimender: WALK MODE [{sens_val:.2f}x]")
        try:
            context.workspace.status_text_set(
                f"Vimender: WALK MODE [{sens_val:.2f}x] | WASD/Arrows: Move | HJKL: Look | N/M: Sens (+/-) | Q/E: Down/Up | Alt+Q/Esc: Exit"
            )
        except Exception:
            pass

    elif mode_name == 'TABLET':
        S.tablet_mode = True
        S.tablet_active_transform = 'G'
        S.tablet_in_transform = None
        S.tablet_transform_backup = None
        S.tablet_active_axis = None
        S.tablet_touch_start = None
        S.tablet_touch_dragged = False
        S.tablet_select_modifier = None
        S.tablet_select_tool = 'TWEAK'
        if target_area:
            S.tablet_ptr = target_area.as_pointer()
        if getattr(S, 'touch_nav', None):
            S.touch_nav.reset()
        try:
            prefs = get_preferences(context)
            cfg = load_vimender_config()
            use_stylus = bool(getattr(prefs, 'stylus_pen', False)) or bool(cfg.get('stylus_pen', False))
            if use_stylus:
                inputs = context.preferences.inputs
                if getattr(S, 'tablet_prev_emulate_3_button', None) is None:
                    S.tablet_prev_emulate_3_button = inputs.use_mouse_emulate_3_button
                inputs.use_mouse_emulate_3_button = True
        except Exception:
            pass
        try:
            from .mode_tablet import enable_touchview_integration, _tracker, apply_tablet_view_settings
            enable_touchview_integration(context)
            apply_tablet_view_settings(context, target_area)
            _tracker.start()
        except Exception:
            pass
        if report_fn:
            report_fn({'INFO'}, "Vimender: TABLET MODE")
        try:
            context.workspace.status_text_set(
                "Vimender: TABLET MODE | Tap: Select | Left GUI: G/R/S & X/Y/Z | 1-Finger: Orbit/Move | 2-Fingers: Pinch Zoom/Pan/Twist | 2-Tap: Undo | 3-Tap: Redo | Ctrl+W/Esc: Exit"
            )
        except Exception:
            pass


    elif mode_name == 'CURSOR':
        S.cursor_mode = True
        if S.smooth_cursor:
            S.smooth_cursor.reset()
        if report_fn:
            report_fn({'INFO'}, f"Vimender: CURSOR MODE - {S.cursor_plane}")
        try:
            context.workspace.status_text_set(f"Vimender: CURSOR MODE [{S.cursor_plane}] | HJKL: Move | TAB: Plane | 0: Origin | S: Snap | Esc: Exit")
        except Exception:
            pass

    elif mode_name == 'NAV':
        S.navigation_mode = True
        S.workspace_nav = True
        if target_area:
            S.hover_ptr = target_area.as_pointer()
        if report_fn:
            report_fn({'INFO'}, "Vimender: NAVIGATION MODE")
        try:
            context.workspace.status_text_set("Vimender: NAVIGATION MODE | HJKL: Move focus | Alt+HJKL: Workspace | Tab: Maximize | Esc: Exit")
        except Exception:
            pass

    elif mode_name == 'PEN':
        S.pen_mode = True
        if target_area:
            S.pen_ptr = target_area.as_pointer()
        if getattr(S, 'pen_nav', None) is None:
            try:
                from .mode_pen import _PenNavigator
                S.pen_nav = _PenNavigator()
            except Exception:
                pass
        else:
            S.pen_nav.reset()
        try:
            from .mode_tablet import _tracker, _stylus_tracker
            _tracker.start()
            _stylus_tracker.start()
        except Exception:
            pass

        ctx = context or getattr(bpy, 'context', None)
        if ctx and hasattr(ctx, 'preferences'):
            try:
                prefs = get_preferences(ctx)
                factor = float(getattr(prefs, 'pen_ui_scale', 1.25)) if prefs else 1.25
                cur_scale = float(ctx.preferences.view.ui_scale)
                if getattr(S, 'pen_prev_ui_scale', None) is None:
                    S.pen_prev_ui_scale = cur_scale
                target_scale = round(cur_scale * max(1.05, factor), 2)
                ctx.preferences.view.ui_scale = target_scale
            except Exception as e:
                print(f"[Vimender] pen_mode ui_scale boost error: {e}")

        if report_fn:
            report_fn({'INFO'}, "Vimender: PEN MODE")
        try:
            context.workspace.status_text_set("Vimender: PEN MODE | Pass-Through: Pen/Mouse | 2-Finger Pinch: Zoom | Ctrl+Alt+P: Exit")
        except Exception:
            pass

    elif mode_name in {'ORBIT_PAN', 'ORBIT', 'PAN'}:
        if mode_name == 'PAN':
            S.nav_mode = 'PAN'
        elif mode_name == 'ORBIT':
            S.nav_mode = 'ORBIT'
        S.preview_mode = False
        if report_fn:
            report_fn({'INFO'}, f"Vimender: {S.nav_mode} MODE")
        try:
            if S.nav_mode == 'PAN':
                context.workspace.status_text_set("Vimender: PAN MODE | HJKL: Pan (Shift: Fast) | N/M: Zoom | U: Orbit | F: Hint | C: Box | Esc/Ctrl+Y: Preview")
            else:
                context.workspace.status_text_set("Vimender: ORBIT MODE | HJKL: Orbit (Shift: Pan) | N/M: Zoom | P: Pan | F: Hint | C: Box | Esc/Ctrl+Y: Preview")
        except Exception:
            pass

    else:
        S.preview_mode = True
        win = getattr(context, 'window', None)
        if target_area:
            S.hover_ptr = target_area.as_pointer()
        elif win and getattr(win, 'screen', None):
            cur_a = area_by_ptr(win, S.hover_ptr)
            if not cur_a or cur_a.type not in {'VIEW_3D', 'IMAGE_EDITOR', 'NODE_EDITOR'}:
                v3d = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None)
                if v3d:
                    S.hover_ptr = v3d.as_pointer()
        if report_fn:
            report_fn({'INFO'}, "Vimender: PREVIEW MODE")
        try:
            context.workspace.status_text_set("Vimender: PREVIEW MODE | Ctrl+Y: Orbit/Pan")
        except Exception:
            pass

    tag_all()

