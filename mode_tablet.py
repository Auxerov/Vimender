import math
import os
import threading
import time
import bpy
import blf
from mathutils import Vector, Quaternion
from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d, location_3d_to_region_2d
from .state import (
    S, GREY, tag_all, window_region, area_by_ptr, area_at,
    set_active_mode, get_preferences, SENS_LEVELS, change_sens,
    check_shortcut, ui_scale, send_fake_key, send_fake_escape,
    send_fake_numpad, transform_active, is_blender_modal_active,
    get_active_transform_type
)

try:
    import evdev
    from evdev import ecodes
    HAVE_EVDEV = True
except ImportError:
    HAVE_EVDEV = False


def enable_touchview_integration(context=None):
    """
    Ensure any TouchView on-screen menus, gizmos, and overlays are completely hidden.
    Tablet Mode uses pure, clean hardware/touch gestures without on-screen button clutter.
    """
    if context is None:
        context = bpy.context
    try:
        if 'touchview' in context.preferences.addons:
            tv_prefs = context.preferences.addons['touchview'].preferences
            tv_prefs.is_enabled = False
            tv_prefs.show_menu = False
            tv_prefs.show_gizmos = False
            tv_prefs.show_float_menu = False
            wm = getattr(context, 'window_manager', None)
            if wm:
                for window in wm.windows:
                    if getattr(window, 'screen', None):
                        for area in window.screen.areas:
                            if area.type in {'VIEW_3D', 'IMAGE_EDITOR', 'NODE_EDITOR'}:
                                area.tag_redraw()
    except Exception:
        pass


def disable_touchview_integration(context=None):
    """
    Ensure any TouchView on-screen menus and gizmos remain hidden when leaving tablet mode.
    """
    if context is None:
        context = bpy.context
    try:
        if 'touchview' in context.preferences.addons:
            tv_prefs = context.preferences.addons['touchview'].preferences
            tv_prefs.is_enabled = False
            tv_prefs.show_menu = False
            tv_prefs.show_gizmos = False
            tv_prefs.show_float_menu = False
    except Exception:
        pass


class EvdevMultiTouchTracker:
    """
    True Linux Hardware Multi-Touch Gesture Recognizer using python-evdev.
    Reads raw multi-touch Type B slots directly from the touchscreen kernel device:
      - 1 finger: Smooth 3D Orbit (yaw + pitch rotation around pivot)
      - 2 fingers: Pinch to Zoom (smooth geometric scaling)
      - 2 fingers: Two-finger Pan (camera lateral movement)
      - 2 fingers quick tap: Undo
      - 3 fingers quick tap: Redo
    """
    TOUCH_PATHS = [
        "/dev/input/by-path/platform-AMDI0010:02-event",
        "/dev/input/event7",
    ]

    def __init__(self):
        self.dev = None
        self.thread = None
        self.running = False
        self.active = False
        self.lock = threading.Lock()

        # Multi-touch slots state
        self.current_slot = 0
        self.slots = {}  # slot_id: {'x': float, 'y': float, 'down': bool}
        self.prev_single = None
        self.prev_dist = None
        self.prev_mid = None

        # Tap detection
        self.touch_start_time = 0.0
        self.touch_max_fingers = 0
        self.touch_moved = False

        # Queued deltas for Blender main thread
        self.orbit_delta = [0.0, 0.0]
        self.transform_delta = [0.0, 0.0]
        self.pan_delta = [0.0, 0.0]
        self.zoom_delta = 0.0
        self.pending_undo = False
        self.pending_redo = False

    def find_device(self):
        if not HAVE_EVDEV:
            return None
        # Try direct known touch paths first
        for p in self.TOUCH_PATHS:
            if os.path.exists(p) and os.access(p, os.R_OK):
                try:
                    return evdev.InputDevice(p)
                except Exception:
                    pass
        # Search all evdev devices for touchscreen capabilities
        try:
            for path in evdev.list_devices():
                try:
                    d = evdev.InputDevice(path)
                    caps = d.capabilities()
                    if ecodes.EV_ABS in caps:
                        abs_codes = [c[0] if isinstance(c, tuple) else c for c in caps[ecodes.EV_ABS]]
                        if ecodes.ABS_MT_POSITION_X in abs_codes:
                            return d
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def start(self):
        if self.running and self.dev:
            return True

        self.stop()
        dev = self.find_device()
        if not dev:
            self.active = False
            return False

        self.dev = dev
        self.running = True
        self.active = True
        self.slots.clear()
        self.prev_single = None
        self.prev_dist = None
        self.prev_mid = None
        self.touch_max_fingers = 0
        self.touch_moved = False

        self.thread = threading.Thread(target=self._read_loop, daemon=True, name="EvdevMT")
        self.thread.start()
        print(f"[Vimender] Evdev Multi-touch active on {self.dev.name} ({self.dev.path})")
        return True

    def stop(self):
        self.running = False
        if self.dev:
            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None
        self.thread = None
        self.active = False

    def _read_loop(self):
        while self.running and self.dev:
            try:
                for event in self.dev.read():
                    if event.type == ecodes.EV_ABS:
                        if event.code == ecodes.ABS_MT_SLOT:
                            self.current_slot = event.value
                        elif event.code == ecodes.ABS_MT_TRACKING_ID:
                            if event.value == -1:
                                if self.current_slot in self.slots:
                                    self.slots[self.current_slot]['down'] = False
                            else:
                                if self.current_slot not in self.slots:
                                    self.slots[self.current_slot] = {'x': 0.0, 'y': 0.0, 'down': True}
                                else:
                                    self.slots[self.current_slot]['down'] = True
                        elif event.code == ecodes.ABS_MT_POSITION_X:
                            if self.current_slot not in self.slots:
                                self.slots[self.current_slot] = {'x': float(event.value), 'y': 0.0, 'down': True}
                            else:
                                self.slots[self.current_slot]['x'] = float(event.value)
                        elif event.code == ecodes.ABS_MT_POSITION_Y:
                            if self.current_slot not in self.slots:
                                self.slots[self.current_slot] = {'x': 0.0, 'y': float(event.value), 'down': True}
                            else:
                                self.slots[self.current_slot]['y'] = float(event.value)

                    elif event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                        self._process_frame()
            except BlockingIOError:
                time.sleep(0.003)
            except Exception:
                break
        self.active = False

    def _process_frame(self):
        active = [s for s in self.slots.values() if s.get('down', False)]
        n = len(active)
        now = time.perf_counter()

        if n == 0:
            if self.touch_max_fingers == 2 and not self.touch_moved:
                if (now - self.touch_start_time) < 0.32:
                    with self.lock:
                        self.pending_undo = True
            elif self.touch_max_fingers == 3 and not self.touch_moved:
                if (now - self.touch_start_time) < 0.32:
                    with self.lock:
                        self.pending_redo = True

            self.prev_single = None
            self.prev_dist = None
            self.prev_mid = None
            self.touch_max_fingers = 0
            self.touch_moved = False
            return

        if self.touch_max_fingers == 0:
            self.touch_start_time = now
            self.touch_moved = False

        if n > self.touch_max_fingers:
            self.touch_max_fingers = n

        # 1 FINGER: ORBIT OR AXIS TRANSFORM
        if n == 1:
            p = (active[0]['x'], active[0]['y'])
            if self.prev_single is not None:
                dx = p[0] - self.prev_single[0]
                dy = p[1] - self.prev_single[1]
                if abs(dx) > 1.0 or abs(dy) > 1.0:
                    self.touch_moved = True
                    with self.lock:
                        if getattr(S, 'tablet_in_transform', None) or getattr(S, 'tablet_active_axis', None) in {'X', 'Y', 'Z'}:
                            self.transform_delta[0] += dx
                            self.transform_delta[1] += dy
                        elif getattr(S, 'tablet_select_tool', None) == 'BOX':
                            pass
                        else:
                            self.orbit_delta[0] += dx
                            self.orbit_delta[1] += dy
            self.prev_single = p
            self.prev_dist = None
            self.prev_mid = None

        # 2 FINGERS: PINCH-ZOOM AND PAN
        elif n == 2:
            self.prev_single = None
            if getattr(S, 'tablet_in_transform', None):
                self.prev_dist = None
                self.prev_mid = None
            else:
                p0 = (active[0]['x'], active[0]['y'])
                p1 = (active[1]['x'], active[1]['y'])

                dist = math.hypot(p0[0] - p1[0], p0[1] - p1[1])
                if self.prev_dist is not None:
                    d_dist = dist - self.prev_dist
                    if abs(d_dist) > 2.0:
                        self.touch_moved = True
                        with self.lock:
                            self.zoom_delta += d_dist
                self.prev_dist = dist

                mid = ((p0[0] + p1[0]) * 0.5, (p0[1] + p1[1]) * 0.5)
                if self.prev_mid is not None:
                    dmx = mid[0] - self.prev_mid[0]
                    dmy = mid[1] - self.prev_mid[1]
                    if math.hypot(dmx, dmy) > 2.0:
                        self.touch_moved = True
                        with self.lock:
                            self.pan_delta[0] += dmx
                            self.pan_delta[1] += dmy
                self.prev_mid = mid


