import math
import time
import ctypes
import bpy
from mathutils import Vector, Matrix, Quaternion
from .state import (
    S, ORBIT_MAX_SPEED, PAN_MAX_SPEED, ZOOM_MAX_SPEED, VIEW2D_PAN_SPEED,
    NAV_ACCEL_RATE, NAV_FRICTION, SENS_LEVELS, tag_all, window_region,
    focusable_areas, area_at, area_by_ptr, change_sens, _exec_view3d_op,
    get_preferences, check_shortcut, NUMPAD_PIE_KEYS, MOUSE_CANCEL,
    PREFIX_DIRS, PREFIX_REPEAT_TIMEOUT, PREFIX_TIMEOUT, ALL_DIRS,
    ARROW_DIRS, NUDGE_DIRS, is_edit_mode, is_altgr_pressed, NUM_TO_PAD, UNICODE_TO_PAD,
    send_fake_numpad, adjust_active_modal_or_modifier, send_modal_plus_minus,
    transform_active, is_blender_modal_active, PASS_KEYS_IN, set_active_mode
)

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

            if rv3d.view_perspective == 'CAMERA' and not getattr(area.spaces.active, 'lock_camera', False) and (math.hypot(self.vx, self.vy) > 0.015 or abs(self.vz) > 0.01):
                rv3d.view_perspective = 'PERSP'

            is_pan = (S.nav_mode == 'PAN') or self.shift

            if is_pan and (self.vx != 0.0 or self.vy != 0.0):
                dist = max(rv3d.view_distance, 0.05)
                spd = PAN_MAX_SPEED * sens * dist
                if self.shift and S.nav_mode == 'PAN':
                    spd *= 2.5
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
                test_pitch_rot = (q_pitch @ rv3d.view_rotation).normalized()
                up_z = (test_pitch_rot @ Vector((0.0, 1.0, 0.0))).z
                if up_z > 0.01:
                    rv3d.view_rotation = (q_yaw @ test_pitch_rot).normalized()
                else:
                    rv3d.view_rotation = (q_yaw @ rv3d.view_rotation).normalized()
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


S.nav = _SmoothNavigator()

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





def enter_nav_mode(context, area=None, report_fn=None):
    set_active_mode('NAV', context, report_fn, target_area=area)


def exit_nav_mode(context, report_fn=None):
    set_active_mode('PREVIEW', context, report_fn)


def handle_nav_event(op, context, event, win, area):
    t, v = event.type, event.value
    prefs = get_preferences(context)
    now = time.perf_counter()
    mx, my = event.mouse_x, event.mouse_y

    if t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
        return {'PASS_THROUGH'}

    if t in {'RIGHT_ALT', 'LINEFEED'}:
        if v == 'PRESS':
            S.right_alt_down = True
        elif v == 'RELEASE':
            S.right_alt_down = False

    if ((t == 'SPACE' and is_altgr_pressed(event) and event.shift) or
        check_shortcut(prefs, 'fuzzy_search', event, 'SPACE', fallback_ctrl=True, fallback_shift=True, fallback_alt=True)) and v == 'PRESS':
        S.navigation_mode = False
        S.workspace_nav = False
        try:
            with context.temp_override(window=win):
                bpy.ops.wm.vim_fuzzy_search('INVOKE_DEFAULT')
        except Exception as e:
            print(f"[Vimender] fuzzy_search error: {e}")
        return {'RUNNING_MODAL'}

    if ((t == 'SPACE' and is_altgr_pressed(event) and not event.shift) or
        check_shortcut(prefs, 'search_mode', event, 'SPACE', fallback_ctrl=True, fallback_alt=True)) and v == 'PRESS':
        S.navigation_mode = False
        S.workspace_nav = False
        try:
            with context.temp_override(window=win):
                bpy.ops.wm.vim_search_mode('INVOKE_DEFAULT')
        except Exception as e:
            print(f"[Vimender] search_mode error: {e}")
        return {'RUNNING_MODAL'}

    if t in {'ESC', 'RET', 'NUMPAD_ENTER'} and v == 'PRESS':
        exit_nav_mode(context, op.report)
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
                if now - getattr(op, '_last_ws_cycle_t', 0.0) >= 0.22:
                    op._last_ws_cycle_t = now
                    tgt_ws = cycle_workspace(context, ws_dir)
                    ws_name = tgt_ws.name if tgt_ws else (win.workspace.name if win and win.workspace else "")
                    if ws_name:
                        op.report({'INFO'}, f"Vimender: Workspace -> {ws_name}")
                    op._set_status(context)
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
                        op._focus_area(win, tgt)
                    else:
                        op._focus_area(win, target_a)
                    op._set_status(context)
                    tag_all()
            return {'RUNNING_MODAL'}

        if t == 'O' or event.unicode in {'o', 'O'}:
            if v == 'PRESS':
                areas = sorted(focusable_areas(win), key=lambda a: (-a.y, a.x))
                ptrs = [a.as_pointer() for a in areas]
                target_a = area or area_by_ptr(win, S.hover_ptr) or area_at(win, mx, my)
                if target_a and target_a.as_pointer() in ptrs:
                    nxt = areas[(ptrs.index(target_a.as_pointer()) + 1) % len(areas)]
                    op._focus_area(win, nxt)
                elif areas:
                    op._focus_area(win, areas[0])
                op._set_status(context)
                tag_all()
            return {'RUNNING_MODAL'}

    return {'RUNNING_MODAL'}


