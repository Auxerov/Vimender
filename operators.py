import math
import time
import bpy
from mathutils import Vector, Matrix, Quaternion
from .state import (
    S, tag_all, window_region, area_at, area_by_ptr, focusable_areas, transform_active,
    is_blender_modal_active, send_modal_plus_minus, send_fake_escape, send_fake_numpad,
    adjust_active_modal_or_modifier, check_shortcut, get_preferences, is_altgr_pressed,
    is_view_pie_trigger, change_sens, _exec_view3d_op, save_vimender_state,
    MOUSE_CANCEL, MODIFIER_KEYS, CONSUME_KEYS, PASS_KEYS_IN,
    PREFIX_DIRS, PREFIX_REPEAT_TIMEOUT, PREFIX_TIMEOUT, ALL_DIRS, NUDGE_DIRS,
    ARROW_DIRS, _addon_keymaps, NUMPAD_PIE_KEYS, is_edit_mode
)
from .mode_walk import enter_walk_mode, exit_walk_mode, handle_walk_event
from .mode_tablet import enter_tablet_mode, exit_tablet_mode, handle_tablet_event
from .mode_cursor import enter_cursor_mode, exit_cursor_mode, handle_cursor_event
from .mode_nav import neighbor_area, enter_nav_mode, exit_nav_mode, handle_nav_event, handle_orbit_pan_event
from .mode_preview import enter_preview_mode, exit_preview_mode, handle_preview_event
from .mode_hint import start_hint_global, apply_single, apply_box
from .ui import clean_modal_keymaps, install_draw_handlers, remove_draw_handlers, shutdown, draw_editor_pie, draw_vim_view_pie

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
        if S.preview_mode or S.navigation_mode or S.cursor_mode:
            return {'CANCELLED'}
        S.nudge_on = True
        S.nav.reset()
        S.mover.reset()

        self.locked_axis = None
        self.axis_press_count = 0
        self.axis_dir_2d = None

        try:
            self._prev_continuous = bpy.context.preferences.inputs.use_mouse_continuous
            bpy.context.preferences.inputs.use_mouse_continuous = False
        except Exception:
            pass

        area = area_at(context.window, event.mouse_x, event.mouse_y) or context.area
        if not area or area.type != 'VIEW_3D':
            area = next((a for a in context.screen.areas if a.type == 'VIEW_3D'), context.area)
        self.area = area
        self.region = window_region(area) if area else None

        win = context.window
        margin = 25.0
        self.min_x = margin
        self.max_x = float(max(win.width - margin, 50.0))
        self.min_y = margin
        self.max_y = float(max(win.height - margin, 50.0))

        self.sub_x = float(event.mouse_x)
        self.sub_y = float(event.mouse_y)
        self.sub_x = max(min(self.sub_x, self.max_x), self.min_x)
        self.sub_y = max(min(self.sub_y, self.max_y), self.min_y)

        self.pivot_2d = Vector((self.sub_x, self.sub_y))
        center_info = get_selection_center_2d(context, self.area)
        if center_info and center_info[0] is not None and center_info[1] is not None:
            self.pivot_2d = Vector((float(center_info[0]), float(center_info[1])))

        self.pivot_2d.x = max(min(self.pivot_2d.x, self.max_x - 30.0), self.min_x + 30.0)
        self.pivot_2d.y = max(min(self.pivot_2d.y, self.max_y - 30.0), self.min_y + 30.0)

        dx_p = self.sub_x - self.pivot_2d.x
        dy_p = self.sub_y - self.pivot_2d.y
        dist_p = math.hypot(dx_p, dy_p)
        self.initial_dist = dist_p

        edge_dist = min(
            self.pivot_2d.x - self.min_x,
            self.max_x - self.pivot_2d.x,
            self.pivot_2d.y - self.min_y,
            self.max_y - self.pivot_2d.y
        )
        max_safe_r = max(edge_dist - 15.0, 30.0)

        if dist_p < 25.0:
            self.scale_dir = Vector((0.7071, 0.7071))
            self.scale_dist = 60.0
            self.rot_angle = 0.0
            self.rot_radius = max(min(120.0, max_safe_r), 30.0)
        else:
            dx_abs = abs(dx_p) if abs(dx_p) > 0.01 else 0.7071
            dy_abs = abs(dy_p) if abs(dy_p) > 0.01 else 0.7071
            self.scale_dir = Vector((dx_abs, dy_abs)).normalized()
            self.scale_dist = max(dist_p, 25.0)
            self.rot_angle = math.atan2(dy_p, dx_p)
            self.rot_radius = max(min(120.0, max_safe_r), 30.0)

        self.vx = 0.0
        self.vy = 0.0
        self.ang_vel = 0.0
        self.scale_vel = 0.0
        self.held_nudge = set()
        self.last_t = time.perf_counter()
        self._shift_held = bool(event.shift)

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
        self.scale_vel = 0.0
        self.held_nudge.clear()
        if hasattr(self, '_prev_continuous'):
            try:
                bpy.context.preferences.inputs.use_mouse_continuous = self._prev_continuous
            except Exception:
                pass
        try:
            if S.cursor_mode:
                txt = f"Vimender: CURSOR MODE [{S.cursor_plane}] | HJKL: Move | TAB: Plane | 0: Origin | S: Snap | Esc: Exit"
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

    def _clamp_to_bounds(self, win):
        if self.sub_x >= self.max_x:
            self.sub_x = self.max_x
            if self.vx > 0.0:
                self.vx = 0.0
        elif self.sub_x <= self.min_x:
            self.sub_x = self.min_x
            if self.vx < 0.0:
                self.vx = 0.0

        if self.sub_y >= self.max_y:
            self.sub_y = self.max_y
            if self.vy > 0.0:
                self.vy = 0.0
        elif self.sub_y <= self.min_y:
            self.sub_y = self.min_y
            if self.vy < 0.0:
                self.vy = 0.0

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

    def _step(self, context, win, dt, is_press=False):
        tr_type = get_active_transform_type(win)
        sens = SENS_LEVELS[S.sens_idx]
        if getattr(self, '_shift_held', False):
            sens *= 0.15

        up_held = ('NUMPAD_8' in self.held_nudge) or ('UP_ARROW' in self.held_nudge) or ('K' in self.held_nudge)
        down_held = ('NUMPAD_2' in self.held_nudge) or ('DOWN_ARROW' in self.held_nudge) or ('J' in self.held_nudge)
        right_held = ('NUMPAD_6' in self.held_nudge) or ('RIGHT_ARROW' in self.held_nudge) or ('L' in self.held_nudge)
        left_held = ('NUMPAD_4' in self.held_nudge) or ('LEFT_ARROW' in self.held_nudge) or ('H' in self.held_nudge)

        if tr_type == 'ROTATE':
            rot_input = (1.0 if (up_held or right_held) else 0.0) - (1.0 if (down_held or left_held) else 0.0)
            target_w = rot_input * (2.8 * sens)

            if rot_input != 0.0:
                if is_press and abs(self.ang_vel) < 0.5:
                    self.ang_vel = target_w * 0.5
                alpha = 1.0 - math.exp(-48.0 * dt)
                self.ang_vel += (target_w - self.ang_vel) * alpha
            else:
                self.ang_vel = 0.0

            if abs(self.ang_vel) > 0.001:
                self.rot_angle += self.ang_vel * dt
                raw_x = self.pivot_2d.x + self.rot_radius * math.cos(self.rot_angle)
                raw_y = self.pivot_2d.y + self.rot_radius * math.sin(self.rot_angle)
                self.sub_x = max(min(raw_x, self.max_x), self.min_x)
                self.sub_y = max(min(raw_y, self.max_y), self.min_y)
                win.cursor_warp(int(round(self.sub_x)), int(round(self.sub_y)))
            return

        elif tr_type == 'RESIZE':
            scale_input = (1.0 if (up_held or right_held) else 0.0) - (1.0 if (down_held or left_held) else 0.0)
            target_v = scale_input * (240.0 * sens)

            if scale_input != 0.0:
                if is_press and abs(self.scale_vel) < 5.0:
                    self.scale_vel = target_v * 0.5
                alpha = 1.0 - math.exp(-48.0 * dt)
                self.scale_vel += (target_v - self.scale_vel) * alpha
            else:
                self.scale_vel = 0.0

            if abs(self.scale_vel) > 0.1:
                limit_x = (self.max_x - self.pivot_2d.x) if self.scale_dir.x > 0 else (self.pivot_2d.x - self.min_x)
                limit_y = (self.max_y - self.pivot_2d.y) if self.scale_dir.y > 0 else (self.pivot_2d.y - self.min_y)
                dist_lim_x = limit_x / max(abs(self.scale_dir.x), 1e-4)
                dist_lim_y = limit_y / max(abs(self.scale_dir.y), 1e-4)
                max_dist = max(min(dist_lim_x, dist_lim_y) - 5.0, 30.0)

                new_dist = self.scale_dist + self.scale_vel * dt
                if new_dist > max_dist:
                    new_dist = max_dist
                    self.scale_vel = 0.0
                elif new_dist < 2.0:
                    new_dist = 2.0
                    self.scale_vel = 0.0

                self.scale_dist = new_dist
                self.sub_x = self.pivot_2d.x + self.scale_dir.x * self.scale_dist
                self.sub_y = self.pivot_2d.y + self.scale_dir.y * self.scale_dist
                win.cursor_warp(int(round(self.sub_x)), int(round(self.sub_y)))
            return

        elif self.locked_axis is not None and self.axis_dir_2d is not None:
            axis_input = (1.0 if (up_held or right_held) else 0.0) - (1.0 if (down_held or left_held) else 0.0)
            max_spd = CURSOR_MAX_SPEED * sens
            target_v = self.axis_dir_2d * (axis_input * max_spd)

            if axis_input != 0.0:
                if is_press and math.hypot(self.vx, self.vy) < 10.0:
                    self.vx = target_v.x * 0.5
                    self.vy = target_v.y * 0.5
                alpha = 1.0 - math.exp(-48.0 * dt)
                self.vx += (target_v.x - self.vx) * alpha
                self.vy += (target_v.y - self.vy) * alpha
            else:
                self.vx = 0.0
                self.vy = 0.0

            if math.hypot(self.vx, self.vy) > 0.1:
                self.sub_x += self.vx * dt
                self.sub_y += self.vy * dt
                self._clamp_to_bounds(win)
            return

        else:
            dx = (1.0 if right_held else 0.0) - (1.0 if left_held else 0.0)
            dy = (1.0 if up_held else 0.0) - (1.0 if down_held else 0.0)
            max_spd = CURSOR_MAX_SPEED * sens

            if dx != 0.0 or dy != 0.0:
                mag = math.hypot(dx, dy)
                target_vx = (dx / mag) * max_spd
                target_vy = (dy / mag) * max_spd
                if is_press and math.hypot(self.vx, self.vy) < 10.0:
                    self.vx = target_vx * 0.5
                    self.vy = target_vy * 0.5
                alpha = 1.0 - math.exp(-48.0 * dt)
                self.vx += (target_vx - self.vx) * alpha
                self.vy += (target_vy - self.vy) * alpha
            else:
                self.vx = 0.0
                self.vy = 0.0

            if math.hypot(self.vx, self.vy) > 0.1:
                self.sub_x += self.vx * dt
                self.sub_y += self.vy * dt
                self._clamp_to_bounds(win)
            return

    def modal(self, context, event):
        win = context.window
        if not S.active or not transform_active(win) or S.preview_mode or S.navigation_mode or S.cursor_mode:
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
            self.scale_vel = 0.0
            self.held_nudge.clear()
            return {'PASS_THROUGH'}

        if t == 'C' and v == 'PRESS' and not (event.ctrl or event.alt or event.oskey):
            self.locked_axis = None
            self.axis_press_count = 0
            self.axis_dir_2d = None
            self.vx = 0.0
            self.vy = 0.0
            self.ang_vel = 0.0
            self.scale_vel = 0.0
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
                self._shift_held = bool(event.shift)
                self._step(context, win, 0.016, is_press=True)
                return {'RUNNING_MODAL'}
            elif v == 'RELEASE':
                if t in self.held_nudge:
                    self.held_nudge.discard(t)
                    if not self.held_nudge:
                        self.vx = 0.0
                        self.vy = 0.0
                        self.ang_vel = 0.0
                        self.scale_vel = 0.0
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

        if (t in {'N', 'M'} or event.unicode in {'n', 'N', 'm', 'M'}) and not (event.ctrl or event.alt or event.oskey):
            if v == 'PRESS':
                is_up = (t == 'N' or event.unicode in {'n', 'N'})
                adjust_active_modal_or_modifier(context, is_up)
                send_modal_plus_minus(is_up)
                if area:
                    area.tag_redraw()
            return {'RUNNING_MODAL'}

        if t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            if not self.held_nudge and math.hypot(self.vx, self.vy) < 0.1 and abs(self.ang_vel) < 0.01 and abs(self.scale_vel) < 0.01:
                self.sub_x = max(min(float(event.mouse_x), self.max_x), self.min_x)
                self.sub_y = max(min(float(event.mouse_y), self.max_y), self.min_y)
                dx_p = self.sub_x - self.pivot_2d.x
                dy_p = self.sub_y - self.pivot_2d.y
                dist_p = math.hypot(dx_p, dy_p)
                if dist_p >= 15.0:
                    self.rot_angle = math.atan2(dy_p, dx_p)
                    self.scale_dist = max(dist_p, 1.0)
                    dx_abs = abs(dx_p) if abs(dx_p) > 0.01 else 0.7071
                    dy_abs = abs(dy_p) if abs(dy_p) > 0.01 else 0.7071
                    self.scale_dir = Vector((dx_abs, dy_abs)).normalized()
            return {'PASS_THROUGH'}

        if t in {'TIMER', 'TIMER_REPORT'}:
            now = time.perf_counter()
            dt = min(max(now - self.last_t, 0.001), 0.05)
            self.last_t = now
            self._shift_held = bool(event.shift)
            self._step(context, win, dt, is_press=False)
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
            if S.walk_mode:
                txt = "Vimender: WALK MODE | WASD: Move | HJKL: Look | Q/E: Down/Up | Alt+Q/Esc: Exit"
            elif S.cursor_mode:
                txt = f"Vimender: CURSOR MODE [{S.cursor_plane}] | HJKL: Move | TAB: Plane | 0: Origin | S: Snap | Esc: Exit"
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
        start_hint_global(context, win, area, box, action, mx, my, self.report)

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
        enter_cursor_mode(context, self.report)

    def _exit_cursor_mode(self, context):
        exit_cursor_mode(context, self.report)

    def _enter_walk_mode(self, context, area):
        if S.mode:
            self._cancel_hint(context, context.window)
        enter_walk_mode(context, area, self.report)

    def _exit_walk_mode(self, context):
        exit_walk_mode(context, self.report)


    def _enter_tablet_mode(self, context, area=None):
        if S.mode:
            self._cancel_hint(context, context.window)
        enter_tablet_mode(context, area, self.report)

    def _exit_tablet_mode(self, context):
        exit_tablet_mode(context, self.report)
    def _open_pie(self, context, event, win, area):
        region = window_region(area)
        if region is None:
            return
        S.pie_ptr = area.as_pointer()
        S.pie_active = True
        S.pie_open_time = time.time()
        with context.temp_override(window=win, area=area, region=region):
            context.window_manager.popup_menu_pie(event, draw_editor_pie, title="Editor")

    def _open_view_pie(self, context, event, win, area):
        v3d = area or area_at(win, event.mouse_x, event.mouse_y) or area_by_ptr(win, S.hover_ptr) or context.area
        if not v3d or v3d.type != 'VIEW_3D':
            v3d = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), v3d or context.area)
        region = window_region(v3d) if v3d else None
        if not v3d or not region:
            return
        S.pie_ptr = v3d.as_pointer()
        S.pie_active = True
        S.view_pie_active = True
        S.pie_open_time = time.time()
        try:
            with context.temp_override(window=win, area=v3d, region=region):
                context.window_manager.popup_menu_pie(event, draw_vim_view_pie, title="View")
        except Exception as e:
            print(f"[Vimender] view_pie error: {e}")

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
            if S.pie_active and time.time() - S.pie_open_time > 5.0:
                S.pie_active = False
                S.view_pie_active = False
            if S.mode == 'BOX2':
                a = area_by_ptr(win, S.hint_ptr)
                r = window_region(a) if a else None
                if r and S.smooth_box.tick(r):
                    win.cursor_warp(int(round(r.x + S.mouse[0])), int(round(r.y + S.mouse[1])))
                    a.tag_redraw()
            elif S.cursor_mode:
                S.smooth_cursor.tick(context, win)
            elif S.walk_mode:
                S.smooth_walker.tick(context, win)
            elif not S.mode and not S.preview_mode and not S.nudge_on and not S.tablet_mode and not transform_active(win) and not is_blender_modal_active(win):
                S.nav.tick(context, win)

            if not S.preview_mode and not S.navigation_mode and not S.cursor_mode and not S.walk_mode and not S.tablet_mode:
                if not S.nudge_on and transform_active(win):
                    self._start_nudge(context, win)
            return {'PASS_THROUGH'}

        mx, my = event.mouse_x, event.mouse_y
        area = area_at(win, mx, my) or area_by_ptr(win, S.hover_ptr) or context.area
        ptr = area.as_pointer() if area else 0
        if ptr != S.hover_ptr and not S.mode:
            old = area_by_ptr(win, S.hover_ptr)
            S.hover_ptr = ptr
            if S.nav:
                S.nav.reset()
            if old:
                old.tag_redraw()
            if area:
                area.tag_redraw()

        if S.save_dialog_open:
            if v == 'PRESS':
                if (t in {'Y', 'y'} or event.unicode in {'y', 'Y'}) and not (event.ctrl or event.alt or event.oskey):
                    execute_save_action(S.save_dialog_action, 'SAVE')
                    return {'RUNNING_MODAL'}
                if (t in {'N', 'n'} or event.unicode in {'n', 'N'}) and not (event.ctrl or event.alt or event.oskey):
                    execute_save_action(S.save_dialog_action, 'DONT_SAVE')
                    return {'RUNNING_MODAL'}
                if (t in {'C', 'c', 'ESC', 'TAB'} or event.unicode in {'c', 'C'}) and not (event.ctrl or event.alt or event.oskey):
                    execute_save_action(S.save_dialog_action, 'CANCEL')
                    return {'RUNNING_MODAL'}

        if t == 'TAB' and v == 'PRESS':
            if S.search_active:
                S.search_active = False
                send_fake_escape()
                tag_all()
                return {'RUNNING_MODAL'}

        if S.search_active:
            if t == 'ESC' and v == 'PRESS':
                S.search_active = False
                tag_all()
            elif t in MOUSE_CANCEL and v == 'PRESS':
                if time.time() - S.search_open_time > 0.4:
                    S.search_active = False
                    tag_all()
                    tag_all()

        if S.view_pie_active:
            if t in {'ESC', 'RET', 'NUMPAD_ENTER', 'TAB'} and v == 'PRESS':
                S.view_pie_active = False
                S.pie_active = False
                send_fake_escape()
                tag_all()
                return {'RUNNING_MODAL'}
            if t in MOUSE_CANCEL and v == 'PRESS' and time.time() - S.pie_open_time > 0.15:
                S.view_pie_active = False
                S.pie_active = False
            if is_view_pie_trigger(event) and v == 'PRESS' and time.time() - S.pie_open_time > 0.15:
                S.view_pie_active = False
                S.pie_active = False
                send_fake_escape()
                tag_all()
                return {'RUNNING_MODAL'}
            if v == 'PRESS':
                v3d = area or area_at(win, mx, my) or area_by_ptr(win, S.hover_ptr) or context.area
                if not v3d or v3d.type != 'VIEW_3D':
                    v3d = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), v3d or context.area)

                if (t in {'ZERO', 'NUMPAD_0', 'O'} or event.unicode == '0') and not (event.ctrl or event.alt or event.oskey):
                    S.view_pie_active = False
                    S.pie_active = False
                    send_fake_escape()
                    _exec_view3d_op(context, win, v3d, bpy.ops.view3d.view_camera)
                    tag_all()
                    return {'RUNNING_MODAL'}

                if (t in {'ONE', 'NUMPAD_1'} or event.unicode == '1') and not (event.alt or event.oskey):
                    S.view_pie_active = False
                    S.pie_active = False
                    send_fake_escape()
                    axis = 'BACK' if event.ctrl else 'FRONT'
                    _exec_view3d_op(context, win, v3d, bpy.ops.view3d.view_axis, type=axis, align_active=bool(event.shift))
                    tag_all()
                    return {'RUNNING_MODAL'}

                if (t in {'THREE', 'NUMPAD_3'} or event.unicode == '3') and not (event.alt or event.oskey):
                    S.view_pie_active = False
                    S.pie_active = False
                    send_fake_escape()
                    axis = 'LEFT' if event.ctrl else 'RIGHT'
                    _exec_view3d_op(context, win, v3d, bpy.ops.view3d.view_axis, type=axis, align_active=bool(event.shift))
                    tag_all()
                    return {'RUNNING_MODAL'}

                if (t in {'SEVEN', 'NUMPAD_7'} or event.unicode == '7') and not (event.alt or event.oskey):
                    S.view_pie_active = False
                    S.pie_active = False
                    send_fake_escape()
                    axis = 'BOTTOM' if event.ctrl else 'TOP'
                    _exec_view3d_op(context, win, v3d, bpy.ops.view3d.view_axis, type=axis, align_active=bool(event.shift))
                    tag_all()
                    return {'RUNNING_MODAL'}

                if (t in {'NINE', 'NUMPAD_9'} or event.unicode == '9') and not (event.ctrl or event.alt or event.oskey or event.shift):
                    S.view_pie_active = False
                    S.pie_active = False
                    send_fake_escape()
                    _exec_view3d_op(context, win, v3d, bpy.ops.view3d.view_orbit, angle=math.pi, type='ORBITRIGHT')
                    tag_all()
                    return {'RUNNING_MODAL'}

                if (t in {'FOUR', 'NUMPAD_4'} or event.unicode == '4') and not (event.ctrl or event.alt or event.oskey or event.shift):
                    S.view_pie_active = False
                    S.pie_active = False
                    send_fake_escape()
                    _exec_view3d_op(context, win, v3d, bpy.ops.view3d.view_axis, type='LEFT')
                    tag_all()
                    return {'RUNNING_MODAL'}

                if (t in {'EIGHT', 'NUMPAD_8'} or event.unicode == '8') and not (event.ctrl or event.alt or event.oskey or event.shift):
                    S.view_pie_active = False
                    S.pie_active = False
                    send_fake_escape()
                    _exec_view3d_op(context, win, v3d, bpy.ops.view3d.view_axis, type='BACK')
                    tag_all()
                    return {'RUNNING_MODAL'}

                if (t in {'TWO', 'NUMPAD_2'} or event.unicode == '2') and not (event.ctrl or event.alt or event.oskey or event.shift):
                    S.view_pie_active = False
                    S.pie_active = False
                    send_fake_escape()
                    _exec_view3d_op(context, win, v3d, bpy.ops.view3d.view_axis, type='BOTTOM')
                    tag_all()
                    return {'RUNNING_MODAL'}

                if (t in {'FIVE', 'NUMPAD_5'} or event.unicode == '5') and not (event.ctrl or event.alt or event.oskey or event.shift):
                    S.view_pie_active = False
                    S.pie_active = False
                    send_fake_escape()
                    _exec_view3d_op(context, win, v3d, bpy.ops.view3d.view_persportho)
                    tag_all()
                    return {'RUNNING_MODAL'}

                if (t in {'NUMPAD_PERIOD', 'NUMPAD_COMMA', 'PERIOD', 'COMMA'} or event.unicode in {'.', ','}) and not (event.ctrl or event.alt or event.oskey):
                    S.view_pie_active = False
                    S.pie_active = False
                    send_fake_escape()
                    _exec_view3d_op(context, win, v3d, bpy.ops.view3d.view_selected, use_all_regions=False)
                    tag_all()
                    return {'RUNNING_MODAL'}

                if (t in {'NUMPAD_SLASH', 'SLASH'} or event.unicode == '/') and not (event.ctrl or event.alt or event.oskey):
                    S.view_pie_active = False
                    S.pie_active = False
                    send_fake_escape()
                    _exec_view3d_op(context, win, v3d, bpy.ops.view3d.localview)
                    tag_all()
                    return {'RUNNING_MODAL'}
            elif v == 'RELEASE' and t in CONSUME_KEYS:
                return {'RUNNING_MODAL'}

        if is_view_pie_trigger(event) and not (area and area.type in PASS_KEYS_IN) and not S.mode and not S.search_active and not S.save_dialog_open and not S.walk_mode:
            if v == 'PRESS':
                if S.view_pie_active:
                    S.view_pie_active = False
                    S.pie_active = False
                    send_fake_escape()
                    tag_all()
                    return {'RUNNING_MODAL'}
                self._open_view_pie(context, event, win, area)
                return {'RUNNING_MODAL'}
            elif v == 'RELEASE':
                return {'RUNNING_MODAL'}

        if S.mode:
            return self._modal_hint(context, event, win, mx, my, area)

        if transform_active(win) or is_blender_modal_active(win):
            if not S.preview_mode and not S.navigation_mode and not S.cursor_mode and not S.walk_mode and not S.tablet_mode:
                if (t in {'N', 'M'} or event.unicode in {'n', 'N', 'm', 'M'}) and not (event.ctrl or event.alt or event.oskey):
                    if v == 'PRESS':
                        is_up = (t == 'N' or event.unicode in {'n', 'N'})
                        adjust_active_modal_or_modifier(context, is_up)
                        send_modal_plus_minus(is_up)
                        if area:
                            area.tag_redraw()
                    return {'RUNNING_MODAL'}
                if not S.nudge_on and transform_active(win):
                    self._start_nudge(context, win)
            return {'PASS_THROUGH'}

        prefs = get_preferences(context)
        use_arrow_layout = bool(getattr(prefs, 'arrow_keys_layout', False)) if prefs else False
        plain = not (event.alt or event.oskey)

        if t in {'RIGHT_ALT', 'LINEFEED'}:
            if v == 'PRESS':
                S.right_alt_down = True
            elif v == 'RELEASE':
                S.right_alt_down = False

        if (check_shortcut(prefs, 'walk', event, 'Q', fallback_alt=True) or
            ((t == 'Q' or event.unicode in {'q', 'Q'}) and event.alt and not (event.ctrl or event.oskey or event.shift))):
            if v == 'PRESS':
                if S.walk_mode:
                    self._exit_walk_mode(context)
                else:
                    target_v3d = area or area_at(win, mx, my) or area_by_ptr(win, S.hover_ptr) or context.area
                    if target_v3d and target_v3d.type == 'VIEW_3D':
                        self._enter_walk_mode(context, target_v3d)
                    else:
                        self.report({'INFO'}, "Vimender: Walk Mode only available in 3D Viewport")
                return {'RUNNING_MODAL'}
            elif v == 'RELEASE':
                return {'RUNNING_MODAL'}

        if (check_shortcut(prefs, 'tablet', event, 'W', fallback_ctrl=True) or
            ((t == 'W' or event.unicode in {'w', 'W'}) and event.ctrl and not (event.alt or event.oskey or event.shift))):
            if v == 'PRESS':
                if S.tablet_mode:
                    self._exit_tablet_mode(context)
                else:
                    self._enter_tablet_mode(context, area)
                return {'RUNNING_MODAL'}
            elif v == 'RELEASE':
                return {'RUNNING_MODAL'}

        if check_shortcut(prefs, 'cursor', event, 'NUMPAD_5', fallback_ctrl=True) and v == 'PRESS':
            if S.cursor_mode:
                self._exit_cursor_mode(context)
            else:
                self._enter_cursor_mode(context)
            return {'RUNNING_MODAL'}

        if check_shortcut(prefs, 'nav', event, 'B', fallback_ctrl=True):
            if v == 'RELEASE':
                return {'RUNNING_MODAL'}
            if v == 'PRESS':
                if S.navigation_mode or S.workspace_nav:
                    exit_nav_mode(context, self.report)
                else:
                    enter_nav_mode(context, area, self.report)
                self._set_status(context)
                tag_all()
                return {'RUNNING_MODAL'}

        if check_shortcut(prefs, 'preview', event, 'Y', fallback_ctrl=True):
            if v == 'RELEASE':
                return {'RUNNING_MODAL'}
            if v == 'PRESS':
                if S.preview_mode:
                    exit_preview_mode(context, self.report)
                else:
                    enter_preview_mode(context, self.report)
                self._set_status(context)
                tag_all()
                return {'RUNNING_MODAL'}

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
                    S.search_active = True
                    S.search_open_time = time.time()
                except Exception as e:
                    print(f"[Vimender] search_menu error: {e}")
                return {'RUNNING_MODAL'}

        is_bevel_key = (
            check_shortcut(prefs, 'bevel', event, 'B', fallback_ctrl=False) or
            (check_shortcut(prefs, 'bevel', event, 'B', fallback_ctrl=False, allow_shift_alt_extend=True) and not event.alt) or
            ((t == 'B' or event.unicode in {'b', 'B'}) and not (event.ctrl or event.alt or event.oskey))
        )
        if is_bevel_key:
            if not (area and area.type in PASS_KEYS_IN) and not S.mode and not S.search_active and not S.save_dialog_open and not S.view_pie_active and not transform_active(win) and not is_blender_modal_active(win):
                v3d = area or area_at(win, mx, my) or area_by_ptr(win, S.hover_ptr) or context.area
                if not v3d or v3d.type != 'VIEW_3D':
                    v3d = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), v3d or context.area)
                region = window_region(v3d) if v3d else None
                if v3d and region:
                    can_bevel = False
                    try:
                        with context.temp_override(window=win, area=v3d, region=region):
                            can_bevel = bpy.ops.mesh.bevel.poll()
                    except Exception:
                        can_bevel = False
                    if can_bevel:
                        if v == 'PRESS':
                            S.exit_all_modes(context, self.report)
                            try:
                                with context.temp_override(window=win, area=v3d, region=region):
                                    if event.shift:
                                        bpy.ops.mesh.bevel('INVOKE_DEFAULT', affect='VERTICES')
                                    else:
                                        bpy.ops.mesh.bevel('INVOKE_DEFAULT', affect='EDGES')
                            except Exception as e:
                                print(f"[Vimender] Bevel error: {e}")
                            tag_all()
                            return {'RUNNING_MODAL'}
                        elif v == 'RELEASE':
                            return {'RUNNING_MODAL'}

        if S.walk_mode:
            return handle_walk_event(self, context, event, win, area)

        if S.tablet_mode:
            return handle_tablet_event(self, context, event, win, area)

        if S.cursor_mode:
            return handle_cursor_event(self, context, event, win, area)

        if S.navigation_mode or S.workspace_nav:
            return handle_nav_event(self, context, event, win, area)

        if S.preview_mode:
            return handle_preview_event(self, context, event, win, area)

        return handle_orbit_pan_event(self, context, event, win, area)
    def _modal_hint(self, context, event, win, mx, my, area=None):
        t, v = event.type, event.value
        prefs = get_preferences(context)

        use_arrow_layout = bool(getattr(prefs, 'arrow_keys_layout', False)) if prefs else False
        if ((t == 'SPACE' and is_altgr_pressed(event)) or
            check_shortcut(prefs, 'fuzzy_search', event, 'SPACE', fallback_ctrl=True, fallback_alt=True)) and v == 'PRESS':
            self._cancel_hint(context, win)
            try:
                with context.temp_override(window=win):
                    bpy.ops.wm.vim_fuzzy_search('INVOKE_DEFAULT')
            except Exception as e:
                print(f"[Vimender] fuzzy_search error: {e}")
            return {'RUNNING_MODAL'}

        if (check_shortcut(prefs, 'search_mode', event, 'SPACE', fallback_alt=True) or
            (t == 'SPACE' and event.alt and not (event.ctrl or event.shift or event.oskey))) and v == 'PRESS':
            self._cancel_hint(context, win)
            try:
                with context.temp_override(window=win):
                    bpy.ops.wm.vim_search_mode('INVOKE_DEFAULT')
            except Exception as e:
                print(f"[Vimender] search_mode error: {e}")
            return {'RUNNING_MODAL'}

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
            target_a = area or area_by_ptr(win, S.hover_ptr) or area_at(win, mx, my) or (win.screen.areas[0] if win and win.screen and win.screen.areas else None)
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
        S.search_active = True
        S.search_open_time = time.time()
        context.window_manager.invoke_search_popup(self)
        return {'FINISHED'}


