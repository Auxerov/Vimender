import bpy
from .state import S, YELLOW, tag_all, get_preferences, check_shortcut, set_active_mode, area_at, area_by_ptr, is_edit_mode, _exec_view3d_op, window_region


def enter_preview_mode(context, report_fn=None):
    set_active_mode('PREVIEW', context, report_fn)


def exit_preview_mode(context, report_fn=None):
    set_active_mode('ORBIT_PAN', context, report_fn)


def handle_preview_event(op, context, event, win, area):
    t, v = event.type, event.value
    prefs = get_preferences(context)

    if check_shortcut(prefs, 'hint_select', event, 'F', fallback_ctrl=False, allow_shift_alt_extend=True) and v == 'PRESS':
        target_area = area or area_at(win, event.mouse_x, event.mouse_y) or area_by_ptr(win, S.hover_ptr) or context.area
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
        target_area = area or area_at(win, event.mouse_x, event.mouse_y) or area_by_ptr(win, S.hover_ptr) or context.area
        if not target_area or target_area.type != 'VIEW_3D':
            target_area = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), context.area) if (win and win.screen) else context.area
        if target_area and target_area.type == 'VIEW_3D':
            action = 'EXTEND' if event.shift else 'NORMAL'
            op._start_hint(context, win, target_area, True, action, event.mouse_x, event.mouse_y)
            return {'RUNNING_MODAL'}

    use_arrow_layout = bool(getattr(prefs, 'arrow_keys_layout', False)) if prefs else False
    if use_arrow_layout and not is_edit_mode(context):
        target_v3d = area or area_at(win, event.mouse_x, event.mouse_y) or area_by_ptr(win, S.hover_ptr) or context.area
        if not target_v3d or target_v3d.type != 'VIEW_3D':
            target_v3d = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None) if (win and win.screen) else None
        if target_v3d:
            if (t in {'NUMPAD_0', 'ZERO'} or getattr(event, 'unicode', '') == '0') and event.ctrl and event.alt and not (event.shift or getattr(event, 'oskey', False)):
                if v == 'PRESS':
                    _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.camera_to_view)
                    op.report({'INFO'}, "Vimender: Align Camera to View")
                    tag_all()
                return {'RUNNING_MODAL'}

            if (t in {'NUMPAD_0', 'ZERO'} or getattr(event, 'unicode', '') == '0') and not (event.ctrl or event.alt or getattr(event, 'oskey', False) or event.shift):
                if v == 'PRESS':
                    _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_camera)
                return {'RUNNING_MODAL'}

            if (t in {'NUMPAD_1', 'ONE'} or getattr(event, 'unicode', '') == '1') and not (event.alt or getattr(event, 'oskey', False)):
                if v == 'PRESS':
                    axis = 'BACK' if event.ctrl else 'FRONT'
                    _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_axis, type=axis, align_active=bool(event.shift))
                return {'RUNNING_MODAL'}

            if (t in {'NUMPAD_3', 'THREE'} or getattr(event, 'unicode', '') == '3') and not (event.alt or getattr(event, 'oskey', False)):
                if v == 'PRESS':
                    axis = 'LEFT' if event.ctrl else 'RIGHT'
                    _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_axis, type=axis, align_active=bool(event.shift))
                return {'RUNNING_MODAL'}

            if (t in {'NUMPAD_7', 'SEVEN'} or getattr(event, 'unicode', '') == '7') and not (event.alt or getattr(event, 'oskey', False)):
                if v == 'PRESS':
                    axis = 'BOTTOM' if event.ctrl else 'TOP'
                    _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_axis, type=axis, align_active=bool(event.shift))
                return {'RUNNING_MODAL'}

            if (t in {'NUMPAD_9', 'NINE'} or getattr(event, 'unicode', '') == '9') and not (event.ctrl or event.alt or getattr(event, 'oskey', False) or event.shift):
                if v == 'PRESS':
                    _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_orbit, angle=math.pi, type='ORBITRIGHT')
                return {'RUNNING_MODAL'}

            if (t in {'NUMPAD_5', 'FIVE'} or getattr(event, 'unicode', '') == '5') and not (event.ctrl or event.alt or getattr(event, 'oskey', False) or event.shift):
                if v == 'PRESS':
                    _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_persportho)
                return {'RUNNING_MODAL'}

            if (t in {'NUMPAD_SLASH', 'SLASH'} or getattr(event, 'unicode', '') == '/') and not (event.ctrl or event.alt or getattr(event, 'oskey', False) or event.shift):
                if v == 'PRESS':
                    _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.localview)
                return {'RUNNING_MODAL'}

            is_i_selected = (t == 'I' or getattr(event, 'unicode', '') in {'i', 'I'}) and not (event.ctrl or event.alt or getattr(event, 'oskey', False) or event.shift)
            is_key_period = (
                check_shortcut(prefs, 'view_selected', event, 'NUMPAD_PERIOD', fallback_ctrl=False) or
                (t in {'NUMPAD_PERIOD', 'NUMPAD_COMMA', 'PERIOD', 'COMMA'} and not (event.ctrl or event.alt or getattr(event, 'oskey', False))) or
                (getattr(event, 'unicode', '') in {',', '.'} and not (event.ctrl or event.alt or getattr(event, 'oskey', False))) or
                is_i_selected
            )
            if is_key_period and v == 'PRESS':
                reg = window_region(target_v3d)
                if reg:
                    try:
                        with context.temp_override(window=win, area=target_v3d, region=reg):
                            bpy.ops.view3d.view_selected(use_all_regions=False)
                    except Exception:
                        pass
                    rv3d = target_v3d.spaces.active.region_3d if target_v3d.spaces else None
                    if rv3d and rv3d.view_distance < 3.2:
                        rv3d.view_distance = 3.2
                    target_v3d.tag_redraw()
                return {'RUNNING_MODAL'}

            is_asterisk_persp = (
                check_shortcut(prefs, 'persp_ortho', event, 'Y', fallback_ctrl=False) or
                (t in {'NUMPAD_ASTERIX', 'ASTERISK'} and not (event.ctrl or event.alt or getattr(event, 'oskey', False))) or
                (getattr(event, 'unicode', '') == '*' and not (event.ctrl or event.alt or getattr(event, 'oskey', False)))
            )
            if is_asterisk_persp and v == 'PRESS':
                _exec_view3d_op(context, win, target_v3d, bpy.ops.view3d.view_persportho)
                return {'RUNNING_MODAL'}

    return {'PASS_THROUGH'}
