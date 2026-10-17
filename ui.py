import math
import time
import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from .state import (
    S, GREEN, CYAN, RED, PURPLE, YELLOW, ORANGE, GREY, BORDER_PX, HINT_BG,
    HINT_TEXT, HINT_TYPED, LEADER, SPACE_NAMES, window_region, area_by_ptr, ui_scale,
    tag_all, _handlers, _shader, _ORIGINAL_VIEW_PIE_DRAW, get_preferences
)

def _get_shader():
    global _shader
    if _shader is None:
        try:
            _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        except Exception:
            _shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')
    return _shader


def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1)]


def _line(x0, y0, x1, y1, w):
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return []
    nx, ny = -dy / L * w / 2, dx / L * w / 2
    a, b = (x0 + nx, y0 + ny), (x0 - nx, y0 - ny)
    c, d = (x1 - nx, y1 - ny), (x1 + nx, y1 + ny)
    return [a, b, c, a, c, d]


def _circle(cx, cy, r, seg=14):
    v = []
    for i in range(seg):
        a0 = 2 * math.pi * i / seg
        a1 = 2 * math.pi * (i + 1) / seg
        v += [(cx, cy), (cx + r * math.cos(a0), cy + r * math.sin(a0)),
              (cx + r * math.cos(a1), cy + r * math.sin(a1))]
    return v


def _fill(verts, color):
    if not verts:
        return
    sh = _get_shader()
    batch = batch_for_shader(sh, 'TRIS', {"pos": verts})
    sh.bind()
    sh.uniform_float("color", color)
    batch.draw(sh)


def _font_size(size):
    try:
        blf.size(0, size)
    except TypeError:
        blf.size(0, size, 72)


def _text(x, y, s, color):
    blf.color(0, *color)
    blf.position(0, x, y, 0)
    blf.draw(0, s)


def glyph_tris(t, cx, cy, r):
    """Simple vector icon for object types (triangle list)."""
    lw = max(1.3, r * 0.3)
    if t == 'MESH':
        return [(cx - r, cy - r * 0.8), (cx + r, cy - r * 0.8), (cx, cy + r)]
    if t in {'CURVE', 'SURFACE', 'CURVES'}:
        pts = [(cx - r + 2 * r * i / 10, cy + 0.6 * r * math.sin(2 * math.pi * i / 10))
               for i in range(11)]
        v = []
        for a, b in zip(pts, pts[1:]):
            v += _line(a[0], a[1], b[0], b[1], lw)
        return v
    if t == 'CAMERA':
        return (_rect(cx - r, cy - r * 0.55, cx + r * 0.3, cy + r * 0.55) +
                [(cx + r * 0.3, cy), (cx + r, cy + r * 0.65), (cx + r, cy - r * 0.65)])
    if t == 'LIGHT':
        v = _circle(cx, cy, r * 0.5)
        for k in range(8):
            a = math.pi * k / 4
            v += _line(cx + math.cos(a) * r * 0.72, cy + math.sin(a) * r * 0.72,
                       cx + math.cos(a) * r, cy + math.sin(a) * r, lw * 0.8)
        return v
    if t == 'ARMATURE':
        return [(cx, cy + r), (cx - r * 0.45, cy), (cx + r * 0.45, cy),
                (cx, cy - r), (cx + r * 0.45, cy), (cx - r * 0.45, cy)]
    if t == 'FONT':
        return _rect(cx - r, cy + r * 0.5, cx + r, cy + r) + _rect(cx - lw / 2, cy - r, cx + lw / 2, cy + r * 0.5)
    if t == 'LATTICE':
        v = []
        for i in (-1, 0, 1):
            v += _line(cx - r, cy + i * r * 0.7, cx + r, cy + i * r * 0.7, lw * 0.7)
            v += _line(cx + i * r * 0.7, cy - r, cx + i * r * 0.7, cy + r, lw * 0.7)
        return v
    if t == 'META':
        return _circle(cx - r * 0.35, cy, r * 0.6) + _circle(cx + r * 0.35, cy, r * 0.6)
    if t == 'EMPTY':
        return _line(cx - r, cy, cx + r, cy, lw) + _line(cx, cy - r, cx, cy + r, lw)
    if t in {'GPENCIL', 'GREASEPENCIL'}:
        return _line(cx - r, cy - r, cx + r * 0.5, cy + r * 0.5, lw * 1.6) + \
            [(cx + r * 0.2, cy + r * 0.9), (cx + r * 0.9, cy + r * 0.2), (cx + r, cy + r)]
    if t == 'SPEAKER':
        return _rect(cx - r, cy - r * 0.4, cx - r * 0.2, cy + r * 0.4) + \
            [(cx - r * 0.2, cy + r * 0.4), (cx + r * 0.6, cy + r), (cx + r * 0.6, cy - r),
             (cx - r * 0.2, cy + r * 0.4), (cx + r * 0.6, cy - r), (cx - r * 0.2, cy - r * 0.4)]
    if t in {'LIGHT_PROBE'}:
        return _circle(cx, cy, r * 0.8)
    if t in {'VOLUME', 'POINTCLOUD'}:
        return (_circle(cx - r * 0.5, cy - r * 0.3, r * 0.3) + _circle(cx + r * 0.4, cy - r * 0.5, r * 0.3) +
                _circle(cx, cy + r * 0.5, r * 0.3) + _circle(cx + r * 0.6, cy + r * 0.3, r * 0.25))
    return _rect(cx - r * 0.7, cy - r * 0.7, cx + r * 0.7, cy + r * 0.7)



