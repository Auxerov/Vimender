bl_info = {
    "name": "Vimender",
    "author": "Auxerov",
    "version": (1, 0, 0),
    "blender": (5, 0, 0),
    "location": "F3 > Vim Mode",
    "description": "Keyboard controller for Blender",
    "category": "Interface",
}

from collections import deque
import ctypes
import json
import math
import os
import string
import time

import blf
import bmesh
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector, Quaternion

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
    'SPACE', 'RET', 'NUMPAD_ENTER', 'TAB', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE',
    'SIX', 'SEVEN', 'EIGHT', 'NINE', 'ZERO',
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
    'transform', '_move', 'extrude', 'duplicate', 'bevel',
    'shrink_fatten', 'push_pull', 'translate', 'rotate', 'resize',
    'scale', 'tosphere', 'shear', 'bend', 'trackball', 'edge_slide', 'vert_slide'
)


class Hint:
    __slots__ = ("label", "x", "y", "ax", "ay", "payload", "cluster",
                 "color", "color_idx", "edges", "nbrs")

    def __init__(self, label, x, y, payload=None):
        self.label = label
        self.x = x
        self.y = y
        self.ax = x
        self.ay = y
        self.payload = payload
        self.cluster = False
        self.color = None
        self.color_idx = None
        self.edges = []
        self.nbrs = []


class _SmoothCursor:
    """Smooth accelerated cursor controller for transform modal."""
    def __init__(self):
        self.held = set()
        self.vx = 0.0
        self.vy = 0.0
        self.sub_x = 0.0
        self.sub_y = 0.0
        self.last_t = 0.0
        self.initialized = False

    def reset(self):
        self.held.clear()
        self.vx = 0.0
        self.vy = 0.0
        self.last_t = 0.0
        self.initialized = False

    def on_key(self, win, event, key, val):
        if val == 'PRESS':
            if not self.initialized or (not self.held and math.hypot(self.vx, self.vy) < 5.0):
                self.sub_x = float(event.mouse_x)
                self.sub_y = float(event.mouse_y)
                self.last_t = time.perf_counter()
                self.initialized = True
            self.held.add(key)
        elif val == 'RELEASE':
            self.held.discard(key)

    def tick(self, win, event):
        now = time.perf_counter()
        if not self.initialized:
            return False
        dt = min(max(now - self.last_t, 0.001), 0.05)
        self.last_t = now

        dx = sum(ALL_DIRS[k][0] for k in self.held if k in ALL_DIRS)
        dy = sum(ALL_DIRS[k][1] for k in self.held if k in ALL_DIRS)

        sens = SENS_LEVELS[S.sens_idx]
        max_spd = CURSOR_MAX_SPEED * sens

        if dx != 0 or dy != 0:
            mag = math.hypot(dx, dy)
            tx = (dx / mag) * max_spd
            ty = (dy / mag) * max_spd
            alpha = 1.0 - math.exp(-CURSOR_ACCEL_RATE * dt)
            self.vx += (tx - self.vx) * alpha
            self.vy += (ty - self.vy) * alpha
        else:
            decay = math.exp(-CURSOR_FRICTION * dt)
            self.vx *= decay
            self.vy *= decay
            if math.hypot(self.vx, self.vy) < 4.0:
                self.vx = 0.0
                self.vy = 0.0
                return False

        if math.hypot(event.mouse_x - self.sub_x, event.mouse_y - self.sub_y) > 45:
            self.sub_x = float(event.mouse_x)
            self.sub_y = float(event.mouse_y)

        self.sub_x += self.vx * dt
        self.sub_y += self.vy * dt

        area = area_at(win, event.mouse_x, event.mouse_y)
        if not area or area.type != 'VIEW_3D':
            area = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None) if (win and win.screen) else None
        reg = window_region(area) if area else None
        if reg:
            min_x, max_x = float(reg.x + 8.0), float(reg.x + reg.width - 8.0)
            min_y, max_y = float(reg.y + 8.0), float(reg.y + reg.height - 8.0)
        else:
            min_x, max_x = 8.0, float(max(win.width - 8.0, 20.0))
            min_y, max_y = 8.0, float(max(win.height - 8.0, 20.0))
        span_x = max(max_x - min_x, 20.0)
        span_y = max(max_y - min_y, 20.0)
        if self.sub_x > max_x:
            self.sub_x -= span_x
        elif self.sub_x < min_x:
            self.sub_x += span_x
        if self.sub_y > max_y:
            self.sub_y -= span_y
        elif self.sub_y < min_y:
            self.sub_y += span_y
        self.sub_x = max(min(self.sub_x, max_x), min_x)
        self.sub_y = max(min(self.sub_y, max_y), min_y)

        win.cursor_warp(int(round(self.sub_x)), int(round(self.sub_y)))
        return True


class _SmoothBox:
    """Smooth accelerated box selection controller."""
    def __init__(self):
        self.held = set()
        self.vx = 0.0
        self.vy = 0.0
        self.x = 0.0
        self.y = 0.0
        self.last_t = 0.0
        self.active = False

    def reset(self, start_x=0.0, start_y=0.0):
        self.held.clear()
        self.vx = 0.0
        self.vy = 0.0
        self.x = float(start_x)
        self.y = float(start_y)
        self.last_t = time.perf_counter()
        self.active = True

    def on_key(self, key, val):
        if val == 'PRESS':
            if not self.held and math.hypot(self.vx, self.vy) < 0.1:
                self.last_t = time.perf_counter()
            self.held.add(key)
        elif val == 'RELEASE':
            self.held.discard(key)

    def tick(self, region):
        if not self.active:
            return False
        now = time.perf_counter()
        dt = min(max(now - self.last_t, 0.001), 0.05)
        self.last_t = now

        dx = sum(ALL_DIRS[k][0] for k in self.held if k in ALL_DIRS)
        dy = sum(ALL_DIRS[k][1] for k in self.held if k in ALL_DIRS)

        sens = SENS_LEVELS[S.sens_idx]
        max_spd = CURSOR_MAX_SPEED * sens * 1.25

        if dx != 0 or dy != 0:
            mag = math.hypot(dx, dy)
            tx = (dx / mag) * max_spd
            ty = (dy / mag) * max_spd
            alpha = 1.0 - math.exp(-NAV_ACCEL_RATE * dt)
            self.vx += (tx - self.vx) * alpha
            self.vy += (ty - self.vy) * alpha
        else:
            decay = math.exp(-NAV_FRICTION * dt)
            self.vx *= decay
            self.vy *= decay
            if math.hypot(self.vx, self.vy) < 2.0:
                self.vx = 0.0
                self.vy = 0.0
                return False

        self.x = min(max(self.x + self.vx * dt, 0.0), float(region.width))
        self.y = min(max(self.y + self.vy * dt, 0.0), float(region.height))
        S.mouse = (self.x, self.y)
        return True


class _Smooth3DCursor:
    """
    Smooth 3D Cursor controller.
    Moves the 3D Cursor along X-Y or Z-Y plane using Numpad 8/2/4/6.
    Switches between X-Y and Z-Y planes using Numpad 5.
    """
    def __init__(self):
        self.held = set()
        self.shift = False
        self.vx = 0.0
        self.vy = 0.0
        self.last_t = 0.0

    def reset(self):
        self.held.clear()
        self.shift = False
        self.vx = 0.0
        self.vy = 0.0
        self.last_t = 0.0

    def on_key(self, key, val, shift=False):
        self.shift = shift
        if val == 'PRESS':
            if not self.held and math.hypot(self.vx, self.vy) < 1e-3:
                self.last_t = time.perf_counter()
            self.held.add(key)
        elif val == 'RELEASE':
            self.held.discard(key)

    def tick(self, context, win):
        if not S.cursor_mode:
            return False
        now = time.perf_counter()
        if self.last_t == 0.0:
            self.last_t = now
        dt = min(max(now - self.last_t, 0.001), 0.05)
        self.last_t = now

        dx = sum(ALL_DIRS[k][0] for k in self.held if k in ALL_DIRS)
        dy = sum(ALL_DIRS[k][1] for k in self.held if k in ALL_DIRS)

        if dx != 0 or dy != 0:
            mag = math.hypot(dx, dy)
            tx, ty = dx / mag, dy / mag
            alpha = 1.0 - math.exp(-NAV_ACCEL_RATE * dt)
            self.vx += (tx - self.vx) * alpha
            self.vy += (ty - self.vy) * alpha
        else:
            decay = math.exp(-NAV_FRICTION * dt)
            self.vx *= decay
            self.vy *= decay
            if math.hypot(self.vx, self.vy) < 0.015:
                self.vx = 0.0
                self.vy = 0.0
                return False

        sens = SENS_LEVELS[S.sens_idx]
        area = area_by_ptr(win, S.hover_ptr) or context.area
        dist_factor = 1.0
        if area and area.type == 'VIEW_3D':
            rv3d = area.spaces.active.region_3d if area.spaces else None
            if rv3d:
                dist_factor = max(0.25, min(rv3d.view_distance / 5.0, 4.0))

        speed = CURSOR_3D_SPEED * sens * dist_factor
        if self.shift:
            speed *= 0.15

        if S.cursor_plane == 'XY':
            dx_val = self.vx * speed * dt
            dy_val = self.vy * speed * dt
            dz_val = 0.0
        else:
            dx_val = 0.0
            dy_val = self.vx * speed * dt
            dz_val = self.vy * speed * dt

        delta = Vector((dx_val, dy_val, dz_val))
        try:
            context.scene.cursor.location = context.scene.cursor.location + delta
            for a in win.screen.areas:
                if a.type == 'VIEW_3D':
                    a.tag_redraw()
            return True
        except Exception:
            return False


class _SmoothNavigator:
    """
    Smooth camera orbit and pan navigation using Numpad 8/4/6/2.
    Smooth zoom using Numpad +/-.
    Toggle between ORBIT and PAN mode using Numpad 5.
    Toggle between Orthographic and Perspective using Numpad *.
    """
    def __init__(self):
        self.held = set()
        self.held_zoom = set()
        self.shift = False
        self.vx = 0.0
        self.vy = 0.0
        self.vz = 0.0
        self.last_t = 0.0

    def reset(self):
        self.held.clear()
        self.held_zoom.clear()
        self.vx = 0.0
        self.vy = 0.0
        self.vz = 0.0
        self.last_t = 0.0

    def on_key(self, key, val, shift=False):
        self.shift = shift
        if key in {'NUMPAD_PLUS', 'NUMPAD_MINUS', 'PLUS', 'MINUS', 'EQUAL', 'PAGE_UP', 'PAGE_DOWN'}:
            if val == 'PRESS':
                if not self.held_zoom and abs(self.vz) < 1e-3:
                    self.last_t = time.perf_counter()
                self.held_zoom.add(key)
            elif val == 'RELEASE':
                self.held_zoom.discard(key)
            return

        if val == 'PRESS':
            if not self.held and math.hypot(self.vx, self.vy) < 1e-3:
                self.last_t = time.perf_counter()
            self.held.add(key)
        elif val == 'RELEASE':
            self.held.discard(key)

    def tick(self, context, win):
        now = time.perf_counter()
        if self.last_t == 0.0:
            self.last_t = now
        dt = min(max(now - self.last_t, 0.001), 0.05)
        self.last_t = now

        dx = sum(ALL_DIRS[k][0] for k in self.held if k in ALL_DIRS)
        dy = sum(ALL_DIRS[k][1] for k in self.held if k in ALL_DIRS)

        if dx != 0 or dy != 0:
            mag = math.hypot(dx, dy)
            tx, ty = dx / mag, dy / mag
            alpha = 1.0 - math.exp(-NAV_ACCEL_RATE * dt)
            self.vx += (tx - self.vx) * alpha
            self.vy += (ty - self.vy) * alpha
        else:
            decay = math.exp(-NAV_FRICTION * dt)
            self.vx *= decay
            self.vy *= decay
            if math.hypot(self.vx, self.vy) < 0.015:
                self.vx = 0.0
                self.vy = 0.0

        dz = 0
        if any(k in self.held_zoom for k in ('NUMPAD_PLUS', 'PLUS', 'EQUAL', 'PAGE_UP')):
            dz += 1
        if any(k in self.held_zoom for k in ('NUMPAD_MINUS', 'MINUS', 'PAGE_DOWN')):
            dz -= 1

        if dz != 0:
            sens = SENS_LEVELS[S.sens_idx]
            target_vz = dz * ZOOM_MAX_SPEED * sens
            alpha = 1.0 - math.exp(-NAV_ACCEL_RATE * dt)
            self.vz += (target_vz - self.vz) * alpha
        else:
            decay = math.exp(-NAV_FRICTION * dt)
            self.vz *= decay
            if abs(self.vz) < 0.01:
                self.vz = 0.0

        if math.hypot(self.vx, self.vy) < 0.015 and abs(self.vz) < 0.005:
            return False

        area = area_by_ptr(win, S.hover_ptr)
        if area is None:
            return False

        sens = SENS_LEVELS[S.sens_idx]
        moved = False

        if area.type == 'VIEW_3D':
            rv3d = area.spaces.active.region_3d
            if rv3d is None:
                return False

            if rv3d.view_perspective == 'CAMERA' and (math.hypot(self.vx, self.vy) > 0.015 or abs(self.vz) > 0.01):
                rv3d.view_perspective = 'PERSP'

            is_pan = (S.nav_mode == 'PAN') or self.shift

            if is_pan and (self.vx != 0.0 or self.vy != 0.0):
                dist = max(rv3d.view_distance, 0.05)
                spd = PAN_MAX_SPEED * sens * dist
                right = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
                up = rv3d.view_rotation @ Vector((0.0, 1.0, 0.0))
                rv3d.view_location = rv3d.view_location + (right * (self.vx * spd * dt) + up * (self.vy * spd * dt))
                moved = True
            elif not is_pan and (self.vx != 0.0 or self.vy != 0.0):
                spd = ORBIT_MAX_SPEED * sens
                yaw = self.vx * spd * dt
                pitch = -self.vy * spd * dt
                q_yaw = Quaternion(Vector((0.0, 0.0, 1.0)), yaw)
                right_axis = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
                q_pitch = Quaternion(right_axis, pitch)
                rv3d.view_rotation = (q_yaw @ q_pitch @ rv3d.view_rotation).normalized()
                moved = True

            if abs(self.vz) > 0.001:
                zoom_factor = math.exp(-self.vz * dt)
                rv3d.view_distance = max(0.01, rv3d.view_distance * zoom_factor)
                moved = True

            if moved:
                area.tag_redraw()
            return moved

        region = window_region(area)
        if region is not None:
            px = int(round(self.vx * VIEW2D_PAN_SPEED * sens * dt))
            py = int(round(self.vy * VIEW2D_PAN_SPEED * sens * dt))
            if px != 0 or py != 0:
                try:
                    with context.temp_override(window=win, area=area, region=region):
                        bpy.ops.view2d.pan(deltax=px, deltay=py)
                    moved = True
                except Exception:
                    pass

            if abs(self.vz) > 0.001:
                zoom_delta = int(round(self.vz * 450.0 * dt))
                if zoom_delta != 0:
                    try:
                        with context.temp_override(window=win, area=area, region=region):
                            bpy.ops.view2d.zoom(deltax=zoom_delta, deltay=zoom_delta)
                        moved = True
                    except Exception:
                        pass

            if moved:
                area.tag_redraw()
            return moved

        return False


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
        self.mover = _SmoothCursor()
        self.smooth_box = _SmoothBox()
        self.nav = _SmoothNavigator()
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
        self.prev_mode_state = {}
        self.smooth_cursor = _Smooth3DCursor()
        self.preview_mode = True
        self.clear_hint()

    def clear_hint(self):
        self.mode = None
        self.kind = None
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
        self.mover.reset()
        self.smooth_box.reset()


