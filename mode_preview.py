import bpy
from .state import S, YELLOW, tag_all, get_preferences, check_shortcut, set_active_mode


def enter_preview_mode(context, report_fn=None):
    set_active_mode('PREVIEW', context, report_fn)


def exit_preview_mode(context, report_fn=None):
    set_active_mode('ORBIT_PAN', context, report_fn)


def handle_preview_event(op, context, event, win, area):
    return {'PASS_THROUGH'}