def draw_cb():
    ctx = bpy.context
    area, region = ctx.area, ctx.region
    if not S.active or area is None or region is None:
        return
    ptr = area.as_pointer()
    active_ptr = S.hint_ptr if S.mode else (S.walk_ptr if S.walk_mode else (S.tablet_ptr if S.tablet_mode else S.hover_ptr))
    if not active_ptr:
        active_ptr = ptr
    if ptr != active_ptr:
        return

    ui = ui_scale()
    w, h = region.width, region.height
    gpu.state.blend_set('ALPHA')

    if S.walk_mode:
        border_col = ORANGE
    elif S.tablet_mode:
        border_col = GREY
    elif S.cursor_mode:
        border_col = PURPLE
    elif S.preview_mode:
        border_col = YELLOW
    elif S.navigation_mode or S.workspace_nav:
        border_col = RED
    elif S.nav_mode == 'PAN' and not S.mode:
        border_col = CYAN
    else:
        border_col = GREEN
    t = BORDER_PX if not (S.cursor_mode or S.preview_mode or S.navigation_mode or S.workspace_nav or S.walk_mode or S.tablet_mode) else (BORDER_PX + 1)
    _fill(_rect(0, 0, w, t) + _rect(0, h - t, w, h) +
          _rect(0, 0, t, h) + _rect(w - t, 0, w, h), border_col)

    if S.mode in {'HINT', 'BOX1', 'BOX2'}:
        _font_size(int(13 * ui))
        pad = 4 * ui
        th = blf.dimensions(0, "Ag")[1]
        edges, leaders, pills = {}, [], {}
        draw_list, icon_list = [], []
        for hn in S.hints:
            if not hn.label.startswith(S.typed):
                continue
            tw = blf.dimensions(0, hn.label)[0]
            x0, y0 = hn.x - tw / 2 - pad, hn.y - th / 2 - pad
            x1, y1 = x0 + tw + 2 * pad, y0 + th + 2 * pad
            col = hn.color if (S.vertex_mode and hn.color) else HINT_BG
            pills.setdefault(col, []).extend(_rect(x0, y0, x1, y1))
            draw_list.append((x0 + pad, y0 + pad, hn.label))
            if hn.cluster:
                if math.hypot(hn.x - hn.ax, hn.y - hn.ay) > 3:
                    leaders += _line(hn.ax, hn.ay, hn.x, hn.y, 1.5 * ui)
                    leaders += _rect(hn.ax - 2.5 * ui, hn.ay - 2.5 * ui, hn.ax + 2.5 * ui, hn.ay + 2.5 * ui)
                for seg in hn.edges:
                    edges.setdefault(col, []).extend(_line(seg[0], seg[1], seg[2], seg[3], 3 * ui))
                if S.kind == 'OBJ' and hn.payload:
                    icon_list.append((hn.x, y1, hn.payload[1]))

        for col, verts in edges.items():
            _fill(verts, col)
        _fill(leaders, LEADER)
        for col, verts in pills.items():
            _fill(verts, col)
        typed_w = blf.dimensions(0, S.typed)[0] if S.typed else 0
        for x, y, label in draw_list:
            if S.typed:
                _text(x, y, S.typed, HINT_TYPED)
            _text(x + typed_w, y, label[len(S.typed):], HINT_TEXT)

        if icon_list:
            tile = 18 * ui
            tiles, glyphs = [], []
            for cx, top, otype in icon_list:
                cy = top + 2 * ui + tile / 2
                tiles += _rect(cx - tile / 2, cy - tile / 2, cx + tile / 2, cy + tile / 2)
                glyphs += glyph_tris(otype, cx, cy, tile * 0.32)
            _fill(tiles, HINT_BG)
            _fill(glyphs, HINT_TEXT)

    if S.mode == 'BOX2' and S.box_a:
        ax, ay = S.box_a
        mx, my = S.mouse
        pad = 14 * ui
        x0, x1 = min(ax, mx) - pad, max(ax, mx) + pad
        y0, y1 = min(ay, my) - pad, max(ay, my) + pad
        _fill(_rect(x0, y0, x1, y1), (GREEN[0], GREEN[1], GREEN[2], 0.18))
        b = 2 * ui
        _fill(_rect(x0, y0, x1, y0 + b) + _rect(x0, y1 - b, x1, y1) +
              _rect(x0, y0, x0 + b, y1) + _rect(x1 - b, y0, x1, y1), GREEN)
        _fill(_rect(ax - 5 * ui, ay - 5 * ui, ax + 5 * ui, ay + 5 * ui), GREEN)
        _fill(_rect(mx - 4 * ui, my - 4 * ui, mx + 4 * ui, my + 4 * ui), HINT_BG)

    if S.walk_mode:
        label = "WALK MODE"
    elif S.tablet_mode:
        label = "TABLET MODE"
    elif S.cursor_mode:
        label = f"CURSOR MODE - {S.cursor_plane}"
    elif S.preview_mode:
        label = "PREVIEW MODE"
    elif S.navigation_mode or S.workspace_nav:
        label = "NAVIGATION MODE"
    elif S.mode == 'HINT':
        label = "HINT MODE"
    elif S.mode in {'BOX1', 'BOX2'}:
        label = "BOX SELECT MODE"
    elif S.nav_mode == 'PAN':
        label = "PAN MODE"
    else:
        label = "ORBIT MODE"
    _font_size(int(14 * ui))
    tw, th = blf.dimensions(0, label)
    inset = 0
    if region.width >= area.width - 1:
        inset = sum(r.width for r in area.regions
                    if r.type == 'UI' and r.alignment == 'RIGHT' and r.width > 1)
    pad = 6 * ui
    x1 = w - inset - 12 * ui
    x0 = x1 - tw - 2 * pad
    y0 = 12 * ui
    _fill(_rect(x0, y0, x1, y0 + th + 2 * pad), (0.0, 0.0, 0.0, 0.65))
    _text(x0 + pad, y0 + pad, label, border_col)

    gpu.state.blend_set('NONE')