S = _State()
_handlers = []
_shader = None
_addon_keymaps = []


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

        center_3d = None
        if mode == 'OBJECT':
            sel = [o for o in context.selected_objects if o.visible_get()]
            if sel:
                pts = []
                for o in sel:
                    try:
                        c = sum((Vector(v) for v in o.bound_box), Vector()) / 8.0
                        pts.append(o.matrix_world @ c)
                    except Exception:
                        pts.append(o.matrix_world.translation)
                if pts:
                    center_3d = sum(pts, Vector()) / len(pts)
            elif obj:
                center_3d = obj.matrix_world.translation
        elif mode == 'EDIT' and obj and obj.type == 'MESH':
            bm = bmesh.from_edit_mesh(obj.data)
            sel_cos = [v.co for v in bm.verts if v.select and not v.hide]
            if sel_cos:
                center_3d = obj.matrix_world @ (sum(sel_cos, Vector()) / len(sel_cos))
            else:
                center_3d = obj.matrix_world.translation
        elif mode == 'POSE' and obj and obj.type == 'ARMATURE':
            sel_bones = [b for b in obj.pose.bones if b.bone.select]
            if sel_bones:
                center_3d = obj.matrix_world @ (sum((b.head for b in sel_bones), Vector()) / len(sel_bones))
            else:
                center_3d = obj.matrix_world.translation
        elif obj:
            center_3d = obj.matrix_world.translation

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
    for a in win.screen.areas:
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


def neighbor_area(win, cur, direction):
    cx0, cy0, cx1, cy1 = cur.x, cur.y, cur.x + cur.width, cur.y + cur.height
    ccx, ccy = (cx0 + cx1) / 2, (cy0 + cy1) / 2
    cur_ptr = cur.as_pointer()
    best, best_key = None, None
    for a in focusable_areas(win):
        if a.as_pointer() == cur_ptr:
            continue
        x0, y0, x1, y1 = a.x, a.y, a.x + a.width, a.y + a.height
        if direction == 'RIGHT':
            gap, ov = x0 - cx1, min(cy1, y1) - max(cy0, y0)
            perp = abs((y0 + y1) / 2 - ccy)
        elif direction == 'LEFT':
            gap, ov = cx0 - x1, min(cy1, y1) - max(cy0, y0)
            perp = abs((y0 + y1) / 2 - ccy)
        elif direction == 'UP':
            gap, ov = y0 - cy1, min(cx1, x1) - max(cx0, x0)
            perp = abs((x0 + x1) / 2 - ccx)
        else:
            gap, ov = cy0 - y1, min(cx1, x1) - max(cx0, x0)
            perp = abs((x0 + x1) / 2 - ccx)
        if gap < -2:
            continue
        key = (0, gap, -ov) if ov > 0 else (1, gap, perp)
        if best_key is None or key < best_key:
            best, best_key = a, key
    return best


def get_ordered_workspaces():
    ws_list = list(bpy.data.workspaces)
    if len(ws_list) <= 1:
        return ws_list

    best_offset = None
    check_offsets = [500] + [o for o in range(300, 700, 4) if o != 500]
    for off in check_offsets:
        orders = []
        for w in ws_list:
            try:
                orders.append(ctypes.c_int32.from_address(w.as_pointer() + off).value)
            except Exception:
                break
        if len(orders) == len(ws_list):
            if all(0 <= v < 500 for v in orders) and len(set(orders)) >= len(orders) - 1:
                best_offset = off
                break

    if best_offset is not None:
        try:
            return sorted(ws_list, key=lambda w: (ctypes.c_int32.from_address(w.as_pointer() + best_offset).value, w.name))
        except Exception:
            pass

    return ws_list


def cycle_workspace(context, direction='NEXT'):
    win = context.window
    if not win:
        return None

    workspaces = get_ordered_workspaces()
    if not workspaces:
        return None

    now = time.perf_counter()
    cur_ws = win.workspace
    last_target = getattr(S, 'last_target_ws', None)
    last_time = getattr(S, 'last_ws_switch_time', 0.0)
    if last_target and (now - last_time < 0.8) and (last_target in workspaces):
        cur_ws = last_target

    try:
        idx = workspaces.index(cur_ws)
    except ValueError:
        idx = 0

    step = 1 if direction == 'NEXT' else -1
    target = workspaces[(idx + step) % len(workspaces)]
    try:
        win.workspace = target
        S.last_target_ws = target
        S.last_ws_switch_time = now
        if win.screen and win.screen.areas:
            first_area = win.screen.areas[0]
            S.hover_ptr = first_area.as_pointer()
            win.cursor_warp(int(first_area.x + first_area.width / 2), int(first_area.y + first_area.height / 2))
        tag_all()
        return target
    except Exception as e:
        print(f"[Vimender] Workspace switch error: {e}")
        return None


def navigate_in_editor(context, win, area, direction='DOWN'):
    if not area:
        area = area_by_ptr(win, S.hover_ptr) or (win.screen.areas[0] if win and win.screen and win.screen.areas else None)
    if not area:
        return
    atype = area.type
    r = window_region(area)

    if atype == 'OUTLINER':
        walk_ok = False
        if r:
            with context.temp_override(window=win, area=area, region=r):
                try:
                    res = bpy.ops.outliner.select_walk('INVOKE_DEFAULT', direction=direction)
                    if 'FINISHED' in res:
                        walk_ok = True
                except Exception:
                    pass
        if not walk_ok:
            objs = sorted([o for o in context.view_layer.objects if o.visible_get()], key=lambda o: o.name)
            if objs:
                act = context.view_layer.objects.active
                step = -1 if direction == 'UP' else 1
                if act in objs:
                    idx = objs.index(act)
                    new_act = objs[(idx + step) % len(objs)]
                else:
                    new_act = objs[0]
                for o in objs:
                    o.select_set(False)
                new_act.select_set(True)
                context.view_layer.objects.active = new_act
        area.tag_redraw()

    elif atype == 'PROPERTIES':
        cycled = False
        if r:
            with context.temp_override(window=win, area=area, region=r):
                try:
                    res = bpy.ops.screen.space_context_cycle(
                        'INVOKE_DEFAULT',
                        direction='NEXT' if direction == 'DOWN' else 'PREV'
                    )
                    if 'FINISHED' in res:
                        cycled = True
                except Exception:
                    pass
        if not cycled:
            space = getattr(area, 'spaces', None)
            space_act = space.active if space else None
            if space_act:
                tabs = [
                    'TOOL', 'RENDER', 'OUTPUT', 'VIEW_LAYER', 'SCENE', 'WORLD',
                    'COLLECTION', 'OBJECT', 'MODIFIER', 'CONSTRAINT', 'DATA',
                    'BONE', 'BONE_CONSTRAINT', 'MATERIAL', 'TEXTURE', 'PARTICLES', 'PHYSICS'
                ]
                cur = getattr(space_act, 'context', 'OBJECT')
                idx = tabs.index(cur) if cur in tabs else 0
                step = 1 if direction == 'DOWN' else -1
                new_tab = tabs[(idx + step) % len(tabs)]
                try:
                    space_act.context = new_tab
                except Exception:
                    pass
        area.tag_redraw()

    elif atype == 'VIEW_3D':
        objs = sorted([o for o in context.view_layer.objects if o.visible_get()], key=lambda o: o.name)
        if objs:
            act = context.view_layer.objects.active
            step = -1 if direction == 'UP' else 1
            if act in objs:
                idx = objs.index(act)
                new_act = objs[(idx + step) % len(objs)]
            else:
                new_act = objs[0]
            for o in objs:
                o.select_set(False)
            new_act.select_set(True)
            context.view_layer.objects.active = new_act
            area.tag_redraw()

    elif atype == 'NODE_EDITOR':
        space = getattr(area, 'spaces', None)
        space_act = space.active if space else None
        tree = getattr(space_act, "edit_tree", None) if space_act else None
        if tree and tree.nodes:
            nodes = sorted(tree.nodes, key=lambda n: (-n.location.y, n.location.x))
            act = tree.nodes.active
            step = -1 if direction == 'UP' else 1
            if act in nodes:
                idx = nodes.index(act)
                new_act = nodes[(idx + step) % len(nodes)]
            else:
                new_act = nodes[0]
            for n in nodes:
                n.select = False
            new_act.select = True
            tree.nodes.active = new_act
            area.tag_redraw()

    elif atype == 'FILE_BROWSER':
        if r:
            with context.temp_override(window=win, area=area, region=r):
                try:
                    bpy.ops.file.select_walk(direction=direction)
                except Exception:
                    pass
        area.tag_redraw()

    elif atype in {'DOPESHEET_EDITOR', 'GRAPH_EDITOR', 'NLA_EDITOR', 'SEQUENCE_EDITOR'}:
        step = -1 if direction == 'UP' else 1
        context.scene.frame_current += step
        area.tag_redraw()

    else:
        if r:
            delta = 60 if direction == 'UP' else -60
            try:
                with context.temp_override(window=win, area=area, region=r):
                    bpy.ops.view2d.pan(deltax=0, deltay=delta)
            except Exception:
                pass
        area.tag_redraw()



def _inside(pt, region):
    return pt is not None and 0 <= pt[0] <= region.width and 0 <= pt[1] <= region.height


def _collect_view3d(context, area, region):
    space = area.spaces.active
    rv3d = space.region_3d
    if rv3d is None:
        return None
    obj = context.view_layer.objects.active
    mode = obj.mode if obj else 'OBJECT'
    raw = []

    if mode == 'OBJECT':
        for o in context.view_layer.objects:
            if not o.visible_get():
                continue
            try:
                c = sum((Vector(v) for v in o.bound_box), Vector()) / 8.0
            except Exception:
                c = Vector()
            pt = location_3d_to_region_2d(region, rv3d, o.matrix_world @ c)
            if _inside(pt, region):
                raw.append((pt[0], pt[1], (o.name, o.type)))
        return 'OBJ', raw

    if mode == 'EDIT' and obj.type == 'MESH':
        shading = space.shading
        xray = shading.show_xray_wireframe if shading.type == 'WIREFRAME' else shading.show_xray
        inv = rv3d.view_matrix.inverted()
        eye = inv.translation
        toward = inv.to_3x3() @ Vector((0, 0, 1))
        view_forward = inv.to_3x3() @ Vector((0, 0, -1))
        vm, em, _fm = context.tool_settings.mesh_select_mode
        if not (vm or em or _fm):
            vm = True

        for o in context.view_layer.objects:
            if o.type != 'MESH' or o.mode != 'EDIT' or not o.visible_get():
                continue
            bm = bmesh.from_edit_mesh(o.data)
            mw = o.matrix_world
            m3 = mw.to_3x3()

            front_faces = set()
            if not xray:
                for f in bm.faces:
                    p_f = mw @ f.calc_center_median()
                    d_f = (eye - p_f) if rv3d.is_perspective else toward
                    if d_f.dot(m3 @ f.normal) > -1e-4:
                        front_faces.add(f.index)

            if vm:
                bm.verts.ensure_lookup_table()
                for v in bm.verts:
                    if v.hide:
                        continue
                    p = mw @ v.co
                    if rv3d.is_perspective and (p - eye).dot(view_forward) <= 0:
                        continue
                    if not xray and v.link_faces:
                        if not any(f.index in front_faces for f in v.link_faces):
                            continue
                    pt = location_3d_to_region_2d(region, rv3d, p)
                    if _inside(pt, region):
                        raw.append((pt[0], pt[1], (o.name, 'VERT', v.index)))

            if em:
                bm.edges.ensure_lookup_table()
                for e in bm.edges:
                    if e.hide:
                        continue
                    co = (e.verts[0].co + e.verts[1].co) * 0.5
                    p = mw @ co
                    if rv3d.is_perspective and (p - eye).dot(view_forward) <= 0:
                        continue
                    if not xray and e.link_faces:
                        if not any(f.index in front_faces for f in e.link_faces):
                            continue
                    pt = location_3d_to_region_2d(region, rv3d, p)
                    if _inside(pt, region):
                        raw.append((pt[0], pt[1], (o.name, 'EDGE', e.index)))

            if _fm:
                bm.faces.ensure_lookup_table()
                for f in bm.faces:
                    if f.hide:
                        continue
                    if not xray and f.index not in front_faces:
                        continue
                    p = mw @ f.calc_center_median()
                    if rv3d.is_perspective and (p - eye).dot(view_forward) <= 0:
                        continue
                    pt = location_3d_to_region_2d(region, rv3d, p)
                    if _inside(pt, region):
                        raw.append((pt[0], pt[1], (o.name, 'FACE', f.index)))

        return 'MESH', raw

    return None


def _node_abs_loc(n):
    x, y = n.location.x, n.location.y
    p = n.parent
    while p is not None:
        x += p.location.x
        y += p.location.y
        p = p.parent
    return x, y


def _collect_nodes(context, area, region):
    space = area.spaces.active
    tree = getattr(space, "edit_tree", None)
    if tree is None:
        return None
    sysprefs = context.preferences.system
    fac = sysprefs.dpi * sysprefs.pixel_size / 72.0
    raw = []
    for n in tree.nodes:
        x, y = _node_abs_loc(n)
        vx = x * fac + n.dimensions.x / 2.0
        vy = y * fac - 12.0
        pt = region.view2d.view_to_region(vx, vy, clip=False)
        if _inside(pt, region):
            raw.append((pt[0], pt[1], n.name))
    return 'NODE', raw


def _collect_outliner(context, area, region):
    ui = ui_scale()
    row_h = 22.0 * ui
    raw = []

    items = []
    def traverse_col(col, depth=0):
        items.append(('COLLECTION', col.name, depth))
        for child in col.children:
            traverse_col(child, depth + 1)
        for obj in col.objects:
            items.append(('OBJECT', obj.name, depth + 1))

    vl = context.view_layer
    traverse_col(vl.layer_collection.collection, 0)

    v2d = getattr(region, 'view2d', None)
    for i, (kind, name, depth) in enumerate(items):
        vx = 32.0 * ui + depth * 16.0 * ui
        vy = - (i + 0.5) * row_h
        if v2d:
            pt = v2d.view_to_region(vx, vy, clip=False)
        else:
            pt = (vx, region.height - (i + 0.5) * row_h)
        if _inside(pt, region):
            raw.append((pt[0], pt[1], (kind, name)))

    return 'OUTLINER', raw


def _collect_properties(context, area, region):
    space = area.spaces.active
    ui = ui_scale()
    cur = space.context
    valid_tabs = []
    for item in space.bl_rna.properties['context'].enum_items:
        try:
            space.context = item.identifier
            valid_tabs.append((item.identifier, item.name))
        except Exception:
            pass
    space.context = cur

    if not valid_tabs:
        return None

    raw = []
    tab_h = 28.0 * ui
    start_y = region.height - tab_h * 0.75
    for i, (tab_id, _tab_name) in enumerate(valid_tabs):
        y = start_y - i * tab_h
        if y < 10.0:
            break
        x = 24.0 * ui
        raw.append((x, y, tab_id))

    return 'PROPERTIES', raw


