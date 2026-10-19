import math
import time
import bpy
import bmesh
from mathutils import Vector, Matrix
from bpy_extras.view3d_utils import location_3d_to_region_2d
from .state import (
    S, HINT_BG, HINT_TEXT, HINT_TYPED, LEADER, PALETTE, MAX_HINTS,
    MIN_LABEL_LEN, MAX_LABEL_LEN, ALPHABET, tag_all, window_region,
    ui_scale, make_labels, area_by_ptr, ALL_DIRS, SENS_LEVELS,
    CURSOR_MAX_SPEED, NAV_ACCEL_RATE, NAV_FRICTION
)

class Hint:
    __slots__ = ("label", "x", "y", "ax", "ay", "payload", "cluster",
                 "color", "color_idx", "edges", "nbrs")

    def __init__(self, label, x, y, payload=None):
        self.label = label
        self.x = x
        self.y = y
        self.ax = x
        self.ay = y
        self.payload = payload
        self.cluster = False
        self.color = None
        self.color_idx = None
        self.edges = []
        self.nbrs = []



class _SmoothBox:
    """Smooth accelerated box selection controller."""
    def __init__(self):
        self.held = set()
        self.vx = 0.0
        self.vy = 0.0
        self.x = 0.0
        self.y = 0.0
        self.last_t = 0.0
        self.active = False

    def reset(self, start_x=0.0, start_y=0.0):
        self.held.clear()
        self.vx = 0.0
        self.vy = 0.0
        self.x = float(start_x)
        self.y = float(start_y)
        self.last_t = time.perf_counter()
        self.active = True

    def on_key(self, key, val, shift=False):
        self.shift = shift
        if val == 'PRESS':
            if not self.held and math.hypot(self.vx, self.vy) < 0.1:
                self.last_t = time.perf_counter()
            self.held.add(key)
        elif val == 'RELEASE':
            self.held.discard(key)

    def tick(self, region):
        if not self.active:
            return False
        now = time.perf_counter()
        dt = min(max(now - self.last_t, 0.001), 0.05)
        self.last_t = now

        dx = sum(ALL_DIRS[k][0] for k in self.held if k in ALL_DIRS)
        dy = sum(ALL_DIRS[k][1] for k in self.held if k in ALL_DIRS)

        sens = SENS_LEVELS[S.sens_idx]
        if getattr(self, 'shift', False):
            sens *= 0.25
        max_spd = CURSOR_MAX_SPEED * sens * 1.25

        if dx != 0 or dy != 0:
            mag = math.hypot(dx, dy)
            tx = (dx / mag) * max_spd
            ty = (dy / mag) * max_spd
            alpha = 1.0 - math.exp(-NAV_ACCEL_RATE * dt)
            self.vx += (tx - self.vx) * alpha
            self.vy += (ty - self.vy) * alpha
        else:
            decay = math.exp(-NAV_FRICTION * dt)
            self.vx *= decay
            self.vy *= decay
            if math.hypot(self.vx, self.vy) < 2.0:
                self.vx = 0.0
                self.vy = 0.0
                return False

        self.x = min(max(self.x + self.vx * dt, 0.0), float(region.width))
        self.y = min(max(self.y + self.vy * dt, 0.0), float(region.height))
        S.mouse = (self.x, self.y)
        return True



S.smooth_box = _SmoothBox()

def _inside(pt, region):
    return pt is not None and 0 <= pt[0] <= region.width and 0 <= pt[1] <= region.height


