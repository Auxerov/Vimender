import math
import time
import bpy
from mathutils import Vector
from .state import (
    S, PURPLE, CURSOR_MAX_SPEED, CURSOR_ACCEL_RATE, CURSOR_FRICTION,
    CURSOR_WRAP_MARGIN, CURSOR_3D_SPEED, SENS_LEVELS, tag_all, window_region,
    change_sens, is_altgr_pressed, check_shortcut, get_preferences, set_active_mode
)

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
        rv3d = None
        if area and area.type == 'VIEW_3D':
            rv3d = area.spaces.active.region_3d if area.spaces else None
            if rv3d:
                dist_factor = max(0.25, min(rv3d.view_distance / 5.0, 4.0))

        speed = CURSOR_3D_SPEED * sens * dist_factor
        if self.shift:
            speed *= 0.15

        if S.cursor_plane == 'VIEW':
            if rv3d:
                view_mat = rv3d.view_matrix.inverted()
                view_r = (view_mat.to_3x3() @ Vector((1.0, 0.0, 0.0))).normalized()
                view_u = (view_mat.to_3x3() @ Vector((0.0, 1.0, 0.0))).normalized()
                delta = view_r * (self.vx * speed * dt) + view_u * (self.vy * speed * dt)
            else:
                delta = Vector((self.vx * speed * dt, self.vy * speed * dt, 0.0))
        elif S.cursor_plane == 'XZ':
            delta = Vector((self.vx * speed * dt, 0.0, self.vy * speed * dt))
        elif S.cursor_plane in {'YZ', 'ZY'}:
            delta = Vector((0.0, self.vx * speed * dt, self.vy * speed * dt))
        else:
            delta = Vector((self.vx * speed * dt, self.vy * speed * dt, 0.0))
        try:
            context.scene.cursor.location = context.scene.cursor.location + delta
            for a in win.screen.areas:
                if a.type == 'VIEW_3D':
                    a.tag_redraw()
            return True
        except Exception:
            return False



S.mover = _SmoothCursor()
S.smooth_cursor = _Smooth3DCursor()

def enter_cursor_mode(context, report_fn=None):
    set_active_mode('CURSOR', context, report_fn)


def exit_cursor_mode(context, report_fn=None):
    set_active_mode('PREVIEW', context, report_fn)


S.exit_cursor_fn = exit_cursor_mode