def _collect_grid(region):
    w, h = region.width, region.height
    cell = max(60, int(math.sqrt(max(w * h, 1) / 500.0)))
    cols = max(1, w // cell)
    rows = max(1, h // cell)
    cw, ch = w / cols, h / rows
    raw = []
    for r in range(rows):
        for c in range(cols):
            raw.append(((c + 0.5) * cw, (r + 0.5) * ch, None))
    return 'GRID', raw


def collect(context, area, region):
    res = None
    if area.type == 'VIEW_3D':
        res = _collect_view3d(context, area, region)
    elif area.type == 'NODE_EDITOR':
        res = _collect_nodes(context, area, region)
    elif area.type == 'OUTLINER':
        res = _collect_outliner(context, area, region)
    elif area.type == 'PROPERTIES':
        res = _collect_properties(context, area, region)
    if res is None:
        res = _collect_grid(region)
    return res


def declutter(hints, region):
    """Spreads overlapping hint labels apart and marks clusters."""
    n = len(hints)
    if n < 2:
        return
    ui = ui_scale()
    L = max(len(h.label) for h in hints)
    W = L * 9.0 * ui + 10 * ui
    H = 22 * ui
    M = 8 * ui
    cw, ch = W + M, H + M
    max_disp = 95 * ui

    for it in range(16):
        grid = {}
        for i, h in enumerate(hints):
            grid.setdefault((int(h.x // cw), int(h.y // ch)), []).append(i)
        moved = False
        for i, h in enumerate(hints):
            gx, gy = int(h.x // cw), int(h.y // ch)
            for ax in (gx - 1, gx, gx + 1):
                for ay in (gy - 1, gy, gy + 1):
                    for j in grid.get((ax, ay), ()):
                        if j <= i:
                            continue
                        g = hints[j]
                        dx, dy = g.x - h.x, g.y - h.y
                        ox, oy = cw - abs(dx), ch - abs(dy)
                        if ox <= 0 or oy <= 0:
                            continue
                        if it == 0:
                            h.cluster = g.cluster = True
                            h.nbrs.append(j)
                            g.nbrs.append(i)
                        if oy <= ox:
                            s = 1 if dy > 0 else (-1 if dy < 0 else (1 if (i + j) % 2 == 0 else -1))
                            sh = oy / 2
                            h.y -= s * sh
                            g.y += s * sh
                        else:
                            s = 1 if dx > 0 else (-1 if dx < 0 else (1 if (i + j) % 2 == 0 else -1))
                            sh = ox / 2
                            h.x -= s * sh
                            g.x += s * sh
                        moved = True
        for h in hints:
            dx, dy = h.x - h.ax, h.y - h.ay
            d = math.hypot(dx, dy)
            if d > max_disp:
                h.x = h.ax + dx * max_disp / d
                h.y = h.ay + dy * max_disp / d
            h.x = min(max(h.x, W / 2), max(region.width - W / 2, W / 2))
            h.y = min(max(h.y, H / 2), max(region.height - H / 2, H / 2))
        if not moved:
            break


def assign_cluster_colors(hints):
    for h in hints:
        if not h.cluster:
            continue
        used = {hints[j].color_idx for j in h.nbrs if hints[j].color_idx is not None}
        k = 0
        while k in used and k < len(PALETTE) - 1:
            k += 1
        h.color_idx = k
        h.color = PALETTE[k]


def attach_vertex_edges(context, area, region, hints):
    """Calculates screen segments of connected edges for overlapping vertex hints."""
    rv3d = area.spaces.active.region_3d
    cache = {}
    for h in hints:
        if not h.cluster or not h.payload:
            continue
        if len(h.payload) == 3:
            name, elem_type, idx = h.payload
            if elem_type != 'VERT':
                continue
        else:
            name, idx = h.payload
        o = bpy.data.objects.get(name)
        if o is None:
            continue
        bm = cache.get(name)
        if bm is None:
            bm = bmesh.from_edit_mesh(o.data)
            bm.verts.ensure_lookup_table()
            cache[name] = bm
        try:
            v = bm.verts[idx]
        except IndexError:
            continue
        mw = o.matrix_world
        segs = []
        for e in v.link_edges:
            if e.hide:
                continue
            pt = location_3d_to_region_2d(region, rv3d, mw @ e.other_vert(v).co)
            if pt is not None:
                segs.append((h.ax, h.ay, pt[0], pt[1]))
        h.edges = segs


def apply_single(context, win, area, region, hint, action):
    kind = S.kind
    extend = (action == 'EXTEND')
    is_alt = (action == 'ALT')

    if kind == 'OBJ':
        obj = bpy.data.objects.get(hint.payload[0])
        if obj is None:
            return
        vl = context.view_layer
        if extend:
            if obj.select_get() and vl.objects.active == obj:
                obj.select_set(False)
            else:
                obj.select_set(True)
                vl.objects.active = obj
        elif is_alt:
            with context.temp_override(window=win, area=area, region=region):
                try:
                    bpy.ops.view3d.select(extend=False, deselect_all=True, enumerate=True,
                                          location=(int(hint.ax), int(hint.ay)))
                except Exception:
                    for o in vl.objects:
                        o.select_set(False)
                    obj.select_set(True)
                    vl.objects.active = obj
        else:
            for o in vl.objects:
                o.select_set(False)
            obj.select_set(True)
            vl.objects.active = obj

    elif kind == 'MESH':
        loc = (int(hint.ax), int(hint.ay))
        applied_direct = False
        if not is_alt and hint.payload and len(hint.payload) >= 2:
            if len(hint.payload) == 3:
                obj_name, elem_type, idx = hint.payload
            else:
                obj_name, idx = hint.payload
                vm, em, fm = context.tool_settings.mesh_select_mode
                elem_type = 'VERT' if vm else ('EDGE' if em else 'FACE')
            o = bpy.data.objects.get(obj_name)
            if o and o.mode == 'EDIT' and o.type == 'MESH':
                try:
                    bm = bmesh.from_edit_mesh(o.data)
                    elem = None
                    if elem_type == 'VERT':
                        bm.verts.ensure_lookup_table()
                        if 0 <= idx < len(bm.verts):
                            elem = bm.verts[idx]
                    elif elem_type == 'EDGE':
                        bm.edges.ensure_lookup_table()
                        if 0 <= idx < len(bm.edges):
                            elem = bm.edges[idx]
                    elif elem_type == 'FACE':
                        bm.faces.ensure_lookup_table()
                        if 0 <= idx < len(bm.faces):
                            elem = bm.faces[idx]

                    if elem is not None:
                        if extend:
                            elem.select = not elem.select
                        else:
                            for v in bm.verts:
                                v.select = False
                            for e in bm.edges:
                                e.select = False
                            for f in bm.faces:
                                f.select = False
                            elem.select = True
                        bm.select_flush_mode()
                        bmesh.update_edit_mesh(o.data)
                        applied_direct = True
                except Exception:
                    pass

        if not applied_direct:
            with context.temp_override(window=win, area=area, region=region):
                if is_alt:
                    try:
                        bpy.ops.mesh.loop_select(extend=False, deselect=False, toggle=False, location=loc)
                    except Exception:
                        bpy.ops.view3d.select(extend=False, deselect_all=True, location=loc)
                elif extend:
                    bpy.ops.view3d.select(extend=False, deselect=False, toggle=True, location=loc)
                else:
                    bpy.ops.view3d.select(extend=False, deselect_all=True, location=loc)

    elif kind == 'NODE':
        tree = area.spaces.active.edit_tree
        node = tree.nodes.get(hint.payload)
        if node is None:
            return
        if extend:
            node.select = not node.select
            if node.select:
                tree.nodes.active = node
        else:
            for n in tree.nodes:
                n.select = False
            node.select = True
            tree.nodes.active = node

    elif kind == 'OUTLINER':
        item_kind, item_name = hint.payload
        if item_kind == 'OBJECT':
            obj = bpy.data.objects.get(item_name)
            if obj:
                vl = context.view_layer
                if extend:
                    obj.select_set(not obj.select_get())
                else:
                    for o in vl.objects:
                        o.select_set(False)
                    obj.select_set(True)
                    vl.objects.active = obj
        elif item_kind == 'COLLECTION':
            col = bpy.data.collections.get(item_name)
            if col:
                def find_lc(lc):
                    if lc.collection == col:
                        return lc
                    for child in lc.children:
                        res = find_lc(child)
                        if res:
                            return res
                    return None
                target_lc = find_lc(context.view_layer.layer_collection)
                if target_lc:
                    context.view_layer.active_layer_collection = target_lc
                if extend:
                    for o in col.objects:
                        o.select_set(True)
                else:
                    for o in context.view_layer.objects:
                        o.select_set(False)
                    for o in col.objects:
                        o.select_set(True)
                    if col.objects:
                        context.view_layer.objects.active = col.objects[0]
        win.cursor_warp(region.x + int(hint.x), region.y + int(hint.y))

    elif kind == 'PROPERTIES':
        tab_id = hint.payload
        try:
            area.spaces.active.context = tab_id
        except Exception:
            pass
        win.cursor_warp(region.x + int(hint.x), region.y + int(hint.y))

    elif kind == 'GRID':
        gx, gy = region.x + int(hint.x), region.y + int(hint.y)
        win.cursor_warp(gx, gy)

    area.tag_redraw()


def apply_box(context, win, area, region, a, b, extend):
    ui = ui_scale()
    pad = int(max(14.0 * ui, 14.0))
    xmin = int(min(a[0], b[0])) - pad
    xmax = int(max(a[0], b[0])) + pad
    ymin = int(min(a[1], b[1])) - pad
    ymax = int(max(a[1], b[1])) + pad
    if region:
        xmin = max(0, min(xmin, int(region.width)))
        xmax = max(0, min(xmax, int(region.width)))
        ymin = max(0, min(ymin, int(region.height)))
        ymax = max(0, min(ymax, int(region.height)))

    mode = 'ADD' if extend else 'SET'

    if S.kind == 'MESH' and area.type == 'VIEW_3D':
        obj = context.active_object
        if obj and obj.mode == 'EDIT' and obj.type == 'MESH':
            try:
                bm = bmesh.from_edit_mesh(obj.data)
                vm, em, fm = context.tool_settings.mesh_select_mode
                if not extend:
                    for v in bm.verts:
                        v.select = False
                    for e in bm.edges:
                        e.select = False
                    for f in bm.faces:
                        f.select = False

                start_h = getattr(S, 'box_start_hint', None)
                if start_h and start_h.payload:
                    idx = start_h.payload[-1]
                    if vm:
                        bm.verts.ensure_lookup_table()
                        if 0 <= idx < len(bm.verts):
                            bm.verts[idx].select = True
                    elif em:
                        bm.edges.ensure_lookup_table()
                        if 0 <= idx < len(bm.edges):
                            bm.edges[idx].select = True
                    elif fm:
                        bm.faces.ensure_lookup_table()
                        if 0 <= idx < len(bm.faces):
                            bm.faces[idx].select = True

                end_h = getattr(S, 'box_end_hint', None)
                if end_h and end_h.payload:
                    idx = end_h.payload[-1]
                    if vm:
                        bm.verts.ensure_lookup_table()
                        if 0 <= idx < len(bm.verts):
                            bm.verts[idx].select = True
                    elif em:
                        bm.edges.ensure_lookup_table()
                        if 0 <= idx < len(bm.edges):
                            bm.edges[idx].select = True
                    elif fm:
                        bm.faces.ensure_lookup_table()
                        if 0 <= idx < len(bm.faces):
                            bm.faces[idx].select = True

                for h in S.hints:
                    if xmin <= h.ax <= xmax and ymin <= h.ay <= ymax:
                        if h.payload:
                            idx = h.payload[-1]
                            if vm:
                                bm.verts.ensure_lookup_table()
                                if 0 <= idx < len(bm.verts):
                                    bm.verts[idx].select = True
                            elif em:
                                bm.edges.ensure_lookup_table()
                                if 0 <= idx < len(bm.edges):
                                    bm.edges[idx].select = True
                            elif fm:
                                bm.faces.ensure_lookup_table()
                                if 0 <= idx < len(bm.faces):
                                    bm.faces[idx].select = True

                bm.select_flush_mode()
                bmesh.update_edit_mesh(obj.data)
            except Exception as e:
                print(f"[Vimender] bmesh box select error: {e}")

    if not bpy.app.background:
        with context.temp_override(window=win, area=area, region=region):
            try:
                if S.kind in {'OBJ', 'MESH'}:
                    bpy.ops.view3d.select_box(xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax,
                                              wait_for_input=False, mode=mode)
                elif S.kind == 'NODE':
                    bpy.ops.node.select_box(tweak=False, xmin=xmin, xmax=xmax, ymin=ymin,
                                            ymax=ymax, wait_for_input=False, mode=mode)
            except Exception as e:
                pass
    area.tag_redraw()


def _get_shader():
    global _shader
    if _shader is None:
        try:
            _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        except Exception:
            _shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')
    return _shader


def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1)]


def _line(x0, y0, x1, y1, w):
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return []
    nx, ny = -dy / L * w / 2, dx / L * w / 2
    a, b = (x0 + nx, y0 + ny), (x0 - nx, y0 - ny)
    c, d = (x1 - nx, y1 - ny), (x1 + nx, y1 + ny)
    return [a, b, c, a, c, d]


def _circle(cx, cy, r, seg=14):
    v = []
    for i in range(seg):
        a0 = 2 * math.pi * i / seg
        a1 = 2 * math.pi * (i + 1) / seg
        v += [(cx, cy), (cx + r * math.cos(a0), cy + r * math.sin(a0)),
              (cx + r * math.cos(a1), cy + r * math.sin(a1))]
    return v


def _fill(verts, color):
    if not verts:
        return
    sh = _get_shader()
    batch = batch_for_shader(sh, 'TRIS', {"pos": verts})
    sh.bind()
    sh.uniform_float("color", color)
    batch.draw(sh)


def _font_size(size):
    try:
        blf.size(0, size)
    except TypeError:
        blf.size(0, size, 72)


def _text(x, y, s, color):
    blf.color(0, *color)
    blf.position(0, x, y, 0)
    blf.draw(0, s)


def glyph_tris(t, cx, cy, r):
    """Simple vector icon for object types (triangle list)."""
    lw = max(1.3, r * 0.3)
    if t == 'MESH':
        return [(cx - r, cy - r * 0.8), (cx + r, cy - r * 0.8), (cx, cy + r)]
    if t in {'CURVE', 'SURFACE', 'CURVES'}:
        pts = [(cx - r + 2 * r * i / 10, cy + 0.6 * r * math.sin(2 * math.pi * i / 10))
               for i in range(11)]
        v = []
        for a, b in zip(pts, pts[1:]):
            v += _line(a[0], a[1], b[0], b[1], lw)
        return v
    if t == 'CAMERA':
        return (_rect(cx - r, cy - r * 0.55, cx + r * 0.3, cy + r * 0.55) +
                [(cx + r * 0.3, cy), (cx + r, cy + r * 0.65), (cx + r, cy - r * 0.65)])
    if t == 'LIGHT':
        v = _circle(cx, cy, r * 0.5)
        for k in range(8):
            a = math.pi * k / 4
            v += _line(cx + math.cos(a) * r * 0.72, cy + math.sin(a) * r * 0.72,
                       cx + math.cos(a) * r, cy + math.sin(a) * r, lw * 0.8)
        return v
    if t == 'ARMATURE':
        return [(cx, cy + r), (cx - r * 0.45, cy), (cx + r * 0.45, cy),
                (cx, cy - r), (cx + r * 0.45, cy), (cx - r * 0.45, cy)]
    if t == 'FONT':
        return _rect(cx - r, cy + r * 0.5, cx + r, cy + r) + _rect(cx - lw / 2, cy - r, cx + lw / 2, cy + r * 0.5)
    if t == 'LATTICE':
        v = []
        for i in (-1, 0, 1):
            v += _line(cx - r, cy + i * r * 0.7, cx + r, cy + i * r * 0.7, lw * 0.7)
            v += _line(cx + i * r * 0.7, cy - r, cx + i * r * 0.7, cy + r, lw * 0.7)
        return v
    if t == 'META':
        return _circle(cx - r * 0.35, cy, r * 0.6) + _circle(cx + r * 0.35, cy, r * 0.6)
    if t == 'EMPTY':
        return _line(cx - r, cy, cx + r, cy, lw) + _line(cx, cy - r, cx, cy + r, lw)
    if t in {'GPENCIL', 'GREASEPENCIL'}:
        return _line(cx - r, cy - r, cx + r * 0.5, cy + r * 0.5, lw * 1.6) + \
            [(cx + r * 0.2, cy + r * 0.9), (cx + r * 0.9, cy + r * 0.2), (cx + r, cy + r)]
    if t == 'SPEAKER':
        return _rect(cx - r, cy - r * 0.4, cx - r * 0.2, cy + r * 0.4) + \
            [(cx - r * 0.2, cy + r * 0.4), (cx + r * 0.6, cy + r), (cx + r * 0.6, cy - r),
             (cx - r * 0.2, cy + r * 0.4), (cx + r * 0.6, cy - r), (cx - r * 0.2, cy - r * 0.4)]
    if t in {'LIGHT_PROBE'}:
        return _circle(cx, cy, r * 0.8)
    if t in {'VOLUME', 'POINTCLOUD'}:
        return (_circle(cx - r * 0.5, cy - r * 0.3, r * 0.3) + _circle(cx + r * 0.4, cy - r * 0.5, r * 0.3) +
                _circle(cx, cy + r * 0.5, r * 0.3) + _circle(cx + r * 0.6, cy + r * 0.3, r * 0.25))
    return _rect(cx - r * 0.7, cy - r * 0.7, cx + r * 0.7, cy + r * 0.7)


def draw_cb():
    ctx = bpy.context
    area, region = ctx.area, ctx.region
    if not S.active or area is None or region is None:
        return
    ptr = area.as_pointer()
    active_ptr = S.hint_ptr if S.mode else S.hover_ptr
    if ptr != active_ptr:
        return

    ui = ui_scale()
    w, h = region.width, region.height
    gpu.state.blend_set('ALPHA')

    if S.cursor_mode:
        border_col = PURPLE
    elif S.preview_mode:
        border_col = YELLOW
    elif S.navigation_mode or S.workspace_nav:
        border_col = RED
    elif S.nav_mode == 'PAN' and not S.mode:
        border_col = CYAN
    else:
        border_col = GREEN
    t = BORDER_PX if not (S.cursor_mode or S.preview_mode or S.navigation_mode or S.workspace_nav) else (BORDER_PX + 1)
    _fill(_rect(0, 0, w, t) + _rect(0, h - t, w, h) +
          _rect(0, 0, t, h) + _rect(w - t, 0, w, h), border_col)

    if S.mode in {'HINT', 'BOX1', 'BOX2'}:
        _font_size(int(13 * ui))
        pad = 4 * ui
        th = blf.dimensions(0, "Ag")[1]
        edges, leaders, pills = {}, [], {}
        draw_list, icon_list = [], []
        for hn in S.hints:
            if not hn.label.startswith(S.typed):
                continue
            tw = blf.dimensions(0, hn.label)[0]
            x0, y0 = hn.x - tw / 2 - pad, hn.y - th / 2 - pad
            x1, y1 = x0 + tw + 2 * pad, y0 + th + 2 * pad
            col = hn.color if (S.vertex_mode and hn.color) else HINT_BG
            pills.setdefault(col, []).extend(_rect(x0, y0, x1, y1))
            draw_list.append((x0 + pad, y0 + pad, hn.label))
            if hn.cluster:
                if math.hypot(hn.x - hn.ax, hn.y - hn.ay) > 3:
                    leaders += _line(hn.ax, hn.ay, hn.x, hn.y, 1.5 * ui)
                    leaders += _rect(hn.ax - 2.5 * ui, hn.ay - 2.5 * ui, hn.ax + 2.5 * ui, hn.ay + 2.5 * ui)
                for seg in hn.edges:
                    edges.setdefault(col, []).extend(_line(seg[0], seg[1], seg[2], seg[3], 3 * ui))
                if S.kind == 'OBJ' and hn.payload:
                    icon_list.append((hn.x, y1, hn.payload[1]))

        for col, verts in edges.items():
            _fill(verts, col)
        _fill(leaders, LEADER)
        for col, verts in pills.items():
            _fill(verts, col)
        typed_w = blf.dimensions(0, S.typed)[0] if S.typed else 0
        for x, y, label in draw_list:
            if S.typed:
                _text(x, y, S.typed, HINT_TYPED)
            _text(x + typed_w, y, label[len(S.typed):], HINT_TEXT)

        if icon_list:
            tile = 18 * ui
            tiles, glyphs = [], []
            for cx, top, otype in icon_list:
                cy = top + 2 * ui + tile / 2
                tiles += _rect(cx - tile / 2, cy - tile / 2, cx + tile / 2, cy + tile / 2)
                glyphs += glyph_tris(otype, cx, cy, tile * 0.32)
            _fill(tiles, HINT_BG)
            _fill(glyphs, HINT_TEXT)

    if S.mode == 'BOX2' and S.box_a:
        ax, ay = S.box_a
        mx, my = S.mouse
        pad = 14 * ui
        x0, x1 = min(ax, mx) - pad, max(ax, mx) + pad
        y0, y1 = min(ay, my) - pad, max(ay, my) + pad
        _fill(_rect(x0, y0, x1, y1), (GREEN[0], GREEN[1], GREEN[2], 0.18))
        b = 2 * ui
        _fill(_rect(x0, y0, x1, y0 + b) + _rect(x0, y1 - b, x1, y1) +
              _rect(x0, y0, x0 + b, y1) + _rect(x1 - b, y0, x1, y1), GREEN)
        _fill(_rect(ax - 5 * ui, ay - 5 * ui, ax + 5 * ui, ay + 5 * ui), GREEN)
        _fill(_rect(mx - 4 * ui, my - 4 * ui, mx + 4 * ui, my + 4 * ui), HINT_BG)

    if S.cursor_mode:
        label = f"CURSOR MODE - {S.cursor_plane}"
    elif S.preview_mode:
        label = "PREVIEW MODE"
    elif S.navigation_mode or S.workspace_nav:
        label = "NAVIGATION MODE"
    elif S.mode == 'HINT':
        label = "HINT MODE"
    elif S.mode in {'BOX1', 'BOX2'}:
        label = "BOX SELECT MODE"
    elif S.nav_mode == 'PAN':
        label = "PAN MODE"
    else:
        label = "ORBIT MODE"
    _font_size(int(14 * ui))
    tw, th = blf.dimensions(0, label)
    inset = 0
    if region.width >= area.width - 1:
        inset = sum(r.width for r in area.regions
                    if r.type == 'UI' and r.alignment == 'RIGHT' and r.width > 1)
    pad = 6 * ui
    x1 = w - inset - 12 * ui
    x0 = x1 - tw - 2 * pad
    y0 = 12 * ui
    _fill(_rect(x0, y0, x1, y0 + th + 2 * pad), (0.0, 0.0, 0.0, 0.65))
    _text(x0 + pad, y0 + pad, label, border_col)

    gpu.state.blend_set('NONE')


def install_draw_handlers():
    if _handlers:
        return
    for name in SPACE_NAMES:
        cls = getattr(bpy.types, name, None)
        if cls is None:
            continue
        try:
            h = cls.draw_handler_add(draw_cb, (), 'WINDOW', 'POST_PIXEL')
            _handlers.append((cls, h))
        except Exception:
            pass


def remove_draw_handlers():
    for cls, h in _handlers:
        try:
            cls.draw_handler_remove(h, 'WINDOW')
        except Exception:
            pass
    _handlers.clear()


def clean_modal_keymaps():
    wm = getattr(bpy.context, 'window_manager', None)
    if not wm or not getattr(wm, 'keyconfigs', None):
        return
    modified = False
    for kc in (wm.keyconfigs.default, wm.keyconfigs.user, getattr(wm.keyconfigs, 'active', None)):
        if not kc:
            continue
        for map_name in ('Transform Modal Map', 'Bevel Modal Map'):
            km = kc.keymaps.get(map_name)
            if km:
                for kmi in list(km.keymap_items):
                    if getattr(kmi, 'propvalue', '') in {'PROPORTIONAL_SIZE_UP', 'PROPORTIONAL_SIZE_DOWN', 'SEGMENTS_UP', 'SEGMENTS_DOWN'}:
                        try:
                            km.keymap_items.remove(kmi)
                            modified = True
                        except Exception:
                            pass
    prefs = get_preferences()
    if prefs and getattr(prefs, 'key_orbit_pan', '') == 'NUMPAD_5':
        try:
            prefs.key_orbit_pan = 'U'
            modified = True
        except Exception:
            pass
    if modified and not getattr(bpy.app, 'background', False):
        try:
            bpy.ops.wm.save_userpref()
        except Exception:
            pass


def shutdown():
    S.active = False
    S.prefix = False
    S.navigation_mode = False
    S.workspace_nav = False
    S.cursor_mode = False
    S.preview_mode = True
    S.smooth_cursor.reset()
    S.nav.reset()
    S.clear_hint()
    S.hover_ptr = 0
    remove_draw_handlers()
    try:
        tag_all()
    except Exception:
        pass


def _pie_item(layout, label, icon, candidates):
    op = layout.operator("wm.vim_set_editor", text=label, icon=icon)
    op.candidates = "|".join(candidates)


def draw_editor_pie(self, context):
    pie = self.layout.menu_pie()
    _pie_item(pie, "Outliner", 'OUTLINER', ("OUTLINER",))
    _pie_item(pie, "Properties", 'PROPERTIES', ("PROPERTIES",))
    _pie_item(pie, "Shader Editor", 'NODE_MATERIAL', ("ShaderNodeTree",))
    _pie_item(pie, "3D Viewport", 'VIEW3D', ("VIEW_3D",))
    _pie_item(pie, "Text Editor", 'TEXT', ("TEXT_EDITOR",))
    _pie_item(pie, "Image Editor", 'IMAGE_DATA', ("IMAGE_EDITOR",))
    _pie_item(pie, "Geometry Nodes", 'GEOMETRY_NODES', ("GeometryNodeTree",))
    pie.menu("VIM_MT_editor_more", text="More...", icon='COLLAPSEMENU')


class VIM_MT_editor_more(bpy.types.Menu):
    bl_label = "Other Editors"

    def draw(self, context):
        l = self.layout
        _pie_item(l, "UV Editor", 'UV', ("UV",))
        _pie_item(l, "Compositor", 'NODE_COMPOSITING', ("CompositorNodeTree",))
        _pie_item(l, "Texture Nodes", 'NODE_TEXTURE', ("TextureNodeTree",))
        _pie_item(l, "Timeline", 'TIME', ("TIMELINE",))
        _pie_item(l, "Dope Sheet", 'ACTION', ("DOPESHEET",))
        _pie_item(l, "Graph Editor", 'GRAPH', ("FCURVES",))
        _pie_item(l, "Drivers", 'DRIVER', ("DRIVERS",))
        _pie_item(l, "NLA", 'NLA', ("NLA_EDITOR",))
        _pie_item(l, "Video Sequencer", 'SEQUENCE', ("SEQUENCER", "SEQUENCE_EDITOR"))
        _pie_item(l, "Movie Clip", 'TRACKER', ("CLIP_EDITOR",))
        _pie_item(l, "File Browser", 'FILEBROWSER', ("FILES",))
        _pie_item(l, "Asset Browser", 'ASSET_MANAGER', ("ASSETS",))
        _pie_item(l, "Spreadsheet", 'SPREADSHEET', ("SPREADSHEET",))
        _pie_item(l, "Python Console", 'CONSOLE', ("CONSOLE",))
        _pie_item(l, "Info", 'INFO', ("INFO",))


class WM_OT_vim_set_editor(bpy.types.Operator):
    bl_idname = "wm.vim_set_editor"
    bl_label = "Change Editor Type"
    bl_options = {'INTERNAL'}

    candidates: bpy.props.StringProperty()

    def execute(self, context):
        win = context.window
        area = area_by_ptr(win, S.pie_ptr) or context.area
        if area is None:
            return {'CANCELLED'}
        for c in self.candidates.split("|"):
            try:
                area.ui_type = c
                area.tag_redraw()
                return {'FINISHED'}
            except (TypeError, ValueError):
                continue
        self.report({'WARNING'}, "Editor type not found in this Blender version")
        return {'CANCELLED'}


class WM_OT_vim_nudge(bpy.types.Operator):
    bl_idname = "wm.vim_nudge"
    bl_label = "Vim Smooth Transform Control"
    bl_options = {'INTERNAL'}

    _timer = None

    def invoke(self, context, event):
        S.nudge_on = True
        S.nav.reset()
        S.mover.reset()

        self.locked_axis = None
        self.axis_press_count = 0
        self.axis_dir_2d = None

        area = area_at(context.window, event.mouse_x, event.mouse_y) or context.area
        if not area or area.type != 'VIEW_3D':
            area = next((a for a in context.screen.areas if a.type == 'VIEW_3D'), context.area)
        self.area = area
        self.region = window_region(area) if area else None

        if self.region:
            margin = 8.0
            self.min_x = float(self.region.x + margin)
            self.max_x = float(self.region.x + self.region.width - margin)
            self.min_y = float(self.region.y + margin)
            self.max_y = float(self.region.y + self.region.height - margin)
        else:
            self.min_x = 8.0
            self.max_x = float(max(context.window.width - 8.0, 20.0))
            self.min_y = 8.0
            self.max_y = float(max(context.window.height - 8.0, 20.0))

        self.pivot_2d = Vector((float(event.mouse_x), float(event.mouse_y)))
        center_info = get_selection_center_2d(context, self.area)
        if center_info and center_info[0] is not None and center_info[1] is not None:
            self.pivot_2d = Vector((float(center_info[0]), float(center_info[1])))

        self.sub_x = float(event.mouse_x)
        self.sub_y = float(event.mouse_y)
        self.sub_x = max(min(self.sub_x, self.max_x), self.min_x)
        self.sub_y = max(min(self.sub_y, self.max_y), self.min_y)

        self.vx = 0.0
        self.vy = 0.0
        self.ang_vel = 0.0
        self.held_nudge = set()
        self.last_t = time.perf_counter()
        self.last_wrap_time = 0.0
        self.last_span_x = 0.0
        self.last_span_y = 0.0

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.008, window=context.window)
        wm.modal_handler_add(self)
        try:
            context.workspace.status_text_set(None)
        except Exception:
            pass
        return {'RUNNING_MODAL'}

    def _cleanup(self, context):
        if self._timer is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except Exception:
                pass
            self._timer = None
        S.mover.reset()
        S.nudge_on = False
        self.locked_axis = None
        self.axis_press_count = 0
        self.axis_dir_2d = None
        self.held_nudge.clear()
        try:
            if S.cursor_mode:
                txt = f"Vimender: CURSOR MODE - {S.cursor_plane}"
            elif S.navigation_mode or S.workspace_nav:
                txt = "Vimender: NAVIGATION MODE"
            elif S.preview_mode:
                txt = "Vimender: PREVIEW MODE"
            else:
                txt = f"Vimender: {S.nav_mode} MODE"
            context.workspace.status_text_set(txt)
        except Exception:
            pass

    def _get_active_bounds(self, win, area=None):
        return (self.min_x, self.max_x, self.min_y, self.max_y)

    def _warp_and_wrap(self, win, area=None):
        span_x = max(self.max_x - self.min_x, 20.0)
        span_y = max(self.max_y - self.min_y, 20.0)

        wrapped = False
        if self.sub_x > self.max_x:
            self.sub_x = self.min_x + ((self.sub_x - self.min_x) % span_x)
            wrapped = True
        elif self.sub_x < self.min_x:
            self.sub_x = self.max_x - ((self.max_x - self.sub_x) % span_x)
            wrapped = True

        if self.sub_y > self.max_y:
            self.sub_y = self.min_y + ((self.sub_y - self.min_y) % span_y)
            wrapped = True
        elif self.sub_y < self.min_y:
            self.sub_y = self.max_y - ((self.max_y - self.sub_y) % span_y)
            wrapped = True

        self.sub_x = max(min(self.sub_x, self.max_x), self.min_x)
        self.sub_y = max(min(self.sub_y, self.max_y), self.min_y)

        if wrapped:
            self.last_wrap_time = time.perf_counter()
            self.last_span_x = span_x
            self.last_span_y = span_y

        win.cursor_warp(int(round(self.sub_x)), int(round(self.sub_y)))

    def _calc_axis_dir(self, context, area, axis, press_count):
        if not area or area.type != 'VIEW_3D':
            if axis == 'Z':
                self.axis_dir_2d = Vector((0.0, 1.0))
            elif axis == 'X':
                self.axis_dir_2d = Vector((1.0, 0.0))
            else:
                self.axis_dir_2d = Vector((0.7071, 0.7071))
            return

        region = window_region(area)
        rv3d = getattr(area.spaces.active, 'region_3d', None) if area.spaces else None
        if not region or not rv3d:
            self.axis_dir_2d = Vector((0.0, 1.0)) if axis == 'Z' else Vector((1.0, 0.0))
            return

        act_obj = context.active_object
        if act_obj:
            pivot_3d = act_obj.matrix_world.translation.copy()
        else:
            pivot_3d = Vector((0.0, 0.0, 0.0))

        axis_map = {
            'X': Vector((1.0, 0.0, 0.0)),
            'Y': Vector((0.0, 1.0, 0.0)),
            'Z': Vector((0.0, 0.0, 1.0)),
        }
        vec_3d = axis_map.get(axis, Vector((0.0, 0.0, 1.0)))

        if press_count == 2 and act_obj:
            vec_3d = (act_obj.matrix_world.to_3x3() @ vec_3d).normalized()

        p0 = location_3d_to_region_2d(region, rv3d, pivot_3d)
        p1 = location_3d_to_region_2d(region, rv3d, pivot_3d + vec_3d * 2.0)

        if p0 is not None and p1 is not None:
            d = Vector((p1[0] - p0[0], p1[1] - p0[1]))
            if d.length > 1e-3:
                d = d.normalized()
                if abs(d.y) >= abs(d.x):
                    if d.y < 0:
                        d = -d
                else:
                    if d.x < 0:
                        d = -d
                self.axis_dir_2d = d
                return

        if axis == 'Z':
            self.axis_dir_2d = Vector((0.0, 1.0))
        elif axis == 'X':
            self.axis_dir_2d = Vector((1.0, 0.0))
        else:
            self.axis_dir_2d = Vector((0.7071, 0.7071))

    def _set_status(self, context):
        try:
            context.workspace.status_text_set(None)
        except Exception:
            pass

    def modal(self, context, event):
        win = context.window
        if not S.active or not transform_active(win):
            self._cleanup(context)
            return {'FINISHED'}

        t, v = event.type, event.value
        area = self.area if (self.area and self.area in list(context.screen.areas)) else (area_at(win, event.mouse_x, event.mouse_y) or context.area)

        if (t in {'ESC', 'RET', 'NUMPAD_ENTER', 'LEFTMOUSE', 'RIGHTMOUSE'}) and v == 'PRESS':
            self._cleanup(context)
            return {'PASS_THROUGH'}

        if t in {'X', 'Y', 'Z'} and v == 'PRESS' and not (event.ctrl or event.alt or event.oskey or event.shift):
            if self.locked_axis == t:
                self.axis_press_count += 1
                if self.axis_press_count >= 3:
                    self.locked_axis = None
                    self.axis_press_count = 0
                    self.axis_dir_2d = None
                else:
                    self._calc_axis_dir(context, area, t, self.axis_press_count)
            else:
                self.locked_axis = t
                self.axis_press_count = 1
                self._calc_axis_dir(context, area, t, 1)

            self.vx = 0.0
            self.vy = 0.0
            self.ang_vel = 0.0
            self.held_nudge.clear()
            return {'PASS_THROUGH'}

        if t == 'C' and v == 'PRESS' and not (event.ctrl or event.alt or event.oskey):
            self.locked_axis = None
            self.axis_press_count = 0
            self.axis_dir_2d = None
            self.vx = 0.0
            self.vy = 0.0
            self.ang_vel = 0.0
            self.held_nudge.clear()
            return {'PASS_THROUGH'}

        NUDGE_KEYS = {
            'NUMPAD_8', 'NUMPAD_2', 'NUMPAD_4', 'NUMPAD_6',
            'UP_ARROW', 'DOWN_ARROW', 'LEFT_ARROW', 'RIGHT_ARROW',
            'H', 'J', 'K', 'L'
        }
        if t in NUDGE_KEYS:
            if v == 'PRESS' and not (event.ctrl or event.alt or event.oskey):
                self.held_nudge.add(t)
                return {'RUNNING_MODAL'}
            elif v == 'RELEASE':
                if t in self.held_nudge:
                    self.held_nudge.discard(t)
                    return {'RUNNING_MODAL'}

        if (t in {'NUMPAD_PLUS', 'NUMPAD_MINUS', 'PLUS', 'MINUS', 'EQUAL', 'PAGE_UP', 'PAGE_DOWN'}
                or (event.unicode in {'+', '-'} and event.ctrl)) and event.ctrl and v == 'PRESS':
            is_up = t in {'NUMPAD_PLUS', 'PLUS', 'EQUAL', 'PAGE_UP'} or event.unicode == '+'
            change_sens(1 if is_up else -1)
            return {'RUNNING_MODAL'}

        if (t in {'NUMPAD_PLUS', 'NUMPAD_MINUS', 'PLUS', 'MINUS', 'EQUAL', 'PAGE_UP', 'PAGE_DOWN'}
                or (event.unicode in {'+', '-'} and not (event.ctrl or event.alt or event.oskey))):
            if not (event.ctrl or event.alt or event.oskey):
                if v == 'PRESS':
                    try:
                        ts = context.scene.tool_settings
                        is_prop = (
                            getattr(ts, 'use_proportional_edit', False) or
                            getattr(ts, 'use_proportional_edit_objects', False)
                        )
                        if is_prop:
                            is_up = (t in {'NUMPAD_PLUS', 'PLUS', 'EQUAL', 'PAGE_UP'} or event.unicode == '+')
                            factor = 1.15 if is_up else (1.0 / 1.15)
                            ts.proportional_size = max(min(ts.proportional_size * factor, 10000.0), 0.001)
                            if area:
                                area.tag_redraw()
                    except Exception:
                        pass
                return {'PASS_THROUGH'}

        if t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            if not self.held_nudge and math.hypot(self.vx, self.vy) < 0.1:
                self.sub_x = float(event.mouse_x)
                self.sub_y = float(event.mouse_y)
            return {'PASS_THROUGH'}

        if t in {'TIMER', 'TIMER_REPORT'}:
            now = time.perf_counter()
            dt = min(max(now - self.last_t, 0.001), 0.05)
            self.last_t = now

            sens = SENS_LEVELS[S.sens_idx]
            if event.shift:
                sens *= 0.12
            max_spd = CURSOR_MAX_SPEED * sens

            tr_type = get_active_transform_type(win)

            up_held = ('NUMPAD_8' in self.held_nudge) or ('UP_ARROW' in self.held_nudge) or ('K' in self.held_nudge)
            down_held = ('NUMPAD_2' in self.held_nudge) or ('DOWN_ARROW' in self.held_nudge) or ('J' in self.held_nudge)
            right_held = ('NUMPAD_6' in self.held_nudge) or ('RIGHT_ARROW' in self.held_nudge) or ('L' in self.held_nudge)
            left_held = ('NUMPAD_4' in self.held_nudge) or ('LEFT_ARROW' in self.held_nudge) or ('H' in self.held_nudge)

            if tr_type == 'ROTATE':
                rot_input = (1.0 if (up_held or right_held) else 0.0) - (1.0 if (down_held or left_held) else 0.0)
                dx_p = self.sub_x - self.pivot_2d.x
                dy_p = self.sub_y - self.pivot_2d.y
                radius = max(math.hypot(dx_p, dy_p), 80.0)
                min_x, max_x, min_y, max_y = self._get_active_bounds(win, area)
                max_r = max(min(self.pivot_2d.x - min_x, max_x - self.pivot_2d.x,
                                self.pivot_2d.y - min_y, max_y - self.pivot_2d.y) - 10.0, 40.0)
                radius = min(radius, max_r)
                current_angle = math.atan2(dy_p, dx_p)

                target_w = rot_input * (max_spd / radius)
                if rot_input != 0.0:
                    alpha = 1.0 - math.exp(-CURSOR_ACCEL_RATE * dt)
                    self.ang_vel += (target_w - self.ang_vel) * alpha
                else:
                    decay = math.exp(-CURSOR_FRICTION * dt)
                    self.ang_vel *= decay
                    if abs(self.ang_vel) < 0.01:
                        self.ang_vel = 0.0

                if abs(self.ang_vel) > 0.001:
                    new_angle = current_angle + self.ang_vel * dt
                    self.sub_x = self.pivot_2d.x + radius * math.cos(new_angle)
                    self.sub_y = self.pivot_2d.y + radius * math.sin(new_angle)
                    win.cursor_warp(int(round(self.sub_x)), int(round(self.sub_y)))
                return {'PASS_THROUGH'}

            elif self.locked_axis is not None and self.axis_dir_2d is not None:
                axis_input = (1.0 if (up_held or right_held) else 0.0) - (1.0 if (down_held or left_held) else 0.0)
                target_v = self.axis_dir_2d * (axis_input * max_spd)

                if axis_input != 0.0:
                    alpha = 1.0 - math.exp(-CURSOR_ACCEL_RATE * dt)
                    self.vx += (target_v.x - self.vx) * alpha
                    self.vy += (target_v.y - self.vy) * alpha
                else:
                    decay = math.exp(-CURSOR_FRICTION * dt)
                    self.vx *= decay
                    self.vy *= decay
                    if math.hypot(self.vx, self.vy) < 2.0:
                        self.vx = 0.0
                        self.vy = 0.0

                if math.hypot(self.vx, self.vy) > 0.1:
                    self.sub_x += self.vx * dt
                    self.sub_y += self.vy * dt
                    self._warp_and_wrap(win, area)
                return {'PASS_THROUGH'}

            else:
                dx = (1.0 if right_held else 0.0) - (1.0 if left_held else 0.0)
                dy = (1.0 if up_held else 0.0) - (1.0 if down_held else 0.0)
                if dx != 0.0 or dy != 0.0:
                    mag = math.hypot(dx, dy)
                    target_vx = (dx / mag) * max_spd
                    target_vy = (dy / mag) * max_spd
                    alpha = 1.0 - math.exp(-CURSOR_ACCEL_RATE * dt)
                    self.vx += (target_vx - self.vx) * alpha
                    self.vy += (target_vy - self.vy) * alpha
                else:
                    decay = math.exp(-CURSOR_FRICTION * dt)
                    self.vx *= decay
                    self.vy *= decay
                    if math.hypot(self.vx, self.vy) < 2.0:
                        self.vx = 0.0
                        self.vy = 0.0

                if math.hypot(self.vx, self.vy) > 0.1:
                    self.sub_x += self.vx * dt
                    self.sub_y += self.vy * dt
                    self._warp_and_wrap(win, area)
                return {'PASS_THROUGH'}

        return {'PASS_THROUGH'}


class WM_OT_vim_mode_toggle(bpy.types.Operator):
    bl_idname = "wm.vim_mode_toggle"
    bl_label = "Vimender Mode"
    bl_description = (
        "Toggle Vimender mode (F: select, Shift+F: extend, Alt+F: loop, "
        "C: box select, Numpad 5: Orbit/Pan, Numpad *: Persp/Ortho, "
        "Numpad 8/4/6/2: smooth navigation, Numpad +/-: smooth zoom)"
    )

    _timer = None

    def invoke(self, context, event):
        if S.active:
            shutdown()
            save_vimender_state(False)
            self.report({'INFO'}, "Vimender: Disabled")
            return {'FINISHED'}
        S.active = True
        S.preview_mode = True
        S.clear_hint()
        S.nav.reset()
        S.hover_ptr = 0
        S.prefix = False
        S.nudge_on = False
        S.navigation_mode = False
        S.workspace_nav = False
        S.cursor_mode = False
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.008, window=context.window)
        wm.modal_handler_add(self)
        install_draw_handlers()
        clean_modal_keymaps()
        tag_all()
        self._set_status(context)
        save_vimender_state(True)
        self.report({'INFO'}, "Vimender: PREVIEW MODE")
        return {'RUNNING_MODAL'}

    def execute(self, context):
        return self.invoke(context, None)

    def _finish(self, context):
        if self._timer is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except Exception:
                pass
            self._timer = None
        try:
            context.workspace.status_text_set(None)
        except Exception:
            pass
        shutdown()
        return {'FINISHED'}

    def _set_status(self, context):
        try:
            if S.cursor_mode:
                txt = f"Vimender: CURSOR MODE - {S.cursor_plane}"
            elif S.preview_mode:
                txt = "Vimender: PREVIEW MODE"
            elif S.navigation_mode or S.workspace_nav:
                txt = "Vimender: NAVIGATION MODE"
            elif S.mode == 'HINT':
                txt = "Vimender: HINT MODE"
            elif S.mode in {'BOX1', 'BOX2'}:
                txt = "Vimender: BOX SELECT MODE"
            elif S.prefix:
                txt = "Vimender: PREFIX"
            elif S.nav_mode == 'PAN':
                txt = "Vimender: PAN MODE"
            else:
                txt = "Vimender: ORBIT MODE"
            context.workspace.status_text_set(txt)
        except Exception:
            pass

    def _cancel_hint(self, context, win):
        a = area_by_ptr(win, S.hint_ptr)
        S.clear_hint()
        self._set_status(context)
        if a:
            a.tag_redraw()
        tag_all()

    def _tag_hover(self, win):
        a = area_by_ptr(win, S.hover_ptr)
        if a:
            a.tag_redraw()

    def _start_nudge(self, context, win):
        try:
            with context.temp_override(window=win):
                bpy.ops.wm.vim_nudge('INVOKE_DEFAULT')
        except Exception:
            S.nudge_on = False

    def _start_hint(self, context, win, area, box, action, mx, my):
        region = window_region(area)
        if region is None:
            return
        kind, raw = collect(context, area, region)
        if kind == 'GRID' and box:
            self.report({'WARNING'}, "Box selection is not supported in this editor (3D Viewport and Node editors only)")
            return
        if not raw:
            self.report({'INFO'}, "No selectable elements found")
            return
        lx = mx - region.x
        ly = my - region.y
        if kind not in {'OUTLINER', 'PROPERTIES'}:
            raw.sort(key=lambda r: (r[0] - lx) ** 2 + (r[1] - ly) ** 2)
        if len(raw) > MAX_HINTS:
            raw = raw[:MAX_HINTS]
        labels = make_labels(len(raw))
        hints = [Hint(lb, r[0], r[1], r[2]) for lb, r in zip(labels, raw)]

        vertex_mode = kind == 'MESH' and context.tool_settings.mesh_select_mode[0]
        if kind not in {'GRID', 'OUTLINER', 'PROPERTIES'}:
            declutter(hints, region)
            if vertex_mode:
                assign_cluster_colors(hints)
                try:
                    attach_vertex_edges(context, area, region, hints)
                except Exception as e:
                    print(f"[Vimender] Edge coloring error: {e}")

        S.nav.reset()
        S.hints = hints
        S.kind = kind
        S.action = action
        S.box_extend = (action == 'EXTEND')
        S.vertex_mode = vertex_mode
        S.typed = ""
        S.box_a = None
        S.hint_ptr = area.as_pointer()
        S.mouse = (lx, ly)
        S.mode = 'BOX1' if box else 'HINT'
        S.mover.reset()
        S.smooth_box.reset(lx, ly)
        self._set_status(context)
        area.tag_redraw()
        tag_all()

    def _fire(self, context, win, hint, shift_held):
        area = area_by_ptr(win, S.hint_ptr)
        region = window_region(area) if area else None
        if area is None or region is None:
            self._cancel_hint(context, win)
            return
        try:
            if S.mode == 'HINT':
                act = 'EXTEND' if shift_held else S.action
                apply_single(context, win, area, region, hint, act)
                self._cancel_hint(context, win)
            elif S.mode == 'BOX1':
                S.box_a = (hint.ax, hint.ay)
                S.box_start_hint = hint
                S.box_end_hint = None
                S.mode = 'BOX2'
                S.typed = ""
                S.smooth_box.reset(hint.ax, hint.ay)
                gx = int(region.x + hint.ax)
                gy = int(region.y + hint.ay)
                win.cursor_warp(gx, gy)
                S.mouse = (hint.ax, hint.ay)
                self._set_status(context)
                area.tag_redraw()
            elif S.mode == 'BOX2':
                S.box_end_hint = hint
                S.mouse = (hint.ax, hint.ay)
                self._finish_box2(context, win, shift_held)
        except Exception as e:
            self.report({'ERROR'}, f"Vimender: {e}")
            self._cancel_hint(context, win)

    def _finish_box2(self, context, win, shift_held):
        area = area_by_ptr(win, S.hint_ptr)
        region = window_region(area) if area else None
        if area is None or region is None or S.box_a is None:
            self._cancel_hint(context, win)
            return
        try:
            extend = S.box_extend or shift_held
            apply_box(context, win, area, region, S.box_a, S.mouse, extend)
        except Exception as e:
            self.report({'ERROR'}, f"Vimender: {e}")
        self._cancel_hint(context, win)

    def _enter_cursor_mode(self, context):
        S.prev_mode_state = {
            'preview_mode': S.preview_mode,
            'navigation_mode': S.navigation_mode,
            'workspace_nav': S.workspace_nav,
            'nav_mode': S.nav_mode,
        }
        S.preview_mode = False
        S.navigation_mode = False
        S.workspace_nav = False
        S.cursor_mode = True
        S.cursor_plane = 'XY'
        S.smooth_cursor.reset()
        self.report({'INFO'}, "Vimender: CURSOR MODE - XY")
        self._set_status(context)
        tag_all()

    def _exit_cursor_mode(self, context):
        S.cursor_mode = False
        S.smooth_cursor.reset()
        prev = getattr(S, 'prev_mode_state', {})
        S.preview_mode = prev.get('preview_mode', True)
        S.navigation_mode = prev.get('navigation_mode', False)
        S.workspace_nav = prev.get('workspace_nav', False)
        S.nav_mode = prev.get('nav_mode', 'ORBIT')
        if S.preview_mode:
            self.report({'INFO'}, "Vimender: PREVIEW MODE")
        elif S.navigation_mode:
            self.report({'INFO'}, "Vimender: NAVIGATION MODE")
        elif S.nav_mode == 'PAN':
            self.report({'INFO'}, "Vimender: PAN MODE")
        else:
            self.report({'INFO'}, "Vimender: ORBIT MODE")
        self._set_status(context)
        tag_all()

    def _open_pie(self, context, event, win, area):
        region = window_region(area)
        if region is None:
            return
        S.pie_ptr = area.as_pointer()
        with context.temp_override(window=win, area=area, region=region):
            context.window_manager.popup_menu_pie(event, draw_editor_pie, title="Editor")

    def _begin_prefix(self, context, repeat=False):
        S.prefix = True
        S.prefix_t = time.time()
        S.prefix_timeout = PREFIX_REPEAT_TIMEOUT if repeat else PREFIX_TIMEOUT
        self._set_status(context)
        self._tag_hover(context.window)

    def _end_prefix(self, context):
        S.prefix = False
        self._set_status(context)
        self._tag_hover(context.window)

    def _focus_area(self, win, tgt):
        win.cursor_warp(int(tgt.x + tgt.width / 2), int(tgt.y + tgt.height / 2))
        old = area_by_ptr(win, S.hover_ptr)
        S.hover_ptr = tgt.as_pointer()
        if old:
            old.tag_redraw()
        tgt.tag_redraw()

    def _area_op(self, context, win, area, fn):
        region = window_region(area)
        if region is None:
            return
        try:
            with context.temp_override(window=win, area=area, region=region):
                fn()
        except Exception as e:
            self.report({'WARNING'}, f"Vimender: {e}")

    def _modal_prefix(self, context, event, win, area):
        t, v = event.type, event.value
        if t in MOUSE_CANCEL:
            self._end_prefix(context)
            return {'PASS_THROUGH'}
        if t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} or t in MODIFIER_KEYS:
            return {'PASS_THROUGH'}
        if v != 'PRESS':
            return {'RUNNING_MODAL'}

        uni = event.unicode or ""
        if t in PREFIX_DIRS:
            if area is not None:
                tgt = neighbor_area(win, area, PREFIX_DIRS[t])
                if tgt is None:
                    self.report({'INFO'}, "No editor in this direction")
                else:
                    self._focus_area(win, tgt)
            self._begin_prefix(context, repeat=True)
            return {'RUNNING_MODAL'}

        self._end_prefix(context)
        if area is None or t == 'ESC':
            return {'RUNNING_MODAL'}
        if t == 'O':
            areas = sorted(focusable_areas(win), key=lambda a: (-a.y, a.x))
            ptrs = [a.as_pointer() for a in areas]
            if area.as_pointer() in ptrs:
                nxt = areas[(ptrs.index(area.as_pointer()) + 1) % len(areas)]
                self._focus_area(win, nxt)
        elif t == 'Z':
            self._area_op(context, win, area, lambda: bpy.ops.screen.screen_full_area())
        elif t == 'S' or uni == '"':
            self._area_op(context, win, area,
                          lambda: bpy.ops.screen.area_split(direction='HORIZONTAL', factor=0.5))
        elif t == 'V' or uni == '%':
            self._area_op(context, win, area,
                          lambda: bpy.ops.screen.area_split(direction='VERTICAL', factor=0.5))
        elif t == 'X':
            self._area_op(context, win, area, lambda: bpy.ops.screen.area_close())
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if not S.active:
            return self._finish(context)

        win = context.window
        t, v = event.type, event.value

        if t in {'TIMER', 'TIMER_REPORT'}:
            if S.prefix and time.time() - S.prefix_t > S.prefix_timeout:
                self._end_prefix(context)
            if S.mode == 'BOX2':
                a = area_by_ptr(win, S.hint_ptr)
                r = window_region(a) if a else None
                if r and S.smooth_box.tick(r):
                    win.cursor_warp(int(round(r.x + S.mouse[0])), int(round(r.y + S.mouse[1])))
                    a.tag_redraw()
            elif S.cursor_mode:
                S.smooth_cursor.tick(context, win)
            elif not S.mode and not S.preview_mode and not S.nudge_on and not transform_active(win):
                S.nav.tick(context, win)

            if not S.nudge_on and transform_active(win):
                self._start_nudge(context, win)
            return {'PASS_THROUGH'}

        mx, my = event.mouse_x, event.mouse_y
        area = area_at(win, mx, my) or area_by_ptr(win, S.hover_ptr) or context.area
        ptr = area.as_pointer() if area else 0
        if ptr != S.hover_ptr and not S.mode:
            old = area_by_ptr(win, S.hover_ptr)
            S.hover_ptr = ptr
            S.nav.reset()
            if old:
                old.tag_redraw()
            if area:
                area.tag_redraw()

        if S.mode:
            return self._modal_hint(context, event, win, mx, my)

        if transform_active(win):
            if not S.nudge_on:
                self._start_nudge(context, win)
            return {'PASS_THROUGH'}

        if t in {'G', 'R', 'S'} and v == 'PRESS' and not (event.ctrl or event.alt or event.oskey):
            if t == 'R':
                S.last_transform_type = 'ROTATE'
            elif t == 'S':
                S.last_transform_type = 'RESIZE'
            else:
                S.last_transform_type = 'TRANSLATE'

        prefs = get_preferences(context)
        use_arrow_layout = bool(getattr(prefs, 'arrow_keys_layout', False)) if prefs else False
        plain = not (event.alt or event.oskey)

        if (check_shortcut(prefs, 'search_object', event, 'SPACE', fallback_ctrl=True) or
            (t == 'F3' and event.ctrl and not (event.alt or event.shift or event.oskey))) and v == 'PRESS':
            bpy.ops.wm.vim_search_object('INVOKE_DEFAULT')
            return {'RUNNING_MODAL'}

        anim_editors = {'DOPESHEET_EDITOR', 'NLA_EDITOR', 'GRAPH_EDITOR', 'SEQUENCE_EDITOR', 'CLIP_EDITOR'}
        if t == 'SPACE' and not event.ctrl and area and (area.type in anim_editors or area.type in PASS_KEYS_IN):
            return {'PASS_THROUGH'}

        if check_shortcut(prefs, 'search_menu', event, 'SPACE', fallback_ctrl=False) and v == 'PRESS':
            if not S.mode and not (area and (area.type in PASS_KEYS_IN or area.type in anim_editors)) and not transform_active(win):
                target_area = area or area_at(win, mx, my) or area_by_ptr(win, S.hover_ptr) or context.area
                if not target_area or target_area.type != 'VIEW_3D':
                    target_area = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), target_area or context.area)
                target_region = window_region(target_area) if target_area else None
                try:
                    if target_area and target_region:
                        if not (target_region.x <= mx <= target_region.x + target_region.width and
                                target_region.y <= my <= target_region.y + target_region.height):
                            win.cursor_warp(int(target_region.x + target_region.width / 2),
                                            int(target_region.y + target_region.height / 2))
                        with context.temp_override(window=win, area=target_area, region=target_region):
                            bpy.ops.wm.search_menu('INVOKE_DEFAULT')
                    else:
                        bpy.ops.wm.search_menu('INVOKE_DEFAULT')
                except Exception as e:
                    print(f"[Vimender] search_menu error: {e}")
                return {'RUNNING_MODAL'}

        if check_shortcut(prefs, 'cursor', event, 'NUMPAD_5', fallback_ctrl=True) and v == 'PRESS':
            if not S.cursor_mode:
                self._enter_cursor_mode(context)
            else:
                self._exit_cursor_mode(context)
            return {'RUNNING_MODAL'}

        if S.cursor_mode:
            if (t == 'NUMPAD_5' or (use_arrow_layout and t == 'TAB')) and not event.ctrl:
                if v == 'PRESS':
                    S.cursor_plane = 'ZY' if S.cursor_plane == 'XY' else 'XY'
                    S.smooth_cursor.reset()
                    self.report({'INFO'}, f"Vimender: CURSOR MODE - {S.cursor_plane}")
                    self._set_status(context)
                    tag_all()
                return {'RUNNING_MODAL'}

            if t in NUDGE_DIRS or (use_arrow_layout and t in ARROW_DIRS):
                S.smooth_cursor.on_key(t, v, shift=event.shift)
                return {'RUNNING_MODAL'}

            if t in {'ESC'} and v == 'PRESS':
                self._exit_cursor_mode(context)
                return {'RUNNING_MODAL'}

            if (t in {'NUMPAD_PLUS', 'NUMPAD_MINUS'} or (use_arrow_layout and (t in {'PLUS', 'MINUS', 'EQUAL'} or event.unicode in {'+', '-'}))) and v == 'PRESS':
                change_sens(1 if (t in {'NUMPAD_PLUS', 'PLUS', 'EQUAL'} or event.unicode == '+') else -1)
                return {'RUNNING_MODAL'}

            if t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                return {'PASS_THROUGH'}

            return {'RUNNING_MODAL'}

        if check_shortcut(prefs, 'nav', event, 'B', fallback_ctrl=True):
            if v == 'RELEASE':
                return {'RUNNING_MODAL'}
            if v == 'PRESS':
                if S.cursor_mode:
                    self._exit_cursor_mode(context)
                if S.mode:
                    self._cancel_hint(context, win)
                if S.navigation_mode or S.workspace_nav:
                    S.navigation_mode = False
                    S.workspace_nav = False
                    S.preview_mode = True
                    self.report({'INFO'}, "Vimender: PREVIEW MODE")
                else:
                    S.preview_mode = False
                    S.navigation_mode = True
                    S.workspace_nav = True
                    target_a = area or area_at(win, mx, my) or (win.screen.areas[0] if win and win.screen and win.screen.areas else None)
                    if target_a:
                        S.hover_ptr = target_a.as_pointer()
                    self.report({'INFO'}, "Vimender: NAVIGATION MODE")
                self._set_status(context)
                tag_all()
                return {'RUNNING_MODAL'}

        if check_shortcut(prefs, 'preview', event, 'Y', fallback_ctrl=True):
            if v == 'RELEASE':
                return {'RUNNING_MODAL'}
            if v == 'PRESS':
                if S.cursor_mode:
                    self._exit_cursor_mode(context)
                if S.mode:
                    self._cancel_hint(context, win)
                if S.navigation_mode or S.workspace_nav:
                    S.navigation_mode = False
                    S.workspace_nav = False
                    S.preview_mode = True
                    self.report({'INFO'}, "Vimender: PREVIEW MODE")
                elif S.preview_mode:
                    S.preview_mode = False
                    S.navigation_mode = False
                    S.workspace_nav = False
                    self.report({'INFO'}, f"Vimender: {S.nav_mode} MODE")
                else:
                    S.navigation_mode = False
                    S.workspace_nav = False
                    S.preview_mode = True
                    self.report({'INFO'}, "Vimender: PREVIEW MODE")
                self._set_status(context)
                tag_all()
                return {'RUNNING_MODAL'}

        if S.navigation_mode or S.workspace_nav:
            now = time.perf_counter()

            if t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                return {'PASS_THROUGH'}

            if ((t in {'ESC', 'RET', 'NUMPAD_ENTER'}) or
                check_shortcut(prefs, 'nav', event, 'B', fallback_ctrl=True) or
                check_shortcut(prefs, 'preview', event, 'Y', fallback_ctrl=True)) and v == 'PRESS':
                S.navigation_mode = False
                S.workspace_nav = False
                S.preview_mode = True
                self.report({'INFO'}, "Vimender: PREVIEW MODE")
                self._set_status(context)
                tag_all()
                return {'RUNNING_MODAL'}

            if (t in {'NUMPAD_PLUS', 'NUMPAD_MINUS', 'PLUS', 'MINUS', 'EQUAL'}) and event.ctrl and v == 'PRESS':
                change_sens(1 if (t in {'NUMPAD_PLUS', 'PLUS', 'EQUAL'} or event.unicode == '+') else -1)
                return {'RUNNING_MODAL'}

            is_alt = bool(event.alt) and not (event.ctrl or event.oskey)

            if is_alt:
                ws_dir = None
                if t in {'H', 'LEFT_ARROW', 'NUMPAD_4'} or event.unicode in {'h', 'H'}:
                    ws_dir = 'PREV'
                elif t in {'L', 'RIGHT_ARROW', 'NUMPAD_6'} or event.unicode in {'l', 'L'}:
                    ws_dir = 'NEXT'
                elif t in {'K', 'UP_ARROW', 'NUMPAD_8'} or event.unicode in {'k', 'K'}:
                    ws_dir = 'PREV'
                elif t in {'J', 'DOWN_ARROW', 'NUMPAD_2'} or event.unicode in {'j', 'J'}:
                    ws_dir = 'NEXT'

                if ws_dir:
                    if v == 'PRESS':
                        if now - getattr(self, '_last_ws_cycle_t', 0.0) >= 0.22:
                            self._last_ws_cycle_t = now
                            tgt_ws = cycle_workspace(context, ws_dir)
                            ws_name = tgt_ws.name if tgt_ws else (win.workspace.name if win and win.workspace else "")
                            if ws_name:
                                self.report({'INFO'}, f"Vimender: Workspace -> {ws_name}")
                            self._set_status(context)
                            tag_all()
                    return {'RUNNING_MODAL'}

            if not (event.ctrl or event.alt or event.oskey):
                area_dir = None
                if t in {'H', 'LEFT_ARROW', 'NUMPAD_4'} or event.unicode in {'h', 'H'}:
                    area_dir = 'LEFT'
                elif t in {'L', 'RIGHT_ARROW', 'NUMPAD_6'} or event.unicode in {'l', 'L'}:
                    area_dir = 'RIGHT'
                elif t in {'K', 'UP_ARROW', 'NUMPAD_8'} or event.unicode in {'k', 'K'}:
                    area_dir = 'UP'
                elif t in {'J', 'DOWN_ARROW', 'NUMPAD_2'} or event.unicode in {'j', 'J'}:
                    area_dir = 'DOWN'

                if area_dir:
                    if v == 'PRESS':
                        target_a = area or area_by_ptr(win, S.hover_ptr) or area_at(win, mx, my) or context.area
                        if not target_a and win and win.screen and win.screen.areas:
                            target_a = win.screen.areas[0]
                        if target_a is not None:
                            tgt = neighbor_area(win, target_a, area_dir)
                            if tgt is not None:
                                self._focus_area(win, tgt)
                            else:
                                self._focus_area(win, target_a)
                            self._set_status(context)
                            tag_all()
                    return {'RUNNING_MODAL'}

                if t == 'O' or event.unicode in {'o', 'O'}:
                    if v == 'PRESS':
                        areas = sorted(focusable_areas(win), key=lambda a: (-a.y, a.x))
                        ptrs = [a.as_pointer() for a in areas]
                        target_a = area or area_by_ptr(win, S.hover_ptr) or area_at(win, mx, my)
                        if target_a and target_a.as_pointer() in ptrs:
                            nxt = areas[(ptrs.index(target_a.as_pointer()) + 1) % len(areas)]
                            self._focus_area(win, nxt)
                        elif areas:
                            self._focus_area(win, areas[0])
                        self._set_status(context)
                        tag_all()
                    return {'RUNNING_MODAL'}

            return {'RUNNING_MODAL'}

        target_v3d = area or area_at(win, mx, my) or area_by_ptr(win, S.hover_ptr) or context.area
        if not target_v3d or target_v3d.type != 'VIEW_3D':
            target_v3d = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None)

        if (target_v3d and target_v3d.type == 'VIEW_3D' and
                not (area and area.type in PASS_KEYS_IN) and
                not transform_active(win)):

            allow_view_switch = (not S.preview_mode) or (use_arrow_layout and not is_edit_mode(context))

            is_key_1 = (t == 'NUMPAD_1')
            if is_key_1 and allow_view_switch and not (event.alt or event.oskey):
                if v == 'PRESS':
                    axis = 'BACK' if event.ctrl else 'FRONT'
                    _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_axis, type=axis, align_active=bool(event.shift))
                return {'RUNNING_MODAL'}

            is_key_3 = (t == 'NUMPAD_3')
            if is_key_3 and allow_view_switch and not (event.alt or event.oskey):
                if v == 'PRESS':
                    axis = 'LEFT' if event.ctrl else 'RIGHT'
                    _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_axis, type=axis, align_active=bool(event.shift))
                return {'RUNNING_MODAL'}

            is_key_7 = (t == 'NUMPAD_7')
            if is_key_7 and allow_view_switch and not (event.alt or event.oskey):
                if v == 'PRESS':
                    axis = 'BOTTOM' if event.ctrl else 'TOP'
                    _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_axis, type=axis, align_active=bool(event.shift))
                return {'RUNNING_MODAL'}

            is_key_9 = (t == 'NUMPAD_9')
            if is_key_9 and (allow_view_switch or use_arrow_layout) and not (event.ctrl or event.alt or event.oskey or event.shift):
                if v == 'PRESS':
                    _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_orbit, angle=math.pi, type='ORBITRIGHT')
                return {'RUNNING_MODAL'}

            is_key_0 = (t == 'NUMPAD_0')
            if is_key_0 and (allow_view_switch or use_arrow_layout) and not (event.ctrl or event.alt or event.oskey or event.shift):
                if v == 'PRESS':
                    _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_camera)
                return {'RUNNING_MODAL'}

            is_key_slash = (t == 'NUMPAD_SLASH')
            if is_key_slash and (allow_view_switch or use_arrow_layout) and not (event.ctrl or event.alt or event.oskey or event.shift):
                if v == 'PRESS':
                    _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.localview)
                return {'RUNNING_MODAL'}

            is_i_selected = ((not S.preview_mode) and (t == 'I' or event.unicode in {'i', 'I'}) and not (event.ctrl or event.alt or event.oskey or event.shift))
            is_key_period = (
                check_shortcut(prefs, 'view_selected', event, 'NUMPAD_PERIOD', fallback_ctrl=False) or
                (t in {'NUMPAD_PERIOD', 'NUMPAD_COMMA', 'PERIOD', 'COMMA'} and not (event.ctrl or event.alt or event.oskey)) or
                (event.unicode in {',', '.'} and not (event.ctrl or event.alt or event.oskey)) or
                is_i_selected
            )
            if is_key_period and ((not S.preview_mode) or use_arrow_layout):
                if v == 'PRESS':
                    region = window_region(target_v3d)
                    if region:
                        try:
                            with context.temp_override(window=win, area=target_v3d, region=region):
                                bpy.ops.view3d.view_selected(use_all_regions=False)
                        except Exception:
                            pass
                        rv3d = target_v3d.spaces.active.region_3d if target_v3d.spaces else None
                        if rv3d and rv3d.view_distance < 3.2:
                            rv3d.view_distance = 3.2
                        target_v3d.tag_redraw()
                return {'RUNNING_MODAL'}

            is_y_persp = (t == 'Y' or event.unicode in {'y', 'Y'}) and not (event.ctrl or event.alt or event.oskey or event.shift)
            is_asterisk_persp = (
                check_shortcut(prefs, 'persp_ortho', event, 'Y', fallback_ctrl=False) or
                (t in {'NUMPAD_ASTERIX', 'ASTERISK'} and not (event.ctrl or event.alt or event.oskey)) or
                (event.unicode == '*' and not (event.ctrl or event.alt or event.oskey))
            )
            if (is_y_persp and not S.preview_mode) or (is_asterisk_persp and ((not S.preview_mode) or use_arrow_layout)):
                if v == 'PRESS':
                    _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_persportho)
                return {'RUNNING_MODAL'}

        if S.preview_mode:
            return {'PASS_THROUGH'}

        if check_shortcut(prefs, 'editor_pie', event, 'W', fallback_ctrl=True) and v == 'PRESS':
            if S.prefix:
                self._end_prefix(context)
            if area:
                self._open_pie(context, event, win, area)
            return {'RUNNING_MODAL'}

        if event.ctrl and (t in {'NUMPAD_4', 'NUMPAD_6'} or (use_arrow_layout and t in {'LEFT_ARROW', 'RIGHT_ARROW'})) and v == 'PRESS':
            tgt_ws = cycle_workspace(context, 'PREV' if t in {'NUMPAD_4', 'LEFT_ARROW'} else 'NEXT')
            if tgt_ws:
                self.report({'INFO'}, f"Vimender: Workspace -> {tgt_ws.name}")
            self._set_status(context)
            tag_all()
            return {'RUNNING_MODAL'}

        if S.prefix:
            return self._modal_prefix(context, event, win, area)

        if (t in {'NUMPAD_PLUS', 'NUMPAD_MINUS'} or (use_arrow_layout and (t in {'PLUS', 'MINUS', 'EQUAL'} or event.unicode in {'+', '-'}))) and event.ctrl and not (area and area.type in PASS_KEYS_IN):
            if v == 'PRESS':
                change_sens(1 if (t in {'NUMPAD_PLUS', 'PLUS', 'EQUAL'} or event.unicode == '+') else -1)
            return {'RUNNING_MODAL'}

        if area and area.type == 'OUTLINER' and not (event.ctrl or event.alt or event.oskey):
            if t in {'NUMPAD_8', 'NUMPAD_2'} or (use_arrow_layout and t in {'UP_ARROW', 'DOWN_ARROW'}):
                if v == 'PRESS':
                    direction = 'UP' if t in {'NUMPAD_8', 'UP_ARROW'} else 'DOWN'
                    navigate_in_editor(context, win, area, direction)
                    return {'RUNNING_MODAL'}
                elif v == 'RELEASE':
                    return {'RUNNING_MODAL'}


        if (check_shortcut(prefs, 'orbit_pan', event, 'U', fallback_ctrl=False) or
            (t in {'U', 'NUMPAD_5'} and not (event.ctrl or event.alt or event.oskey or event.shift))) and not (area and area.type in PASS_KEYS_IN):
            if v == 'PRESS':
                S.nav_mode = 'PAN' if S.nav_mode == 'ORBIT' else 'ORBIT'
                self.report({'INFO'}, f"Vimender: {S.nav_mode} MODE")
                self._set_status(context)
                if area:
                    area.tag_redraw()
                tag_all()
            return {'RUNNING_MODAL'}

        zoom_key = None
        if t in {'NUMPAD_PLUS', 'PLUS', 'EQUAL', 'PAGE_UP'} or event.unicode in {'+', '='}:
            if not (event.ctrl or event.alt or event.oskey):
                zoom_key = 'PLUS'
        elif t in {'NUMPAD_MINUS', 'MINUS', 'PAGE_DOWN'} or event.unicode == '-':
            if not (event.ctrl or event.alt or event.oskey):
                zoom_key = 'MINUS'
        elif not S.preview_mode:
            if t == 'N' or event.unicode in {'n', 'N'}:
                if not (event.ctrl or event.alt or event.oskey):
                    zoom_key = 'PLUS'
                elif v == 'RELEASE' and 'PLUS' in S.nav.held_zoom:
                    zoom_key = 'PLUS'
            elif t == 'M' or event.unicode in {'m', 'M'}:
                if not (event.ctrl or event.alt or event.oskey):
                    zoom_key = 'MINUS'
                elif v == 'RELEASE' and 'MINUS' in S.nav.held_zoom:
                    zoom_key = 'MINUS'

        if zoom_key and not (area and area.type in PASS_KEYS_IN) and not transform_active(win):
            S.nav.on_key(zoom_key, v)
            return {'RUNNING_MODAL'}

        nav_key = None
        if t in {'NUMPAD_8', 'NUMPAD_2', 'NUMPAD_4', 'NUMPAD_6', 'UP_ARROW', 'DOWN_ARROW', 'LEFT_ARROW', 'RIGHT_ARROW'}:
            if not (event.ctrl or event.alt or event.oskey):
                nav_key = t
        elif t in {'K', 'J', 'H', 'L'}:
            if not (event.ctrl or event.alt or event.oskey or event.shift):
                nav_key = t
            elif v == 'RELEASE' and t in S.nav.held:
                nav_key = t

        if nav_key and not (area and area.type in PASS_KEYS_IN) and not transform_active(win):
            S.nav.on_key(nav_key, v, shift=event.shift)
            return {'RUNNING_MODAL'}


        if check_shortcut(prefs, 'hint_select', event, 'F', fallback_ctrl=False, allow_shift_alt_extend=True) and v == 'PRESS':
            target_area = area or area_at(win, mx, my) or area_by_ptr(win, S.hover_ptr) or context.area
            if target_area:
                if target_area.type != 'VIEW_3D':
                    return {'PASS_THROUGH'}
                if event.alt:
                    action = 'ALT'
                elif event.shift:
                    action = 'EXTEND'
                else:
                    action = 'NORMAL'
                self._start_hint(context, win, target_area, False, action, mx, my)
                return {'RUNNING_MODAL'}

        if check_shortcut(prefs, 'box_select', event, 'C', fallback_ctrl=False, allow_shift_alt_extend=True) and v == 'PRESS':
            target_area = area or area_at(win, mx, my) or area_by_ptr(win, S.hover_ptr) or context.area
            if target_area:
                if target_area.type != 'VIEW_3D':
                    return {'PASS_THROUGH'}
                action = 'EXTEND' if event.shift else 'NORMAL'
                self._start_hint(context, win, target_area, True, action, mx, my)
                return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}

    def _modal_hint(self, context, event, win, mx, my):
        t, v = event.type, event.value
        prefs = get_preferences(context)

        use_arrow_layout = bool(getattr(prefs, 'arrow_keys_layout', False)) if prefs else False
        if check_shortcut(prefs, 'cursor', event, 'NUMPAD_5', fallback_ctrl=True) and v == 'PRESS':
            self._cancel_hint(context, win)
            self._enter_cursor_mode(context)
            return {'RUNNING_MODAL'}

        if check_shortcut(prefs, 'preview', event, 'Y', fallback_ctrl=True) and v == 'PRESS':
            self._cancel_hint(context, win)
            S.preview_mode = True
            S.navigation_mode = False
            S.workspace_nav = False
            self.report({'INFO'}, "Vimender: PREVIEW MODE")
            self._set_status(context)
            tag_all()
            return {'RUNNING_MODAL'}

        if check_shortcut(prefs, 'nav', event, 'B', fallback_ctrl=True) and v == 'PRESS':
            self._cancel_hint(context, win)
            S.preview_mode = False
            S.navigation_mode = True
            S.workspace_nav = True
            target_a = area or area_at(win, mx, my) or (win.screen.areas[0] if win and win.screen and win.screen.areas else None)
            if target_a:
                S.hover_ptr = target_a.as_pointer()
            self.report({'INFO'}, "Vimender: NAVIGATION MODE")
            self._set_status(context)
            tag_all()
            return {'RUNNING_MODAL'}

        if S.mode == 'BOX2':
            if t in NUDGE_DIRS or (use_arrow_layout and t in ARROW_DIRS):
                S.smooth_box.on_key(t, v)
                return {'RUNNING_MODAL'}

            if (t in {'NUMPAD_PLUS', 'NUMPAD_MINUS'} or
                (use_arrow_layout and (t in {'PLUS', 'MINUS', 'EQUAL'} or event.unicode in {'+', '-'}))) and v == 'PRESS':
                change_sens(1 if (t in {'NUMPAD_PLUS', 'PLUS', 'EQUAL'} or event.unicode == '+') else -1)
                return {'RUNNING_MODAL'}

            if t in {'C', 'RET', 'NUMPAD_ENTER'} and v == 'PRESS':
                self._finish_box2(context, win, event.shift)
                return {'RUNNING_MODAL'}

            if t in {'ESC', 'F'} and v == 'PRESS':
                self._cancel_hint(context, win)
                return {'RUNNING_MODAL'}

            if t == 'BACK_SPACE' and v == 'PRESS':
                if S.typed:
                    S.typed = S.typed[:-1]
                else:
                    S.mode, S.box_a = 'BOX1', None
                    S.box_start_hint = None
                    S.smooth_box.active = False
                self._set_status(context)
                a = area_by_ptr(win, S.hint_ptr)
                if a:
                    a.tag_redraw()
                return {'RUNNING_MODAL'}

            if t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                if not S.smooth_box.held and math.hypot(S.smooth_box.vx, S.smooth_box.vy) < 1.0:
                    a = area_by_ptr(win, S.hint_ptr)
                    r = window_region(a) if a else None
                    if r:
                        S.mouse = (mx - r.x, my - r.y)
                        S.smooth_box.x = float(mx - r.x)
                        S.smooth_box.y = float(my - r.y)
                        a.tag_redraw()
                return {'RUNNING_MODAL'}

            if t == 'LEFTMOUSE' and v == 'PRESS':
                self._finish_box2(context, win, event.shift)
                return {'RUNNING_MODAL'}

            if t in MOUSE_CANCEL and v == 'PRESS':
                self._cancel_hint(context, win)
                return {'RUNNING_MODAL'}

            if t in LETTERS and not (event.ctrl or event.alt or event.oskey):
                if v == 'PRESS':
                    cand = S.typed + t.lower()
                    if any(h.label.startswith(cand) for h in S.hints):
                        S.typed = cand
                        full = next((h for h in S.hints if h.label == cand), None)
                        if full is not None:
                            self._fire(context, win, full, shift_held=event.shift)
                        else:
                            a = area_by_ptr(win, S.hint_ptr)
                            if a:
                                a.tag_redraw()
                return {'RUNNING_MODAL'}

            return {'RUNNING_MODAL'}

        if t in MOUSE_CANCEL and v in {'PRESS', 'NOTHING'}:
            self._cancel_hint(context, win)
            return {'PASS_THROUGH'}

        if t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            return {'PASS_THROUGH'}

        if (t in {'ESC', 'F'} or (S.mode == 'BOX1' and t == 'C')) and v == 'PRESS':
            self._cancel_hint(context, win)
            return {'RUNNING_MODAL'}

        if t == 'BACK_SPACE':
            if v == 'PRESS' and S.typed:
                S.typed = S.typed[:-1]
                a = area_by_ptr(win, S.hint_ptr)
                if a:
                    a.tag_redraw()
            return {'RUNNING_MODAL'}

        if t in LETTERS:
            if event.ctrl or event.alt or event.oskey:
                return {'PASS_THROUGH'}
            if v == 'PRESS':
                cand = S.typed + t.lower()
                if any(h.label.startswith(cand) for h in S.hints):
                    S.typed = cand
                    full = next((h for h in S.hints if h.label == cand), None)
                    if full is not None:
                        self._fire(context, win, full, shift_held=event.shift)
                    else:
                        a = area_by_ptr(win, S.hint_ptr)
                        if a:
                            a.tag_redraw()
            return {'RUNNING_MODAL'}

        if t in CONSUME_KEYS:
            return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}


