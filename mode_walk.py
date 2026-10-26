import math
import time
import bpy
from mathutils import Vector, Quaternion
from .state import (
    S, ORANGE, WALK_MOVE_SPEED, WALK_LOOK_SPEED, NAV_ACCEL_RATE, NAV_FRICTION,
    SENS_LEVELS, tag_all, MOUSE_CANCEL, change_sens, area_by_ptr, set_active_mode
)

class _SmoothWalker:
    def __init__(self):
        self.held_move = set()
        self.held_look = set()
        self.shift = False
        self.vx = 0.0
        self.vy = 0.0
        self.vz = 0.0
        self.yaw_v = 0.0
        self.pitch_v = 0.0
        self.last_t = 0.0

    def reset(self):
        self.held_move.clear()
        self.held_look.clear()
        self.shift = False
        self.vx = 0.0
        self.vy = 0.0
        self.vz = 0.0
        self.yaw_v = 0.0
        self.pitch_v = 0.0
        self.last_t = 0.0

    def on_key(self, key, val, shift=False):
        self.shift = shift
        move_keys = {'W', 'S', 'A', 'D', 'Q', 'E'}
        look_keys = {'H', 'J', 'K', 'L'}
        if key in move_keys:
            if val == 'PRESS':
                if not self.held_move and math.hypot(self.vx, self.vy, self.vz) < 1e-3:
                    self.last_t = time.perf_counter()
                self.held_move.add(key)
            elif val == 'RELEASE':
                self.held_move.discard(key)
        elif key in look_keys:
            if val == 'PRESS':
                if not self.held_look and math.hypot(self.yaw_v, self.pitch_v) < 1e-3:
                    self.last_t = time.perf_counter()
                self.held_look.add(key)
            elif val == 'RELEASE':
                self.held_look.discard(key)

    def tick(self, context, win):
        if not S.walk_mode:
            return False
        now = time.perf_counter()
        if self.last_t == 0.0:
            self.last_t = now
        dt = min(max(now - self.last_t, 0.001), 0.05)
        self.last_t = now

        target_yaw = (+1.0 if 'H' in self.held_look else 0.0) + (-1.0 if 'L' in self.held_look else 0.0)
        target_pitch = (+1.0 if 'K' in self.held_look else 0.0) + (-1.0 if 'J' in self.held_look else 0.0)

        if target_yaw != 0.0:
            alpha = 1.0 - math.exp(-NAV_ACCEL_RATE * dt)
            self.yaw_v += (target_yaw - self.yaw_v) * alpha
        else:
            self.yaw_v *= math.exp(-NAV_FRICTION * dt)
            if abs(self.yaw_v) < 0.015:
                self.yaw_v = 0.0

        if target_pitch != 0.0:
            alpha = 1.0 - math.exp(-NAV_ACCEL_RATE * dt)
            self.pitch_v += (target_pitch - self.pitch_v) * alpha
        else:
            self.pitch_v *= math.exp(-NAV_FRICTION * dt)
            if abs(self.pitch_v) < 0.015:
                self.pitch_v = 0.0

        target_fwd = (+1.0 if 'W' in self.held_move else 0.0) + (-1.0 if 'S' in self.held_move else 0.0)
        target_strafe = (+1.0 if 'D' in self.held_move else 0.0) + (-1.0 if 'A' in self.held_move else 0.0)
        target_up = (+1.0 if 'E' in self.held_move else 0.0) + (-1.0 if 'Q' in self.held_move else 0.0)

        if target_fwd != 0.0 or target_strafe != 0.0:
            mag = math.hypot(target_fwd, target_strafe)
            tf, ts = target_fwd / mag, target_strafe / mag
            alpha = 1.0 - math.exp(-NAV_ACCEL_RATE * dt)
            self.vy += (tf - self.vy) * alpha
            self.vx += (ts - self.vx) * alpha
        else:
            decay = math.exp(-NAV_FRICTION * dt)
            self.vx *= decay
            self.vy *= decay
            if math.hypot(self.vx, self.vy) < 0.015:
                self.vx = 0.0
                self.vy = 0.0

        if target_up != 0.0:
            alpha = 1.0 - math.exp(-NAV_ACCEL_RATE * dt)
            self.vz += (target_up - self.vz) * alpha
        else:
            self.vz *= math.exp(-NAV_FRICTION * dt)
            if abs(self.vz) < 0.015:
                self.vz = 0.0

        active_moving = (abs(self.vx) > 0.005 or abs(self.vy) > 0.005 or abs(self.vz) > 0.005)
        active_looking = (abs(self.yaw_v) > 0.005 or abs(self.pitch_v) > 0.005)
        if not active_moving and not active_looking:
            return False

        area = area_by_ptr(win, S.walk_ptr or S.hover_ptr) or context.area
        if not area or area.type != 'VIEW_3D':
            area = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None)
        if not area or area.type != 'VIEW_3D':
            return False

        rv3d = area.spaces.active.region_3d if area.spaces else None
        if not rv3d:
            return False

        if rv3d.view_perspective == 'CAMERA' and not getattr(area.spaces.active, 'lock_camera', False):
            rv3d.view_perspective = 'PERSP'

        sens = SENS_LEVELS[S.sens_idx]
        moved = False

        if active_looking:
            look_spd = WALK_LOOK_SPEED * sens
            yaw_angle = self.yaw_v * look_spd * dt
            pitch_angle = self.pitch_v * look_spd * dt

            eye = rv3d.view_location + (rv3d.view_rotation @ Vector((0.0, 0.0, rv3d.view_distance)))

            q_yaw = Quaternion(Vector((0.0, 0.0, 1.0)), yaw_angle)
            right_axis = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
            q_pitch = Quaternion(right_axis, pitch_angle)

            test_pitch_rot = (q_pitch @ rv3d.view_rotation).normalized()
            up_z = (test_pitch_rot @ Vector((0.0, 1.0, 0.0))).z
            if up_z > 0.05:
                new_rot = (q_yaw @ test_pitch_rot).normalized()
            else:
                new_rot = (q_yaw @ rv3d.view_rotation).normalized()

            rv3d.view_rotation = new_rot
            rv3d.view_location = eye - (new_rot @ Vector((0.0, 0.0, rv3d.view_distance)))
            moved = True

        if active_moving:
            dist = max(rv3d.view_distance, 0.2)
            move_spd = WALK_MOVE_SPEED * sens * dist
            if self.shift:
                move_spd *= 2.5

            fwd = (rv3d.view_rotation @ Vector((0.0, 0.0, -1.0)))
            right = (rv3d.view_rotation @ Vector((1.0, 0.0, 0.0)))
            up = Vector((0.0, 0.0, 1.0))

            delta = (fwd * self.vy + right * self.vx + up * self.vz) * (move_spd * dt)
            rv3d.view_location = rv3d.view_location + delta
            moved = True

        if moved:
            area.tag_redraw()
        return moved