class WM_OT_vim_search_mode(bpy.types.Operator):
    bl_idname = "wm.vim_search_mode"
    bl_label = "Mode Search"
    bl_description = "Search and switch Vimender modes, Blender interaction modes, or Editor types"
    bl_property = "target"
    bl_options = {'REGISTER', 'UNDO'}

    def get_items(self, context):
        items = [
            ("VIM_NAV", "Navigation Mode", "Vimender: Workspace & Area window navigation (Ctrl+B)", 'SCREEN_BACK', 0),
            ("VIM_ORBIT", "Orbit Mode", "Vimender: Turntable/Trackball orbit camera navigation", 'VIEW_ORBIT', 1),
            ("VIM_PAN", "Pan Mode", "Vimender: Camera Pan navigation", 'VIEW_PAN', 2),
            ("VIM_CURSOR", "3D Cursor Mode", "Vimender: Smooth 3D Cursor control (HJKL)", 'PIVOT_CURSOR', 3),
            ("VIM_PREVIEW", "Preview Mode", "Vimender: Preview hover and select mode", 'RESTRICT_VIEW_OFF', 4),
            ("VIM_PREFERENCES", "Vimender: Preferences / Keymaps", "Open Vimender preferences and customize keymaps", 'PREFERENCES', 5),

            ("BL_OBJECT", "Blender: Object Mode", "Switch active object to Object Mode", 'OBJECT_DATAMODE', 10),
            ("BL_EDIT", "Blender: Edit Mode", "Switch active object to Edit Mode", 'EDITMODE_HLT', 11),
            ("BL_SCULPT", "Blender: Sculpt Mode", "Switch active object to Sculpt Mode", 'SCULPTMODE_HLT', 12),
            ("BL_VERTEX_PAINT", "Blender: Vertex Paint", "Switch to Vertex Paint Mode", 'VPAINT_HLT', 13),
            ("BL_WEIGHT_PAINT", "Blender: Weight Paint", "Switch to Weight Paint Mode", 'WPAINT_HLT', 14),
            ("BL_TEXTURE_PAINT", "Blender: Texture Paint", "Switch to Texture Paint Mode", 'TPAINT_HLT', 15),
            ("BL_POSE", "Blender: Pose Mode", "Switch active armature to Pose Mode", 'POSE_HLT', 16),
            ("BL_PARTICLE_EDIT", "Blender: Particle Edit", "Switch to Particle Edit Mode", 'PARTICLEMODE', 17),
            ("BL_PAINT_GPENCIL", "Blender: GP Draw", "Grease pencil draw mode", 'GREASEPENCIL', 18),
            ("BL_EDIT_GPENCIL", "Blender: GP Edit", "Grease pencil edit mode", 'EDITMODE_HLT', 19),
            ("BL_SCULPT_GPENCIL", "Blender: GP Sculpt", "Grease pencil sculpt mode", 'SCULPTMODE_HLT', 20),
            ("BL_WEIGHT_GPENCIL", "Blender: GP Weight", "Grease pencil weight paint mode", 'WPAINT_HLT', 21),
            ("BL_VERTEX_GPENCIL", "Blender: GP Vertex", "Grease pencil vertex paint mode", 'VPAINT_HLT', 22),
            ("BL_SCULPT_CURVES", "Blender: Curves Sculpt", "Curves sculpt mode", 'SCULPTMODE_HLT', 23),

            ("ED_VIEW_3D", "Editor: 3D Viewport", "Switch area to 3D Viewport", 'VIEW3D', 30),
            ("ED_UV", "Editor: UV Editor", "Switch area to UV Editor", 'UV', 31),
            ("ED_IMAGE_EDITOR", "Editor: Image Editor", "Switch area to Image Editor", 'IMAGE_DATA', 32),
            ("ED_ShaderNodeTree", "Editor: Shader Editor", "Switch area to Shader Editor", 'NODE_MATERIAL', 33),
            ("ED_CompositorNodeTree", "Editor: Compositor", "Switch area to Compositor", 'NODE_COMPOSITING', 34),
            ("ED_GeometryNodeTree", "Editor: Geometry Nodes", "Switch area to Geometry Nodes", 'GEOMETRY_NODES', 35),
            ("ED_DOPESHEET", "Editor: Dope Sheet", "Switch area to Dope Sheet", 'ACTION', 36),
            ("ED_TIMELINE", "Editor: Timeline", "Switch area to Timeline", 'TIME', 37),
            ("ED_FCURVES", "Editor: Graph Editor", "Switch area to Graph Editor", 'GRAPH', 38),
            ("ED_DRIVERS", "Editor: Drivers Editor", "Switch area to Drivers Editor", 'DRIVER', 39),
            ("ED_NLA_EDITOR", "Editor: Non-Linear Animation", "Switch area to NLA Editor", 'NLA', 40),
            ("ED_SEQUENCE_EDITOR", "Editor: Video Sequencer", "Switch area to Video Sequencer", 'SEQUENCE', 41),
            ("ED_CLIP_EDITOR", "Editor: Movie Clip Editor", "Switch area to Movie Clip Editor", 'TRACKER', 42),
            ("ED_TEXT_EDITOR", "Editor: Text Editor", "Switch area to Text Editor", 'TEXT', 43),
            ("ED_CONSOLE", "Editor: Python Console", "Switch area to Python Console", 'CONSOLE', 44),
            ("ED_INFO", "Editor: Info Editor", "Switch area to Info Editor", 'INFO', 45),
            ("ED_OUTLINER", "Editor: Outliner", "Switch area to Outliner", 'OUTLINER', 46),
            ("ED_PROPERTIES", "Editor: Properties", "Switch area to Properties", 'PROPERTIES', 47),
            ("ED_FILES", "Editor: File Browser", "Switch area to File Browser", 'FILEBROWSER', 48),
            ("ED_ASSETS", "Editor: Asset Browser", "Switch area to Asset Browser", 'ASSET_MANAGER', 49),
            ("ED_SPREADSHEET", "Editor: Spreadsheet", "Switch area to Spreadsheet", 'SPREADSHEET', 50),
            ("ED_PREFERENCES", "Editor: Preferences", "Switch area to Preferences", 'PREFERENCES', 51),
        ]
        return items

    target: bpy.props.EnumProperty(
        name="Mode Search",
        description="Select mode or editor to activate",
        items=get_items,
    )

    def execute(self, context):
        S.search_active = False
        tgt = self.target
        win = context.window

        if tgt.startswith("VIM_") and not S.active:
            try:
                bpy.ops.wm.vim_mode_toggle('INVOKE_DEFAULT')
            except Exception:
                pass

        if tgt == "VIM_NAV":
            S.cursor_mode = False
            S.preview_mode = False
            S.navigation_mode = True
            S.workspace_nav = True
            S.clear_hint()
            S.nav.reset()
            S.smooth_cursor.reset()
            area = context.area or (win.screen.areas[0] if win and win.screen and win.screen.areas else None)
            if area:
                S.hover_ptr = area.as_pointer()
            self.report({'INFO'}, "Vimender: NAVIGATION MODE")
            try:
                context.workspace.status_text_set("Vimender: NAVIGATION MODE")
            except Exception:
                pass
            tag_all()
            return {'FINISHED'}

        if tgt == "VIM_ORBIT":
            S.cursor_mode = False
            S.navigation_mode = False
            S.workspace_nav = False
            S.preview_mode = False
            S.nav_mode = 'ORBIT'
            S.clear_hint()
            S.nav.reset()
            S.smooth_cursor.reset()
            self.report({'INFO'}, "Vimender: ORBIT MODE")
            try:
                context.workspace.status_text_set("Vimender: ORBIT MODE")
            except Exception:
                pass
            tag_all()
            return {'FINISHED'}

        if tgt == "VIM_PAN":
            S.cursor_mode = False
            S.navigation_mode = False
            S.workspace_nav = False
            S.preview_mode = False
            S.nav_mode = 'PAN'
            S.clear_hint()
            S.nav.reset()
            S.smooth_cursor.reset()
            self.report({'INFO'}, "Vimender: PAN MODE")
            try:
                context.workspace.status_text_set("Vimender: PAN MODE")
            except Exception:
                pass
            tag_all()
            return {'FINISHED'}

        if tgt == "VIM_CURSOR":
            enter_cursor_mode(context, self.report)
            return {'FINISHED'}

        if tgt == "VIM_PREVIEW":
            S.cursor_mode = False
            S.navigation_mode = False
            S.workspace_nav = False
            S.preview_mode = True
            S.clear_hint()
            S.nav.reset()
            S.smooth_cursor.reset()
            self.report({'INFO'}, "Vimender: PREVIEW MODE")
            try:
                context.workspace.status_text_set("Vimender: PREVIEW MODE")
            except Exception:
                pass
            tag_all()
            return {'FINISHED'}

        if tgt.startswith("BL_"):
            mode_name = tgt[3:]
            try:
                bpy.ops.object.mode_set(mode=mode_name)
                self.report({'INFO'}, f"Blender Mode: {mode_name.replace('_', ' ').title()}")
                tag_all()
                return {'FINISHED'}
            except Exception as e:
                self.report({'WARNING'}, f"Cannot switch mode: {e}")
                return {'CANCELLED'}

        if tgt in {"VIM_PREFERENCES", "ED_PREFERENCES"}:
            try:
                bpy.ops.wm.vim_open_preferences('INVOKE_DEFAULT')
            except Exception as e:
                print(f"[Vimender] open preferences error: {e}")
            return {'FINISHED'}

        if tgt.startswith("ED_"):
            ui_type = tgt[3:]
            area = area_by_ptr(win, S.hover_ptr) or context.area
            if not area and win and win.screen and win.screen.areas:
                area = win.screen.areas[0]
            if area:
                try:
                    area.ui_type = ui_type
                    area.tag_redraw()
                    self.report({'INFO'}, f"Editor: {ui_type}")
                    return {'FINISHED'}
                except Exception as e:
                    self.report({'WARNING'}, f"Cannot switch editor to {ui_type}: {e}")
                    return {'CANCELLED'}

        return {'CANCELLED'}

    def invoke(self, context, event):
        S.search_active = True
        S.search_open_time = time.time()
        tag_all()
        context.window_manager.invoke_search_popup(self)
        return {'FINISHED'}