class WM_OT_vim_search_object(bpy.types.Operator):
    bl_idname = "wm.vim_search_object"
    bl_label = "Search Objects and Locations"
    bl_description = "Search and focus on objects, 3D Cursor, or World Origin in the scene"
    bl_property = "target"
    bl_options = {'REGISTER', 'UNDO'}

    def get_items(self, context):
        items = [
            ("__CURSOR__", "3D Cursor", "Focus on 3D Cursor", 'PIVOT_CURSOR', 0),
            ("__ORIGIN__", "World Origin", "Focus on World Origin (0, 0, 0)", 'ORIENTATION_GLOBAL', 1),
        ]
        vl = context.view_layer if context else bpy.context.view_layer
        if vl:
            for i, o in enumerate(vl.objects, start=2):
                icon = 'OBJECT_DATA'
                if o.type == 'MESH':
                    icon = 'MESH_DATA'
                elif o.type == 'CAMERA':
                    icon = 'CAMERA_DATA'
                elif o.type == 'LIGHT':
                    icon = 'LIGHT_DATA'
                elif o.type == 'CURVE':
                    icon = 'CURVE_DATA'
                elif o.type == 'EMPTY':
                    icon = 'EMPTY_DATA'
                elif o.type == 'ARMATURE':
                    icon = 'ARMATURE_DATA'
                items.append((o.name, o.name, f"{o.type.capitalize()}: {o.name}", icon, i))
        return items

    target: bpy.props.EnumProperty(
        name="Target",
        description="Target object or location to select",
        items=get_items,
    )

    def execute(self, context):
        tgt = self.target
        win = context.window

        if tgt == "__CURSOR__":
            for a in win.screen.areas:
                if a.type == 'VIEW_3D':
                    for r in a.regions:
                        if r.type == 'WINDOW':
                            with context.temp_override(window=win, area=a, region=r):
                                try:
                                    bpy.ops.view3d.view_center_cursor()
                                except Exception:
                                    rv3d = getattr(a.spaces.active, 'region_3d', None)
                                    if rv3d:
                                        rv3d.view_location = context.scene.cursor.location.copy()
                            a.tag_redraw()
            self.report({'INFO'}, "Vimender: Focused on 3D Cursor")
            return {'FINISHED'}

        if tgt == "__ORIGIN__":
            for a in win.screen.areas:
                if a.type == 'VIEW_3D':
                    for r in a.regions:
                        if r.type == 'WINDOW':
                            rv3d = getattr(a.spaces.active, 'region_3d', None)
                            if rv3d:
                                rv3d.view_location = Vector((0.0, 0.0, 0.0))
                            a.tag_redraw()
            self.report({'INFO'}, "Vimender: Focused on World Origin (0, 0, 0)")
            return {'FINISHED'}

        obj = bpy.data.objects.get(tgt)
        if obj:
            vl = context.view_layer
            for o in vl.objects:
                o.select_set(False)
            obj.select_set(True)
            vl.objects.active = obj

            for a in win.screen.areas:
                if a.type == 'VIEW_3D':
                    for r in a.regions:
                        if r.type == 'WINDOW':
                            with context.temp_override(window=win, area=a, region=r):
                                try:
                                    bpy.ops.view3d.view_selected(use_all_regions=False)
                                except Exception:
                                    rv3d = getattr(a.spaces.active, 'region_3d', None)
                                    if rv3d:
                                        rv3d.view_location = obj.matrix_world.translation.copy()
                            a.tag_redraw()
            self.report({'INFO'}, f"Vimender: Selected and focused on '{obj.name}'")
            return {'FINISHED'}

        return {'CANCELLED'}

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'FINISHED'}