_tracker = EvdevMultiTouchTracker()


class _TouchNavigator:
    """
    Main manager for tablet gestures, managing evdev multitouch and single-touch fallbacks.
    """
    def __init__(self):
        self.last_tap_time = 0.0
        self.last_tap_x = 0.0
        self.last_tap_y = 0.0

    def reset(self):
        self.last_tap_time = 0.0
        self.last_tap_x = 0.0
        self.last_tap_y = 0.0
        _tracker.stop()

    def tick(self, context=None, win=None):
        if not _tracker.active:
            return False

        if context is None:
            context = bpy.context
        if win is None:
            win = context.window

        area = (
            area_by_ptr(win, S.tablet_ptr) or
            area_by_ptr(win, S.hover_ptr) or
            next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None) if win and win.screen else None
        )
        if not area or area.type != 'VIEW_3D':
            return False

        rv3d = area.spaces.active.region_3d if area.spaces else None
        if not rv3d:
            return False

        with _tracker.lock:
            odx, ody = _tracker.orbit_delta
            _tracker.orbit_delta[0] = 0.0
            _tracker.orbit_delta[1] = 0.0

            tdx, tdy = _tracker.transform_delta
            _tracker.transform_delta[0] = 0.0
            _tracker.transform_delta[1] = 0.0

            pdx, pdy = _tracker.pan_delta
            _tracker.pan_delta[0] = 0.0
            _tracker.pan_delta[1] = 0.0

            z_delta = _tracker.zoom_delta
            _tracker.zoom_delta = 0.0

            undo = _tracker.pending_undo
            _tracker.pending_undo = False

            redo = _tracker.pending_redo
            _tracker.pending_redo = False

        changed = False

        if undo:
            try:
                bpy.ops.ed.undo()
            except Exception:
                pass
            return True

        if redo:
            try:
                bpy.ops.ed.redo()
            except Exception:
                pass
            return True

        sens = SENS_LEVELS[S.sens_idx]

        # Suppress viewport navigation while transform is active or in BOX select
        if getattr(S, 'tablet_in_transform', None) or getattr(S, 'tablet_select_tool', None) == 'BOX':
            odx = 0.0
            ody = 0.0
            pdx = 0.0
            pdy = 0.0
            z_delta = 0.0

        # 0. TOUCH OBJECT TRANSFORM (when in transform or axis locked)
        if abs(tdx) > 0.05 or abs(tdy) > 0.05:
            if apply_touch_transform(context, area, tdx, tdy, sens):
                changed = True

        # 1. ORBIT (1-finger drag)
        if abs(odx) > 0.05 or abs(ody) > 0.05:
            yaw = -odx * (0.00055 * sens)
            pitch = -ody * (0.00050 * sens)

            q_yaw = Quaternion(Vector((0.0, 0.0, 1.0)), yaw)
            right = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
            q_pitch = Quaternion(right, pitch)

            eye = rv3d.view_location + (rv3d.view_rotation @ Vector((0.0, 0.0, rv3d.view_distance)))
            test_rot = (q_pitch @ rv3d.view_rotation).normalized()
            up_z = (test_rot @ Vector((0.0, 1.0, 0.0))).z
            if up_z > 0.02:
                new_rot = (q_yaw @ test_rot).normalized()
            else:
                new_rot = (q_yaw @ rv3d.view_rotation).normalized()

            rv3d.view_rotation = new_rot
            rv3d.view_location = eye - (new_rot @ Vector((0.0, 0.0, rv3d.view_distance)))
            changed = True

        # 2. PINCH TO ZOOM (2 fingers)
        if abs(z_delta) > 0.5:
            zoom_factor = max(0.1, min(10.0, 1.0 - (z_delta * 0.00035 * sens)))
            rv3d.view_distance = max(0.01, rv3d.view_distance * zoom_factor)
            changed = True

        # 3. TWO-FINGER PAN (2 fingers)
        if abs(pdx) > 0.1 or abs(pdy) > 0.1:
            pan_sens = (rv3d.view_distance / 6000.0) * sens
            right = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
            up = rv3d.view_rotation @ Vector((0.0, 1.0, 0.0))
            rv3d.view_location += (-right * pdx + up * pdy) * pan_sens
            changed = True

        if changed:
            area.tag_redraw()
        return changed


S.touch_nav = _TouchNavigator()