class WM_OT_vim_fuzzy_search(bpy.types.Operator):
    bl_idname = "wm.vim_fuzzy_search"
    bl_label = "Data Search"
    bl_description = "Fuzzy search all objects, shaders, and textures in the scene"
    bl_property = "target"
    bl_options = {'REGISTER', 'UNDO'}

    def get_items(self, context):
        items = []
        idx = 0

        objects = list(context.scene.objects) if context and context.scene else list(bpy.data.objects)
        for o in objects:
            otype = getattr(o, 'type', 'MESH')
            icon_map = {
                'MESH': 'MESH_DATA',
                'CURVE': 'CURVE_DATA',
                'SURFACE': 'SURFACE_DATA',
                'META': 'META_DATA',
                'FONT': 'FONT_DATA',
                'ARMATURE': 'ARMATURE_DATA',
                'LATTICE': 'LATTICE_DATA',
                'EMPTY': 'EMPTY_DATA',
                'GPENCIL': 'GREASEPENCIL',
                'CAMERA': 'CAMERA_DATA',
                'LIGHT': 'LIGHT_DATA',
                'SPEAKER': 'SPEAKER',
                'VOLUME': 'VOLUME_DATA',
                'LIGHT_PROBE': 'LIGHTPROBE_CUBEMAP',
                'POINTCLOUD': 'POINTCLOUD_DATA',
            }
            icon = icon_map.get(otype, 'OBJECT_DATA')
            ident = f"OBJ:{o.name}"
            name = f"{o.name}  [{otype.capitalize()}]"
            desc = f"Object ({otype}): {o.name} - Select and focus in 3D Viewport"
            items.append((ident, name, desc, icon, idx))
            idx += 1

        for mat in bpy.data.materials:
            ident = f"MAT:{mat.name}"
            name = f"{mat.name}  [Shader]"
            desc = f"Material / Shader: {mat.name} - Open in Shading workspace"
            items.append((ident, name, desc, 'MATERIAL', idx))
            idx += 1

        for img in bpy.data.images:
            ident = f"IMG:{img.name}"
            dims = f"{img.size[0]}x{img.size[1]}" if (hasattr(img, 'size') and len(img.size) >= 2 and img.size[0] > 0) else "image"
            name = f"{img.name}  [Texture: {dims}]"
            desc = f"Texture / Image: {img.name} - Open in UV/Texture editor"
            items.append((ident, name, desc, 'IMAGE_DATA', idx))
            idx += 1

        for tex in bpy.data.textures:
            if tex.name not in [img.name for img in bpy.data.images]:
                ident = f"TEX:{tex.name}"
                name = f"{tex.name}  [Texture]"
                desc = f"Texture: {tex.name} - Open in Texture editor"
                items.append((ident, name, desc, 'TEXTURE', idx))
                idx += 1

        for grp in bpy.data.node_groups:
            gtype = getattr(grp, 'type', 'GEOMETRY')
            icon = 'GEOMETRY_NODES' if gtype == 'GEOMETRY' else ('NODE_COMPOSITING' if gtype == 'COMPOSITING' else 'NODETREE')
            ident = f"GRP:{grp.name}"
            name = f"{grp.name}  [{gtype.capitalize()} NodeTree]"
            desc = f"Node Group: {grp.name} ({gtype}) - Open in {gtype.capitalize()} editor"
            items.append((ident, name, desc, icon, idx))
            idx += 1

        for w in bpy.data.worlds:
            ident = f"WLD:{w.name}"
            name = f"{w.name}  [World Shader]"
            desc = f"World Shader: {w.name} - Environment / World background"
            items.append((ident, name, desc, 'WORLD', idx))
            idx += 1

        if not items:
            items.append(("NONE", "No items found", "Scene is empty", 'INFO', 0))

        return items

    target: bpy.props.EnumProperty(
        name="Data Search",
        description="Select scene item to focus or open in editor",
        items=get_items,
    )

    def execute(self, context):
        S.search_active = False
        tgt = self.target
        if not tgt or tgt == "NONE":
            return {'CANCELLED'}
        win = context.window
        if not win:
            return {'CANCELLED'}

        prefix, _, name = tgt.partition(":")
        ordered_ws = get_ordered_workspaces()

        if prefix == "OBJ":
            obj = bpy.data.objects.get(name)
            if not obj:
                self.report({'WARNING'}, f"Vimender: Object '{name}' not found")
                return {'CANCELLED'}

            has_v3d = any(a.type == 'VIEW_3D' for a in win.screen.areas) if win.screen else False
            if not has_v3d:
                target_ws = next((w for w in ordered_ws if any(k in w.name.lower() for k in ('layout', 'modeling', 'general'))), None)
                if target_ws and win.workspace != target_ws:
                    try:
                        win.workspace = target_ws
                    except Exception:
                        pass

            try:
                if hasattr(obj, 'hide_viewport'):
                    obj.hide_viewport = False
                if hasattr(obj, 'hide_set'):
                    obj.hide_set(False)
            except Exception:
                pass

            vl = context.view_layer
            for o in vl.objects:
                try:
                    o.select_set(False)
                except Exception:
                    pass
            try:
                obj.select_set(True)
                vl.objects.active = obj
            except Exception:
                pass

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
            tag_all()
            return {'FINISHED'}

        elif prefix == "MAT":
            mat = bpy.data.materials.get(name)
            if not mat:
                self.report({'WARNING'}, f"Vimender: Material '{name}' not found")
                return {'CANCELLED'}

            user_obj = None
            for o in context.scene.objects:
                if hasattr(o, 'material_slots'):
                    for slot in o.material_slots:
                        if slot.material == mat:
                            user_obj = o
                            break
                if user_obj:
                    break

            if user_obj:
                vl = context.view_layer
                for o in vl.objects:
                    try:
                        o.select_set(False)
                    except Exception:
                        pass
                try:
                    user_obj.select_set(True)
                    vl.objects.active = user_obj
                    user_obj.active_material = mat
                except Exception:
                    pass

            has_shader_editor = any(a.type == 'NODE_EDITOR' for a in win.screen.areas) if win.screen else False
            if not has_shader_editor:
                target_ws = next((w for w in ordered_ws if any(k in w.name.lower() for k in ('shading', 'shader', 'material'))), None)
                if target_ws and win.workspace != target_ws:
                    try:
                        win.workspace = target_ws
                    except Exception:
                        pass

            for a in win.screen.areas:
                if a.type == 'NODE_EDITOR':
                    space = a.spaces.active if a.spaces else None
                    if space:
                        try:
                            space.tree_type = 'ShaderNodeTree'
                            space.shader_type = 'OBJECT'
                            if mat.node_tree:
                                space.node_tree = mat.node_tree
                        except Exception:
                            pass
                    a.tag_redraw()

            msg = f"Vimender: Shader '{mat.name}'" + (f" on object '{user_obj.name}'" if user_obj else "")
            self.report({'INFO'}, msg)
            tag_all()
            return {'FINISHED'}

        elif prefix in ("IMG", "TEX"):
            img = bpy.data.images.get(name) if prefix == "IMG" else None
            tex = bpy.data.textures.get(name) if prefix == "TEX" else None

            user_obj = None
            user_mat = None
            if img:
                for m in bpy.data.materials:
                    if m.node_tree:
                        for node in m.node_tree.nodes:
                            if getattr(node, 'image', None) == img:
                                user_mat = m
                                break
                    if user_mat:
                        break
            if user_mat:
                for o in context.scene.objects:
                    if hasattr(o, 'material_slots'):
                        for slot in o.material_slots:
                            if slot.material == user_mat:
                                user_obj = o
                                break
                    if user_obj:
                        break

            if user_obj:
                vl = context.view_layer
                for o in vl.objects:
                    try:
                        o.select_set(False)
                    except Exception:
                        pass
                try:
                    user_obj.select_set(True)
                    vl.objects.active = user_obj
                    if user_mat:
                        user_obj.active_material = user_mat
                except Exception:
                    pass

            has_img_editor = any(a.type == 'IMAGE_EDITOR' for a in win.screen.areas) if win.screen else False
            if not has_img_editor:
                target_ws = next((w for w in ordered_ws if any(k in w.name.lower() for k in ('texture', 'uv', 'image', 'shading'))), None)
                if target_ws and win.workspace != target_ws:
                    try:
                        win.workspace = target_ws
                    except Exception:
                        pass

            if img:
                for a in win.screen.areas:
                    if a.type == 'IMAGE_EDITOR':
                        space = a.spaces.active if a.spaces else None
                        if space:
                            try:
                                space.image = img
                            except Exception:
                                pass
                        a.tag_redraw()

            msg = f"Vimender: Texture '{name}'" + (f" on object '{user_obj.name}'" if user_obj else "")
            self.report({'INFO'}, msg)
            tag_all()
            return {'FINISHED'}

        elif prefix == "GRP":
            grp = bpy.data.node_groups.get(name)
            if not grp:
                self.report({'WARNING'}, f"Vimender: Node group '{name}' not found")
                return {'CANCELLED'}

            gtype = getattr(grp, 'type', 'GEOMETRY')
            user_obj = None
            if gtype == 'GEOMETRY':
                for o in context.scene.objects:
                    for mod in getattr(o, 'modifiers', []):
                        if getattr(mod, 'node_group', None) == grp:
                            user_obj = o
                            break
                    if user_obj:
                        break

            if user_obj:
                vl = context.view_layer
                for o in vl.objects:
                    try:
                        o.select_set(False)
                    except Exception:
                        pass
                try:
                    user_obj.select_set(True)
                    vl.objects.active = user_obj
                except Exception:
                    pass

            if gtype == 'GEOMETRY':
                target_ws = next((w for w in ordered_ws if any(k in w.name.lower() for k in ('geometry', 'geo'))), None)
            elif gtype == 'COMPOSITING':
                target_ws = next((w for w in ordered_ws if any(k in w.name.lower() for k in ('compositing', 'comp'))), None)
            else:
                target_ws = next((w for w in ordered_ws if any(k in w.name.lower() for k in ('shading', 'shader'))), None)

            if target_ws and win.workspace != target_ws:
                try:
                    win.workspace = target_ws
                except Exception:
                    pass

            for a in win.screen.areas:
                if a.type == 'NODE_EDITOR':
                    space = a.spaces.active if a.spaces else None
                    if space:
                        try:
                            if gtype == 'GEOMETRY':
                                space.tree_type = 'GeometryNodeTree'
                            elif gtype == 'COMPOSITING':
                                space.tree_type = 'CompositorNodeTree'
                            space.node_tree = grp
                        except Exception:
                            pass
                    a.tag_redraw()

            self.report({'INFO'}, f"Vimender: Node group '{grp.name}'")
            tag_all()
            return {'FINISHED'}

        elif prefix == "WLD":
            wld = bpy.data.worlds.get(name)
            if wld:
                context.scene.world = wld
                target_ws = next((w for w in ordered_ws if any(k in w.name.lower() for k in ('shading', 'world'))), None)
                if target_ws and win.workspace != target_ws:
                    try:
                        win.workspace = target_ws
                    except Exception:
                        pass
                for a in win.screen.areas:
                    if a.type == 'NODE_EDITOR':
                        space = a.spaces.active if a.spaces else None
                        if space:
                            try:
                                space.tree_type = 'ShaderNodeTree'
                                space.shader_type = 'WORLD'
                                if wld.node_tree:
                                    space.node_tree = wld.node_tree
                            except Exception:
                                pass
                        a.tag_redraw()
                self.report({'INFO'}, f"Vimender: Active world set to '{wld.name}'")
                tag_all()
                return {'FINISHED'}

        return {'CANCELLED'}

    def invoke(self, context, event):
        S.search_active = True
        S.search_open_time = time.time()
        tag_all()
        context.window_manager.invoke_search_popup(self)
        return {'FINISHED'}