def register_search_key():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc is None:
        return
    km = kc.keymaps.new('Window', space_type='EMPTY', region_type='WINDOW')
    kmi1 = km.keymap_items.new(WM_OT_vim_search_object.bl_idname, 'SPACE', 'PRESS', ctrl=True)
    kmi2 = km.keymap_items.new(WM_OT_vim_search_object.bl_idname, 'F3', 'PRESS', ctrl=True)
    _addon_keymaps.extend([(km, kmi1), (km, kmi2)])


def unregister_search_key():
    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _addon_keymaps.clear()


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
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        existing.update(data)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        print(f"[Vimender] Config save error: {e}")


def load_vimender_config() -> dict:
    try:
        p = get_config_path()
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
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
def _on_load_post(dummy):
    if bpy.app.background:
        return
    if load_vimender_state() and not S.active:
        bpy.app.timers.register(_auto_start, first_interval=0.1)


def _menu_func(self, context):
    self.layout.separator()
    self.layout.operator(WM_OT_vim_mode_toggle.bl_idname, text="Vimender Mode",
                         icon='CHECKBOX_HLT' if S.active else 'CHECKBOX_DEHLT')



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
    if context is None:
        context = bpy.context
    try:
        addon_name = __package__ or __name__.partition('.')[0] or "Vimender"
        addon = context.preferences.addons.get(addon_name)
        if addon:
            return addon.preferences
    except Exception:
        pass
    return None