def apply_touch_transform(context, area, tdx, tdy, sens, is_blender_coords=False):
    """
    Direct hand / touch object transform:
    - If axis in {'X', 'Y', 'Z'}: transforms strictly along that axis.
    - If axis is None: transforms freely on the view plane / screen orientation.
    Supports G (Move), R (Rotate), and S (Scale) on all selected objects.
    """
    targets = [o for o in context.selected_objects if o.visible_get()]
    if not targets:
        act = getattr(context, 'active_object', None)
        if act and act.visible_get():
            targets = [act]
        else:
            return False

    mode = getattr(S, 'tablet_in_transform', None) or getattr(S, 'tablet_active_transform', 'G')
    axis = getattr(S, 'tablet_active_axis', None)
    rv3d = area.spaces.active.region_3d if area and area.spaces else None
    dist = getattr(rv3d, 'view_distance', 10.0) if rv3d else 10.0

    if rv3d:
        cam_right = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
        cam_up = rv3d.view_rotation @ Vector((0.0, 1.0, 0.0))
        cam_normal = rv3d.view_rotation @ Vector((0.0, 0.0, 1.0))
    else:
        cam_right = Vector((1.0, 0.0, 0.0))
        cam_up = Vector((0.0, 1.0, 0.0))
        cam_normal = Vector((0.0, 0.0, 1.0))

    screen_dx = tdx
    screen_dy = tdy if is_blender_coords else -tdy

    changed = False

    if axis in {'X', 'Y', 'Z'}:
        axis_vec = Vector((1.0, 0.0, 0.0)) if axis == 'X' else (Vector((0.0, 1.0, 0.0)) if axis == 'Y' else Vector((0.0, 0.0, 1.0)))
        proj_x = axis_vec.dot(cam_right)
        proj_y = axis_vec.dot(cam_up)
        proj_len = math.hypot(proj_x, proj_y)

        if proj_len > 0.05:
            ux = proj_x / proj_len
            uy = proj_y / proj_len
            drag_motion = (screen_dx * ux + screen_dy * uy)
        else:
            drag_motion = (screen_dx + screen_dy)

        if mode == 'G':
            delta_val = drag_motion * (0.00030 * sens * max(0.4, dist * 0.25))
            delta_vec = axis_vec * delta_val
            for obj in targets:
                obj.location += delta_vec
            changed = True

        elif mode == 'R':
            rot = drag_motion * (0.00080 * sens)
            for obj in targets:
                if getattr(obj, 'rotation_mode', 'XYZ') == 'QUATERNION':
                    obj.rotation_quaternion = (Quaternion(axis_vec, rot) @ obj.rotation_quaternion).normalized()
                else:
                    if axis == 'X':
                        obj.rotation_euler.x += rot
                    elif axis == 'Y':
                        obj.rotation_euler.y += rot
                    elif axis == 'Z':
                        obj.rotation_euler.z += rot
            changed = True

        elif mode == 'S':
            s_val = 1.0 + drag_motion * (0.00060 * sens)
            s_val = max(0.01, min(10.0, s_val))
            for obj in targets:
                if axis == 'X':
                    obj.scale.x = max(0.0001, obj.scale.x * s_val)
                elif axis == 'Y':
                    obj.scale.y = max(0.0001, obj.scale.y * s_val)
                elif axis == 'Z':
                    obj.scale.z = max(0.0001, obj.scale.z * s_val)
            changed = True

    else:
        # Free / View-Plane transform (No specific axis locked)
        if mode == 'G':
            speed = 0.00030 * sens * max(0.4, dist * 0.25)
            delta_vec = (cam_right * screen_dx + cam_up * screen_dy) * speed
            for obj in targets:
                obj.location += delta_vec
            changed = True

        elif mode == 'R':
            rot = (screen_dx - screen_dy) * (0.00060 * sens)
            q_rot = Quaternion(cam_normal, rot)
            for obj in targets:
                if getattr(obj, 'rotation_mode', 'XYZ') == 'QUATERNION':
                    obj.rotation_quaternion = (q_rot @ obj.rotation_quaternion).normalized()
                else:
                    e = (q_rot @ obj.rotation_euler.to_quaternion()).to_euler(obj.rotation_mode)
                    obj.rotation_euler = e
            changed = True

        elif mode == 'S':
            drag_val = (screen_dx + screen_dy) * 0.5
            s_val = 1.0 + drag_val * (0.00060 * sens)
            s_val = max(0.01, min(10.0, s_val))
            for obj in targets:
                obj.scale = Vector((
                    max(0.0001, obj.scale.x * s_val),
                    max(0.0001, obj.scale.y * s_val),
                    max(0.0001, obj.scale.z * s_val),
                ))
            changed = True

    if changed:
        area.tag_redraw()
    return changed


def start_tablet_transform(mode_id, context=None, area=None, report_fn=None):
    """
    Activates transform mode (G=Move, R=Rotate, S=Scale) in Tablet Mode:
    1. Saves initial transforms for undo/cancel.
    2. Sets S.tablet_in_transform = mode_id and activates the 3D Gizmo.
    3. Reveals the axis constraint dock (X, Y, Z, OK, ESC).
    4. Suppresses viewport orbit/pan so touch directly manipulates the transform.
    """
    if context is None:
        context = bpy.context

    targets = [o for o in context.selected_objects if o.visible_get()]
    if not targets:
        act = getattr(context, 'active_object', None)
        if act:
            act.select_set(True)
            targets = [act]

    # Save backup of object matrices for 100% accurate Cancel (ESC)
    S.tablet_transform_backup = {o: o.matrix_world.copy() for o in targets}
    S.tablet_in_transform = mode_id
    S.tablet_active_transform = mode_id
    S.tablet_active_axis = None  # Free view-plane by default until an axis is tapped

    set_tablet_transform_mode(mode_id, context, area)
    tag_all()
    if report_fn:
        tr_name = 'Move' if mode_id == 'G' else ('Rotate' if mode_id == 'R' else 'Scale')
        report_fn({'INFO'}, f"Tablet: {tr_name} Active (Touch Drag / Axes / OK / ESC)")


def confirm_tablet_transform(context=None, area=None, report_fn=None):
    """
    Confirms and commits the active transform:
    1. Pushes undo step to Blender history.
    2. Hides axis constraint dock and returns to normal navigation.
    """
    if not getattr(S, 'tablet_in_transform', None):
        return
    tr_name = 'Move' if S.tablet_in_transform == 'G' else ('Rotate' if S.tablet_in_transform == 'R' else 'Scale')
    try:
        bpy.ops.ed.undo_push(message=f"Tablet {tr_name}")
    except Exception:
        pass
    S.tablet_in_transform = None
    S.tablet_active_axis = None
    S.tablet_transform_backup = None
    cur_tool = getattr(S, 'tablet_select_tool', 'TWEAK')
    set_tablet_select_tool(cur_tool, context, area)
    tag_all()
    if report_fn:
        report_fn({'INFO'}, f"Tablet: {tr_name} Confirmed")


def cancel_tablet_transform(context=None, area=None, report_fn=None):
    """
    Cancels the active transform:
    1. Restores original object matrices from backup.
    2. Hides axis constraint dock and returns to normal navigation.
    """
    if not getattr(S, 'tablet_in_transform', None):
        return
    tr_name = 'Move' if S.tablet_in_transform == 'G' else ('Rotate' if S.tablet_in_transform == 'R' else 'Scale')
    if getattr(S, 'tablet_transform_backup', None):
        for o, mat in S.tablet_transform_backup.items():
            try:
                o.matrix_world = mat.copy()
            except Exception:
                pass
    S.tablet_in_transform = None
    S.tablet_active_axis = None
    S.tablet_transform_backup = None
    cur_tool = getattr(S, 'tablet_select_tool', 'TWEAK')
    set_tablet_select_tool(cur_tool, context, area)
    tag_all()
    if report_fn:
        report_fn({'INFO'}, f"Tablet: {tr_name} Canceled")


def set_tablet_transform_mode(mode_id, context=None, area=None):
    """
    Sets active transform tool (G=Move, R=Rotate, S=Scale, ALL=Transform)
    and enables the corresponding 3D interactive gizmo.
    """
    if context is None:
        context = bpy.context
    S.tablet_active_transform = mode_id
    win = getattr(context, 'window', None)
    tool_map = {
        'G': ('builtin.move', True, False, False),
        'R': ('builtin.rotate', False, True, False),
        'S': ('builtin.scale', False, False, True),
        'ALL': ('builtin.transform', True, True, True),
    }
    tool_name, gz_t, gz_r, gz_s = tool_map.get(mode_id, ('builtin.move', True, False, False))

    if area and area.spaces:
        sp = area.spaces.active
        if hasattr(sp, 'show_gizmo'):
            sp.show_gizmo = True
            sp.show_gizmo_tool = True
            sp.show_gizmo_object_translate = gz_t
            sp.show_gizmo_object_rotate = gz_r
            sp.show_gizmo_object_scale = gz_s
        area.tag_redraw()

    try:
        use_area = area or (next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None) if win and win.screen else None)
        if use_area:
            with context.temp_override(window=win, screen=win.screen if win else None, area=use_area):
                bpy.ops.wm.tool_set_by_id(name=tool_name)
    except Exception:
        pass
    tag_all()


def set_tablet_select_tool(tool_id, context=None, area=None, report_fn=None):
    """
    Directly switches Blender into its native selection tools:
    - 'BOX': Blender's native Box Select tool ('builtin.select_box').
    - 'TWEAK': Blender's native Tweak / Direct select tool ('builtin.select').
    Syncs selection modifier mode (SET, ADD, SUB, XOR) to Blender's native tool settings.
    """
    if context is None:
        context = bpy.context
    S.tablet_select_tool = tool_id
    win = getattr(context, 'window', None)
    blender_tool = 'builtin.select_box' if tool_id == 'BOX' else 'builtin.select'
    use_area = area or (next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None) if win and win.screen else None)
    if use_area and win:
        try:
            with context.temp_override(window=win, screen=win.screen if win else None, area=use_area):
                bpy.ops.wm.tool_set_by_id(name=blender_tool)
        except Exception:
            pass
    sync_tablet_tool_mode(context, use_area)
    tag_all()
    if report_fn:
        tname = "Blender Box Select (Kutu Seçimi)" if tool_id == 'BOX' else "Tweak (Düz Seçim)"
        report_fn({'INFO'}, f"Tablet Tool: {tname}")