class WM_OT_vim_general_search(bpy.types.Operator):
    bl_idname = "wm.vim_general_search"
    bl_label = "General Search"
    bl_description = "General Menu and Operator Search"
    bl_options = {'REGISTER'}

    def execute(self, context):
        S.search_active = False
        tag_all()
        return {'FINISHED'}

    def invoke(self, context, event):
        S.search_active = True
        S.search_open_time = time.time()
        tag_all()
        win = context.window
        area = context.area or (win.screen.areas[0] if win and win.screen and win.screen.areas else None)
        target_area = area or (next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None) if win and win.screen else None)
        target_region = window_region(target_area) if target_area else None
        try:
            if target_area and target_region:
                win.cursor_warp(int(target_region.x + target_region.width / 2),
                                int(target_region.y + target_region.height / 2))
                with context.temp_override(window=win, area=target_area, region=target_region):
                    bpy.ops.wm.search_menu('INVOKE_DEFAULT')
            else:
                bpy.ops.wm.search_menu('INVOKE_DEFAULT')
        except Exception as e:
            print(f"[Vimender] general_search error: {e}")
        return {'FINISHED'}


def execute_save_action(action, choice):
    S.save_dialog_open = False
    send_fake_escape()
    if choice == 'CANCEL':
        tag_all()
        return {'CANCELLED'}
    if choice == 'SAVE':
        try:
            if bpy.data.filepath:
                bpy.ops.wm.save_mainfile()
            else:
                bpy.ops.wm.save_as_mainfile('INVOKE_DEFAULT')
                return {'FINISHED'}
        except Exception as e:
            print(f"[Vimender] save error: {e}")
    if action == 'QUIT':
        try:
            bpy.ops.wm.quit_blender('EXEC_DEFAULT')
        except Exception:
            bpy.ops.wm.quit_blender()
    elif action == 'NEW':
        try:
            bpy.ops.wm.read_homefile('EXEC_DEFAULT')
        except Exception:
            bpy.ops.wm.read_homefile('INVOKE_DEFAULT')
    elif action == 'OPEN':
        bpy.ops.wm.open_mainfile('INVOKE_DEFAULT')
    tag_all()
    return {'FINISHED'}