S.smooth_walker = _SmoothWalker()



_PRESS_ALIAS = {}

def enter_walk_mode(context, area, report_fn=None):
    set_active_mode('WALK', context, report_fn, target_area=area)


def exit_walk_mode(context, report_fn=None):
    set_active_mode('PREVIEW', context, report_fn)


S.exit_walk_fn = exit_walk_mode


def handle_walk_event(op, context, event, win, area):
    t, v = event.type, event.value
    if t in {'ESC', 'RET', 'NUMPAD_ENTER'} and v == 'PRESS':
        exit_walk_mode(context, op.report)
        return {'RUNNING_MODAL'}

    if t in {'LEFTMOUSE', 'RIGHTMOUSE'} and v == 'PRESS':
        exit_walk_mode(context, op.report)
        return {'RUNNING_MODAL'}

    if event.ctrl or event.alt or getattr(event, 'oskey', False):
        return {'PASS_THROUGH'}

    if t in {'U', 'P', 'NUMPAD_5'} or (event.unicode or '').upper() in {'U', 'P'}:
        return {'PASS_THROUGH'}

    WALK_MOVE_KEYS = {'W', 'S', 'A', 'D', 'Q', 'E'}
    WALK_LOOK_KEYS = {'H', 'J', 'K', 'L'}
    UNICODE_WALK_MAP = {
        'w': 'W', 'W': 'W', 's': 'S', 'S': 'S',
        'a': 'A', 'A': 'A', 'd': 'D', 'D': 'D',
        'q': 'Q', 'Q': 'Q', 'e': 'E', 'E': 'E',
        'h': 'H', 'H': 'H', 'j': 'J', 'J': 'J',
        'k': 'K', 'K': 'K', 'l': 'L', 'L': 'L',
    }
    key_matched = t if (t in WALK_MOVE_KEYS or t in WALK_LOOK_KEYS) else UNICODE_WALK_MAP.get(event.unicode)
    if v == 'PRESS' and key_matched and key_matched != t:
        _PRESS_ALIAS[t] = key_matched
    elif v == 'RELEASE' and t in _PRESS_ALIAS:
        key_matched = _PRESS_ALIAS.pop(t)
    if key_matched:
        if not (event.ctrl or event.alt or event.oskey):
            S.smooth_walker.on_key(key_matched, v, shift=event.shift)
            return {'RUNNING_MODAL'}
        elif v == 'RELEASE':
            S.smooth_walker.on_key(key_matched, v, shift=event.shift)
            return {'RUNNING_MODAL'}

    if t in {'LEFT_SHIFT', 'RIGHT_SHIFT'}:
        S.smooth_walker.shift = (v == 'PRESS')
        return {'RUNNING_MODAL'}

    is_sens_key = (
        t in {'NUMPAD_PLUS', 'NUMPAD_MINUS', 'PLUS', 'MINUS', 'EQUAL', 'PAGE_UP', 'PAGE_DOWN', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} or
        (t in {'N', 'M'} or (event.unicode or '').upper() in {'N', 'M'}) or
        (event.unicode in {'+', '-'} and not (event.alt or event.oskey))
    ) and not (event.ctrl or event.alt or event.oskey)

    if is_sens_key and v == 'PRESS':
        is_up = (
            t in {'NUMPAD_PLUS', 'PLUS', 'EQUAL', 'PAGE_UP', 'N', 'WHEELUPMOUSE'} or
            (event.unicode or '').upper() in {'+', 'N'}
        )
        delta = 1 if is_up else -1
        change_sens(delta)
        sens_val = SENS_LEVELS[S.sens_idx]
        op.report({'INFO'}, f"Vimender: Walk Sensitivity {sens_val:.2f}x")
        try:
            context.workspace.status_text_set(
                f"Vimender: WALK MODE [{sens_val:.2f}x] | WASD: Move | HJKL: Look | N/M: Sens (+/-) | Q/E: Down/Up | Alt+Q/Esc: Exit"
            )
        except Exception:
            pass
        tag_all()
        return {'RUNNING_MODAL'}

    if t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
        return {'PASS_THROUGH'}

    return {'RUNNING_MODAL'}