def sync_tablet_tool_mode(context=None, area=None):
    """
    Syncs active Vimender selection modifier (SEL, +SHIFT, +CTRL, +ALT)
    with Blender's native tool settings for select_box and select.
    """
    if context is None:
        context = bpy.context
    eff_mod = getattr(S, 'tablet_select_modifier', None)
    mode_map = {
        'SHIFT': 'ADD',
        'CTRL': 'SUB',
        'ALT': 'XOR',
    }
    box_mode = mode_map.get(eff_mod, 'SET')
    try:
        win = getattr(context, 'window', None)
        use_area = area or (next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None) if win and win.screen else None)
        if use_area and win:
            with context.temp_override(window=win, screen=win.screen if win else None, area=use_area):
                ws = context.workspace
                tool = ws.tools.from_space_view3d_mode(context.mode)
                if tool and hasattr(tool, 'operator_properties'):
                    for op_id in ('view3d.select_box', 'view3d.select'):
                        try:
                            props = tool.operator_properties(op_id)
                            if props and hasattr(props, 'mode'):
                                props.mode = box_mode
                        except Exception:
                            pass
        else:
            ws = context.workspace
            tool = ws.tools.from_space_view3d_mode(context.mode)
            if tool and hasattr(tool, 'operator_properties'):
                for op_id in ('view3d.select_box', 'view3d.select'):
                    try:
                        props = tool.operator_properties(op_id)
                        if props and hasattr(props, 'mode'):
                            props.mode = box_mode
                    except Exception:
                        pass
    except Exception:
        pass


def apply_tablet_view_settings(context=None, target_area=None):
    """
    Called when entering Tablet Mode:
    1. Hides the default Blender T-toolbar ('t ile açılan model gizlensin').
    2. Enables 3D interactive transform gizmos (arrows/rings/boxes) for touch.
    3. Activates the active selection tool ('builtin.select' or 'builtin.select_box').
    """
    if context is None:
        context = bpy.context
    win = getattr(context, 'window', None)
    if not win or not getattr(win, 'screen', None):
        return
    for area in win.screen.areas:
        if area.type == 'VIEW_3D' and area.spaces:
            sp = area.spaces.active
            if hasattr(sp, 'show_region_toolbar'):
                S.tablet_prev_show_toolbar[area.as_pointer()] = sp.show_region_toolbar
                sp.show_region_toolbar = False  # Hide default T toolbar
            if hasattr(sp, 'show_gizmo'):
                S.tablet_prev_gizmos[area.as_pointer()] = {
                    'show_gizmo': sp.show_gizmo,
                    'show_gizmo_tool': getattr(sp, 'show_gizmo_tool', True),
                    'show_gizmo_object_translate': getattr(sp, 'show_gizmo_object_translate', False),
                    'show_gizmo_object_rotate': getattr(sp, 'show_gizmo_object_rotate', False),
                    'show_gizmo_object_scale': getattr(sp, 'show_gizmo_object_scale', False),
                }
                sp.show_gizmo = True
                sp.show_gizmo_tool = True
                sp.show_gizmo_object_translate = True
                sp.show_gizmo_object_rotate = False
                sp.show_gizmo_object_scale = False
            area.tag_redraw()

    try:
        use_area = target_area or next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None)
        if use_area:
            with context.temp_override(window=win, screen=win.screen, area=use_area):
                cur_tool = getattr(S, 'tablet_select_tool', 'TWEAK')
                btool = 'builtin.select_box' if cur_tool == 'BOX' else 'builtin.select'
                bpy.ops.wm.tool_set_by_id(name=btool)
    except Exception:
        pass


def restore_tablet_view_settings(context=None):
    """
    Called when leaving Tablet Mode:
    Restores the previous state of the T-toolbar and gizmos.
    """
    if context is None:
        context = bpy.context
    win = getattr(context, 'window', None)
    if not win or not getattr(win, 'screen', None):
        return
    for area in win.screen.areas:
        if area.type == 'VIEW_3D' and area.spaces:
            sp = area.spaces.active
            old_tb = S.tablet_prev_show_toolbar.get(area.as_pointer())
            if old_tb is not None and hasattr(sp, 'show_region_toolbar'):
                sp.show_region_toolbar = old_tb
            old_gz = S.tablet_prev_gizmos.get(area.as_pointer())
            if old_gz and hasattr(sp, 'show_gizmo'):
                for k, v in old_gz.items():
                    if hasattr(sp, k):
                        setattr(sp, k, v)
            area.tag_redraw()
    S.tablet_prev_show_toolbar.clear()
    S.tablet_prev_gizmos.clear()


def get_tablet_gui_buttons(region, ui):
    """
    Returns rectangular button hitboxes and dividers for Tablet Mode Upper-Left Transform GUI:
    - FIT (Fit view)
    - G (Move)
    - R (Rotate)
    - S (Scale)
    - REDO (Redo)
    - UNDO (Undo)
    """
    if not region:
        return []

    bw = int(52 * ui)
    bh = int(40 * ui)
    gap = int(6 * ui)
    sep_h = int(12 * ui)
    x0 = int(12 * ui)
    x1 = x0 + bw

    specs = [
        ('FIT', 'FIT', 'VIEW', (0.16, 0.16, 0.19, 0.88), (0.75, 0.75, 0.75, 0.96), False),
        ('sep1', '', '', None, None, True),
        ('G', 'G', 'MOVE',  (0.16, 0.16, 0.19, 0.88), (0.95, 0.60, 0.12, 0.96), False),
        ('R', 'R', 'ROT',   (0.16, 0.16, 0.19, 0.88), (0.95, 0.60, 0.12, 0.96), False),
        ('S', 'S', 'SCALE', (0.16, 0.16, 0.19, 0.88), (0.95, 0.60, 0.12, 0.96), False),
        ('sep2', '', '', None, None, True),
        ('REDO', 'REDO', '', (0.16, 0.16, 0.19, 0.88), (0.75, 0.75, 0.75, 0.96), False),
        ('UNDO', 'UNDO', '', (0.16, 0.16, 0.19, 0.88), (0.75, 0.75, 0.75, 0.96), False),
    ]

    total_h = sum(sep_h if s[5] else (bh + gap) for s in specs) - gap
    cy = min(int(region.height - 14 * ui), max(int(190 * ui + total_h), int((region.height + total_h) * 0.52)))

    win = getattr(bpy.context, 'window', None)
    is_tr = transform_active(win) or is_blender_modal_active(win)
    cur_tr = get_active_transform_type(win) if is_tr else None

    items = []
    for btn_id, label, subtext, base_col, active_col, is_sep in specs:
        if is_sep:
            items.append({
                'id': btn_id,
                'is_sep': True,
                'x0': x0 + int(4 * ui),
                'x1': x1 - int(4 * ui),
                'y0': cy - sep_h,
                'y1': cy,
                'y': cy - int(sep_h / 2),
            })
            cy -= sep_h
            continue

        btn_y1 = cy
        btn_y0 = cy - bh

        is_active = False
        cur_in_tr = getattr(S, 'tablet_in_transform', None)
        if btn_id == 'G':
            is_active = (cur_in_tr == 'G' or cur_tr == 'TRANSLATE')
        elif btn_id == 'R':
            is_active = (cur_in_tr == 'R' or cur_tr == 'ROTATE')
        elif btn_id == 'S':
            is_active = (cur_in_tr == 'S' or cur_tr in {'RESIZE', 'SCALE'})

        items.append({
            'id': btn_id,
            'is_sep': False,
            'label': label,
            'subtext': subtext,
            'x0': x0,
            'y0': btn_y0,
            'x1': x1,
            'y1': btn_y1,
            'base_color': base_col,
            'active_color': active_col,
            'is_active': is_active,
        })
        cy -= (bh + gap)

    return items


