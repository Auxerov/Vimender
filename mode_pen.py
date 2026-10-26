import math
import time
import bpy
from mathutils import Vector
from .state import (
    S, WHITE, tag_all, get_preferences, check_shortcut,
    set_active_mode, area_at, area_by_ptr, window_region, SENS_LEVELS
)

VIEW_SENSOR_MM = 36.0


class _PenNavigator:
    """
    Dedicated navigator for Pen Mode:
    - Normal mouse/stylus inputs pass through 100% to Blender.
    - Two-finger hand pinching zooms the viewport smoothly around the midpoint anchor.
    - Single finger touches (palm rejection, accidental taps while drawing) are strictly
      ignored so drawing/sculpting with the pen is never interrupted.
    - Zero coasting/inertia on release to keep the 3D viewport firmly stable for the pen.
    """
    def __init__(self):
        self._last_t = 0.0
        self._anchor = (0.0, 0.0)

    def reset(self):
        self._last_t = 0.0
        self._anchor = (0.0, 0.0)

    def tick(self, context=None, win=None):
        try:
            from .mode_tablet import _tracker
        except Exception:
            return False

        if not getattr(_tracker, 'active', False):
            return False

        if context is None:
            context = bpy.context
        if win is None:
            win = context.window

        area = (
            area_by_ptr(win, getattr(S, 'pen_ptr', 0)) or
            area_by_ptr(win, S.hover_ptr) or
            next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None) if win and win.screen else None
        )
        if not area or area.type != 'VIEW_3D':
            return False

        space = area.spaces.active if area.spaces else None
        rv3d = space.region_3d if space else None
        reg = window_region(area)
        if not rv3d or not reg:
            return False

        with _tracker.lock:



            _tracker.orbit_delta[0] = _tracker.orbit_delta[1] = 0.0
            _tracker.transform_delta[0] = _tracker.transform_delta[1] = 0.0
            _tracker.twist = 0.0
            _tracker.pending_undo = False
            _tracker.pending_redo = False

            fingers = _tracker.finger_count
            pdx, pdy = _tracker.pan_delta
            _tracker.pan_delta[0] = _tracker.pan_delta[1] = 0.0
            zlog = _tracker.zoom_log
            _tracker.zoom_log = 0.0
            mid_n = _tracker.mid_n

        now = time.perf_counter()
        dt = min(max(now - self._last_t, 0.001), 0.05) if self._last_t else 0.016
        self._last_t = now

        W = max(float(win.width), 1.0)
        H = max(float(win.height), 1.0)


        dpx = pdx * W
        dpy = -pdy * H

        if fingers != 2 and not (dpx or dpy or zlog):
            return False

        if mid_n is not None:
            self._anchor = (
                mid_n[0] * W - reg.x - reg.width * 0.5,
                (1.0 - mid_n[1]) * H - reg.y - reg.height * 0.5
            )

        changed = False
        if dpx or dpy or zlog:
            if rv3d.view_perspective == 'CAMERA' and not getattr(space, 'lock_camera', False):
                rv3d.view_perspective = 'PERSP'

            rot = rv3d.view_rotation
            d = rv3d.view_distance

            sens = SENS_LEVELS[S.sens_idx] if hasattr(S, 'sens_idx') else 1.0
            effective_zlog = zlog * 1.5 * sens
            effective_dpx = dpx * sens
            effective_dpy = dpy * sens


            f = max(0.2, min(5.0, math.exp(-effective_zlog)))
            d_new = max(d * f, 0.001)
            f = d_new / d

            lens = max(getattr(space, 'lens', 50.0), 1.0)
            u = d * VIEW_SENSOR_MM / (lens * max(reg.width, reg.height, 1))
            right = rot @ Vector((1.0, 0.0, 0.0))
            up = rot @ Vector((0.0, 1.0, 0.0))
            sx, sy = self._anchor
            s_cur = (right * sx + up * sy) * u
            s_prev = (right * (sx - effective_dpx) + up * (sy - effective_dpy)) * u


            rv3d.view_location = rv3d.view_location + s_prev - s_cur * f
            rv3d.view_distance = d_new
            changed = True

        return changed


def enter_pen_mode(context, report_fn=None):
    set_active_mode('PEN', context, report_fn)


def exit_pen_mode(context, report_fn=None):
    set_active_mode('PREVIEW', context, report_fn)


def handle_pen_event(op, context, event, win, area):
    t, v = event.type, event.value
    prefs = get_preferences(context)

    if t == 'ESC' and v == 'PRESS':
        exit_pen_mode(context, op.report)
        return {'RUNNING_MODAL'}

    if check_shortcut(prefs, 'hint_select', event, 'F', fallback_ctrl=False, allow_shift_alt_extend=True) and v == 'PRESS':
        target_area = area or area_at(win, event.mouse_x, event.mouse_y) or area_by_ptr(win, getattr(S, 'pen_ptr', 0)) or area_by_ptr(win, S.hover_ptr) or context.area
        if not target_area or target_area.type != 'VIEW_3D':
            target_area = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), context.area) if (win and win.screen) else context.area
        if target_area and target_area.type == 'VIEW_3D':
            if event.alt:
                action = 'ALT'
            elif event.shift:
                action = 'EXTEND'
            else:
                action = 'NORMAL'
            op._start_hint(context, win, target_area, False, action, event.mouse_x, event.mouse_y)
            return {'RUNNING_MODAL'}

    if check_shortcut(prefs, 'box_select', event, 'C', fallback_ctrl=False, allow_shift_alt_extend=True) and v == 'PRESS':
        target_area = area or area_at(win, event.mouse_x, event.mouse_y) or area_by_ptr(win, getattr(S, 'pen_ptr', 0)) or area_by_ptr(win, S.hover_ptr) or context.area
        if not target_area or target_area.type != 'VIEW_3D':
            target_area = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), context.area) if (win and win.screen) else context.area
        if target_area and target_area.type == 'VIEW_3D':
            action = 'EXTEND' if event.shift else 'NORMAL'
            op._start_hint(context, win, target_area, True, action, event.mouse_x, event.mouse_y)
            return {'RUNNING_MODAL'}


    return {'PASS_THROUGH'}
