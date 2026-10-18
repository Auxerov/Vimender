import bpy
from .state import S, GREY, tag_all, get_preferences, load_vimender_config, set_active_mode


def enter_tablet_mode(context, area=None, report_fn=None):
    set_active_mode('TABLET', context, report_fn, target_area=area)


def exit_tablet_mode(context, report_fn=None):
    set_active_mode('PREVIEW', context, report_fn)


S.exit_tablet_fn = exit_tablet_mode


def handle_tablet_event(op, context, event, win, area):
    t, v = event.type, event.value
    if t in {'ESC', 'RET', 'NUMPAD_ENTER'} and v == 'PRESS':
        exit_tablet_mode(context, op.report)
        return {'RUNNING_MODAL'}

    return {'PASS_THROUGH'}