def _collect_view3d(context, area, region):
    space = area.spaces.active
    rv3d = space.region_3d
    if rv3d is None:
        return None
    obj = context.view_layer.objects.active
    mode = obj.mode if obj else 'OBJECT'
    raw = []

    if mode == 'OBJECT':
        for o in context.view_layer.objects:
            if not o.visible_get():
                continue
            try:
                c = sum((Vector(v) for v in o.bound_box), Vector()) / 8.0
            except Exception:
                c = Vector()
            pt = location_3d_to_region_2d(region, rv3d, o.matrix_world @ c)
            if _inside(pt, region):
                raw.append((pt[0], pt[1], (o.name, o.type)))
        return 'OBJ', raw

    if mode == 'EDIT' and obj.type == 'MESH':
        shading = space.shading
        xray = shading.show_xray_wireframe if shading.type == 'WIREFRAME' else shading.show_xray
        inv = rv3d.view_matrix.inverted()
        eye = inv.translation
        toward = inv.to_3x3() @ Vector((0, 0, 1))
        view_forward = inv.to_3x3() @ Vector((0, 0, -1))
        vm, em, _fm = context.tool_settings.mesh_select_mode
        if not (vm or em or _fm):
            vm = True

        for o in context.view_layer.objects:
            if o.type != 'MESH' or o.mode != 'EDIT' or not o.visible_get():
                continue
            bm = bmesh.from_edit_mesh(o.data)
            mw = o.matrix_world
            m3 = mw.to_3x3()

            front_faces = set()
            if not xray:
                for f in bm.faces:
                    p_f = mw @ f.calc_center_median()
                    d_f = (eye - p_f) if rv3d.is_perspective else toward
                    if d_f.dot(m3 @ f.normal) > -1e-4:
                        front_faces.add(f.index)

            if vm:
                bm.verts.ensure_lookup_table()
                for v in bm.verts:
                    if v.hide:
                        continue
                    p = mw @ v.co
                    if rv3d.is_perspective and (p - eye).dot(view_forward) <= 0:
                        continue
                    if not xray and v.link_faces:
                        if not any(f.index in front_faces for f in v.link_faces):
                            continue
                    pt = location_3d_to_region_2d(region, rv3d, p)
                    if _inside(pt, region):
                        raw.append((pt[0], pt[1], (o.name, 'VERT', v.index)))

            if em:
                bm.edges.ensure_lookup_table()
                for e in bm.edges:
                    if e.hide:
                        continue
                    co = (e.verts[0].co + e.verts[1].co) * 0.5
                    p = mw @ co
                    if rv3d.is_perspective and (p - eye).dot(view_forward) <= 0:
                        continue
                    if not xray and e.link_faces:
                        if not any(f.index in front_faces for f in e.link_faces):
                            continue
                    pt = location_3d_to_region_2d(region, rv3d, p)
                    if _inside(pt, region):
                        raw.append((pt[0], pt[1], (o.name, 'EDGE', e.index)))

            if _fm:
                bm.faces.ensure_lookup_table()
                for f in bm.faces:
                    if f.hide:
                        continue
                    if not xray and f.index not in front_faces:
                        continue
                    p = mw @ f.calc_center_median()
                    if rv3d.is_perspective and (p - eye).dot(view_forward) <= 0:
                        continue
                    pt = location_3d_to_region_2d(region, rv3d, p)
                    if _inside(pt, region):
                        raw.append((pt[0], pt[1], (o.name, 'FACE', f.index)))

        return 'MESH', raw

    return None


def _node_abs_loc(n):
    x, y = n.location.x, n.location.y
    p = n.parent
    while p is not None:
        x += p.location.x
        y += p.location.y
        p = p.parent
    return x, y


def _collect_nodes(context, area, region):
    space = area.spaces.active
    tree = getattr(space, "edit_tree", None)
    if tree is None:
        return None
    sysprefs = context.preferences.system
    fac = sysprefs.dpi * sysprefs.pixel_size / 72.0
    raw = []
    for n in tree.nodes:
        x, y = _node_abs_loc(n)
        vx = x * fac + n.dimensions.x / 2.0
        vy = y * fac - 12.0
        pt = region.view2d.view_to_region(vx, vy, clip=False)
        if _inside(pt, region):
            raw.append((pt[0], pt[1], n.name))
    return 'NODE', raw