def get_tablet_axis_buttons(region, ui):
    """
    Returns rectangular button hitboxes for Tablet Mode Right Axis Selection GUI
    placed vertically directly above the mode badge ('TABLET MODE [SEL: ...]'):
    - OK (Confirm transform, green)
    - ESC (Cancel transform, red)
    - sep
    - X (X Axis, red)
    - Y (Y Axis, green)
    - Z (Z Axis, blue)

    HIDDEN when not transforming (prevents accidental key presses or deletes).
    """
    if not region:
        return []

    bw = int(54 * ui)
    bh = int(36 * ui)
    gap = int(5 * ui)
    sep_h = int(10 * ui)

    # Compute mode badge bounds
    inset = 0
    win = getattr(bpy.context, 'window', None)
    if win and win.screen:
        area = area_by_ptr(win, S.tablet_ptr) or next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None)
        if area and region.width >= area.width - 1:
            inset = sum(r.width for r in area.regions
                        if r.type == 'UI' and r.alignment == 'RIGHT' and r.width > 1)

    sel_name = getattr(S, 'tablet_select_modifier', None) or 'DIRECT'
    tool_name = getattr(S, 'tablet_select_tool', 'TWEAK')
    label = f"TABLET MODE [{tool_name} | {sel_name}]"
    tw = int(len(label) * 7.5 * ui)
    th = int(14 * ui)
    try:
        blf.size(0, int(14 * ui))
        dtw, dth = blf.dimensions(0, label)
        if dtw > 50:
            tw, th = dtw, dth
    except Exception:
        pass

    pad = 6 * ui
    x1 = region.width - inset - 12 * ui
    x0 = x1 - tw - 2 * pad
    mode_y1 = 12 * ui + th + 2 * pad

    # Center buttons above mode badge
    badge_cx = (x0 + x1) * 0.5
    bx0 = int(badge_cx - bw * 0.5)
    bx1 = bx0 + bw

    # Stack vertically above mode badge:
    # TWEAK (closest to badge) -> BOX -> [sep -> Z -> Y -> X -> sep -> ESC -> OK (when in transform)]
    gap_above = int(8 * ui)
    specs = [
        ('TWEAK', 'TWEAK', 'DIRECT', (0.14, 0.20, 0.16, 0.88), (0.22, 0.82, 0.40, 0.96), False),
        ('BOX',   'BOX',   'SELECT', (0.14, 0.18, 0.26, 0.88), (0.20, 0.65, 0.95, 0.96), False),
    ]

    if getattr(S, 'tablet_in_transform', None):
        specs += [
            ('sep1', '',    '',       None,                     None,                     True),
            ('Z',   'Z',   'AXIS',   (0.12, 0.16, 0.26, 0.88), (0.25, 0.55, 0.95, 0.96), False),
            ('Y',   'Y',   'AXIS',   (0.12, 0.24, 0.12, 0.88), (0.22, 0.82, 0.30, 0.96), False),
            ('X',   'X',   'AXIS',   (0.24, 0.12, 0.12, 0.88), (0.92, 0.22, 0.22, 0.96), False),
            ('sep2', '',    '',       None,                     None,                     True),
            ('ESC', 'ESC', 'CANCEL', (0.35, 0.12, 0.12, 0.90), (0.92, 0.25, 0.25, 0.98), False),
            ('OK',  'OK',  'ENTER',  (0.12, 0.35, 0.16, 0.90), (0.22, 0.82, 0.35, 0.98), False),
        ]

    buttons = []
    cy = int(mode_y1 + gap_above)
    for btn_id, lbl, subtext, base_col, active_col, is_sep in specs:
        if is_sep:
            buttons.append({
                'id': btn_id,
                'is_sep': True,
                'x0': bx0 + int(4 * ui),
                'x1': bx1 - int(4 * ui),
                'y0': cy,
                'y1': cy + sep_h,
                'y': cy + int(sep_h / 2),
            })
            cy += sep_h
            continue

        btn_y0 = cy
        btn_y1 = cy + bh
        is_active = False
        cur_tool = getattr(S, 'tablet_select_tool', 'TWEAK')
        if btn_id == 'TWEAK':
            is_active = (cur_tool == 'TWEAK')
        elif btn_id == 'BOX':
            is_active = (cur_tool == 'BOX')
        elif btn_id in {'X', 'Y', 'Z'}:
            is_active = (getattr(S, 'tablet_active_axis', None) == btn_id)
        buttons.append({
            'id': btn_id,
            'is_sep': False,
            'label': lbl,
            'subtext': subtext,
            'x0': bx0,
            'y0': btn_y0,
            'x1': bx1,
            'y1': btn_y1,
            'base_color': base_col,
            'active_color': active_col,
            'is_active': is_active,
        })
        cy += (bh + gap)

    return buttons


def get_tablet_selection_buttons(region, ui):
    """
    Returns rectangular button hitboxes for Tablet Mode Bottom-Left Selection GUI:
    - SEL (Düz seçim / Normal Select)
    - +SHIFT (Shift + seçim / Multi-Select Toggle)
    - +CTRL (Ctrl + seçim / Deselect)
    - +ALT (Alt + seçim / Loop & Depth Select)
    """
    if not region:
        return []

    bw = int(52 * ui)
    bh = int(36 * ui)
    gap = int(5 * ui)
    x0 = int(12 * ui)
    x1 = x0 + bw

    specs = [
        ('SEL_NORMAL', 'SEL',    'DIRECT', (0.16, 0.16, 0.19, 0.88), (0.22, 0.75, 0.40, 0.96)),
        ('SEL_SHIFT',  '+SHIFT', 'TOGGLE', (0.14, 0.20, 0.28, 0.88), (0.20, 0.65, 0.95, 0.96)),
        ('SEL_CTRL',   '+CTRL',  'SUB',    (0.26, 0.14, 0.14, 0.88), (0.95, 0.30, 0.25, 0.96)),
        ('SEL_ALT',    '+ALT',   'LOOP',   (0.22, 0.14, 0.28, 0.88), (0.85, 0.35, 0.95, 0.96)),
    ]

    total_h = len(specs) * bh + (len(specs) - 1) * gap
    cy = int(16 * ui) + total_h

    buttons = []
    for btn_id, label, subtext, base_col, active_col in specs:
        btn_y1 = cy
        btn_y0 = cy - bh

        is_active = False
        cur_mod = getattr(S, 'tablet_select_modifier', None)
        if btn_id == 'SEL_NORMAL':
            is_active = (cur_mod is None)
        elif btn_id == 'SEL_SHIFT':
            is_active = (cur_mod == 'SHIFT')
        elif btn_id == 'SEL_CTRL':
            is_active = (cur_mod == 'CTRL')
        elif btn_id == 'SEL_ALT':
            is_active = (cur_mod == 'ALT')

        buttons.append({
            'id': btn_id,
            'is_sep': False,
            'label': label,
            'subtext': subtext,
            'x0': x0,
            'y0': btn_y0,
            'x1': x1,
            'y1': btn_y1,
            'base_color': base_col,
            'active_color': active_col,
            'is_active': is_active,
        })
        cy -= (bh + gap)

    return buttons


def enter_tablet_mode(context, area=None, report_fn=None):
    set_active_mode('TABLET', context, report_fn, target_area=area)
    tag_all()


def exit_tablet_mode(context, report_fn=None):
    set_active_mode('PREVIEW', context, report_fn)
    tag_all()


S.exit_tablet_fn = exit_tablet_mode