def handle_cursor_event(op, context, event, win, area):
    t, v = event.type, event.value
    prefs = get_preferences(context)

    if ((t == 'SPACE' and is_altgr_pressed(event)) or
        check_shortcut(prefs, 'fuzzy_search', event, 'SPACE', fallback_ctrl=True, fallback_alt=True)) and v == 'PRESS':
        try:
            with context.temp_override(window=win):
                bpy.ops.wm.vim_fuzzy_search('INVOKE_DEFAULT')
        except Exception as e:
            print(f"[Vimender] fuzzy_search error: {e}")
        return {'RUNNING_MODAL'}

    if (check_shortcut(prefs, 'search_mode', event, 'SPACE', fallback_alt=True) or
        (t == 'SPACE' and event.alt and not (event.ctrl or event.shift or event.oskey))) and v == 'PRESS':
        try:
            with context.temp_override(window=win):
                bpy.ops.wm.vim_search_mode('INVOKE_DEFAULT')
        except Exception as e:
            print(f"[Vimender] search_mode error: {e}")
        return {'RUNNING_MODAL'}

    if (t in {'TAB', 'NUMPAD_5'} or (t == 'P' and not (event.ctrl or event.alt or event.oskey))) and v == 'PRESS':
        planes = ('XY', 'VIEW', 'XZ', 'YZ')
        idx = (planes.index(S.cursor_plane) + 1) % len(planes) if S.cursor_plane in planes else 0
        S.cursor_plane = planes[idx]
        S.smooth_cursor.reset()
        op.report({'INFO'}, f"Vimender: CURSOR MODE - {S.cursor_plane}")
        try:
            context.workspace.status_text_set(f"Vimender: CURSOR MODE [{S.cursor_plane}] | HJKL: Move | TAB: Plane | 0: Origin | S: Snap | Esc: Exit")
        except Exception:
            pass
        tag_all()
        return {'RUNNING_MODAL'}

    if t in {'V', 'X', 'Z', 'Y'} and v == 'PRESS' and not (event.ctrl or event.alt or event.oskey or event.shift):
        if t == 'V':
            S.cursor_plane = 'VIEW'
        elif t == 'X':
            S.cursor_plane = 'XY'
        elif t == 'Z':
            S.cursor_plane = 'XZ'
        elif t == 'Y':
            S.cursor_plane = 'YZ'
        S.smooth_cursor.reset()
        op.report({'INFO'}, f"Vimender: CURSOR MODE - {S.cursor_plane}")
        try:
            context.workspace.status_text_set(f"Vimender: CURSOR MODE [{S.cursor_plane}] | HJKL: Move | TAB: Plane | 0: Origin | S: Snap | Esc: Exit")
        except Exception:
            pass
        tag_all()
        return {'RUNNING_MODAL'}

    if t in {'ZERO', 'NUMPAD_0', 'O'} and v == 'PRESS' and not (event.ctrl or event.alt or event.oskey):
        context.scene.cursor.location = Vector((0.0, 0.0, 0.0))
        tag_all()
        op.report({'INFO'}, "Vimender: Cursor snapped to World Origin (0,0,0)")
        return {'RUNNING_MODAL'}

    if t == 'S' and v == 'PRESS' and not (event.ctrl or event.alt or event.oskey):
        try:
            with context.temp_override(window=win, area=area):
                bpy.ops.view3d.snap_cursor_to_selected()
            tag_all()
            op.report({'INFO'}, "Vimender: Cursor snapped to Selected")
        except Exception:
            pass
        return {'RUNNING_MODAL'}

    if t in {'C', 'PERIOD', 'NUMPAD_PERIOD'} and v == 'PRESS' and not (event.ctrl or event.alt or event.oskey or event.shift):
        try:
            with context.temp_override(window=win, area=area):
                bpy.ops.view3d.view_center_cursor()
            tag_all()
            op.report({'INFO'}, "Vimender: View centered on Cursor")
        except Exception:
            pass
        return {'RUNNING_MODAL'}

    CURSOR_MOVE_KEYS = {
        'H', 'J', 'K', 'L',
        'NUMPAD_8', 'NUMPAD_2', 'NUMPAD_4', 'NUMPAD_6',
        'UP_ARROW', 'DOWN_ARROW', 'LEFT_ARROW', 'RIGHT_ARROW'
    }
    if t in CURSOR_MOVE_KEYS:
        if not (event.ctrl or event.alt or event.oskey):
            S.smooth_cursor.on_key(t, v, shift=event.shift)
            return {'RUNNING_MODAL'}
        elif v == 'RELEASE' and t in S.smooth_cursor.held:
            S.smooth_cursor.on_key(t, v, shift=event.shift)
            return {'RUNNING_MODAL'}

    if t in {'LEFT_SHIFT', 'RIGHT_SHIFT'}:
        S.smooth_cursor.shift = (v == 'PRESS')
        return {'RUNNING_MODAL'}

    if (t in {'NUMPAD_PLUS', 'NUMPAD_MINUS', 'PLUS', 'MINUS', 'EQUAL', 'PAGE_UP', 'PAGE_DOWN'} or
        (event.unicode in {'+', '-'} and not (event.alt or event.oskey))) and v == 'PRESS':
        is_up = t in {'NUMPAD_PLUS', 'PLUS', 'EQUAL', 'PAGE_UP'} or event.unicode == '+'
        change_sens(1 if is_up else -1)
        return {'RUNNING_MODAL'}

    if t in {'ESC', 'RET', 'NUMPAD_ENTER', 'SPACE'} and v == 'PRESS' and not (event.ctrl or event.alt or event.oskey):
        exit_cursor_mode(context, op.report)
        return {'RUNNING_MODAL'}

    if t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
        return {'PASS_THROUGH'}

    return {'RUNNING_MODAL'}
