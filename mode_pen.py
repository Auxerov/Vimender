import math
import time
import bpy
from mathutils import Vector, Quaternion, Euler
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
            from .mode_tablet import _tracker, _stylus_tracker
        except Exception:
            return False

        if not getattr(_tracker, 'active', False):
            return False

        # Inhibit pinch-zoom and pan if pen is actively touching the glass
        if getattr(_stylus_tracker, 'is_pen_touching', None) and _stylus_tracker.is_pen_touching():
            with _tracker.lock:
                _tracker.pan_delta[0] = _tracker.pan_delta[1] = 0.0
                _tracker.zoom_log = 0.0
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

        # If not 2 fingers, discard any gesture motion to prevent single-finger / palm drift
        if fingers != 2:
            self._anchor = (0.0, 0.0)
            return False

        now = time.perf_counter()
        dt = min(max(now - self._last_t, 0.001), 0.05) if self._last_t else 0.016
        self._last_t = now

        W = max(float(win.width), 1.0)
        H = max(float(win.height), 1.0)

        dpx = pdx * W
        dpy = -pdy * H

        if not (dpx or dpy or zlog):
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


MOUSE_TOUCH_SWALLOW_EVENTS = {
    'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE',
    'BUTTON4MOUSE', 'BUTTON5MOUSE', 'BUTTON6MOUSE', 'BUTTON7MOUSE',
    'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE',
    'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE', 'MOUSESMARTZOOM',
    'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE',
}


def apply_pen_mmb_navigation(context, win, area, dx, dy, is_pan):
    space = area.spaces.active if area.spaces else None
    rv3d = space.region_3d if space else None
    reg = window_region(area)
    if not rv3d or not reg:
        return

    prefs = get_preferences(context)
    rot = rv3d.view_rotation
    d = rv3d.view_distance

    if is_pan:
        # Shift + Middle Click Pan
        pan_sens = float(getattr(prefs, 'pen_pan_sensitivity', 1.75)) if prefs else 1.75
        lens = max(getattr(space, 'lens', 50.0), 1.0)
        u = d * VIEW_SENSOR_MM / (lens * max(reg.width, reg.height, 1)) * pan_sens
        right = rot @ Vector((1.0, 0.0, 0.0))
        up = rot @ Vector((0.0, 1.0, 0.0))
        rv3d.view_location = rv3d.view_location - (right * dx + up * dy) * u
    else:
        # Middle Click Orbit (Clean Turntable around fixed target/pivot)
        orbit_sens = float(getattr(prefs, 'pen_orbit_sensitivity', 1.0)) if prefs else 1.0
        e = rot.to_euler('XYZ')
        new_z = e.z - dx * (math.pi / max(reg.width, 1)) * orbit_sens
        new_x = max(0.005, min(math.pi - 0.005, e.x - dy * (math.pi / max(reg.height, 1)) * orbit_sens))
        rv3d.view_rotation = Euler((new_x, 0.0, new_z), 'XYZ').to_quaternion()
        # Note: rv3d.view_location is never modified during orbit so it strictly orbits around the target!

    area.tag_redraw()


def handle_pen_event(op, context, event, win, area):
    t, v = event.type, event.value
    prefs = get_preferences(context)

    try:
        from .mode_tablet import _tracker, _stylus_tracker
    except Exception:
        return {'PASS_THROUGH'}

    # 1. Stylus Pen hardware check:
    # Does the physical Wacom Pen have its tip touching the glass?
    # (Reads directly from /dev/input/event6 via evdev - 100% reliable)
    is_stylus_touching = False
    if getattr(_stylus_tracker, 'active', False):
        is_stylus_touching = _stylus_tracker.is_pen_touching(window_seconds=0.10)
    else:
        is_stylus_touching = getattr(event, 'is_tablet', False)

    # 2. Touch hardware check:
    # Are touchscreen fingers or palm contacts touching the glass?
    # (Reads directly from /dev/input/event7 via evdev)
    touch_active = False
    if getattr(_tracker, 'active', False):
        touch_active = _tracker.is_touch_active(cooldown=0.30)

    # 3. Swallow touch-synthesized mouse events in Pen Mode:
    # When fingers/palm are touching the screen and the physical pen is NOT on the glass:
    # ANY mouse button / move event is synthesized touch input from Mutter/Wayland.
    # We swallow it completely so Blender NEVER starts a sculpt or paint stroke!
    if touch_active and not is_stylus_touching:
        if t in MOUSE_TOUCH_SWALLOW_EVENTS:
            # Synchronously update pinch-to-zoom/pan on touch motion for immediate responsiveness
            if t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                if getattr(S, 'pen_nav', None):
                    if S.pen_nav.tick(context, win):
                        if area:
                            area.tag_redraw()
                        elif win and win.screen:
                            for a in win.screen.areas:
                                if a.type == 'VIEW_3D':
                                    a.tag_redraw()
            return {'RUNNING_MODAL'}

    # 4. Middle-Click Navigation in Pen Mode:
    # - If Shift is held: Intercept for accelerated Shift + MMB Pan (scaled by pen_pan_sensitivity).
    # - If Shift is NOT held: Return PASS_THROUGH so Blender's native view3d.rotate handles it!
    #   Native view3d.rotate keeps the exact turntable axis, selection/cursor center, and auto-depth,
    #   while its speed is dynamically controlled via inputs.view_rotate_sensitivity_turntable.
    if t == 'MIDDLEMOUSE':
        if v == 'PRESS':
            if bool(event.shift):
                target_v3d = area if (area and area.type == 'VIEW_3D') else (
                    area_at(win, event.mouse_x, event.mouse_y) if win else None
                )
                if target_v3d and target_v3d.type == 'VIEW_3D':
                    S.pen_mmb_active = True
                    S.pen_mmb_is_pan = True
                    S.pen_mmb_last_x = event.mouse_x
                    S.pen_mmb_last_y = event.mouse_y
                    S.pen_mmb_area_ptr = target_v3d.as_pointer()
                    return {'RUNNING_MODAL'}
            # Non-shift MMB: pass through to Blender's native view3d.rotate
            return {'PASS_THROUGH'}

        elif v == 'RELEASE':
            if getattr(S, 'pen_mmb_active', False):
                S.pen_mmb_active = False
                S.pen_mmb_is_pan = False
                S.pen_mmb_area_ptr = 0
                if area:
                    area.tag_redraw()
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}

    elif getattr(S, 'pen_mmb_active', False):
        if t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            last_x = getattr(S, 'pen_mmb_last_x', event.mouse_x)
            last_y = getattr(S, 'pen_mmb_last_y', event.mouse_y)
            dx = event.mouse_x - last_x
            dy = event.mouse_y - last_y
            S.pen_mmb_last_x = event.mouse_x
            S.pen_mmb_last_y = event.mouse_y

            if dx != 0 or dy != 0:
                target_v3d = area_by_ptr(win, getattr(S, 'pen_mmb_area_ptr', 0)) or (
                    area if (area and area.type == 'VIEW_3D') else None
                )
                if target_v3d and target_v3d.type == 'VIEW_3D':
                    apply_pen_mmb_navigation(context, win, target_v3d, dx, dy, is_pan=True)
            return {'RUNNING_MODAL'}

        elif t == 'ESC' and v == 'PRESS':
            S.pen_mmb_active = False
            S.pen_mmb_is_pan = False
            return {'RUNNING_MODAL'}

    return {'PASS_THROUGH'}