def handle_orbit_pan_event(op, context, event, win, area):
    t, v = event.type, event.value
    mx, my = event.mouse_x, event.mouse_y
    prefs = get_preferences(context)
    use_arrow_layout = bool(getattr(prefs, 'arrow_keys_layout', False)) if prefs else False

    if t in {'ESC', 'RET', 'NUMPAD_ENTER'} and v == 'PRESS':
        set_active_mode('PREVIEW', context, op.report)
        return {'RUNNING_MODAL'}

    if not S.navigation_mode and not S.cursor_mode and not S.mode:
        is_pie_trigger = False
        if v == 'PRESS':
            if t in {'Z', 'ACCENT_GRAVE', 'TILDE', 'Q'} and not (event.ctrl or event.alt or event.oskey):
                is_pie_trigger = True
            elif t == 'TAB' and event.ctrl and not (event.alt or event.oskey):
                is_pie_trigger = True
            elif t == 'S' and event.shift and not (event.ctrl or event.alt or event.oskey):
                is_pie_trigger = True
            elif t == 'SPACE' and event.shift and not (event.ctrl or event.alt or event.oskey):
                is_pie_trigger = True
            elif t in {'COMMA', 'PERIOD'} and (event.alt or event.ctrl or event.shift):
                is_pie_trigger = True
        if is_pie_trigger:
            S.pie_active = True
            S.pie_open_time = time.time()

    if time.time() - getattr(S, 'pie_just_selected_time', 0.0) < 0.15:
        if t in NUMPAD_PIE_KEYS:
            return {'PASS_THROUGH'}

    is_pie = False
    if not S.search_active and not S.save_dialog_open and not S.mode:
        if S.pie_active and (time.time() - S.pie_open_time < 5.0):
            is_pie = True
        elif getattr(context, 'region_popup', None) is not None:
            is_pie = True

    if is_pie:
        if t in {'ESC', 'RET', 'NUMPAD_ENTER', 'TAB'} and v == 'PRESS':
            S.pie_active = False
        elif t in MOUSE_CANCEL and v == 'PRESS' and time.time() - S.pie_open_time > 0.15:
            S.pie_active = False
        elif t in NUMPAD_PIE_KEYS:
            S.pie_active = False
            return {'PASS_THROUGH'}
        elif not (event.ctrl or event.alt or event.oskey):
            target_pair = NUM_TO_PAD.get(t) or UNICODE_TO_PAD.get(event.unicode)
            if target_pair:
                num_val, pad_name = target_pair
                if v == 'PRESS':
                    S.pie_active = False
                    S.pie_just_selected_time = time.time()
                    try:
                        win.event_simulate(type=pad_name, value='PRESS')
                        win.event_simulate(type=pad_name, value='RELEASE')
                    except Exception:
                        pass
                    send_fake_numpad(num_val)
                    tag_all()
                    return {'RUNNING_MODAL'}
                elif v == 'RELEASE':
                    return {'RUNNING_MODAL'}

    target_v3d = area or area_at(win, mx, my) or area_by_ptr(win, S.hover_ptr) or context.area
    if not target_v3d or target_v3d.type != 'VIEW_3D':
        target_v3d = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None)

    if (target_v3d and target_v3d.type == 'VIEW_3D' and
            not (area and area.type in PASS_KEYS_IN) and
            not transform_active(win)):

        allow_view_switch = not S.preview_mode or (use_arrow_layout and not is_edit_mode(context))

        if (t in {'NUMPAD_0', 'ZERO'} or event.unicode == '0') and event.ctrl and event.alt and not (event.shift or getattr(event, 'oskey', False)):
            if v == 'PRESS':
                _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.camera_to_view)
                op.report({'INFO'}, "Vimender: Align Camera to View")
                tag_all()
            return {'RUNNING_MODAL'}

        if (t in {'NUMPAD_1', 'ONE'} or event.unicode == '1') and allow_view_switch and not (event.alt or event.oskey):
            if v == 'PRESS':
                axis = 'BACK' if event.ctrl else 'FRONT'
                _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_axis, type=axis, align_active=bool(event.shift))
            return {'RUNNING_MODAL'}

        if (t in {'NUMPAD_3', 'THREE'} or event.unicode == '3') and allow_view_switch and not (event.alt or event.oskey):
            if v == 'PRESS':
                axis = 'LEFT' if event.ctrl else 'RIGHT'
                _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_axis, type=axis, align_active=bool(event.shift))
            return {'RUNNING_MODAL'}

        if (t in {'NUMPAD_7', 'SEVEN'} or event.unicode == '7') and allow_view_switch and not (event.alt or event.oskey):
            if v == 'PRESS':
                axis = 'BOTTOM' if event.ctrl else 'TOP'
                _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_axis, type=axis, align_active=bool(event.shift))
            return {'RUNNING_MODAL'}

        if (t in {'NUMPAD_9', 'NINE'} or event.unicode == '9') and (allow_view_switch or use_arrow_layout) and not (event.ctrl or event.alt or event.oskey or event.shift):
            if v == 'PRESS':
                _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_orbit, angle=math.pi, type='ORBITRIGHT')
            return {'RUNNING_MODAL'}

        if (t in {'NUMPAD_0', 'ZERO'} or event.unicode == '0') and (allow_view_switch or use_arrow_layout) and not (event.ctrl or event.alt or event.oskey or event.shift):
            if v == 'PRESS':
                _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_camera)
            return {'RUNNING_MODAL'}

        if (t in {'NUMPAD_5', 'FIVE'} or event.unicode == '5') and (allow_view_switch or use_arrow_layout) and not (event.ctrl or event.alt or event.oskey or event.shift):
            if v == 'PRESS':
                _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_persportho)
            return {'RUNNING_MODAL'}

        if (t in {'NUMPAD_SLASH', 'SLASH'} or event.unicode == '/') and (allow_view_switch or use_arrow_layout) and not (event.ctrl or event.alt or event.oskey or event.shift):
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

    if event.ctrl and (t in {'NUMPAD_4', 'NUMPAD_6'} or (use_arrow_layout and t in {'LEFT_ARROW', 'RIGHT_ARROW'})) and v == 'PRESS':
        tgt_ws = cycle_workspace(context, 'PREV' if t in {'NUMPAD_4', 'LEFT_ARROW'} else 'NEXT')
        if tgt_ws:
            op.report({'INFO'}, f"Vimender: Workspace -> {tgt_ws.name}")
        op._set_status(context)
        tag_all()
        return {'RUNNING_MODAL'}

    if S.prefix:
        return op._modal_prefix(context, event, win, area)

    if (t in {'NUMPAD_PLUS', 'NUMPAD_MINUS', 'PLUS', 'MINUS', 'EQUAL'} or event.unicode in {'+', '-'}) and event.ctrl and not (area and area.type in PASS_KEYS_IN):
        if v == 'PRESS':
            change_sens(1 if (t in {'NUMPAD_PLUS', 'PLUS', 'EQUAL'} or event.unicode == '+') else -1)
            sens_val = SENS_LEVELS[S.sens_idx]
            op.report({'INFO'}, f"Vimender: Sensitivity {sens_val:.2f}x")
            op._set_status(context)
            tag_all()
        return {'RUNNING_MODAL'}

    if area and area.type == 'OUTLINER' and not (event.ctrl or event.alt or event.oskey):
        if t in {'NUMPAD_8', 'NUMPAD_2'} or (use_arrow_layout and t in {'UP_ARROW', 'DOWN_ARROW'}):
            if v == 'PRESS':
                direction = 'UP' if t in {'NUMPAD_8', 'UP_ARROW'} else 'DOWN'
                navigate_in_editor(context, win, area, direction)
                return {'RUNNING_MODAL'}
            elif v == 'RELEASE':
                return {'RUNNING_MODAL'}

    is_orbit_pan_toggle = (
        check_shortcut(prefs, 'orbit_pan', event, 'U', fallback_ctrl=False) or
        ((t in {'U', 'NUMPAD_5', 'P'} or (event.unicode or '').upper() in {'U', 'P'}) and not (event.ctrl or event.alt or event.oskey or event.shift))
    )
    if is_orbit_pan_toggle and not (area and area.type in PASS_KEYS_IN):
        if v == 'PRESS':
            key_upper = (event.unicode or t or '').upper()
            if key_upper == 'P':
                S.nav_mode = 'PAN' if S.nav_mode == 'ORBIT' else 'ORBIT'
            elif key_upper == 'U':
                S.nav_mode = 'ORBIT' if S.nav_mode == 'PAN' else 'PAN'
            else:
                S.nav_mode = 'PAN' if S.nav_mode == 'ORBIT' else 'ORBIT'
            op.report({'INFO'}, f"Vimender: {S.nav_mode} MODE")
            op._set_status(context)
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
        if is_blender_modal_active(win):
            if v == 'PRESS':
                is_up = (zoom_key == 'PLUS')
                adjust_active_modal_or_modifier(context, is_up)
                send_modal_plus_minus(is_up)
                if area:
                    area.tag_redraw()
            return {'RUNNING_MODAL'}
        S.nav.on_key(zoom_key, v)
        return {'RUNNING_MODAL'}

    nav_key = None
    if t in {'NUMPAD_8', 'NUMPAD_2', 'NUMPAD_4', 'NUMPAD_6', 'UP_ARROW', 'DOWN_ARROW', 'LEFT_ARROW', 'RIGHT_ARROW'}:
        if not (event.ctrl or event.alt or event.oskey):
            nav_key = t
        elif v == 'RELEASE' and t in S.nav.held:
            nav_key = t
    elif t in {'K', 'J', 'H', 'L'} or ((event.unicode or "").upper() in {'K', 'J', 'H', 'L'}):
        mapped = t if t in {'K', 'J', 'H', 'L'} else (event.unicode or "").upper()
        if not (event.ctrl or event.alt or event.oskey):
            nav_key = mapped
        elif v == 'RELEASE' and (mapped in S.nav.held or t in S.nav.held):
            nav_key = mapped

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
            op._start_hint(context, win, target_area, False, action, mx, my)
            return {'RUNNING_MODAL'}

    if check_shortcut(prefs, 'box_select', event, 'C', fallback_ctrl=False, allow_shift_alt_extend=True) and v == 'PRESS':
        target_area = area or area_at(win, mx, my) or area_by_ptr(win, S.hover_ptr) or context.area
        if target_area:
            if target_area.type != 'VIEW_3D':
                return {'PASS_THROUGH'}
            action = 'EXTEND' if event.shift else 'NORMAL'
            op._start_hint(context, win, target_area, True, action, mx, my)
            return {'RUNNING_MODAL'}

    return {'PASS_THROUGH'}
