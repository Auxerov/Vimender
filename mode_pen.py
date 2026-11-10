import math
import time
import bpy
from mathutils import Vector, Quaternion
from .state import (
    S, get_preferences,
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

        if getattr(_stylus_tracker, 'is_pen_touching', None) and _stylus_tracker.is_pen_touching():
            with _tracker.lock:
                _tracker.pan_delta[0] = _tracker.pan_delta[1] = 0.0
                _tracker.zoom_log = 0.0
            return False

        if context is None:
            context = bpy.context
        if win is None:
            win = context.window

        W = max(float(win.width), 1.0)
        H = max(float(win.height), 1.0)

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

        if fingers != 2:
            self._anchor = (0.0, 0.0)
            return False

        target_area = None
        if mid_n is not None:
            touch_x = mid_n[0] * W
            touch_y = (1.0 - mid_n[1]) * H
            target_area = area_at(win, touch_x, touch_y)
        if not target_area and getattr(S, 'hover_ptr', 0):
            a = area_by_ptr(win, S.hover_ptr)
            if a and a.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                target_area = a
        if not target_area and getattr(S, 'pen_ptr', 0):
            a = area_by_ptr(win, S.pen_ptr)
            if a and a.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                target_area = a
        if not target_area and win and win.screen:
            target_area = next((a for a in win.screen.areas if a.type in {'VIEW_3D', 'IMAGE_EDITOR'}), None)

        area = target_area
        if not area or area.type not in {'VIEW_3D', 'IMAGE_EDITOR'}:
            return False

        reg = window_region(area)
        if not reg:
            return False

        now = time.perf_counter()
        dt = min(max(now - self._last_t, 0.001), 0.05) if self._last_t else 0.016
        self._last_t = now

        dpx = pdx * W
        dpy = -pdy * H

        if not (dpx or dpy or zlog):
            return False

        prefs = get_preferences(context)
        sens = SENS_LEVELS[S.sens_idx] if hasattr(S, 'sens_idx') else 1.0

        if area.type == 'IMAGE_EDITOR':
            pan_sens = float(getattr(prefs, 'pen_pan_sensitivity', 1.75)) if prefs else 1.75
            delta_x = -int(round(dpx * sens * pan_sens))
            delta_y = -int(round(dpy * sens * pan_sens))

            changed = False
            if delta_x != 0 or delta_y != 0:
                try:
                    with context.temp_override(window=win, screen=win.screen, area=area, region=reg):
                        bpy.ops.view2d.pan('EXEC_DEFAULT', deltax=delta_x, deltay=delta_y)
                    changed = True
                except Exception:
                    pass

            if abs(zlog) > 0.005:
                zoom_delta = int(round(zlog * 350.0 * sens))
                if zoom_delta != 0:
                    try:
                        with context.temp_override(window=win, screen=win.screen, area=area, region=reg):
                            bpy.ops.view2d.zoom('EXEC_DEFAULT', deltax=zoom_delta, deltay=zoom_delta)
                        changed = True
                    except Exception:
                        pass

            if changed:
                area.tag_redraw()
            return changed

        space = area.spaces.active if area.spaces else None
        rv3d = space.region_3d if space else None
        if not rv3d:
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
    'LEFTMOUSE',
    'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE',
    'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE', 'MOUSESMARTZOOM',
    'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE',
}