class WM_OT_vim_save_prompt(bpy.types.Operator):
    bl_idname = "wm.vim_save_prompt"
    bl_label = "Save Changes?"
    bl_description = "Save changes before closing, new file, or open file"
    bl_options = {'REGISTER', 'INTERNAL'}
    action: bpy.props.StringProperty(default='QUIT')

    def draw(self, context):
        layout = self.layout
        if self.action == 'QUIT':
            layout.label(text="Save changes before closing?", icon='QUESTION')
        elif self.action == 'NEW':
            layout.label(text="Save changes before new file?", icon='QUESTION')
        else:
            layout.label(text="Save changes before opening file?", icon='QUESTION')
        layout.separator()
        row = layout.row(align=True)
        row.scale_y = 1.4
        op_y = row.operator(WM_OT_vim_save_action.bl_idname, text="[Y] Save", icon='CHECKMARK')
        op_y.choice = 'SAVE'
        op_y.action = self.action
        op_n = row.operator(WM_OT_vim_save_action.bl_idname, text="[N] Don't Save", icon='TRASH')
        op_n.choice = 'DONT_SAVE'
        op_n.action = self.action
        op_c = row.operator(WM_OT_vim_save_action.bl_idname, text="[C] Cancel", icon='CANCEL')
        op_c.choice = 'CANCEL'
        op_c.action = self.action

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        if not bpy.data.is_dirty:
            return execute_save_action(self.action, 'DONT_SAVE')
        S.save_dialog_open = True
        S.save_dialog_action = self.action
        tag_all()
        return context.window_manager.invoke_popup(self, width=320)


