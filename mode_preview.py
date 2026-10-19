import bpy
from .state import S, YELLOW, tag_all, get_preferences, check_shortcut, set_active_mode, area_at, area_by_ptr


def enter_preview_mode(context, report_fn=None):
    set_active_mode('PREVIEW', context, report_fn)


def exit_preview_mode(context, report_fn=None):
    set_active_mode('ORBIT_PAN', context, report_fn)


def handle_preview_event(op, context, event, win, area):
    t, v = event.type, event.value
    prefs = get_preferences(context)
    if check_shortcut(prefs, 'box_select', event, 'C', fallback_ctrl=False, allow_shift_alt_extend=True) and v == 'PRESS':
        target_area = area or area_at(win, event.mouse_x, event.mouse_y) or area_by_ptr(win, S.hover_ptr) or context.area
        if not target_area or target_area.type != 'VIEW_3D':
            target_area = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), context.area) if (win and win.screen) else context.area
        if target_area and target_area.type == 'VIEW_3D':
            action = 'EXTEND' if event.shift else 'NORMAL'
            op._start_hint(context, win, target_area, True, action, event.mouse_x, event.mouse_y)
            return {'RUNNING_MODAL'}
    return {'PASS_THROUGH'}