def _collect_outliner(context, area, region):
    ui = ui_scale()
    row_h = 22.0 * ui
    raw = []

    items = []
    def traverse_col(col, depth=0):
        items.append(('COLLECTION', col.name, depth))
        for child in col.children:
            traverse_col(child, depth + 1)
        for obj in col.objects:
            items.append(('OBJECT', obj.name, depth + 1))

    vl = context.view_layer
    traverse_col(vl.layer_collection.collection, 0)

    v2d = getattr(region, 'view2d', None)
    for i, (kind, name, depth) in enumerate(items):
        vx = 32.0 * ui + depth * 16.0 * ui
        vy = - (i + 0.5) * row_h
        if v2d:
            pt = v2d.view_to_region(vx, vy, clip=False)
        else:
            pt = (vx, region.height - (i + 0.5) * row_h)
        if _inside(pt, region):
            raw.append((pt[0], pt[1], (kind, name)))

    return 'OUTLINER', raw


def _collect_properties(context, area, region):
    space = area.spaces.active
    ui = ui_scale()
    cur = space.context
    valid_tabs = []
    for item in space.bl_rna.properties['context'].enum_items:
        try:
            space.context = item.identifier
            valid_tabs.append((item.identifier, item.name))
        except Exception:
            pass
    space.context = cur

    if not valid_tabs:
        return None

    raw = []
    tab_h = 28.0 * ui
    start_y = region.height - tab_h * 0.75
    for i, (tab_id, _tab_name) in enumerate(valid_tabs):
        y = start_y - i * tab_h
        if y < 10.0:
            break
        x = 24.0 * ui
        raw.append((x, y, tab_id))

    return 'PROPERTIES', raw