class WM_OT_vim_save_action(bpy.types.Operator):
    bl_idname = "wm.vim_save_action"
    bl_label = "Save Action"
    bl_options = {'REGISTER', 'INTERNAL'}
    choice: bpy.props.StringProperty(default='CANCEL')
    action: bpy.props.StringProperty(default='QUIT')

    def execute(self, context):
        return execute_save_action(self.action, self.choice)


class WM_OT_vim_close_search(bpy.types.Operator):
    bl_idname = "wm.vim_close_search"
    bl_label = "Close Search"
    bl_description = "Close active search bar or popup"
    bl_options = {'REGISTER', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return bool(S.search_active)

    def execute(self, context):
        S.search_active = False
        S.save_dialog_open = False
        send_fake_escape()
        tag_all()
        return {'FINISHED'}


def register_search_key():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc is None:
        return
    km = kc.keymaps.new('Window', space_type='EMPTY', region_type='WINDOW')
    kmi1 = km.keymap_items.new(WM_OT_vim_search_object.bl_idname, 'SPACE', 'PRESS', ctrl=True)
    kmi2 = km.keymap_items.new(WM_OT_vim_search_object.bl_idname, 'F3', 'PRESS', ctrl=True)
    kmi3 = km.keymap_items.new(WM_OT_vim_search_mode.bl_idname, 'SPACE', 'PRESS', alt=True)
    kmi4 = km.keymap_items.new(WM_OT_vim_fuzzy_search.bl_idname, 'SPACE', 'PRESS', alt=True, ctrl=True)
    kmi_close = km.keymap_items.new(WM_OT_vim_close_search.bl_idname, 'TAB', 'PRESS')
    kmi_quit = km.keymap_items.new(WM_OT_vim_save_prompt.bl_idname, 'Q', 'PRESS', ctrl=True)
    kmi_quit.properties.action = 'QUIT'
    kmi_new = km.keymap_items.new(WM_OT_vim_save_prompt.bl_idname, 'N', 'PRESS', ctrl=True)
    kmi_new.properties.action = 'NEW'
    kmi_open = km.keymap_items.new(WM_OT_vim_save_prompt.bl_idname, 'O', 'PRESS', ctrl=True)
    kmi_open.properties.action = 'OPEN'
    km_mesh = kc.keymaps.new('Mesh', space_type='EMPTY', region_type='WINDOW')
    kmi_bev1 = km_mesh.keymap_items.new('mesh.bevel', 'B', 'PRESS')
    kmi_bev1.properties.affect = 'EDGES'
    kmi_bev2 = km_mesh.keymap_items.new('mesh.bevel', 'B', 'PRESS', shift=True)
    kmi_bev2.properties.affect = 'VERTICES'
    _addon_keymaps.extend([(km, kmi1), (km, kmi2), (km, kmi3), (km, kmi4),
                           (km, kmi_close),
                           (km, kmi_quit), (km, kmi_new), (km, kmi_open),
                           (km_mesh, kmi_bev1), (km_mesh, kmi_bev2)])



def unregister_search_key():
    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _addon_keymaps.clear()


