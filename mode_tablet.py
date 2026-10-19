import math
import os
import select
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
    get_active_transform_type, load_vimender_config
)

try:
    import evdev
    from evdev import ecodes
    HAVE_EVDEV = True
except ImportError:
    HAVE_EVDEV = False

if HAVE_EVDEV:
    MT_TOOL_PALM = getattr(ecodes, 'MT_TOOL_PALM', 2)
    INPUT_PROP_DIRECT = getattr(ecodes, 'INPUT_PROP_DIRECT', 0x01)
else:
    MT_TOOL_PALM = 2
    INPUT_PROP_DIRECT = 0x01


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


# ───────────────────────────── Touch gesture tuning ─────────────────────────────
TOUCH_SLOP_N = 0.004                  # screen fraction a finger must travel before a drag starts
PINCH_LATCH = 0.05                    # |ln(distance ratio)| before pinch-zoom engages (~5 %)
TWIST_LATCH = math.radians(12.0)      # twist angle before two-finger rotate engages
ORBIT_RAD_PER_WIDTH = math.pi         # one full-width swipe = 180° at sensitivity 1.0
ORBIT_ABOUT_EYE = False               # False = true orbit around the pivot (iPad style)
INERTIA_DECAY = 4.5                   # 1/s, higher = stops sooner
INERTIA_MIN_ORBIT = 0.03              # rad/s
INERTIA_MIN_PAN = 15.0                # px/s
INERTIA_MIN_ZOOM = 0.03               # ln-ratio/s
INERTIA_RELEASE_WINDOW = 0.10         # s: finger must still be moving this recently at lift-off
VIEW_SENSOR_MM = 36.0                 # Blender's default viewport sensor width