def install_draw_handlers():
    if _handlers:
        return
    for name in SPACE_NAMES:
        cls = getattr(bpy.types, name, None)
        if cls is None:
            continue
        try:
            h = cls.draw_handler_add(draw_cb, (), 'WINDOW', 'POST_PIXEL')
            _handlers.append((cls, h))
        except Exception:
            pass


def remove_draw_handlers():
    for cls, h in _handlers:
        try:
            cls.draw_handler_remove(h, 'WINDOW')
        except Exception:
            pass
    _handlers.clear()


def clean_modal_keymaps():
    wm = getattr(bpy.context, 'window_manager', None)
    if not wm or not getattr(wm, 'keyconfigs', None):
        return
    modified = False
    for kc in (wm.keyconfigs.default, wm.keyconfigs.user, getattr(wm.keyconfigs, 'active', None)):
        if not kc:
            continue
        for map_name in ('Transform Modal Map', 'Bevel Modal Map'):
            km = kc.keymaps.get(map_name)
            if km:
                for kmi in list(km.keymap_items):
                    if getattr(kmi, 'propvalue', '') in {'PROPORTIONAL_SIZE_UP', 'PROPORTIONAL_SIZE_DOWN', 'SEGMENTS_UP', 'SEGMENTS_DOWN'}:
                        try:
                            km.keymap_items.remove(kmi)
                            modified = True
                        except Exception:
                            pass
    prefs = get_preferences()
    if prefs and getattr(prefs, 'key_orbit_pan', '') == 'NUMPAD_5':
        try:
            prefs.key_orbit_pan = 'U'
            modified = True
        except Exception:
            pass
    if modified and not getattr(bpy.app, 'background', False):
        try:
            bpy.ops.wm.save_userpref()
        except Exception:
            pass



def shutdown():
    S.active = False
    S.prefix = False
    S.navigation_mode = False
    S.workspace_nav = False
    S.cursor_mode = False
    S.preview_mode = True
    S.smooth_cursor.reset()
    S.nav.reset()
    S.clear_hint()
    S.hover_ptr = 0
    remove_draw_handlers()
    try:
        tag_all()
    except Exception:
        pass


def _pie_item(layout, label, icon, candidates):
    op = layout.operator("wm.vim_set_editor", text=label, icon=icon)
    op.candidates = "|".join(candidates)


