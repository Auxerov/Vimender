import bpy
from .state import S, GREY, tag_all, get_preferences, load_vimender_config


def enter_tablet_mode(context, area=None, report_fn=None):
    S.exit_all_modes(context, report_fn)
    S.tablet_prev_state = {
        'preview_mode': S.preview_mode,
        'navigation_mode': S.navigation_mode,
        'workspace_nav': S.workspace_nav,
        'nav_mode': S.nav_mode,
    }
    S.preview_mode = False
    S.navigation_mode = False
    S.workspace_nav = False
    S.tablet_mode = True
    S.tablet_ptr = area.as_pointer() if area else 0

    prefs = get_preferences(context)
    try:
        inputs = context.preferences.inputs
        S.tablet_prev_emulate_3_button = inputs.use_mouse_emulate_3_button
        cfg = load_vimender_config()
        use_stylus = bool(getattr(prefs, 'stylus_pen', False)) or bool(cfg.get('stylus_pen', False))
        if use_stylus:
            inputs.use_mouse_emulate_3_button = True
    except Exception as e:
        print(f"[Vimender] Tablet mode emulate 3 button error: {e}")

    if report_fn:
        report_fn({'INFO'}, "Vimender: TABLET MODE")
    try:
        context.workspace.status_text_set("Vimender: TABLET MODE | Stylus / Pen | Ctrl+W/Esc: Exit")
    except Exception:
        pass
    tag_all()


def exit_tablet_mode(context, report_fn=None):
    S.tablet_mode = False
    S.tablet_ptr = 0

    try:
        inputs = context.preferences.inputs
        if S.tablet_prev_emulate_3_button is not None:
            inputs.use_mouse_emulate_3_button = S.tablet_prev_emulate_3_button
            S.tablet_prev_emulate_3_button = None
    except Exception as e:
        print(f"[Vimender] Tablet mode restore emulate 3 button error: {e}")

    prev = getattr(S, 'tablet_prev_state', {})
    S.preview_mode = prev.get('preview_mode', True)
    S.navigation_mode = prev.get('navigation_mode', False)
    S.workspace_nav = prev.get('workspace_nav', False)
    S.nav_mode = prev.get('nav_mode', 'ORBIT')
    mode_name = "PREVIEW" if S.preview_mode else ("NAVIGATION" if S.navigation_mode else S.nav_mode)
    if report_fn:
        report_fn({'INFO'}, f"Vimender: {mode_name} MODE")
    try:
        context.workspace.status_text_set(f"Vimender: {mode_name} MODE")
    except Exception:
        pass
    tag_all()


S.exit_tablet_fn = exit_tablet_mode


def handle_tablet_event(op, context, event, win, area):
    t, v = event.type, event.value
    if t in {'ESC', 'RET', 'NUMPAD_ENTER'} and v == 'PRESS':
        exit_tablet_mode(context, op.report)
        return {'RUNNING_MODAL'}

    return {'PASS_THROUGH'}
