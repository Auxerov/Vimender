import bpy
from .state import window_region


def is_bool_tool_available():
    try:
        import addon_utils
        for mod in addon_utils.modules():
            mod_name = mod.__name__.lower()
            if 'bool_tool' in mod_name or 'boolean_tools' in mod_name:
                loaded, enabled = addon_utils.check(mod.__name__)
                if loaded or enabled:
                    return True
    except Exception:
        pass
    if hasattr(bpy.ops.object, 'boolean_auto_difference') or hasattr(bpy.ops, 'btool'):
        return True
    return False


def get_bool_tool_search_items(start_idx=60):
    if not is_bool_tool_available():
        return []

    bool_defs = [
        ("BOOL_AUTO_DIFF", "Bool: Difference (Auto)", "Bool Tool: Directly difference subtract selected objects", 'MOD_BOOLEAN'),
        ("BOOL_AUTO_UNION", "Bool: Union (Auto)", "Bool Tool: Directly join and union selected objects", 'MOD_BOOLEAN'),
        ("BOOL_AUTO_INTERSECT", "Bool: Intersect (Auto)", "Bool Tool: Directly keep only intersection of selected objects", 'MOD_BOOLEAN'),
        ("BOOL_AUTO_SLICE", "Bool: Slice (Auto)", "Bool Tool: Slice active object along selected objects into separate objects", 'MOD_BOOLEAN'),
        ("BOOL_BRUSH_DIFF", "Bool: Difference (Brush)", "Bool Tool: Nondestructive modifier difference cutter", 'MOD_BOOLEAN'),
        ("BOOL_BRUSH_UNION", "Bool: Union (Brush)", "Bool Tool: Nondestructive modifier union cutter", 'MOD_BOOLEAN'),
        ("BOOL_BRUSH_INTERSECT", "Bool: Intersect (Brush)", "Bool Tool: Nondestructive modifier intersect cutter", 'MOD_BOOLEAN'),
        ("BOOL_BRUSH_SLICE", "Bool: Slice (Brush)", "Bool Tool: Nondestructive modifier slice cutter", 'MOD_BOOLEAN'),
        ("BOOL_APPLY_ALL", "Bool: Apply All Cutters", "Bool Tool: Apply all boolean modifiers from cutters to selected canvases", 'CHECKMARK'),
        ("BOOL_APPLY_CUTTER", "Bool: Apply Active Cutter", "Bool Tool: Apply boolean modifier of selected cutter", 'CHECKMARK'),
        ("BOOL_REMOVE_ALL", "Bool: Remove All Cutters", "Bool Tool: Remove all cutters from selected objects", 'CANCEL'),
        ("BOOL_REMOVE_CUTTER", "Bool: Remove Cutter", "Bool Tool: Remove selected cutter modifier", 'CANCEL'),
        ("BOOL_TOGGLE_ALL", "Bool: Toggle All Cutters", "Bool Tool: Show/hide all cutter objects affecting selected canvas", 'RESTRICT_VIEW_OFF'),
        ("BOOL_TOGGLE_CUTTER", "Bool: Toggle Cutter", "Bool Tool: Show/hide selected cutter object", 'RESTRICT_VIEW_OFF'),
        ("BOOL_SELECT_ALL", "Bool: Select All Cutters", "Bool Tool: Select all cutters affecting active canvas", 'RESTRICT_SELECT_OFF'),
        ("BOOL_SELECT_CUTTER", "Bool: Select Cutter", "Bool Tool: Select cutter object for active canvas", 'RESTRICT_SELECT_OFF'),
        ("BOOL_SELECT_CANVAS", "Bool: Select Canvas", "Bool Tool: Select canvas object that uses active object as cutter", 'RESTRICT_SELECT_OFF'),
    ]

    items = []
    idx = start_idx
    for ident, label, desc, icon in bool_defs:
        items.append((ident, label, desc, icon, idx))
        idx += 1
    return items


def execute_bool_tool_action(context, target_ident, report_fn=None):
    win = context.window
    target_v3d = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), context.area) if win and win.screen else context.area
    reg = window_region(target_v3d) if target_v3d else None
    kw = {'window': win}
    if target_v3d:
        kw['area'] = target_v3d
    if reg:
        kw['region'] = reg

    op_map = {
        "BOOL_AUTO_DIFF": ("object", "boolean_auto_difference", "btool", "auto_difference"),
        "BOOL_AUTO_UNION": ("object", "boolean_auto_union", "btool", "auto_union"),
        "BOOL_AUTO_INTERSECT": ("object", "boolean_auto_intersect", "btool", "auto_intersect"),
        "BOOL_AUTO_SLICE": ("object", "boolean_auto_slice", "btool", "auto_slice"),
        "BOOL_BRUSH_DIFF": ("object", "boolean_brush_difference", "btool", "boolean_diff"),
        "BOOL_BRUSH_UNION": ("object", "boolean_brush_union", "btool", "boolean_union"),
        "BOOL_BRUSH_INTERSECT": ("object", "boolean_brush_intersect", "btool", "boolean_inters"),
        "BOOL_BRUSH_SLICE": ("object", "boolean_brush_slice", "btool", "boolean_slice"),
        "BOOL_APPLY_ALL": ("object", "boolean_apply_all", "btool", "apply_all"),
        "BOOL_APPLY_CUTTER": ("object", "boolean_apply_cutter", "btool", "apply_cutter"),
        "BOOL_REMOVE_ALL": ("object", "boolean_remove_all", "btool", "remove_all"),
        "BOOL_REMOVE_CUTTER": ("object", "boolean_remove_cutter", "btool", "remove_cutter"),
        "BOOL_TOGGLE_ALL": ("object", "boolean_toggle_all", "btool", "toggle_all"),
        "BOOL_TOGGLE_CUTTER": ("object", "boolean_toggle_cutter", "btool", "toggle_cutter"),
        "BOOL_SELECT_ALL": ("object", "boolean_select_all", "btool", "select_all"),
        "BOOL_SELECT_CUTTER": ("object", "boolean_select_cutter", "btool", "select_cutter"),
        "BOOL_SELECT_CANVAS": ("object", "select_cutter_canvas", "btool", "select_canvas"),
    }

    info = op_map.get(target_ident)
    if not info:
        return False

    mod_name, op_name, alt_mod, alt_op = info
    for m, o in ((mod_name, op_name), (alt_mod, alt_op)):
        if hasattr(bpy.ops, m) and hasattr(getattr(bpy.ops, m), o):
            op_func = getattr(getattr(bpy.ops, m), o)
            try:
                with context.temp_override(**kw):
                    op_func('INVOKE_DEFAULT')
                if target_v3d:
                    target_v3d.tag_redraw()
                return True
            except Exception:
                try:
                    with context.temp_override(**kw):
                        op_func()
                    if target_v3d:
                        target_v3d.tag_redraw()
                    return True
                except Exception as ex:
                    if report_fn:
                        report_fn({'WARNING'}, f"Bool Tool: {ex}")
                    return False
    if report_fn:
        report_fn({'WARNING'}, "Bool Tool operator not found or requires valid mesh selection")
    return False