def _collect_grid(region):
    w, h = region.width, region.height
    cell = max(60, int(math.sqrt(max(w * h, 1) / 500.0)))
    cols = max(1, w // cell)
    rows = max(1, h // cell)
    cw, ch = w / cols, h / rows
    raw = []
    for r in range(rows):
        for c in range(cols):
            raw.append(((c + 0.5) * cw, (r + 0.5) * ch, None))
    return 'GRID', raw


def collect(context, area, region):
    res = None
    if area.type == 'VIEW_3D':
        res = _collect_view3d(context, area, region)
    elif area.type == 'NODE_EDITOR':
        res = _collect_nodes(context, area, region)
    elif area.type == 'OUTLINER':
        res = _collect_outliner(context, area, region)
    elif area.type == 'PROPERTIES':
        res = _collect_properties(context, area, region)
    if res is None:
        res = _collect_grid(region)
    return res


def declutter(hints, region):
    """Spreads overlapping hint labels apart and marks clusters."""
    n = len(hints)
    if n < 2:
        return
    ui = ui_scale()
    L = max(len(h.label) for h in hints)
    W = L * 9.0 * ui + 10 * ui
    H = 22 * ui
    M = 8 * ui
    cw, ch = W + M, H + M
    max_disp = 95 * ui

    for it in range(16):
        grid = {}
        for i, h in enumerate(hints):
            grid.setdefault((int(h.x // cw), int(h.y // ch)), []).append(i)
        moved = False
        for i, h in enumerate(hints):
            gx, gy = int(h.x // cw), int(h.y // ch)
            for ax in (gx - 1, gx, gx + 1):
                for ay in (gy - 1, gy, gy + 1):
                    for j in grid.get((ax, ay), ()):
                        if j <= i:
                            continue
                        g = hints[j]
                        dx, dy = g.x - h.x, g.y - h.y
                        ox, oy = cw - abs(dx), ch - abs(dy)
                        if ox <= 0 or oy <= 0:
                            continue
                        if it == 0:
                            h.cluster = g.cluster = True
                            h.nbrs.append(j)
                            g.nbrs.append(i)
                        if oy <= ox:
                            s = 1 if dy > 0 else (-1 if dy < 0 else (1 if (i + j) % 2 == 0 else -1))
                            sh = oy / 2
                            h.y -= s * sh
                            g.y += s * sh
                        else:
                            s = 1 if dx > 0 else (-1 if dx < 0 else (1 if (i + j) % 2 == 0 else -1))
                            sh = ox / 2
                            h.x -= s * sh
                            g.x += s * sh
                        moved = True
        for h in hints:
            dx, dy = h.x - h.ax, h.y - h.ay
            d = math.hypot(dx, dy)
            if d > max_disp:
                h.x = h.ax + dx * max_disp / d
                h.y = h.ay + dy * max_disp / d
            h.x = min(max(h.x, W / 2), max(region.width - W / 2, W / 2))
            h.y = min(max(h.y, H / 2), max(region.height - H / 2, H / 2))
        if not moved:
            break


def assign_cluster_colors(hints):
    for h in hints:
        if not h.cluster:
            continue
        used = {hints[j].color_idx for j in h.nbrs if hints[j].color_idx is not None}
        k = 0
        while k in used and k < len(PALETTE) - 1:
            k += 1
        h.color_idx = k
        h.color = PALETTE[k]


def attach_vertex_edges(context, area, region, hints):
    """Calculates screen segments of connected edges for overlapping vertex hints."""
    rv3d = area.spaces.active.region_3d
    cache = {}
    for h in hints:
        if not h.cluster or not h.payload:
            continue
        if len(h.payload) == 3:
            name, elem_type, idx = h.payload
            if elem_type != 'VERT':
                continue
        else:
            name, idx = h.payload
        o = bpy.data.objects.get(name)
        if o is None:
            continue
        bm = cache.get(name)
        if bm is None:
            bm = bmesh.from_edit_mesh(o.data)
            bm.verts.ensure_lookup_table()
            cache[name] = bm
        try:
            v = bm.verts[idx]
        except IndexError:
            continue
        mw = o.matrix_world
        segs = []
        for e in v.link_edges:
            if e.hide:
                continue
            pt = location_3d_to_region_2d(region, rv3d, mw @ e.other_vert(v).co)
            if pt is not None:
                segs.append((h.ax, h.ay, pt[0], pt[1]))
        h.edges = segs


def apply_single(context, win, area, region, hint, action):
    kind = S.kind
    extend = (action == 'EXTEND')
    is_alt = (action == 'ALT')

    if kind == 'OBJ':
        obj = bpy.data.objects.get(hint.payload[0])
        if obj is None:
            return
        vl = context.view_layer
        if extend:
            if obj.select_get() and vl.objects.active == obj:
                obj.select_set(False)
            else:
                obj.select_set(True)
                vl.objects.active = obj
        elif is_alt:
            with context.temp_override(window=win, area=area, region=region):
                try:
                    bpy.ops.view3d.select(extend=False, deselect_all=True, enumerate=True,
                                          location=(int(hint.ax), int(hint.ay)))
                except Exception:
                    for o in vl.objects:
                        o.select_set(False)
                    obj.select_set(True)
                    vl.objects.active = obj
        else:
            for o in vl.objects:
                o.select_set(False)
            obj.select_set(True)
            vl.objects.active = obj

    elif kind == 'MESH':
        loc = (int(hint.ax), int(hint.ay))
        applied_direct = False
        if not is_alt and hint.payload and len(hint.payload) >= 2:
            if len(hint.payload) == 3:
                obj_name, elem_type, idx = hint.payload
            else:
                obj_name, idx = hint.payload
                vm, em, fm = context.tool_settings.mesh_select_mode
                elem_type = 'VERT' if vm else ('EDGE' if em else 'FACE')
            o = bpy.data.objects.get(obj_name)
            if o and o.mode == 'EDIT' and o.type == 'MESH':
                try:
                    bm = bmesh.from_edit_mesh(o.data)
                    elem = None
                    if elem_type == 'VERT':
                        bm.verts.ensure_lookup_table()
                        if 0 <= idx < len(bm.verts):
                            elem = bm.verts[idx]
                    elif elem_type == 'EDGE':
                        bm.edges.ensure_lookup_table()
                        if 0 <= idx < len(bm.edges):
                            elem = bm.edges[idx]
                    elif elem_type == 'FACE':
                        bm.faces.ensure_lookup_table()
                        if 0 <= idx < len(bm.faces):
                            elem = bm.faces[idx]

                    if elem is not None:
                        if extend:
                            elem.select = not elem.select
                        else:
                            for v in bm.verts:
                                v.select = False
                            for e in bm.edges:
                                e.select = False
                            for f in bm.faces:
                                f.select = False
                            elem.select = True
                        bm.select_flush_mode()
                        bmesh.update_edit_mesh(o.data)
                        applied_direct = True
                except Exception:
                    pass

        if not applied_direct:
            with context.temp_override(window=win, area=area, region=region):
                if is_alt:
                    try:
                        bpy.ops.mesh.loop_select(extend=False, deselect=False, toggle=False, location=loc)
                    except Exception:
                        bpy.ops.view3d.select(extend=False, deselect_all=True, location=loc)
                elif extend:
                    bpy.ops.view3d.select(extend=False, deselect=False, toggle=True, location=loc)
                else:
                    bpy.ops.view3d.select(extend=False, deselect_all=True, location=loc)

    elif kind == 'NODE':
        tree = area.spaces.active.edit_tree
        node = tree.nodes.get(hint.payload)
        if node is None:
            return
        if extend:
            node.select = not node.select
            if node.select:
                tree.nodes.active = node
        else:
            for n in tree.nodes:
                n.select = False
            node.select = True
            tree.nodes.active = node

    elif kind == 'OUTLINER':
        item_kind, item_name = hint.payload
        if item_kind == 'OBJECT':
            obj = bpy.data.objects.get(item_name)
            if obj:
                vl = context.view_layer
                if extend:
                    obj.select_set(not obj.select_get())
                else:
                    for o in vl.objects:
                        o.select_set(False)
                    obj.select_set(True)
                    vl.objects.active = obj
        elif item_kind == 'COLLECTION':
            col = bpy.data.collections.get(item_name)
            if col:
                def find_lc(lc):
                    if lc.collection == col:
                        return lc
                    for child in lc.children:
                        res = find_lc(child)
                        if res:
                            return res
                    return None
                target_lc = find_lc(context.view_layer.layer_collection)
                if target_lc:
                    context.view_layer.active_layer_collection = target_lc
                if extend:
                    for o in col.objects:
                        o.select_set(True)
                else:
                    for o in context.view_layer.objects:
                        o.select_set(False)
                    for o in col.objects:
                        o.select_set(True)
                    if col.objects:
                        context.view_layer.objects.active = col.objects[0]
        win.cursor_warp(region.x + int(hint.x), region.y + int(hint.y))

    elif kind == 'PROPERTIES':
        tab_id = hint.payload
        try:
            area.spaces.active.context = tab_id
        except Exception:
            pass
        win.cursor_warp(region.x + int(hint.x), region.y + int(hint.y))

    elif kind == 'GRID':
        gx, gy = region.x + int(hint.x), region.y + int(hint.y)
        win.cursor_warp(gx, gy)

    area.tag_redraw()


def apply_box(context, win, area, region, a, b, extend):
    ui = ui_scale()
    pad = int(max(14.0 * ui, 14.0))
    xmin = int(min(a[0], b[0])) - pad
    xmax = int(max(a[0], b[0])) + pad
    ymin = int(min(a[1], b[1])) - pad
    ymax = int(max(a[1], b[1])) + pad
    if region:
        xmin = max(0, min(xmin, int(region.width)))
        xmax = max(0, min(xmax, int(region.width)))
        ymin = max(0, min(ymin, int(region.height)))
        ymax = max(0, min(ymax, int(region.height)))

    mode = 'ADD' if extend else 'SET'

    if S.kind == 'MESH' and area.type == 'VIEW_3D':
        obj = context.active_object
        if obj and obj.mode == 'EDIT' and obj.type == 'MESH':
            try:
                bm = bmesh.from_edit_mesh(obj.data)
                vm, em, fm = context.tool_settings.mesh_select_mode
                if not extend:
                    for v in bm.verts:
                        v.select = False
                    for e in bm.edges:
                        e.select = False
                    for f in bm.faces:
                        f.select = False

                start_h = getattr(S, 'box_start_hint', None)
                if start_h and start_h.payload:
                    idx = start_h.payload[-1]
                    if vm:
                        bm.verts.ensure_lookup_table()
                        if 0 <= idx < len(bm.verts):
                            bm.verts[idx].select = True
                    elif em:
                        bm.edges.ensure_lookup_table()
                        if 0 <= idx < len(bm.edges):
                            bm.edges[idx].select = True
                    elif fm:
                        bm.faces.ensure_lookup_table()
                        if 0 <= idx < len(bm.faces):
                            bm.faces[idx].select = True

                end_h = getattr(S, 'box_end_hint', None)
                if end_h and end_h.payload:
                    idx = end_h.payload[-1]
                    if vm:
                        bm.verts.ensure_lookup_table()
                        if 0 <= idx < len(bm.verts):
                            bm.verts[idx].select = True
                    elif em:
                        bm.edges.ensure_lookup_table()
                        if 0 <= idx < len(bm.edges):
                            bm.edges[idx].select = True
                    elif fm:
                        bm.faces.ensure_lookup_table()
                        if 0 <= idx < len(bm.faces):
                            bm.faces[idx].select = True

                for h in S.hints:
                    if xmin <= h.ax <= xmax and ymin <= h.ay <= ymax:
                        if h.payload:
                            idx = h.payload[-1]
                            if vm:
                                bm.verts.ensure_lookup_table()
                                if 0 <= idx < len(bm.verts):
                                    bm.verts[idx].select = True
                            elif em:
                                bm.edges.ensure_lookup_table()
                                if 0 <= idx < len(bm.edges):
                                    bm.edges[idx].select = True
                            elif fm:
                                bm.faces.ensure_lookup_table()
                                if 0 <= idx < len(bm.faces):
                                    bm.faces[idx].select = True

                bm.select_flush_mode()
                bmesh.update_edit_mesh(obj.data)
            except Exception as e:
                print(f"[Vimender] bmesh box select error: {e}")

    if not bpy.app.background:
        with context.temp_override(window=win, area=area, region=region):
            try:
                if S.kind in {'OBJ', 'MESH'}:
                    bpy.ops.view3d.select_box(xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax,
                                              wait_for_input=False, mode=mode)
                elif S.kind == 'NODE':
                    bpy.ops.node.select_box(tweak=False, xmin=xmin, xmax=xmax, ymin=ymin,
                                            ymax=ymax, wait_for_input=False, mode=mode)
            except Exception as e:
                pass
    area.tag_redraw()



def start_hint_global(context, win, area, box, action, mx, my, report_fn=None):
    region = window_region(area)
    if region is None:
        return
    kind, raw = collect(context, area, region)
    if kind == 'GRID' and box:
        if report_fn:
            report_fn({'WARNING'}, "Box selection is not supported in this editor (3D Viewport and Node editors only)")
        return
    if not raw:
        if report_fn:
            report_fn({'INFO'}, "No selectable elements found")
        return
    lx = mx - region.x
    ly = my - region.y
    if kind not in {'OUTLINER', 'PROPERTIES'}:
        raw.sort(key=lambda r: (r[0] - lx) ** 2 + (r[1] - ly) ** 2)
    if len(raw) > MAX_HINTS:
        raw = raw[:MAX_HINTS]
    labels = make_labels(len(raw))
    hints = [Hint(lb, r[0], r[1], r[2]) for lb, r in zip(labels, raw)]

    vertex_mode = kind == 'MESH' and context.tool_settings.mesh_select_mode[0]
    if kind not in {'GRID', 'OUTLINER', 'PROPERTIES'}:
        declutter(hints, region)
        if vertex_mode:
            assign_cluster_colors(hints)
            try:
                attach_vertex_edges(context, area, region, hints)
            except Exception as e:
                print(f"[Vimender] Edge coloring error: {e}")

    S.nav.reset()
    S.hints = hints
    S.kind = kind
    S.action = action
    S.box_extend = (action == 'EXTEND')
    S.vertex_mode = vertex_mode
    S.typed = ""
    S.box_a = None
    S.hint_ptr = area.as_pointer()
    S.mouse = (lx, ly)
    S.mode = 'BOX1' if box else 'HINT'
    S.mover.reset()
    S.smooth_box.reset(lx, ly)
    try:
        txt = "Vimender: BOX SELECT MODE" if box else "Vimender: HINT MODE"
        context.workspace.status_text_set(txt)
    except Exception:
        pass
    area.tag_redraw()
    tag_all()