def handle_tablet_event(op, context, event, win, area):
    t, v = event.type, event.value
    mx, my = event.mouse_x, event.mouse_y

    # Robust area resolution
    if not area or not window_region(area):
        area = (
            area_by_ptr(win, S.tablet_ptr) or
            area_at(win, mx, my) or
            area_by_ptr(win, S.hover_ptr) or
            next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None) or
            (win.screen.areas[0] if win and win.screen and win.screen.areas else None)
        )
    reg = window_region(area) if area else None

    prefs = get_preferences(context)

    # 1. Exit Tablet Mode on Ctrl+W, Esc, Enter (when not transforming)
    if getattr(S, 'tablet_in_transform', None):
        if check_shortcut(prefs, 'tablet', event, 'W', fallback_ctrl=True) or ((t == 'W' or getattr(event, 'unicode', '') in {'w', 'W'}) and event.ctrl and not (event.alt or event.oskey or event.shift)):
            if v == 'PRESS':
                cancel_tablet_transform(context, area, op.report)
                set_active_mode('PREVIEW', context, op.report)
            return {'RUNNING_MODAL'}
    else:
        is_tablet_toggle = (
            check_shortcut(prefs, 'tablet', event, 'W', fallback_ctrl=True) or
            ((t == 'W' or getattr(event, 'unicode', '') in {'w', 'W'}) and event.ctrl and not (event.alt or event.oskey or event.shift)) or
            (t in {'ESC', 'RET', 'NUMPAD_ENTER'} and v == 'PRESS')
        )
        if is_tablet_toggle:
            if v == 'RELEASE':
                return {'RUNNING_MODAL'}
            if v == 'PRESS':
                set_active_mode('PREVIEW', context, op.report)
                return {'RUNNING_MODAL'}

    # Direct transition to Walk Mode on Alt+Q
    is_walk_shortcut = (
        check_shortcut(prefs, 'walk', event, 'Q', fallback_alt=True) or
        ((t == 'Q' or getattr(event, 'unicode', '') in {'q', 'Q'}) and event.alt and not (event.ctrl or event.oskey or event.shift))
    )
    if is_walk_shortcut:
        if v == 'RELEASE':
            return {'RUNNING_MODAL'}
        if v == 'PRESS':
            target_v3d = area or area_at(win, mx, my) or area_by_ptr(win, S.hover_ptr) or context.area
            if target_v3d and target_v3d.type == 'VIEW_3D':
                set_active_mode('WALK', context, op.report, target_area=target_v3d)
            else:
                op.report({'INFO'}, "Vimender: Walk Mode only available in 3D Viewport")
            return {'RUNNING_MODAL'}

    # Direct transition to Navigation Mode on Ctrl+B
    is_nav_shortcut = (
        check_shortcut(prefs, 'nav', event, 'B', fallback_ctrl=True) or
        ((t == 'B' or getattr(event, 'unicode', '') in {'b', 'B'}) and event.ctrl and not (event.alt or event.oskey or event.shift))
    )
    if is_nav_shortcut:
        if v == 'RELEASE':
            return {'RUNNING_MODAL'}
        if v == 'PRESS':
            target_a = area or area_by_ptr(win, S.hover_ptr) or area_at(win, mx, my) or (win.screen.areas[0] if win and win.screen and win.screen.areas else None)
            set_active_mode('NAV', context, op.report, target_area=target_a)
            return {'RUNNING_MODAL'}

    # Direct transition to Preview / Orbit-Pan Mode on Ctrl+Y
    is_preview_shortcut = (
        check_shortcut(prefs, 'preview', event, 'Y', fallback_ctrl=True) or
        ((t == 'Y' or getattr(event, 'unicode', '') in {'y', 'Y'}) and event.ctrl and not (event.alt or event.oskey or event.shift))
    )
    if is_preview_shortcut:
        if v == 'RELEASE':
            return {'RUNNING_MODAL'}
        if v == 'PRESS':
            set_active_mode('ORBIT_PAN', context, op.report)
            return {'RUNNING_MODAL'}

    # Direct transition to Cursor Mode on Ctrl+Numpad5
    is_cursor_shortcut = (
        check_shortcut(prefs, 'cursor', event, 'NUMPAD_5', fallback_ctrl=True) or
        (t == 'NUMPAD_5' and event.ctrl and not (event.alt or event.oskey or event.shift))
    )
    if is_cursor_shortcut:
        if v == 'RELEASE':
            return {'RUNNING_MODAL'}
        if v == 'PRESS':
            set_active_mode('CURSOR', context, op.report)
            return {'RUNNING_MODAL'}

    ui = ui_scale()

    # 2. Transform keyboard shortcuts (Esc, Enter, X, Y, Z, G, R, S)
    if v == 'PRESS':
        if getattr(S, 'tablet_in_transform', None):
            if t in {'ESC'}:
                cancel_tablet_transform(context, area, op.report)
                return {'RUNNING_MODAL'}
            if t in {'RET', 'NUMPAD_ENTER'}:
                confirm_tablet_transform(context, area, op.report)
                return {'RUNNING_MODAL'}
            if not (event.ctrl or event.alt or event.oskey):
                key_ch = (getattr(event, 'unicode', '') or t or '')[:1].upper()
                if key_ch in {'X', 'Y', 'Z'}:
                    if getattr(S, 'tablet_active_axis', None) == key_ch:
                        S.tablet_active_axis = None
                        op.report({'INFO'}, f"Tablet: Axis [{key_ch}] Released (Free View-Plane)")
                    else:
                        S.tablet_active_axis = key_ch
                        op.report({'INFO'}, f"Tablet: Axis [{key_ch}] Locked")
                    tag_all()
                    return {'RUNNING_MODAL'}
                if key_ch in {'G', 'R', 'S'}:
                    if S.tablet_in_transform == key_ch:
                        confirm_tablet_transform(context, area, op.report)
                    else:
                        start_tablet_transform(key_ch, context, area, op.report)
                    tag_all()
                    return {'RUNNING_MODAL'}
        else:
            if not (event.ctrl or event.alt or event.oskey):
                key_ch = (getattr(event, 'unicode', '') or t or '')[:1].upper()
                if key_ch in {'G', 'R', 'S'}:
                    start_tablet_transform(key_ch, context, area, op.report)
                    tag_all()
                    return {'RUNNING_MODAL'}
                if key_ch == 'B':
                    new_tool = 'TWEAK' if getattr(S, 'tablet_select_tool', 'TWEAK') == 'BOX' else 'BOX'
                    set_tablet_select_tool(new_tool, context, area, op.report)
                    return {'RUNNING_MODAL'}
                if key_ch == 'W':
                    set_tablet_select_tool('TWEAK', context, area, op.report)
                    return {'RUNNING_MODAL'}

    # 3. Keep default T toolbar hidden if T is pressed in Tablet Mode
    if v == 'PRESS' and not (event.ctrl or event.alt or event.oskey):
        key_str = (getattr(event, 'unicode', '') or t or '').upper()
        if key_str == 'T':
            if area and area.spaces:
                area.spaces.active.show_region_toolbar = False
            op.report({'INFO'}, "Tablet: Custom Touch Toolbar Active")
            tag_all()
            return {'RUNNING_MODAL'}

    # 4. Sensitivity / Recenter View shortcuts
    if v == 'PRESS':
        if (t in {'NUMPAD_PERIOD', 'PERIOD'} or getattr(event, 'unicode', '') == '.') and not (event.ctrl or event.alt or event.oskey):
            if area and reg:
                with context.temp_override(window=win, screen=win.screen, area=area, region=reg):
                    try:
                        bpy.ops.view3d.view_selected('INVOKE_DEFAULT')
                    except Exception:
                        pass
            op.report({'INFO'}, "Tablet: Center View")
            tag_all()
            return {'RUNNING_MODAL'}

        if (t in {'NUMPAD_PLUS', 'NUMPAD_MINUS', 'PLUS', 'MINUS', 'EQUAL'} or
            (getattr(event, 'unicode', '') in {'+', '-'} and not (event.alt or event.oskey))):
            is_up = t in {'NUMPAD_PLUS', 'PLUS', 'EQUAL'} or getattr(event, 'unicode', '') == '+'
            change_sens(1 if is_up else -1)
            op.report({'INFO'}, f"Tablet Sensitivity: {SENS_LEVELS[S.sens_idx]:.2f}x")
            tag_all()
            return {'RUNNING_MODAL'}

    # 5. Two-finger tap / Right-click -> Cancel Transform or Undo
    if t == 'RIGHTMOUSE' and v == 'PRESS':
        if getattr(S, 'tablet_in_transform', None):
            cancel_tablet_transform(context, area, op.report)
            return {'RUNNING_MODAL'}
        try:
            bpy.ops.ed.undo()
            op.report({'INFO'}, "Tablet: Undo")
        except Exception:
            pass
        tag_all()
        return {'RUNNING_MODAL'}

    # 5. Multi-touch trackpad gestures (Pan / Zoom)
    if t in {'TRACKPADPAN'} and area and reg:
        with context.temp_override(window=win, screen=win.screen, area=area, region=reg):
            try:
                bpy.ops.view3d.move('INVOKE_DEFAULT')
            except Exception:
                pass
        return {'RUNNING_MODAL'}

    if t in {'TRACKPADZOOM'} and area and reg:
        with context.temp_override(window=win, screen=win.screen, area=area, region=reg):
            try:
                bpy.ops.view3d.zoom('INVOKE_DEFAULT')
            except Exception:
                pass
        return {'RUNNING_MODAL'}

    # 6. Touch in 3D Viewport & Left Tablet GUI
    if area and area.type == 'VIEW_3D' and reg:
        rx = mx - reg.x
        ry = my - reg.y
        in_region = (0 <= rx <= reg.width) and (0 <= ry <= reg.height)
        if in_region:
            # Check GUI button hit across all docks
            gui_items = get_tablet_gui_buttons(reg, ui)
            sel_buttons = get_tablet_selection_buttons(reg, ui)
            axis_buttons = get_tablet_axis_buttons(reg, ui)

            hit_sel = None
            for b in sel_buttons:
                if b['x0'] <= rx <= b['x1'] and b['y0'] <= ry <= b['y1']:
                    hit_sel = b
                    break

            hit_axis = None
            for b in axis_buttons:
                if b.get('is_sep'):
                    continue
                if b['x0'] <= rx <= b['x1'] and b['y0'] <= ry <= b['y1']:
                    hit_axis = b
                    break

            hit_gui = None
            for b in gui_items:
                if b.get('is_sep'):
                    continue
                if b['x0'] <= rx <= b['x1'] and b['y0'] <= ry <= b['y1']:
                    hit_gui = b
                    break

            dock_x1 = int(72 * ui)
            for b in (gui_items + sel_buttons):
                if not b.get('is_sep'):
                    dock_x1 = max(dock_x1, b['x1'] + int(8 * ui))

            # Hit on Bottom-Left Selection Modifier Dock
            if hit_sel:
                if t == 'LEFTMOUSE':
                    if v == 'PRESS':
                        bid = hit_sel['id']
                        if bid == 'SEL_NORMAL':
                            S.tablet_select_modifier = None
                            op.report({'INFO'}, "Tablet Select: Direct (Düz Seçim)")
                        elif bid == 'SEL_SHIFT':
                            S.tablet_select_modifier = None if S.tablet_select_modifier == 'SHIFT' else 'SHIFT'
                            op.report({'INFO'}, f"Tablet Select: {'+SHIFT (Çoklu Seçim)' if S.tablet_select_modifier else 'Normal'}")
                        elif bid == 'SEL_CTRL':
                            S.tablet_select_modifier = None if S.tablet_select_modifier == 'CTRL' else 'CTRL'
                            op.report({'INFO'}, f"Tablet Select: {'+CTRL (Seçimden Çıkar)' if S.tablet_select_modifier else 'Normal'}")
                        elif bid == 'SEL_ALT':
                            S.tablet_select_modifier = None if S.tablet_select_modifier == 'ALT' else 'ALT'
                            op.report({'INFO'}, f"Tablet Select: {'+ALT (Derinlik / Döngü Seçim)' if S.tablet_select_modifier else 'Normal'}")
                        sync_tablet_tool_mode(context, area)
                        tag_all()
                        return {'RUNNING_MODAL'}
                    elif v == 'RELEASE':
                        return {'RUNNING_MODAL'}
                elif t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                    return {'RUNNING_MODAL'}

            # Hit on Bottom-Right Tools & Axis Selection GUI (vertically above mode badge)
            if hit_axis:
                if t == 'LEFTMOUSE':
                    if v == 'PRESS':
                        bid = hit_axis['id']
                        if bid == 'TWEAK':
                            set_tablet_select_tool('TWEAK', context, area, op.report)
                        elif bid == 'BOX':
                            set_tablet_select_tool('BOX', context, area, op.report)
                        elif bid == 'OK':
                            confirm_tablet_transform(context, area, op.report)
                        elif bid == 'ESC':
                            cancel_tablet_transform(context, area, op.report)
                        elif bid in {'X', 'Y', 'Z'}:
                            if getattr(S, 'tablet_active_axis', None) == bid:
                                S.tablet_active_axis = None
                                op.report({'INFO'}, f"Tablet: Axis [{bid}] Released (Free View-Plane)")
                            else:
                                S.tablet_active_axis = bid
                                op.report({'INFO'}, f"Tablet: Axis [{bid}] Locked")
                        tag_all()
                        return {'RUNNING_MODAL'}
                    elif v == 'RELEASE':
                        return {'RUNNING_MODAL'}
                elif t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                    return {'RUNNING_MODAL'}

            # Hit on Upper-Left Transform GUI (acts directly as pressing the key on keyboard!)
            if hit_gui:
                if t == 'LEFTMOUSE':
                    if v == 'PRESS':
                        bid = hit_gui['id']
                        if bid == 'FIT':
                            with context.temp_override(window=win, screen=win.screen, area=area, region=reg):
                                try:
                                    bpy.ops.view3d.view_selected('INVOKE_DEFAULT')
                                except Exception:
                                    pass
                            op.report({'INFO'}, "Tablet: Fit View")
                        elif bid in {'G', 'R', 'S'}:
                            if getattr(S, 'tablet_in_transform', None) == bid:
                                confirm_tablet_transform(context, area, op.report)
                            else:
                                start_tablet_transform(bid, context, area, op.report)
                        elif bid == 'REDO':
                            try:
                                bpy.ops.ed.redo()
                            except Exception:
                                pass
                            op.report({'INFO'}, "Tablet: Redo")
                        elif bid == 'UNDO':
                            try:
                                bpy.ops.ed.undo()
                            except Exception:
                                pass
                            op.report({'INFO'}, "Tablet: Undo")
                        tag_all()
                        return {'RUNNING_MODAL'}
                    elif v == 'RELEASE':
                        return {'RUNNING_MODAL'}
                elif t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                    return {'RUNNING_MODAL'}

            # Swallow clicks and moves inside the left dock strip
            if rx <= dock_x1:
                if t == 'LEFTMOUSE' or t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                    return {'RUNNING_MODAL'}

            # Swallow clicks and moves inside the right axis dock
            ax_real = [b for b in axis_buttons if not b.get('is_sep')]
            if ax_real:
                ax_x0 = min(b['x0'] for b in ax_real) - int(6 * ui)
                ax_x1 = max(b['x1'] for b in ax_real) + int(6 * ui)
                ax_y0 = min(b['y0'] for b in ax_real) - int(6 * ui)
                ax_y1 = max(b['y1'] for b in ax_real) + int(6 * ui)
                if ax_x0 <= rx <= ax_x1 and ax_y0 <= ry <= ax_y1:
                    if t == 'LEFTMOUSE' or t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                        return {'RUNNING_MODAL'}

            # Swallow clicks on bottom-right mode badge area
            if rx >= (reg.width - int(240 * ui)) and ry <= int(48 * ui):
                if t == 'LEFTMOUSE' or t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                    return {'RUNNING_MODAL'}

            # Stylus / Wacom Pen separation in sculpt or paint modes
            if getattr(event, 'is_tablet', False) and context.mode in {'SCULPT', 'PAINT_TEXTURE', 'PAINT_WEIGHT', 'PAINT_VERTEX', 'PAINT_GPENCIL'}:
                return {'PASS_THROUGH'}

            # Blender Native Box Select Mode:
            # Pass clicks and drags straight through to Blender's native select_box tool!
            # (Prevents C segfaults, lets Blender draw the dashed box and select elements natively)
            if getattr(S, 'tablet_select_tool', None) == 'BOX' and not getattr(S, 'tablet_in_transform', None):
                if t in {'LEFTMOUSE', 'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                    return {'PASS_THROUGH'}

            # Tap selection and drag handling in 3D Viewport
            if t == 'LEFTMOUSE':
                if v == 'PRESS':
                    S.tablet_touch_start = (time.perf_counter(), mx, my)
                    S.tablet_touch_dragged = False
                    return {'RUNNING_MODAL'}
                elif v == 'RELEASE':
                    if S.tablet_touch_start is not None:
                        t0, x0, y0 = S.tablet_touch_start
                        dt = time.perf_counter() - t0
                        dist = math.hypot(mx - x0, my - y0)
                        S.tablet_touch_start = None

                        if not S.tablet_touch_dragged and dt < 0.40 and dist < 22.0:
                            if getattr(S, 'tablet_in_transform', None):
                                confirm_tablet_transform(context, area, op.report)
                                tag_all()
                                return {'RUNNING_MODAL'}

                            # CLICK / TAP DETECTED: Select object under tap with active modifier
                            rv3d = area.spaces.active.region_3d if area.spaces else None
                            if rv3d and reg:
                                try:
                                    eff_mod = getattr(S, 'tablet_select_modifier', None)
                                    if event.shift:
                                        eff_mod = 'SHIFT'
                                    elif event.ctrl:
                                        eff_mod = 'CTRL'
                                    elif event.alt:
                                        eff_mod = 'ALT'

                                    origin = region_2d_to_origin_3d(reg, rv3d, (rx, ry))
                                    direction = region_2d_to_vector_3d(reg, rv3d, (rx, ry))
                                    depsgraph = context.evaluated_depsgraph_get()
                                    hit, loc, norm, idx, hit_obj, matrix = context.scene.ray_cast(depsgraph, origin, direction)
                                    target_obj = None
                                    if hit and hit_obj:
                                        target_obj = hit_obj.original
                                    else:
                                        # Screen distance fallback for non-mesh objects (Camera, Light, Empty, Curves)
                                        best_d = 36.0 * ui
                                        for o in context.visible_objects:
                                            p2d = location_3d_to_region_2d(reg, rv3d, o.matrix_world.translation)
                                            if p2d:
                                                d = math.hypot(p2d[0] - rx, p2d[1] - ry)
                                                if d < best_d:
                                                    best_d = d
                                                    target_obj = o

                                    if eff_mod == 'SHIFT':
                                        if target_obj:
                                            new_s = not target_obj.select_get()
                                            target_obj.select_set(new_s)
                                            if new_s:
                                                context.view_layer.objects.active = target_obj
                                            op.report({'INFO'}, f"Shift-Select: {'+' if new_s else '-'}{target_obj.name}")
                                    elif eff_mod == 'CTRL':
                                        if target_obj:
                                            target_obj.select_set(False)
                                            if context.view_layer.objects.active == target_obj:
                                                sel = [o for o in context.selected_objects if o != target_obj]
                                                context.view_layer.objects.active = sel[0] if sel else None
                                            op.report({'INFO'}, f"Ctrl-Deselect: {target_obj.name}")
                                    elif eff_mod == 'ALT':
                                        candidates = []
                                        for o in context.visible_objects:
                                            p2d = location_3d_to_region_2d(reg, rv3d, o.matrix_world.translation)
                                            if p2d and math.hypot(p2d[0] - rx, p2d[1] - ry) < 40.0 * ui:
                                                candidates.append(o)
                                        if target_obj and target_obj not in candidates:
                                            candidates.insert(0, target_obj)
                                        if candidates:
                                            cur_act = context.view_layer.objects.active
                                            if cur_act in candidates:
                                                next_i = (candidates.index(cur_act) + 1) % len(candidates)
                                                target_obj = candidates[next_i]
                                            else:
                                                target_obj = candidates[0]
                                            for o in context.selected_objects:
                                                o.select_set(False)
                                            target_obj.select_set(True)
                                            context.view_layer.objects.active = target_obj
                                            op.report({'INFO'}, f"Alt Depth-Select: {target_obj.name}")
                                        elif context.mode == 'EDIT_MESH':
                                            with context.temp_override(window=win, screen=win.screen, area=area, region=reg):
                                                try:
                                                    bpy.ops.mesh.loop_select('INVOKE_DEFAULT')
                                                except Exception:
                                                    pass
                                    else:
                                        # Normal direct select
                                        if target_obj:
                                            for o in context.selected_objects:
                                                if o != target_obj:
                                                    o.select_set(False)
                                            target_obj.select_set(True)
                                            context.view_layer.objects.active = target_obj
                                            op.report({'INFO'}, f"Selected: {target_obj.name}")
                                        else:
                                            for o in context.selected_objects:
                                                o.select_set(False)
                                            op.report({'INFO'}, "Deselected all")
                                except Exception as err:
                                    print(f"[Vimender Tablet] Selection error: {err}")
                            tag_all()
                            return {'RUNNING_MODAL'}
                    return {'RUNNING_MODAL'}

            elif t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                if S.tablet_touch_start is not None:
                    t0, x0, y0 = S.tablet_touch_start
                    dist = math.hypot(mx - x0, my - y0)
                    if dist > 8.0:
                        S.tablet_touch_dragged = True


                # Direct touch/mouse drag object transform
                if S.tablet_touch_dragged and getattr(S, 'tablet_in_transform', None):
                    dx = event.mouse_x - event.mouse_prev_x
                    dy = event.mouse_y - event.mouse_prev_y
                    if abs(dx) > 0.01 or abs(dy) > 0.01:
                        sens = SENS_LEVELS[S.sens_idx]
                        apply_touch_transform(context, area, dx, dy, sens, is_blender_coords=True)
                        area.tag_redraw()
                    return {'RUNNING_MODAL'}

                if _tracker.active or getattr(S, 'tablet_select_tool', None) == 'BOX':
                    return {'RUNNING_MODAL'}

            # Fallback when evdev device permissions are not yet configured:
            if not _tracker.active and S.tablet_touch_dragged and not getattr(S, 'tablet_in_transform', None) and getattr(S, 'tablet_select_tool', None) != 'BOX':
                if (mx >= reg.x + reg.width - 75) or event.ctrl:
                    with context.temp_override(window=win, screen=win.screen, area=area, region=reg):
                        try:
                            bpy.ops.view3d.zoom('INVOKE_DEFAULT')
                            return {'RUNNING_MODAL'}
                        except Exception:
                            pass
                elif (my <= reg.y + 70) or event.shift:
                    with context.temp_override(window=win, screen=win.screen, area=area, region=reg):
                        try:
                            bpy.ops.view3d.move('INVOKE_DEFAULT')
                            return {'RUNNING_MODAL'}
                        except Exception:
                            pass
                else:
                    with context.temp_override(window=win, screen=win.screen, area=area, region=reg):
                        try:
                            bpy.ops.view3d.rotate('INVOKE_DEFAULT')
                            return {'RUNNING_MODAL'}
                        except Exception:
                            pass

    return {'PASS_THROUGH'}