def apply_pen_mmb_navigation(context, win, area, dx, dy, is_pan):
    if not area:
        return

    prefs = get_preferences(context)

    if area.type == 'IMAGE_EDITOR':
        reg = window_region(area)
        if not reg:
            return
        sens = SENS_LEVELS[S.sens_idx] if hasattr(S, 'sens_idx') else 1.0
        pan_sens = float(getattr(prefs, 'pen_pan_sensitivity', 1.75)) if prefs else 1.75
        delta_x = -int(round(dx * sens * pan_sens))
        delta_y = -int(round(dy * sens * pan_sens))
        if delta_x != 0 or delta_y != 0:
            try:
                with context.temp_override(window=win, screen=win.screen, area=area, region=reg):
                    bpy.ops.view2d.pan('EXEC_DEFAULT', deltax=delta_x, deltay=delta_y)
                area.tag_redraw()
            except Exception:
                pass
        return

    if area.type != 'VIEW_3D':
        return

    space = area.spaces.active if area.spaces else None
    rv3d = space.region_3d if space else None
    reg = window_region(area)
    if not rv3d or not reg:
        return

    rot = rv3d.view_rotation
    d = rv3d.view_distance

    if is_pan:
        # AUTO PERSPECTIVE: switch from CAMERA (unlocked) to PERSP so pan works
        if getattr(rv3d, 'view_perspective', 'PERSP') == 'CAMERA' and not getattr(space, 'lock_camera', False):
            rv3d.view_perspective = 'PERSP'

        pan_sens = float(getattr(prefs, 'pen_pan_sensitivity', 1.75)) if prefs else 1.75
        lens = max(getattr(space, 'lens', 50.0), 1.0)
        u = d * VIEW_SENSOR_MM / (lens * max(reg.width, reg.height, 1)) * pan_sens
        right = rot @ Vector((1.0, 0.0, 0.0))
        up = rot @ Vector((0.0, 1.0, 0.0))
        rv3d.view_location = rv3d.view_location - (right * dx + up * dy) * u
    else:
        # AUTO PERSPECTIVE: switch from ORTHO / unlocked CAMERA to PERSP when orbiting
        if getattr(rv3d, 'view_perspective', 'PERSP') != 'PERSP' and not (getattr(rv3d, 'view_perspective', 'PERSP') == 'CAMERA' and getattr(space, 'lock_camera', False)):
            rv3d.view_perspective = 'PERSP'

        orbit_sens = float(getattr(prefs, 'pen_orbit_sensitivity', 1.0)) if prefs else 1.0
        yaw = -dx * (math.pi / max(reg.width, 1)) * orbit_sens
        pitch = -dy * (math.pi / max(reg.height, 1)) * orbit_sens
        up_vec = rot @ Vector((0.0, 1.0, 0.0))
        right = rot @ Vector((1.0, 0.0, 0.0))
        q_pitch = Quaternion(right, pitch)
        if up_vec.z > 0.05:
            q_yaw = Quaternion(Vector((0.0, 0.0, 1.0)), yaw)
            test_rot = (q_pitch @ rot).normalized()
            if (test_rot @ Vector((0.0, 1.0, 0.0))).z > 0.02:
                new_rot = (q_yaw @ test_rot).normalized()
            else:
                new_rot = (q_yaw @ rot).normalized()
        else:
            q_yaw = Quaternion(up_vec, yaw)
            new_rot = (q_yaw @ q_pitch @ rot).normalized()
        rv3d.view_rotation = new_rot

    area.tag_redraw()


def handle_pen_event(op, context, event, win, area):
    t, v = event.type, event.value
    prefs = get_preferences(context)

    try:
        from .mode_tablet import _tracker, _stylus_tracker
    except Exception:
        return {'PASS_THROUGH'}

    is_stylus_touching = False
    if getattr(_stylus_tracker, 'active', False):
        is_stylus_touching = _stylus_tracker.is_pen_touching(window_seconds=0.10)
    else:
        is_stylus_touching = getattr(event, 'is_tablet', False)

    touch_active = False
    if getattr(_tracker, 'active', False):
        touch_active = _tracker.is_touch_active(cooldown=0.30)

    is_stylus_event = (
        getattr(event, 'is_tablet', False) or
        (getattr(_stylus_tracker, 'active', False) and (
            _stylus_tracker.is_pen_in_proximity(0.35) or
            _stylus_tracker.is_any_button_down()
        ))
    )
    if touch_active and not is_stylus_touching and not is_stylus_event:
        if t in MOUSE_TOUCH_SWALLOW_EVENTS:
            if t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                if getattr(S, 'pen_nav', None):
                    if S.pen_nav.tick(context, win):
                        if area:
                            area.tag_redraw()
                        elif win and win.screen:
                            for a in win.screen.areas:
                                if a.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                                    a.tag_redraw()
            return {'RUNNING_MODAL'}

    if getattr(_stylus_tracker, 'active', False):
        if getattr(_stylus_tracker, 'btn_orbit_down', False) or getattr(_stylus_tracker, 'btn_pan_down', False):
            if t in {'LEFTMOUSE', 'MIDDLEMOUSE', 'RIGHTMOUSE'}:
                return {'RUNNING_MODAL'}

    if t == 'MIDDLEMOUSE':
        is_stylus = (
            getattr(event, 'is_tablet', False) or
            (getattr(_stylus_tracker, 'active', False) and (
                _stylus_tracker.is_pen_in_proximity(0.35) or
                getattr(_stylus_tracker, 'btn_orbit_down', False) or
                getattr(_stylus_tracker, 'btn_pan_down', False)
            ))
        )
        if is_stylus:
            return {'RUNNING_MODAL'}

        # Physical mouse MMB in VIEW_3D: Auto-perspective protection
        if v == 'PRESS':
            target = area if (area and area.type == 'VIEW_3D') else None
            if not target and win and win.screen:
                target = area_at(win, event.mouse_x, event.mouse_y)
            if target and target.type == 'VIEW_3D' and target.spaces.active:
                rv = getattr(target.spaces.active, 'region_3d', None)
                if rv:
                    persp = getattr(rv, 'view_perspective', 'PERSP')
                    lock_cam = getattr(target.spaces.active, 'lock_camera', False)
                    # Orbit (no shift): ORTHO → PERSP
                    # Pan (shift): CAMERA (unlocked) → PERSP so pan works
                    if not event.shift and persp == 'ORTHO':
                        rv.view_perspective = 'PERSP'
                        target.tag_redraw()
                    elif event.shift and persp == 'CAMERA' and not lock_cam:
                        rv.view_perspective = 'PERSP'
                        target.tag_redraw()

        return {'PASS_THROUGH'}

    return {'PASS_THROUGH'}