def check_shortcut(prefs, prefix, event, fallback_key, fallback_ctrl=False, fallback_shift=False, fallback_alt=False, allow_shift_alt_extend=False):
    if prefs is not None:
        target_key = getattr(prefs, f"key_{prefix}", fallback_key)
        target_ctrl = getattr(prefs, f"ctrl_{prefix}", fallback_ctrl)
        target_shift = getattr(prefs, f"shift_{prefix}", fallback_shift)
        target_alt = getattr(prefs, f"alt_{prefix}", fallback_alt)
    else:
        target_key = fallback_key
        target_ctrl = fallback_ctrl
        target_shift = fallback_shift
        target_alt = fallback_alt

    if event.type != target_key:
        return False
    if bool(event.ctrl) != bool(target_ctrl):
        return False
    if not allow_shift_alt_extend:
        if bool(event.shift) != bool(target_shift):
            return False
        if bool(event.alt) != bool(target_alt):
            return False
    else:
        if target_shift and not event.shift:
            return False
        if target_alt and not event.alt:
            return False
    if getattr(event, 'oskey', False):
        return False
    return True


class VimenderPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    key_preview: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='Y')
    ctrl_preview: bpy.props.BoolProperty(name="Ctrl", default=True)
    shift_preview: bpy.props.BoolProperty(name="Shift", default=False)
    alt_preview: bpy.props.BoolProperty(name="Alt", default=False)

    key_nav: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='B')
    ctrl_nav: bpy.props.BoolProperty(name="Ctrl", default=True)
    shift_nav: bpy.props.BoolProperty(name="Shift", default=False)
    alt_nav: bpy.props.BoolProperty(name="Alt", default=False)

    key_cursor: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='NUMPAD_5')
    ctrl_cursor: bpy.props.BoolProperty(name="Ctrl", default=True)
    shift_cursor: bpy.props.BoolProperty(name="Shift", default=False)
    alt_cursor: bpy.props.BoolProperty(name="Alt", default=False)

    key_orbit_pan: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='U')
    ctrl_orbit_pan: bpy.props.BoolProperty(name="Ctrl", default=False)
    shift_orbit_pan: bpy.props.BoolProperty(name="Shift", default=False)
    alt_orbit_pan: bpy.props.BoolProperty(name="Alt", default=False)

    key_editor_pie: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='W')
    ctrl_editor_pie: bpy.props.BoolProperty(name="Ctrl", default=True)
    shift_editor_pie: bpy.props.BoolProperty(name="Shift", default=False)
    alt_editor_pie: bpy.props.BoolProperty(name="Alt", default=False)

    key_search_menu: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='SPACE')
    ctrl_search_menu: bpy.props.BoolProperty(name="Ctrl", default=False)
    shift_search_menu: bpy.props.BoolProperty(name="Shift", default=False)
    alt_search_menu: bpy.props.BoolProperty(name="Alt", default=False)

    key_search_object: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='SPACE')
    ctrl_search_object: bpy.props.BoolProperty(name="Ctrl", default=True)
    shift_search_object: bpy.props.BoolProperty(name="Shift", default=False)
    alt_search_object: bpy.props.BoolProperty(name="Alt", default=False)

    key_hint_select: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='F')
    ctrl_hint_select: bpy.props.BoolProperty(name="Ctrl", default=False)
    shift_hint_select: bpy.props.BoolProperty(name="Shift", default=False)
    alt_hint_select: bpy.props.BoolProperty(name="Alt", default=False)

    key_box_select: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='C')
    ctrl_box_select: bpy.props.BoolProperty(name="Ctrl", default=False)
    shift_box_select: bpy.props.BoolProperty(name="Shift", default=False)
    alt_box_select: bpy.props.BoolProperty(name="Alt", default=False)

    key_persp_ortho: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='Y')
    ctrl_persp_ortho: bpy.props.BoolProperty(name="Ctrl", default=False)
    shift_persp_ortho: bpy.props.BoolProperty(name="Shift", default=False)
    alt_persp_ortho: bpy.props.BoolProperty(name="Alt", default=False)

    key_view_selected: bpy.props.EnumProperty(name="Key", items=KEY_ITEMS, default='NUMPAD_PERIOD')
    ctrl_view_selected: bpy.props.BoolProperty(name="Ctrl", default=False)
    shift_view_selected: bpy.props.BoolProperty(name="Shift", default=False)
    alt_view_selected: bpy.props.BoolProperty(name="Alt", default=False)

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

    def draw(self, context):
        layout = self.layout

        box0 = layout.box()
        box0.label(text="Keyboard Layout", icon='KEYBOARD')
        box0.prop(self, "arrow_keys_layout", text="Arrow Keys Layout (Laptop / No Numpad)")

        box = layout.box()
        box.label(text="Mode Switching Shortcuts", icon='ARROW_LEFTRIGHT')
        self._draw_shortcut(box, "Preview Mode Toggle", "preview")
        self._draw_shortcut(box, "Navigation Mode Toggle", "nav")
        self._draw_shortcut(box, "Cursor Mode Toggle", "cursor")
        self._draw_shortcut(box, "Orbit / Pan Mode Toggle", "orbit_pan")
        self._draw_shortcut(box, "Editor Pie Menu", "editor_pie")

        box2 = layout.box()
        box2.label(text="Tool & General Shortcuts", icon='EVENT_F')
        self._draw_shortcut(box2, "Search Menu", "search_menu")
        self._draw_shortcut(box2, "Search Objects / Cursor", "search_object")
        self._draw_shortcut(box2, "Hint Select", "hint_select")
        self._draw_shortcut(box2, "Box Hint Select", "box_select")
        self._draw_shortcut(box2, "Persp / Ortho View", "persp_ortho")
        self._draw_shortcut(box2, "Focus Selected View", "view_selected")

        box3 = layout.box()
        box3.label(text="General Settings", icon='PREFERENCES')
        box3.prop(self, "auto_start", text="Auto-Start Vimender on Launch")


classes = (
    VimenderPreferences,
    WM_OT_vim_mode_toggle,
    WM_OT_vim_nudge,
    WM_OT_vim_set_editor,
    WM_OT_vim_search_object,
    VIM_MT_editor_more,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_window.append(_menu_func)
    clean_modal_keymaps()
    register_search_key()
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    if not bpy.app.background and load_vimender_state():
        bpy.app.timers.register(_auto_start, first_interval=0.1)


def unregister():
    shutdown()
    clean_modal_keymaps()
    unregister_search_key()
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    bpy.types.TOPBAR_MT_window.remove(_menu_func)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()

