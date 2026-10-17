import bpy
from .state import S, YELLOW, tag_all, get_preferences, check_shortcut


def enter_preview_mode(context, report_fn=None):
    S.exit_all_modes(context, report_fn)
    S.preview_mode = True
    if report_fn:
        report_fn({'INFO'}, "Vimender: PREVIEW MODE")
    try:
        context.workspace.status_text_set("Vimender: PREVIEW MODE")
    except Exception:
        pass
    tag_all()


def exit_preview_mode(context, report_fn=None):
    S.preview_mode = False
    S.navigation_mode = False
    S.workspace_nav = False
    if report_fn:
        report_fn({'INFO'}, f"Vimender: {S.nav_mode} MODE")
    try:
        context.workspace.status_text_set(f"Vimender: {S.nav_mode} MODE")
    except Exception:
        pass
    tag_all()


def handle_preview_event(op, context, event, win, area):
    return {'PASS_THROUGH'}