def _wrap_angle(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi


class EvdevMultiTouchTracker:
    """
    Linux hardware multi-touch recognizer (python-evdev, MT protocol type B).

    Produces *normalized* (0..1 of the screen) accumulated gesture deltas that
    the main thread consumes in _TouchNavigator.tick():
      - 1 finger : orbit (or direct object transform while a transform is active)
      - 2 fingers: pan (midpoint), pinch-zoom (distance), twist (angle)
      - 2-finger quick tap: undo      - 3-finger quick tap: redo
    Palm contacts are ignored. Each gesture engages only after a small slop so
    taps stay taps and a pinch doesn't jitter the pan.
    """
    TOUCH_PATHS = [
        "/dev/input/by-path/platform-AMDI0010:02-event",
        "/dev/input/event7",
    ]

    def __init__(self):
        self.dev = None
        self.thread = None
        self._stop_evt = None
        self.running = False
        self.active = False
        self.device_kind = ""
        self.last_error = ""
        self.lock = threading.Lock()

        self.current_slot = 0
        self.slots = {}   # slot_id: {'x','y','down','palm'}
        self.x_min, self.x_rng = 0.0, 4096.0
        self.y_min, self.y_rng = 0.0, 4096.0

        # tap detection
        self.touch_start_time = 0.0
        self.touch_max_fingers = 0
        self.touch_moved = False
        self._last_n = 0
        self._reset_tracking()

        # queued for the Blender main thread (guarded by self.lock)
        self.orbit_delta = [0.0, 0.0]       # normalized
        self.pan_delta = [0.0, 0.0]         # normalized (midpoint movement)
        self.zoom_log = 0.0                 # sum of ln(dist_now / dist_prev)
        self.twist = 0.0                    # radians, clockwise-positive on screen
        self.transform_delta = [0.0, 0.0]   # RAW device units (legacy transform path)
        self.mid_n = None                   # latest two-finger midpoint, normalized
        self.finger_count = 0
        self.touch_serial = 0               # increments whenever a new touch starts
        self.pending_undo = False
        self.pending_redo = False

    # ---- device discovery -------------------------------------------------
    @staticmethod
    def _classify(d):
        """'touchscreen' | 'pointer' (touchpad) | 'unknown' | None (not multitouch)."""
        try:
            caps = d.capabilities()
        except Exception:
            return None
        codes = [c[0] if isinstance(c, tuple) else c for c in caps.get(ecodes.EV_ABS, [])]
        if ecodes.ABS_MT_POSITION_X not in codes:
            return None
        try:
            props = d.input_props()
        except Exception:
            return 'unknown'
        return 'touchscreen' if INPUT_PROP_DIRECT in props else 'pointer'

    def find_device(self):
        if not HAVE_EVDEV:
            self.last_error = "python-evdev is not installed in Blender's Python"
            return None
        cfg_path = load_vimender_config().get('touch_device')
        paths = ([cfg_path] if cfg_path else []) + list(self.TOUCH_PATHS)
        for p in paths:
            if p and os.path.exists(p) and os.access(p, os.R_OK):
                try:
                    d = evdev.InputDevice(p)
                    kind = self._classify(d)
                    if kind in {'touchscreen', 'unknown'} or (cfg_path and p == cfg_path and kind):
                        self.device_kind = kind
                        return d
                    d.close()
                except Exception:
                    pass
        best = None
        try:
            for path in evdev.list_devices():
                try:
                    d = evdev.InputDevice(path)
                except Exception:
                    continue
                kind = self._classify(d)
                if kind == 'touchscreen':
                    if best:
                        best.close()
                    self.device_kind = kind
                    return d
                if kind == 'unknown' and best is None:
                    best = d
                    self.device_kind = kind
                else:
                    d.close()
        except Exception:
            pass
        if best is None:
            self.last_error = ("no readable touchscreen in /dev/input "
                               "(add your user to the 'input' group and re-login)")
        return best

    def _load_ranges(self, dev):
        try:
            ax = dev.absinfo(ecodes.ABS_MT_POSITION_X)
            ay = dev.absinfo(ecodes.ABS_MT_POSITION_Y)
            self.x_min, self.x_rng = float(ax.min), float(max(ax.max - ax.min, 1))
            self.y_min, self.y_rng = float(ay.min), float(max(ay.max - ay.min, 1))
        except Exception:
            self.x_min, self.x_rng = 0.0, 4096.0
            self.y_min, self.y_rng = 0.0, 4096.0

    def start(self):
        if self.running and self.dev:
            return True
        self.stop()
        dev = self.find_device()
        if not dev:
            self.active = False
            print(f"[Vimender] Touch unavailable: {self.last_error}")
            return False
        self.dev = dev
        self._load_ranges(dev)
        self.running = True
        self.active = True
        self.slots.clear()
        self.current_slot = 0
        self._last_n = 0
        self._reset_gesture()
        with self.lock:
            self.orbit_delta = [0.0, 0.0]
            self.pan_delta = [0.0, 0.0]
            self.transform_delta = [0.0, 0.0]
            self.zoom_log = 0.0
            self.twist = 0.0
            self.mid_n = None
            self.finger_count = 0
        self._stop_evt = threading.Event()
        self.thread = threading.Thread(target=self._read_loop, args=(dev, self._stop_evt),
                                       daemon=True, name="EvdevMT")
        self.thread.start()
        print(f"[Vimender] Multi-touch active on {dev.name} ({dev.path}) [{self.device_kind}]")
        return True

    def stop(self):
        self.running = False
        if self._stop_evt:
            self._stop_evt.set()
            self._stop_evt = None
        if self.dev:
            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None
        self.thread = None
        self.active = False

    # ---- reader thread ----------------------------------------------------
    def _read_loop(self, dev, stop_evt):
        while not stop_evt.is_set():
            try:
                ready, _, _ = select.select([dev.fd], [], [], 0.05)
                if not ready:
                    continue
                for ev in dev.read():
                    if ev.type == ecodes.EV_ABS:
                        c, val = ev.code, ev.value
                        if c == ecodes.ABS_MT_SLOT:
                            self.current_slot = val
                            continue
                        s = self.slots.setdefault(
                            self.current_slot, {'x': 0.0, 'y': 0.0, 'down': True, 'palm': False})
                        if c == ecodes.ABS_MT_TRACKING_ID:
                            s['down'] = (val != -1)
                            if val != -1:
                                s['palm'] = False
                        elif c == ecodes.ABS_MT_POSITION_X:
                            s['x'] = float(val)
                        elif c == ecodes.ABS_MT_POSITION_Y:
                            s['y'] = float(val)
                        elif c == ecodes.ABS_MT_TOOL_TYPE:
                            s['palm'] = (val == MT_TOOL_PALM)
                    elif ev.type == ecodes.EV_SYN and ev.code == ecodes.SYN_REPORT:
                        self._process_frame()
            except BlockingIOError:
                time.sleep(0.003)
            except Exception as e:
                if not stop_evt.is_set():
                    self.last_error = str(e)
                break
        if self.dev is dev:
            self.active = False

    # ---- gesture state ----------------------------------------------------
    def _reset_tracking(self):
        self.prev_single = None       # (nx, ny, rx, ry)
        self.start_n = None
        self.drag_engaged = False
        self.prev_mid_n = None
        self.prev_dist = None
        self.prev_angle = None
        self.acc_pan = 0.0
        self.acc_zoom = 0.0
        self.acc_twist = 0.0
        self.pan_engaged = False
        self.zoom_engaged = False
        self.twist_engaged = False

    def _reset_gesture(self):
        self._reset_tracking()
        self.touch_max_fingers = 0
        self.touch_moved = False

    def _norm(self, x, y):
        return (x - self.x_min) / self.x_rng, (y - self.y_min) / self.y_rng

    def _process_frame(self):
        active = [s for s in self.slots.values() if s.get('down', False) and not s.get('palm', False)]
        n = len(active)
        now = time.perf_counter()

        with self.lock:
            if n > 0 and self.finger_count == 0:
                self.touch_serial += 1
            self.finger_count = n

        if n == 0:
            if not self.touch_moved and (now - self.touch_start_time) < 0.32:
                with self.lock:
                    if self.touch_max_fingers == 2:
                        self.pending_undo = True
                    elif self.touch_max_fingers == 3:
                        self.pending_redo = True
            self._last_n = 0
            self._reset_gesture()
            return

        if self.touch_max_fingers == 0:
            self.touch_start_time = now
            self.touch_moved = False
        self.touch_max_fingers = max(self.touch_max_fingers, n)

        if n != self._last_n:           # finger count changed: start tracking afresh
            self._reset_tracking()
            self._last_n = n

        if n == 1:
            x, y = active[0]['x'], active[0]['y']
            nx, ny = self._norm(x, y)
            if self.start_n is None:
                self.start_n = (nx, ny)
            if self.prev_single is not None:
                pnx, pny, px, py = self.prev_single
                dx, dy = x - px, y - py
                if not self.drag_engaged and math.hypot(nx - self.start_n[0], ny - self.start_n[1]) > TOUCH_SLOP_N:
                    self.drag_engaged = True
                    self.touch_moved = True
                transforming = (getattr(S, 'tablet_in_transform', None) or
                                getattr(S, 'tablet_active_axis', None) in {'X', 'Y', 'Z'})
                with self.lock:
                    if transforming:
                        if abs(dx) > 1.0 or abs(dy) > 1.0:
                            self.touch_moved = True
                            self.transform_delta[0] += dx
                            self.transform_delta[1] += dy
                    elif self.drag_engaged and getattr(S, 'tablet_select_tool', None) != 'BOX':
                        self.orbit_delta[0] += nx - pnx
                        self.orbit_delta[1] += ny - pny
            self.prev_single = (nx, ny, x, y)

        elif n == 2:
            self.prev_single = None
            p0, p1 = active[0], active[1]
            mx, my = (p0['x'] + p1['x']) * 0.5, (p0['y'] + p1['y']) * 0.5
            mid_n = self._norm(mx, my)
            dist = math.hypot(p1['x'] - p0['x'], p1['y'] - p0['y'])
            angle = math.atan2(p1['y'] - p0['y'], p1['x'] - p0['x'])   # y is down => clockwise-positive
            with self.lock:
                self.mid_n = mid_n

            if getattr(S, 'tablet_in_transform', None):
                self.prev_mid_n = None
                self.prev_dist = None
                self.prev_angle = None
                return

            if self.prev_mid_n is not None and self.prev_dist and dist > 1.0:
                dmx, dmy = mid_n[0] - self.prev_mid_n[0], mid_n[1] - self.prev_mid_n[1]
                dlog = math.log(dist / self.prev_dist)
                dang = _wrap_angle(angle - self.prev_angle)

                self.acc_pan += math.hypot(dmx, dmy)
                self.acc_zoom += dlog
                self.acc_twist += dang
                pan_thresh = 0.003
                zoom_thresh = 0.004
                if not self.pan_engaged and self.acc_pan > pan_thresh:
                    self.pan_engaged = True
                if not self.zoom_engaged and abs(self.acc_zoom) > zoom_thresh:
                    self.zoom_engaged = True
                if not is_pen and not self.twist_engaged and abs(self.acc_twist) > TWIST_LATCH:
                    self.twist_engaged = True
                if self.pan_engaged or self.zoom_engaged or self.twist_engaged:
                    self.touch_moved = True

                with self.lock:
                    if self.pan_engaged:
                        self.pan_delta[0] += dmx
                        self.pan_delta[1] += dmy
                    if self.zoom_engaged:
                        self.zoom_log += dlog
                    if self.twist_engaged:
                        self.twist += dang
            self.prev_mid_n = mid_n
            self.prev_dist = dist
            self.prev_angle = angle

        else:   # 3+ fingers: no navigation (3-finger tap = redo)
            self.prev_single = None
            self.prev_mid_n = None
            self.prev_dist = None
            self.prev_angle = None


_tracker = EvdevMultiTouchTracker()


class _TouchNavigator:
    """
    Consumes the tracker's gesture deltas on Blender's main thread and drives the
    3D view like an iPad app:
      - pan sticks to the fingers 1:1,
      - pinch zooms about the point between the fingers,
      - two-finger twist rolls the view (set "touch_twist": "OFF" in
        vimender_config.json to disable),
      - one-finger orbit, with momentum (inertia) after a flick.
    """
    def __init__(self):
        self.last_tap_time = 0.0
        self.last_tap_x = 0.0
        self.last_tap_y = 0.0
        self._clear_motion()

    def _clear_motion(self):
        self.v_yaw = 0.0
        self.v_pitch = 0.0
        self.v_pan = [0.0, 0.0]       # px/s, region coords (y up)
        self.v_zoom = 0.0             # ln-ratio per second
        self.last_input_t = 0.0
        self.coasting = False
        self._last_t = 0.0
        self._serial = -1
        self._anchor = (0.0, 0.0)     # last two-finger midpoint, px from region centre

    def reset(self):
        self.last_tap_time = 0.0
        self.last_tap_x = 0.0
        self.last_tap_y = 0.0
        self._clear_motion()
        _tracker.stop()

    def _stop_inertia(self):
        self.v_yaw = self.v_pitch = self.v_zoom = 0.0
        self.v_pan = [0.0, 0.0]
        self.coasting = False

    def _step(self, rv3d, space, reg, yaw, pitch, roll, dpx, dpy, zlog, anchor):
        """Apply one frame of navigation. Uses only live rv3d properties (never the
        lazily-updated view matrices), so it stays correct between redraws."""
        changed = False
        rot = rv3d.view_rotation
        d = rv3d.view_distance

        if roll:
            rv3d.view_rotation = (rot @ Quaternion(Vector((0.0, 0.0, 1.0)), roll)).normalized()
            rot = rv3d.view_rotation
            changed = True

        if yaw or pitch:
            up_vec = rot @ Vector((0.0, 1.0, 0.0))
            right = rot @ Vector((1.0, 0.0, 0.0))
            eye = rv3d.view_location + (rot @ Vector((0.0, 0.0, d)))
            q_pitch = Quaternion(right, pitch)
            if up_vec.z > 0.05:                      # turntable: keep the horizon level
                q_yaw = Quaternion(Vector((0.0, 0.0, 1.0)), yaw)
                test_rot = (q_pitch @ rot).normalized()
                if (test_rot @ Vector((0.0, 1.0, 0.0))).z > 0.02:
                    new_rot = (q_yaw @ test_rot).normalized()
                else:
                    new_rot = (q_yaw @ rot).normalized()
            else:                                    # rolled / upside-down: free trackball
                q_yaw = Quaternion(up_vec, yaw)
                new_rot = (q_yaw @ q_pitch @ rot).normalized()
            rv3d.view_rotation = new_rot
            if ORBIT_ABOUT_EYE:
                rv3d.view_location = eye - (new_rot @ Vector((0.0, 0.0, d)))
            rot = new_rot
            changed = True

        if dpx or dpy or zlog:
            f = max(0.2, min(5.0, math.exp(-zlog)))      # fingers spread => view_distance shrinks
            d_new = max(d * f, 0.001)
            f = d_new / d
            lens = max(getattr(space, 'lens', 50.0), 1.0)
            # world units per screen pixel on the focus plane (same for persp and ortho)
            u = d * VIEW_SENSOR_MM / (lens * max(reg.width, reg.height, 1))
            right = rot @ Vector((1.0, 0.0, 0.0))
            up = rot @ Vector((0.0, 1.0, 0.0))
            sx, sy = anchor
            s_cur = (right * sx + up * sy) * u
            s_prev = (right * (sx - dpx) + up * (sy - dpy)) * u
            # keeps the world point that was under the fingers under the fingers
            rv3d.view_location = rv3d.view_location + s_prev - s_cur * f
            rv3d.view_distance = d_new
            changed = True
        return changed

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
        space = area.spaces.active if area.spaces else None
        rv3d = space.region_3d if space else None
        reg = window_region(area)
        if not rv3d or not reg:
            return False

        with _tracker.lock:
            odx, ody = _tracker.orbit_delta
            _tracker.orbit_delta[0] = _tracker.orbit_delta[1] = 0.0
            tdx, tdy = _tracker.transform_delta
            _tracker.transform_delta[0] = _tracker.transform_delta[1] = 0.0
            pdx, pdy = _tracker.pan_delta
            _tracker.pan_delta[0] = _tracker.pan_delta[1] = 0.0
            zlog = _tracker.zoom_log
            _tracker.zoom_log = 0.0
            twist = _tracker.twist
            _tracker.twist = 0.0
            undo, _tracker.pending_undo = _tracker.pending_undo, False
            redo, _tracker.pending_redo = _tracker.pending_redo, False
            mid_n = _tracker.mid_n
            fingers = _tracker.finger_count
            serial = _tracker.touch_serial

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

        now = time.perf_counter()
        dt = min(max(now - self._last_t, 0.001), 0.05) if self._last_t else 0.016
        self._last_t = now

        if serial != self._serial:          # a new touch stops any momentum
            self._serial = serial
            self._stop_inertia()

        sens = SENS_LEVELS[S.sens_idx]
        changed = False

        # direct object transform (raw device units, unchanged behaviour)
        if abs(tdx) > 0.05 or abs(tdy) > 0.05:
            if apply_touch_transform(context, area, tdx, tdy, sens):
                changed = True

        if getattr(S, 'tablet_in_transform', None) or getattr(S, 'tablet_select_tool', None) == 'BOX':
            odx = ody = pdx = pdy = zlog = twist = 0.0
            self._stop_inertia()

        W = max(float(win.width), 1.0)
        H = max(float(win.height), 1.0)

        # normalized touch deltas -> radians / pixels
        yaw = -odx * ORBIT_RAD_PER_WIDTH * sens
        pitch = -ody * (H / W) * ORBIT_RAD_PER_WIDTH * sens
        dpx = pdx * W * sens
        dpy = -pdy * H * sens                # evdev y is down, Blender y is up
        effective_zlog = zlog * 1.5 * sens
        roll = twist if str(load_vimender_config().get('touch_twist', 'ROLL')).upper() != 'OFF' else 0.0

        if mid_n is not None:
            self._anchor = (mid_n[0] * W - reg.x - reg.width * 0.5,
                            (1.0 - mid_n[1]) * H - reg.y - reg.height * 0.5)

        live = bool(yaw or pitch or dpx or dpy or effective_zlog or roll)
        if live:
            self.last_input_t = now
            self.coasting = False
            k = 0.5   # smooth the release velocity
            self.v_yaw = self.v_yaw * (1 - k) + (yaw / dt) * k
            self.v_pitch = self.v_pitch * (1 - k) + (pitch / dt) * k
            self.v_pan[0] = self.v_pan[0] * (1 - k) + (dpx / dt) * k
            self.v_pan[1] = self.v_pan[1] * (1 - k) + (dpy / dt) * k
            self.v_zoom = 0.0  # zero zoom inertia on release to match Pen Mode's rock-solid feel
        elif fingers == 0:
            if not self.coasting:
                if now - self.last_input_t > INERTIA_RELEASE_WINDOW:
                    self._stop_inertia()         # finger rested before lifting: no flick
                else:
                    self.coasting = True
            if self.coasting:
                if (abs(self.v_yaw) < INERTIA_MIN_ORBIT and abs(self.v_pitch) < INERTIA_MIN_ORBIT and
                        math.hypot(*self.v_pan) < INERTIA_MIN_PAN):
                    self._stop_inertia()
                else:
                    yaw, pitch = self.v_yaw * dt, self.v_pitch * dt
                    dpx, dpy = self.v_pan[0] * dt, self.v_pan[1] * dt
                    decay = math.exp(-INERTIA_DECAY * dt)
                    self.v_yaw *= decay
                    self.v_pitch *= decay
                    self.v_pan[0] *= decay
                    self.v_pan[1] *= decay

        cur_zlog = effective_zlog if live else 0.0
        if yaw or pitch or roll or dpx or dpy or cur_zlog:
            if rv3d.view_perspective == 'CAMERA' and not getattr(space, 'lock_camera', False):
                rv3d.view_perspective = 'PERSP'
            if self._step(rv3d, space, reg, yaw, pitch, roll, dpx, dpy, cur_zlog, self._anchor):
                changed = True

        if changed:
            area.tag_redraw()
        return changed


S.touch_nav = _TouchNavigator()


class EvdevStylusTracker:
    """
    Direct Linux hardware tracker for Stylus / Wacom Digital Pen buttons and motion.
    Reads /dev/input/event6 directly via evdev to provide 100% reliable Upper Button Pan
    under Native Wayland without relying on Mutter forwarding or XWayland workarounds.
    """
    STYLUS_PATHS = [
        "/dev/input/by-path/platform-AMDI0010:02-event-mouse",
        "/dev/input/event6",
    ]

    def __init__(self):
        self.dev = None
        self.thread = None
        self._stop_evt = None
        self.running = False
        self.active = False
        self.last_error = ""
        self.lock = threading.Lock()

        self.btn_pan_down = False
        self.last_x = None
        self.last_y = None
        self.pan_delta = [0.0, 0.0]
        self.x_min, self.x_rng = 0.0, 30182.0
        self.y_min, self.y_rng = 0.0, 18864.0

    def find_device(self):
        if not HAVE_EVDEV:
            self.last_error = "python-evdev is not installed in Blender's Python"
            return None
        cfg_path = load_vimender_config().get('stylus_device')
        paths = ([cfg_path] if cfg_path else []) + list(self.STYLUS_PATHS)
        for p in paths:
            if p and os.path.exists(p) and os.access(p, os.R_OK):
                try:
                    d = evdev.InputDevice(p)
                    caps = d.capabilities()
                    if ecodes.EV_KEY in caps:
                        keys = caps[ecodes.EV_KEY]
                        if ecodes.BTN_TOOL_PEN in keys or ecodes.BTN_STYLUS in keys:
                            return d
                    d.close()
                except Exception:
                    pass
        try:
            for p in evdev.list_devices():
                try:
                    d = evdev.InputDevice(p)
                    caps = d.capabilities()
                    if ecodes.EV_KEY in caps:
                        keys = caps[ecodes.EV_KEY]
                        if ecodes.BTN_TOOL_PEN in keys or ecodes.BTN_STYLUS in keys:
                            return d
                    d.close()
                except Exception:
                    pass
        except Exception:
            pass
        self.last_error = "No readable pen / stylus device found"
        return None

    def _load_ranges(self, dev):
        try:
            ax = dev.absinfo(ecodes.ABS_X)
            ay = dev.absinfo(ecodes.ABS_Y)
            self.x_min, self.x_rng = float(ax.min), float(max(ax.max - ax.min, 1))
            self.y_min, self.y_rng = float(ay.min), float(max(ay.max - ay.min, 1))
        except Exception:
            self.x_min, self.x_rng = 0.0, 30182.0
            self.y_min, self.y_rng = 0.0, 18864.0

    def start(self):
        if self.running and self.dev:
            return True
        self.stop()
        dev = self.find_device()
        if not dev:
            self.active = False
            return False
        self.dev = dev
        self._load_ranges(dev)
        self.running = True
        self.active = True
        with self.lock:
            self.btn_pan_down = False
            self.last_x = None
            self.last_y = None
            self.pan_delta = [0.0, 0.0]
        self._stop_evt = threading.Event()
        self.thread = threading.Thread(target=self._read_loop, args=(dev, self._stop_evt),
                                       daemon=True, name="EvdevStylus")
        self.thread.start()
        print(f"[Vimender] Stylus hardware listener active on {dev.name} ({dev.path})")
        return True

    def stop(self):
        self.running = False
        if self._stop_evt:
            self._stop_evt.set()
            self._stop_evt = None
        if self.dev:
            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None
        self.thread = None
        self.active = False

    def _read_loop(self, dev, stop_evt):
        curr_x = None
        curr_y = None
        btn_pan = False

        while not stop_evt.is_set():
            try:
                ready, _, _ = select.select([dev.fd], [], [], 0.02)
                if not ready:
                    continue
                pan_btn_mode = str(load_vimender_config().get('stylus_pan_button', 'BTN_STYLUS')).upper()
                for ev in dev.read():
                    if ev.type == ecodes.EV_KEY:
                        is_pan_btn = False
                        if pan_btn_mode == 'BTN_STYLUS2':
                            is_pan_btn = (ev.code == ecodes.BTN_STYLUS2)
                        elif pan_btn_mode == 'BOTH':
                            is_pan_btn = (ev.code in (ecodes.BTN_STYLUS, ecodes.BTN_STYLUS2))
                        else:
                            is_pan_btn = (ev.code == ecodes.BTN_STYLUS)

                        if is_pan_btn:
                            btn_pan = bool(ev.value)
                            with self.lock:
                                self.btn_pan_down = btn_pan
                                if not btn_pan:
                                    self.last_x = None
                                    self.last_y = None
                    elif ev.type == ecodes.EV_ABS:
                        if ev.code == ecodes.ABS_X:
                            curr_x = float(ev.value)
                        elif ev.code == ecodes.ABS_Y:
                            curr_y = float(ev.value)
                    elif ev.type == ecodes.EV_SYN and ev.code == ecodes.SYN_REPORT:
                        if btn_pan and curr_x is not None and curr_y is not None:
                            with self.lock:
                                if self.last_x is not None and self.last_y is not None:
                                    dx = (curr_x - self.last_x) / self.x_rng
                                    dy = (curr_y - self.last_y) / self.y_rng
                                    self.pan_delta[0] += dx
                                    self.pan_delta[1] += dy
                                self.last_x = curr_x
                                self.last_y = curr_y
                        else:
                            with self.lock:
                                self.last_x = curr_x
                                self.last_y = curr_y
            except BlockingIOError:
                time.sleep(0.003)
            except Exception:
                break
        if self.dev is dev:
            self.active = False


_stylus_tracker = EvdevStylusTracker()


class _StylusNavigator:
    """
    Consumes stylus tracker pan deltas on Blender's main thread and pans
    the active 3D viewport smoothly when the upper barrel button is held.
    """
    def reset(self):
        with _stylus_tracker.lock:
            _stylus_tracker.pan_delta[0] = _stylus_tracker.pan_delta[1] = 0.0
            _stylus_tracker.btn_pan_down = False
            _stylus_tracker.last_x = None
            _stylus_tracker.last_y = None

    def tick(self, context=None, win=None):
        if not getattr(_stylus_tracker, 'active', False):
            return False

        with _stylus_tracker.lock:
            pdx, pdy = _stylus_tracker.pan_delta
            _stylus_tracker.pan_delta[0] = _stylus_tracker.pan_delta[1] = 0.0

        if pdx == 0.0 and pdy == 0.0:
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

        W = max(float(win.width), 1.0)
        H = max(float(win.height), 1.0)
        dpx = pdx * W
        dpy = -pdy * H  # evdev y is down, Blender y is up

        rot = rv3d.view_rotation
        d = rv3d.view_distance
        sens = SENS_LEVELS[S.sens_idx] if hasattr(S, 'sens_idx') else 1.0
        lens = max(getattr(space, 'lens', 50.0), 1.0)
        u = d * VIEW_SENSOR_MM / (lens * max(reg.width, reg.height, 1)) * sens
        right = rot @ Vector((1.0, 0.0, 0.0))
        up = rot @ Vector((0.0, 1.0, 0.0))

        rv3d.view_location = rv3d.view_location - (right * dpx + up * dpy) * u
        area.tag_redraw()
        return True


S.stylus_nav = _StylusNavigator()


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
        tname = "Blender Box Select" if tool_id == 'BOX' else "Tweak (Normal Select)"
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
    1. Hides the default Blender T-toolbar for a clean workspace.
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
    - SEL (Normal Select)
    - +SHIFT (Multi-Select Toggle)
    - +CTRL (Deselect)
    - +ALT (Loop & Depth Select)
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
                            op.report({'INFO'}, "Tablet Select: Direct (Normal Select)")
                        elif bid == 'SEL_SHIFT':
                            S.tablet_select_modifier = None if S.tablet_select_modifier == 'SHIFT' else 'SHIFT'
                            op.report({'INFO'}, f"Tablet Select: {'+SHIFT (Multi-Select)' if S.tablet_select_modifier else 'Normal'}")
                        elif bid == 'SEL_CTRL':
                            S.tablet_select_modifier = None if S.tablet_select_modifier == 'CTRL' else 'CTRL'
                            op.report({'INFO'}, f"Tablet Select: {'+CTRL (Deselect)' if S.tablet_select_modifier else 'Normal'}")
                        elif bid == 'SEL_ALT':
                            S.tablet_select_modifier = None if S.tablet_select_modifier == 'ALT' else 'ALT'
                            op.report({'INFO'}, f"Tablet Select: {'+ALT (Loop & Depth Select)' if S.tablet_select_modifier else 'Normal'}")
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