def draw_editor_pie(self, context):
    pie = self.layout.menu_pie()
    _pie_item(pie, "Outliner", 'OUTLINER', ("OUTLINER",))
    _pie_item(pie, "Properties", 'PROPERTIES', ("PROPERTIES",))
    _pie_item(pie, "Shader Editor", 'NODE_MATERIAL', ("ShaderNodeTree",))
    _pie_item(pie, "3D Viewport", 'VIEW3D', ("VIEW_3D",))
    _pie_item(pie, "Text Editor", 'TEXT', ("TEXT_EDITOR",))
    _pie_item(pie, "Image Editor", 'IMAGE_DATA', ("IMAGE_EDITOR",))
    _pie_item(pie, "Geometry Nodes", 'GEOMETRY_NODES', ("GeometryNodeTree",))
    pie.menu("VIM_MT_editor_more", text="More...", icon='COLLAPSEMENU')


class VIM_MT_editor_more(bpy.types.Menu):
    bl_label = "Other Editors"

    def draw(self, context):
        l = self.layout
        _pie_item(l, "UV Editor", 'UV', ("UV",))
        _pie_item(l, "Compositor", 'NODE_COMPOSITING', ("CompositorNodeTree",))
        _pie_item(l, "Texture Nodes", 'NODE_TEXTURE', ("TextureNodeTree",))
        _pie_item(l, "Timeline", 'TIME', ("TIMELINE",))
        _pie_item(l, "Dope Sheet", 'ACTION', ("DOPESHEET",))
        _pie_item(l, "Graph Editor", 'GRAPH', ("FCURVES",))
        _pie_item(l, "Drivers", 'DRIVER', ("DRIVERS",))
        _pie_item(l, "NLA", 'NLA', ("NLA_EDITOR",))
        _pie_item(l, "Video Sequencer", 'SEQUENCE', ("SEQUENCER", "SEQUENCE_EDITOR"))
        _pie_item(l, "Movie Clip", 'TRACKER', ("CLIP_EDITOR",))
        _pie_item(l, "File Browser", 'FILEBROWSER', ("FILES",))
        _pie_item(l, "Asset Browser", 'ASSET_MANAGER', ("ASSETS",))
        _pie_item(l, "Spreadsheet", 'SPREADSHEET', ("SPREADSHEET",))
        _pie_item(l, "Python Console", 'CONSOLE', ("CONSOLE",))
        _pie_item(l, "Info", 'INFO', ("INFO",))


def draw_vim_view_pie(self, context):
    pie = self.layout.menu_pie()
    pie.operator("view3d.view_axis", text="Left (4)", icon='AXIS_SIDE').type = 'LEFT'
    pie.operator("view3d.view_axis", text="Right (3)", icon='AXIS_SIDE').type = 'RIGHT'
    pie.operator("view3d.view_axis", text="Front (1)", icon='AXIS_FRONT').type = 'FRONT'
    pie.operator("view3d.view_axis", text="Top (7)", icon='AXIS_TOP').type = 'TOP'
    pie.operator("view3d.view_axis", text="Back (8)").type = 'BACK'
    pie.operator("view3d.view_orbit", text="Opposite (9)").type = 'ORBITRIGHT'
    pie.operator("view3d.view_camera", text="Camera (0)", icon='CAMERA_DATA')
    pie.operator("view3d.view_selected", text="Selected (.)", icon='ZOOM_SELECTED')

    col = pie.column()
    col.operator("view3d.view_persportho", text="Persp/Ortho (5)")
    col.operator("view3d.view_axis", text="Bottom (2)").type = 'BOTTOM'
    col.operator("view3d.localview", text="Local View (/)")


class VIEW3D_MT_vim_view_pie(bpy.types.Menu):
    bl_label = "View"
    bl_idname = "VIEW3D_MT_vim_view_pie"

    def draw(self, context):
        draw_vim_view_pie(self, context)


_ORIGINAL_VIEW_PIE_DRAW = None


def patch_view_pie():
    global _ORIGINAL_VIEW_PIE_DRAW
    try:
        import bl_ui.space_view3d as sv
        if hasattr(sv, 'VIEW3D_MT_view_pie'):
            _ORIGINAL_VIEW_PIE_DRAW = getattr(sv.VIEW3D_MT_view_pie, 'draw', None)
            sv.VIEW3D_MT_view_pie.draw = draw_vim_view_pie
    except Exception:
        pass


def unpatch_view_pie():
    global _ORIGINAL_VIEW_PIE_DRAW
    try:
        import bl_ui.space_view3d as sv
        if hasattr(sv, 'VIEW3D_MT_view_pie') and _ORIGINAL_VIEW_PIE_DRAW is not None:
            sv.VIEW3D_MT_view_pie.draw = _ORIGINAL_VIEW_PIE_DRAW
    except Exception:
        pass


