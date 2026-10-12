"""Emma -> Unreal Engine 5: the approved model, its rig, its clips, and a low-poly copy.

Run with the lab's Blender (README of client/assets/emma-3d-lab):

    & .tmp/emma-3d-tools/blender-4.5.3-windows-x64/blender.exe --background `
      --python scripts/export-emma-ue5.py -- [--out exports/emma-ue5] [--tol-mm 0.5] [--ntol-deg 0.5]

Source of truth is client/assets/emma-3d-lab/emma.blend + emma.glb (the 162k build the
owner signed off, 2026-09-23). Nothing here writes back into that folder.

WHAT "LOW-POLY WITHOUT CHANGING IT" TURNED OUT TO MEAN (measured 2026-10-07):
  * ~28k of the 162k triangles sit buried inside another closed part on the SAME bone
    (teardrop body under the enamel panels, shell under the face plate). Parts on one
    bone never move relative to each other, so deleting those changes no pixel in any
    pose -- verified pixel-identical over 21 views. Only same-bone parts may hide each
    other: head-inside-body visibility changes when she turns her head.
  * Beyond that, almost nothing. Every visible surface is a smooth chrome/pearl curve and
    the triangles are what the reflections are made of. A distance cap alone (0.5 mm)
    let the decimator cut another 23k and the chest-ring chrome visibly broke up; with a
    normal cap as well, 0.5 deg keeps 2.6k more cut (head shell only), 2 deg only 4k and
    the chest already shows. The 68k build of 2026-09-25 was the same lesson (band edge).
  * So the decimator runs under BOTH caps (--tol-mm, --ntol-deg) and is free to find
    nothing. Near another part the distance cap is half the gap (a seam moves by
    d/sin(angle)) and those vertices are fenced off from the decimator.
  * Normals travel with the mesh as a corner attribute, so untouched corners come back
    bit-exact; only vertices the decimator moved are re-sampled from the original field.

FBX FOR UE: geometry and bones are converted to centimetres with FBX unit scale 1 and the
armature object named "Armature", so no importer has a unit factor or an extra root bone
to guess about. Animations are baked with curve simplification OFF (the exporter's
default 1.0 rewrites the keys). The GLB keeps glTF's own metres -- UE's glTF importer
converts that itself.
"""
import argparse
import colorsys
import json
import math
import shutil
import sys
import time
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "client" / "assets" / "emma-3d-lab"
SRC_BLEND = LAB / "emma.blend"
SRC_GLB = LAB / "emma.glb"
RIG = "Emma_Rig"
RING = "Hover / luminous ring"
NRM = "_emma_src_normal"

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ap = argparse.ArgumentParser()
ap.add_argument("--out", default=str(ROOT / "exports" / "emma-ue5"))
ap.add_argument("--tol-mm", type=float, default=0.5,
                help="largest distance any visible point may move (Emma is 3.9 m tall)")
ap.add_argument("--ntol-deg", type=float, default=0.5,
                help="largest angle any shading normal may turn (chrome shows this first)")
ap.add_argument("--margin-mm", type=float, default=2.0,
                help="a point counts as hidden only this deep inside another part")
ap.add_argument("--no-render", action="store_true")
ap.add_argument("--no-decimate", action="store_true", help="hidden-face removal only (the lossless part)")
args = ap.parse_args(argv)

OUT = Path(args.out).resolve()
TOL = args.tol_mm / 1000.0
TOL_MIN = 0.05 / 1000.0
MARGIN = args.margin_mm / 1000.0
NTOL = args.ntol_deg
for sub in ("", "glb", "blend", "compare"):
    (OUT / sub).mkdir(parents=True, exist_ok=True)
REPORT = {"source_blend": SRC_BLEND.relative_to(ROOT).as_posix(), "tol_mm": args.tol_mm, "ntol_deg": args.ntol_deg,
          "decimate": not args.no_decimate,
          "margin_mm": args.margin_mm, "blender": bpy.app.version_string, "meshes": {}}


def log(*a):
    print("[emma-ue5]", *a, flush=True)


# --------------------------------------------------------------------------- geometry
class Surf:
    """World-space triangles of one mesh with face normals and a BVH."""

    def __init__(self, me, mw):
        me.calc_loop_triangles()
        self.v = [mw @ v.co for v in me.vertices]
        self.t = [tuple(lt.vertices) for lt in me.loop_triangles]
        self.poly = [lt.polygon_index for lt in me.loop_triangles]
        self.f = []
        for a, b, c in self.t:
            fn = (self.v[b] - self.v[a]).cross(self.v[c] - self.v[a])
            self.f.append(fn.normalized() if fn.length > 1e-14 else Vector((0, 0, 0)))
        self.bvh = BVHTree.FromPolygons(self.v, self.t, all_triangles=True) if self.t else None
        lo = Vector([min(p[i] for p in self.v) for i in range(3)]) if self.v else Vector()
        hi = Vector([max(p[i] for p in self.v) for i in range(3)]) if self.v else Vector()
        self.box = (lo, hi)

    def samples(self):
        """Corners (once per vertex), edge midpoints (once per edge), centroids."""
        seen_e = set()
        yield from self.v
        for tri in self.t:
            pts = [self.v[i] for i in tri]
            for k in range(3):
                e = tuple(sorted((tri[k], tri[(k + 1) % 3])))
                if e not in seen_e:
                    seen_e.add(e)
                    yield (pts[k] + pts[(k + 1) % 3]) / 2
            yield (pts[0] + pts[1] + pts[2]) / 3


class RefField:
    """The ORIGINAL shading normals as a field over the original surface, read from the
    carried corner attribute (so cut boundaries do not disturb it)."""

    def __init__(self, me, mw):
        me.calc_loop_triangles()
        flat = [0.0] * (3 * len(me.loops))
        me.attributes[NRM].data.foreach_get("vector", flat)
        nm = mw.to_3x3().inverted_safe().transposed()
        cn = [(nm @ Vector(flat[i:i + 3])).normalized() for i in range(0, len(flat), 3)]
        self.v = [mw @ v.co for v in me.vertices]
        self.t = [tuple(lt.vertices) for lt in me.loop_triangles]
        self.n = [[cn[li] for li in lt.loops] for lt in me.loop_triangles]
        self.f = []
        for a, b, c in self.t:
            fn = (self.v[b] - self.v[a]).cross(self.v[c] - self.v[a])
            self.f.append(fn.normalized() if fn.length > 1e-14 else Vector((0, 0, 0)))
        self.bvh = BVHTree.FromPolygons(self.v, self.t, all_triangles=True)
        self.kd = KDTree(len(self.v))
        for i, v in enumerate(self.v):
            self.kd.insert(v, i)
        self.kd.balance()

    def normal_at(self, p, fn, reach):
        """Original normal at the surface point nearest p. On a crease both sides are about
        equally near, so facing the same way as fn decides; on a smooth patch neighbours
        agree to ~1e-4 and the small distance term keeps the nearest."""
        loc, _, idx, d = self.bvh.find_nearest(p)
        best, score = (loc, idx), -9.0
        for l2, _, i2, d2 in self.bvh.find_nearest_range(p, d + reach) or [(loc, None, idx, d)]:
            sc = self.f[i2].dot(fn) - .05 * (d2 - d) / reach
            if sc > score:
                best, score = (l2, i2), sc
        loc, idx = best
        a, b, c = (self.v[i] for i in self.t[idx])
        v0, v1, v2 = b - a, c - a, loc - a
        d00, d01, d11, d20, d21 = v0.dot(v0), v0.dot(v1), v1.dot(v1), v2.dot(v0), v2.dot(v1)
        den = d00 * d11 - d01 * d01
        ns = self.n[idx]
        if abs(den) < 1e-24:
            return ns[0]
        w1 = (d11 * d20 - d01 * d21) / den
        w2 = (d00 * d21 - d01 * d20) / den
        return (ns[0] * (1 - w1 - w2) + ns[1] * w1 + ns[2] * w2).normalized()


def shading_error(field, me, mw):
    """Worst angle between the low-poly's interpolated normal and the original's, over
    corners, edge midpoints and centroids of every low-poly triangle."""
    me.calc_loop_triangles()
    nm = mw.to_3x3().inverted_safe().transposed()
    cn = [(nm @ Vector(n.vector)).normalized() for n in me.corner_normals]
    vs = [mw @ v.co for v in me.vertices]
    worst = 0.0
    for lt in me.loop_triangles:
        a, b, c = (vs[i] for i in lt.vertices)
        fn = (b - a).cross(c - a)
        if fn.length < 1e-14:
            continue
        fn.normalize()
        na, nb, nc = (cn[li] for li in lt.loops)
        for q, n in ((a, na), (b, nb), (c, nc), ((a + b) / 2, na + nb), ((b + c) / 2, nb + nc),
                     ((c + a) / 2, nc + na), ((a + b + c) / 3, na + nb + nc)):
            worst = max(worst, n.normalized().angle(field.normal_at(q, fn, TOL * 2), 0))
    return math.degrees(worst)


def closed(me):
    bm = bmesh.new()
    bm.from_mesh(me)
    ok = all(e.is_manifold for e in bm.edges) and len(bm.edges) > 0
    bm.free()
    return ok


def depth_inside(p, occluders):
    """How deep p sits inside the deepest closed occluder (0 = outside all of them).
    Ray parity, and inside only when all three rays agree. The cheaper test (sign of
    the offset from the nearest face) was tried first and is wrong at saddle vertices:
    it deleted visible faces of the chest rings (green specks through the black ring)."""
    deepest = 0.0
    for s in occluders:
        lo, hi = s.box
        if any(p[i] < lo[i] or p[i] > hi[i] for i in range(3)):
            continue
        d = s.bvh.find_nearest(p)[3]
        if d <= deepest or d <= MARGIN / 4:
            continue
        inside = True
        for ray in RAYS:
            hits, q = 0, p.copy()
            for _ in range(64):
                h = s.bvh.ray_cast(q, ray)
                if h[0] is None:
                    break
                hits += 1
                q = h[0] + ray * 1e-6
            if hits % 2 == 0:
                inside = False
                break
        if inside:
            deepest = d
    return deepest


RAYS = [Vector((0.123, 0.456, 0.881)).normalized(), Vector((-0.71, 0.3, -0.63)).normalized(),
        Vector((0.5, -0.85, 0.17)).normalized()]


# --------------------------------------------------------------------------- scene
def open_source():
    bpy.ops.wm.open_mainfile(filepath=str(SRC_BLEND))
    return bpy.context.scene


def bone_of(obj):
    for v in obj.data.vertices:
        for g in v.groups:
            if g.weight > .5:
                return obj.vertex_groups[g.group].name
    return None


def model_meshes():
    # By parent TYPE: export_fbx_set renames the rig to "Armature" first.
    return [o for o in bpy.context.scene.objects if o.type == "MESH"
            and (o.parent and o.parent.type == "ARMATURE" or o.name == RING)]


def tri_count(objs):
    total = 0
    for o in objs:
        o.data.calc_loop_triangles()
        total += len(o.data.loop_triangles)
    return total


# --------------------------------------------------------------------------- low-poly
def tri_hidden(a, b, c, occluders, level=0):
    """True only if EVERY point of the triangle is > MARGIN/2 inside some occluder.
    Proof by balls: a point d deep inside a closed part has the whole ball of radius d
    inside it too, so if the centroid's ball reaches all three corners with MARGIN/2 to
    spare, the triangle is buried; otherwise split in four and prove each quarter.
    Testing a fixed handful of sample points (corners, mids, centroid) is NOT enough and
    was the first version: front-enamel triangles 0.7 m long run under both chest rings,
    every sample landed inside one ring or the other, and the visible groove BETWEEN the
    rings ran between the samples -- deleting them opened holes you could see through."""
    m = (a + b + c) / 3
    d = depth_inside(m, occluders)
    if d <= MARGIN:
        return False
    if max((a - m).length, (b - m).length, (c - m).length) <= d - MARGIN / 2:
        return True
    if level >= 14:
        return False
    ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
    return all(tri_hidden(*t, occluders, level + 1) for t in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)))


def prune_hidden(obj, occluders):
    """Delete faces buried > MARGIN inside other closed parts on the same bone."""
    if not occluders:
        return 0
    s = Surf(obj.data, obj.matrix_world)
    poly_hidden = {}
    for ti, tri in enumerate(s.t):
        pi = s.poly[ti]
        if poly_hidden.get(pi) is False:
            continue
        poly_hidden[pi] = tri_hidden(*(s.v[i] for i in tri), occluders)
    doomed = {pi for pi, h in poly_hidden.items() if h}
    if not doomed:
        return 0
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[i] for i in doomed], context="FACES")
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return len(doomed)


def budget(samples, others):
    """Allowed displacement per sample: TOL, or half the gap to any other part."""
    out = []
    for p in samples:
        gap = TOL * 2
        for s in others:
            lo, hi = s.box
            if any(p[i] < lo[i] - gap or p[i] > hi[i] + gap for i in range(3)):
                continue
            h = s.bvh.find_nearest(p, gap)
            if h[0] is not None:
                gap = min(gap, h[3])
        out.append(TOL if gap >= TOL * 2 else max(TOL_MIN, gap / 2))
    return out


def decimate_mesh(base_me, mw, ratio, weights):
    col = bpy.context.scene.collection
    tmp = bpy.data.objects.new("_dec", base_me.copy())
    col.objects.link(tmp)
    tmp.matrix_world = mw
    mod = tmp.modifiers.new("dec", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    if weights is not None:
        g = tmp.vertex_groups.new(name="_free")
        for i, w in enumerate(weights):
            g.add([i], w, "REPLACE")
        mod.vertex_group = "_free"     # weight 1 = may collapse, 0 = keep (MOD_decimate)
        mod.vertex_group_factor = 1.0
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
    old = tmp.data
    bpy.data.objects.remove(tmp)
    bpy.data.meshes.remove(old)
    return me


def check(O, o_samples, o_allow, kd, R):
    """(worst deviation / allowance, worst raw deviation) both ways; passes at <= 1.
    No sample is exempt for being hidden: a big triangle can be buried at every sample
    point and still show through a groove between them (see tri_hidden)."""
    worst, raw = 0.0, 0.0
    for p, a in zip(o_samples, o_allow):
        d = R.bvh.find_nearest(p)[3]
        raw = max(raw, d)
        worst = max(worst, d / a)
    for p in R.samples():
        d = O.bvh.find_nearest(p)[3]
        raw = max(raw, d)
        worst = max(worst, d / o_allow[kd.find(p)[1]])
    return worst, raw


def stash_normals(me):
    """Copy every corner's shading normal into a plain corner attribute. Deleting faces
    (bmesh) and collapsing edges (Decimate) both carry generic corner data along --
    untouched corners keep their exact value, merged ones are interpolated."""
    flat = [c for n in me.corner_normals for c in n.vector]
    me.attributes.new(NRM, "FLOAT_VECTOR", "CORNER").data.foreach_set("vector", flat)


def restore_normals(me, merge_deg, field=None, mw=None):
    """Turn the carried normals back into custom normals. Re-sampling them from the
    original surface instead was tried first and is wrong at the pinch points of the
    swept parts, where a corner's own normal points against its face (Smile saddle:
    176 deg on an UNCHANGED mesh). Returns the worst readback error in degrees."""
    if NRM not in me.attributes:
        raise RuntimeError(f"{me.name}: carried normals were dropped")
    flat = [0.0] * (3 * len(me.loops))
    me.attributes[NRM].data.foreach_get("vector", flat)
    corner = [Vector(flat[i:i + 3]).normalized() for i in range(0, len(flat), 3)]
    if field is not None:
        # A vertex the decimator MOVED gets the original normal at its new position;
        # the carried value there is only an interpolation between the two it replaced.
        # Vertices still sitting on an original vertex keep the carried (exact) value --
        # resampling everything was the first version and broke at the pinch points.
        nm = mw.to_3x3().inverted_safe().transposed()
        to_local = mw.to_3x3().transposed()
        moved = [field.kd.find(mw @ v.co)[2] > 1e-7 for v in me.vertices]
        for poly in me.polygons:
            if not any(moved[v] for v in poly.vertices):
                continue
            fn = (nm @ poly.normal).normalized()
            for li in poly.loop_indices:
                vi = me.loops[li].vertex_index
                if moved[vi]:
                    corner[li] = (to_local @ field.normal_at(mw @ me.vertices[vi].co, fn, TOL * 2)).normalized()
    # Collapsing interpolates each corner separately, so corners of one smooth fan drift
    # apart; within merge_deg they share one normal (else the GPU splits the vertex and a
    # hairline crease appears). merge_deg stays under half the mesh's smallest real crease,
    # and is 0 for a mesh the decimator did not touch -- those come back bit-exact.
    by_vert = {}
    for li, lp in enumerate(me.loops):
        by_vert.setdefault(lp.vertex_index, []).append(li)
    for lis in by_vert.values():
        if not merge_deg or all((corner[i] - corner[lis[0]]).length < 1e-6 for i in lis):
            continue
        clusters = []
        for li in lis:
            for c in clusters:
                if corner[li].angle(c[0], 0) < math.radians(merge_deg):
                    c[1].append(li)
                    break
            else:
                clusters.append((corner[li], [li]))
        for _, members in clusters:
            avg = sum((corner[i] for i in members), Vector()).normalized()
            for i in members:
                corner[i] = avg
    # Sharp wherever the two faces of an edge disagree AT ALL -- a 1 deg threshold here let
    # Blender average near-equal fans of the face plate (zebra moved on a mesh with no
    # geometry change). FBX smoothing groups follow these edges.
    pv, faces_of = {}, {}
    for poly in me.polygons:
        for li in poly.loop_indices:
            pv[(poly.index, me.loops[li].vertex_index)] = li
        for ek in poly.edge_keys:
            faces_of.setdefault(ek, []).append(poly.index)
    sharp = [False] * len(me.edges)
    for e in me.edges:
        faces = faces_of.get(e.key, [])
        if len(faces) == 2:
            sharp[e.index] = any((corner[pv[(faces[0], vi)]] - corner[pv[(faces[1], vi)]]).length > 1e-6
                                 for vi in e.key)
    for name in ("sharp_edge", "sharp_face", NRM):
        if name in me.attributes:
            me.attributes.remove(me.attributes[name])
    me.attributes.new("sharp_edge", "BOOLEAN", "EDGE").data.foreach_set("value", sharp)
    me.normals_split_custom_set([tuple(n) for n in corner])
    me.update()
    got = [Vector(n.vector) for n in me.corner_normals]
    return math.degrees(max(g.angle(c, 0) for g, c in zip(got, corner))) if got else 0.0


def crease_stats(me):
    """(smallest angle between two different normals at one vertex, vertices that carry
    more than one normal) -- the real creases this mesh has, read from its corners."""
    by_vert = {}
    for lp, n in zip(me.loops, me.corner_normals):
        by_vert.setdefault(lp.vertex_index, []).append(Vector(n.vector))
    smallest, split = 180.0, 0
    for ns in by_vert.values():
        distinct = []
        for n in ns:
            if all((n - m).length > 1e-6 for m in distinct):
                distinct.append(n)
        if len(distinct) > 1:
            split += 1
            for i in range(len(distinct)):
                for j in range(i + 1, len(distinct)):
                    smallest = min(smallest, math.degrees(distinct[i].angle(distinct[j], 0)))
    return smallest, split


def make_lowpoly():
    scene = open_source()
    objs = model_meshes()
    bones = {o.name: bone_of(o) for o in objs}
    full = {o.name: Surf(o.data, o.matrix_world) for o in objs}
    shut = {o.name: closed(o.data) for o in objs}
    # Reference copies for the render check. They go through the same stash/restore as
    # the low-poly (bit-exact, 0 merge) so both sides are drawn as custom-normal meshes:
    # Workbench draws a custom-normal mesh through a different GPU path than an auto-
    # normal one, and that alone moved zebra stripes on the untouched back hatch
    # (103 px > 48/255) while CPU normals matched to < 1e-4 deg. FBX/glTF store the
    # normals explicitly, so UE never sees that difference -- only this check would.
    originals = {}
    for o in objs:
        ref = o.data.copy()
        stash_normals(ref)
        restore_normals(ref, 0.0)
        originals[o.name] = ref
    creases = {o.name: crease_stats(o.data) for o in objs}
    for o in objs:
        stash_normals(o.data)
    before = tri_count(objs)
    log(f"source {before} triangles in {len(objs)} meshes")

    # 1. hidden faces -- occluders are the ORIGINAL closed parts on the same bone
    for o in objs:
        occ = [full[n] for n in full if n != o.name and bones[n] and bones[n] == bones[o.name] and shut[n]]
        t0 = time.time()
        gone = prune_hidden(o, occ)
        REPORT["meshes"].setdefault(o.name, {})["hidden_faces_removed"] = gone
        log(f"  hidden  {o.name:40s} -{gone:5d} faces  ({time.time() - t0:.1f}s)")
    pruned = {o.name: Surf(o.data, o.matrix_world) for o in objs}
    after_prune = tri_count(objs)

    # 2. decimate what is left, against a per-sample distance budget
    for o in objs:
        t0 = time.time()
        others = [pruned[n] for n in pruned if n != o.name]
        O = pruned[o.name]
        o_samples = list(O.samples())
        o_allow = budget(o_samples, others)
        kd = KDTree(len(o_samples))
        for i, p in enumerate(o_samples):
            kd.insert(p, i)
        kd.balance()
        # vertex weights for the decimator: 1 where the full budget applies
        vw = [1.0] * len(o.data.vertices)
        for vi, v in enumerate(O.v):
            a = o_allow[kd.find(v)[1]]
            vw[vi] = 1.0 if a >= TOL else 0.0
        base = o.data.copy()
        tris0 = len(O.t)
        field = RefField(o.data, o.matrix_world)
        merge_if_cut = min(10.0, creases[o.name][0] / 2)
        lo, hi, best = 0.01, 1.0, None
        for _ in range(0 if args.no_decimate else 11):
            mid = math.sqrt(lo * hi)
            me = decimate_mesh(base, o.matrix_world, mid, vw)
            ratio_ok, raw = check(O, o_samples, o_allow, kd, Surf(me, o.matrix_world))
            shade = None
            if ratio_ok <= 1.0:
                # Distance alone passed the chest rings at 0.3 mm and their chrome zebra
                # still broke up -- on a mirror the NORMAL is what you see.
                restore_normals(me, merge_if_cut, field, o.matrix_world)
                shade = shading_error(field, me, o.matrix_world)
            if shade is not None and shade <= NTOL:
                if best:
                    bpy.data.meshes.remove(best[0])
                best = (me, mid, raw, shade)
                hi = mid
            else:
                bpy.data.meshes.remove(me)
                lo = mid
        if best is None:
            me = base.copy()
            restore_normals(me, 0.0)
            best = (me, 1.0, 0.0, 0.0)
        me, ratio, raw, shade = best
        merge = 0.0 if ratio >= 1.0 else merge_if_cut
        _, split_after = crease_stats(me)
        # rigid skin: every vertex 1.0 on the one bone this part follows
        old = o.data
        name = old.name
        o.data = me
        bpy.data.meshes.remove(old)
        me.name = name
        if bones[o.name]:
            for vg in o.vertex_groups:
                vg.remove(list(range(len(me.vertices))))
            o.vertex_groups[bones[o.name]].add(list(range(len(me.vertices))), 1.0, "REPLACE")
        bpy.data.meshes.remove(base)
        me.calc_loop_triangles()
        info = REPORT["meshes"][o.name]
        info.update({"bone": bones[o.name], "triangles_before": len(full[o.name].t),
                     "triangles_after_hidden": tris0, "triangles_after": len(me.loop_triangles),
                     "decimate_ratio": round(ratio, 4), "max_visible_move_mm": round(raw * 1000, 4),
                     "fenced_vertices": sum(1 for w in vw if w < 1),
                     "smallest_crease_deg": round(creases[o.name][0], 3), "normal_merge_deg": round(merge, 3),
                     "crease_vertices_before": creases[o.name][1], "crease_vertices_after": split_after,
                     "max_shading_error_deg": round(shade, 4)})
        log(f"  lowpoly {o.name:40s} {len(full[o.name].t):6d} -> {len(me.loop_triangles):6d}  "
            f"move {raw * 1000:.3f}mm  normal {shade:.3f}deg  fenced {info['fenced_vertices']}  crease-verts "
            f"{creases[o.name][1]}->{split_after} (min {creases[o.name][0]:.1f}deg)  ({time.time() - t0:.1f}s)")
    after = tri_count(objs)
    REPORT.update({"triangles_original": before, "triangles_after_hidden": after_prune,
                   "triangles_lowpoly": after})
    log(f"low-poly {before} -> {after_prune} (hidden) -> {after}")
    return objs, originals


# --------------------------------------------------------------------------- render check
def setup_render(scene):
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.film_transparent = True
    scene.render.resolution_x = scene.render.resolution_y = 1000
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.display.render_aa = "16"
    # 16-bit normals: the default 10-bit packing alone shifts zebra stripes by a
    # sub-pixel when a mesh merely switches from vertex to custom normals.
    scene.render.use_high_quality_normals = True
    scene.view_settings.view_transform = "Standard"
    scene.display.shading.show_object_outline = False
    scene.display.shading.show_specular_highlight = False
    for o in scene.objects:
        if o.type in {"LIGHT"}:
            o.hide_render = True
    cam_data = bpy.data.cameras.new("_cmp")
    cam_data.type = "ORTHO"
    cam = bpy.data.objects.new("_cmp", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    return cam


def aim(cam, target, az, el, scale):
    az, el = math.radians(az), math.radians(el)
    d = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    cam.location = Vector(target) + d * 30
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    cam.data.ortho_scale = scale
    cam.data.clip_end = 100


def views(objs):
    def box(names):
        pts = [o.matrix_world @ v.co for o in objs if o.name in names for v in o.data.vertices]
        lo = Vector([min(p[i] for p in pts) for i in range(3)])
        hi = Vector([max(p[i] for p in pts) for i in range(3)])
        return (lo + hi) / 2, max(hi - lo) * 1.25
    out = [(f"body-az{az:03d}", (0, 0, 2.4), az, 8, 4.7, 1) for az in range(0, 360, 45)]
    out += [("body-top", (0, 0, 2.4), 0, 89.5, 4.7, 1), ("body-below", (0, 0, 2.4), 30, -25, 4.7, 1)]
    for name, az, el in [("Head / chrome side band", 90, 5), ("Head / chrome side band", 50, 25),
                         ("Eye / rounded arch 1", 0, 0), ("Eye / rounded arch 1", -35, 10),
                         ("Smile / broad sculpted chrome saddle", 0, -10),
                         ("Chest / chrome rim", 0, 0), ("Chest / chrome rim", 40, 10),
                         ("Arm / flat fin L", -70, 5), ("Back / service hatch", 180, 0)]:
        c, s = box([name])
        tag = name.split("/")[0].strip().lower() + "-" + name.split("/")[1].strip().split()[-1]
        out.append((f"close-{tag}-az{az}-el{el}", tuple(c), az, el, s, 1))
    out += [("pose-look-az000", (0, 0, 2.4), 0, 8, 4.7, "look"),
            ("pose-look-az045", (0, 0, 2.4), 45, 8, 4.7, "look")]
    return out


def read_png(path):
    img = bpy.data.images.load(str(path))
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)


def write_png(arr, path):
    h, w, _ = arr.shape
    img = bpy.data.images.new("_out", w, h, alpha=True)
    img.pixels.foreach_set(np.ascontiguousarray(arr, dtype=np.float32).ravel())
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def edges_of(seg):
    """Pixels whose 3x3 neighbourhood is not one flat colour -- silhouette/seam AA."""
    e = np.zeros(seg.shape[:2], bool)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            sh = np.roll(np.roll(seg, dy, 0), dx, 1)
            e |= np.abs(sh - seg).max(axis=2) > 1 / 255
    return e


def render_compare(objs, originals):
    scene = bpy.context.scene
    cam = setup_render(scene)
    rig = bpy.data.objects[RIG]
    look = bpy.data.actions["look"]
    neck = [fc for fc in fcurves_of(look) if 'pose.bones["Neck"]' in fc.data_path and "rotation" in fc.data_path]
    frames = range(int(look.frame_range[0]), int(look.frame_range[1]) + 1)
    yaw_frame = max(frames, key=lambda f: sum(abs(fc.evaluate(f)) for fc in neck if fc.array_index in (2, 3)))
    for i, o in enumerate(sorted(objs, key=lambda o: o.name)):
        r, g, b = colorsys.hsv_to_rgb((i * 0.61803) % 1.0, .65, .55 + .4 * ((i * 7) % 3) / 2)
        o.color = (r, g, b, 1)
    lowpoly = {o.name: o.data for o in objs}
    studio = [s.name for s in bpy.context.preferences.studio_lights if s.type == "MATCAP"]
    zebra = "reflection_check_horizontal.exr" if "reflection_check_horizontal.exr" in studio else studio[0]
    results = {}
    for name, target, az, el, scale, pose in views(objs):
        ad = rig.animation_data
        for t in ad.nla_tracks:
            t.mute = True
        ad.action = bpy.data.actions["look" if pose == "look" else "idle"]
        scene.frame_set(yaw_frame if pose == "look" else 1)
        aim(cam, target, az, el, scale)
        shots = {}
        for version in ("orig", "low"):
            for o in objs:
                o.data = originals[o.name] if version == "orig" else lowpoly[o.name]
            for mode in ("seg", "zebra"):
                sh = scene.display.shading
                if mode == "seg":
                    sh.light, sh.color_type = "FLAT", "OBJECT"
                else:
                    sh.light, sh.color_type, sh.studio_light = "MATCAP", "SINGLE", zebra
                    sh.single_color = (.8, .8, .8)
                path = OUT / "compare" / f"_{name}-{version}-{mode}.png"
                scene.render.filepath = str(path)
                bpy.ops.render.render(write_still=True)
                shots[(version, mode)] = read_png(path)
                path.unlink()
        so, sl = shots[("orig", "seg")], shots[("low", "seg")]
        zo, zl = shots[("orig", "zebra")], shots[("low", "zebra")]
        edge = edges_of(so) | edges_of(sl)
        seg_diff = np.abs(so - sl).max(axis=2) > 2 / 255
        z = np.abs(zo[..., :3] - zl[..., :3]).max(axis=2)
        covered = (so[..., 3] > .5) | (sl[..., 3] > .5)
        interior = covered & ~edge
        stats = {"pixels_covered": int(covered.sum()),
                 "seg_changed_off_edge": int((seg_diff & ~edge).sum()),
                 "seg_changed_on_edge": int((seg_diff & edge).sum()),
                 "zebra_interior_p999": round(float(np.quantile(z[interior], .999)) * 255, 2) if interior.any() else 0,
                 "zebra_interior_over_16": int(((z > 16 / 255) & interior).sum()),
                 "zebra_interior_over_48": int(((z > 48 / 255) & interior).sum())}
        results[name] = stats
        # strip: original | low-poly | changes (red = part moved, yellow = shading)
        heat = np.zeros_like(zo)
        heat[..., 3] = 1
        heat[..., :3] = (zo[..., :3] * .25)
        heat[interior & (z > 16 / 255)] = (1, .85, 0, 1)
        heat[seg_diff & ~edge] = (1, 0, 0, 1)
        bg = np.zeros_like(zo)
        bg[...] = (.08, .09, .1, 1)
        comp = lambda img: img * img[..., 3:4] + bg * (1 - img[..., 3:4])
        strip = np.concatenate([comp(zo), comp(zl), heat, comp(so), comp(sl)], axis=1)
        write_png(strip, OUT / "compare" / f"{name}.png")
        log(f"  compare {name:44s} {stats}")
    for o in objs:
        o.data = lowpoly[o.name]
    REPORT["render_compare"] = {"note": "Workbench, 1000x1000 ortho. seg = flat colour per part, "
                                        "zebra = matcap stripes (normal-sensitive). Each strip: "
                                        "zebra original | zebra low-poly | changes | parts original | parts low-poly",
                                "matcap": zebra, "look_frame": yaw_frame, "views": results}


# --------------------------------------------------------------------------- export
def unmute_for_gltf(rig):
    ad = rig.animation_data
    ad.action = None
    for t in ad.nla_tracks:
        t.mute = False


def export_glb(path):
    rig = bpy.data.objects[RIG]
    unmute_for_gltf(rig)
    bpy.ops.object.select_all(action="DESELECT")
    for o in model_meshes() + [rig]:
        o.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.context.scene.frame_set(1)
    # Same settings as build-emma-3d-lab.py, so the web page could load this file as-is.
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True,
                              export_animations=True, export_animation_mode="NLA_TRACKS",
                              export_force_sampling=True, export_frame_range=False,
                              export_skins=True, export_def_bones=True, export_materials="EXPORT",
                              export_cameras=False, export_lights=False)
    for t in rig.animation_data.nla_tracks:
        t.mute = True
    rig.animation_data.action = bpy.data.actions["idle"]


def fcurves_of(action):
    try:
        return list(action.fcurves)
    except AttributeError:
        out = []
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    out += list(bag.fcurves)
        return out


def to_centimetres():
    """Geometry, bones and keyed translations x100; FBX scale then stays exactly 1."""
    scene = bpy.context.scene
    S = Matrix.Scale(100, 4)
    scene.unit_settings.scale_length = 0.01
    rig = bpy.data.objects[RIG]
    rig.data.transform(S)
    rig.location *= 100
    for o in model_meshes():
        assert o.data.users == 1, o.name
        o.data.transform(S)
        o.location *= 100
    for act in bpy.data.actions:
        for fc in fcurves_of(act):
            if fc.data_path.endswith(".location") or fc.data_path == "location":
                for k in fc.keyframe_points:
                    k.co[1] *= 100
                    k.handle_left[1] *= 100
                    k.handle_right[1] *= 100
                fc.update()
    rig.name = "Armature"   # UE drops a node with this name instead of making it a bone
    return rig


FBX_COMMON = dict(use_selection=True, global_scale=1.0, apply_unit_scale=True,
                  apply_scale_options="FBX_SCALE_NONE", axis_forward="-Z", axis_up="Y",
                  use_space_transform=True, bake_space_transform=False, use_mesh_modifiers=False,
                  mesh_smooth_type="FACE", use_subsurf=False, use_mesh_edges=False, use_tspace=False,
                  use_triangles=True, use_custom_props=False, add_leaf_bones=False,
                  primary_bone_axis="Y", secondary_bone_axis="X", use_armature_deform_only=False,
                  armature_nodetype="NULL", path_mode="AUTO", embed_textures=False, colors_type="NONE")


def export_fbx_set(prefix, with_anims):
    scene = bpy.context.scene
    rig = to_centimetres()
    meshes = [o for o in model_meshes() if o.name != RING]
    ring = bpy.data.objects[RING]
    ad = rig.animation_data
    for t in ad.nla_tracks:
        t.mute = True
    files = []

    def select(objs, active):
        bpy.ops.object.select_all(action="DESELECT")
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = active

    rig.data.pose_position = "REST"
    ad.action = None
    select(meshes + [rig], rig)
    path = OUT / f"{prefix}_SK.fbx"
    bpy.ops.export_scene.fbx(filepath=str(path), object_types={"ARMATURE", "MESH"}, bake_anim=False, **FBX_COMMON)
    files.append(path)
    select([ring], ring)
    path = OUT / f"{prefix}_HoverRing.fbx"
    bpy.ops.export_scene.fbx(filepath=str(path), object_types={"MESH"}, bake_anim=False, **FBX_COMMON)
    files.append(path)
    rig.data.pose_position = "POSE"
    if with_anims:
        for name in ("idle", "look"):
            act = bpy.data.actions[name]
            ad.action = act
            scene.frame_start, scene.frame_end = int(act.frame_range[0]), int(act.frame_range[1])
            select([rig], rig)
            path = OUT / f"Emma_Anim_{name}.fbx"
            bpy.ops.export_scene.fbx(filepath=str(path), object_types={"ARMATURE"}, bake_anim=True,
                                     bake_anim_use_all_bones=True, bake_anim_use_nla_strips=False,
                                     bake_anim_use_all_actions=False, bake_anim_force_startend_keying=True,
                                     bake_anim_step=1.0, bake_anim_simplify_factor=0.0, **FBX_COMMON)
            files.append(path)
    return files


# --------------------------------------------------------------------------- verification
def bone_samples(rig, action, frames):
    """Per frame, how far each bone has moved from its rest pose, in world space.
    Rest-relative, so an importer that re-orients bone axes does not count as motion."""
    scene = bpy.context.scene
    ad = rig.animation_data
    ad.action = action
    W = rig.matrix_world
    rest = {b.name: (W @ b.bone.matrix_local).inverted() for b in rig.pose.bones}
    out = []
    for f in frames:
        scene.frame_set(f)
        bpy.context.view_layer.update()
        out.append({b.name: W @ b.matrix @ rest[b.name] for b in rig.pose.bones})
    return out


def verify_fbx(prefix, expect_tris):
    """Re-import each FBX into an empty scene and compare against the source."""
    res = {}
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(OUT / f"{prefix}_SK.fbx"))
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    arm = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    pts = [o.matrix_world @ v.co for o in meshes for v in o.data.vertices]
    res["sk"] = {"meshes": len(meshes), "triangles": tri_count(meshes), "expected_triangles": expect_tris,
                 "armatures": [a.name for a in arm],
                 "bones": [(b.name, b.parent.name if b.parent else None) for b in arm[0].data.bones] if arm else [],
                 "height_after_reimport": round(max(p.z for p in pts) - min(p.z for p in pts), 4),
                 "materials": sorted({m.name for o in meshes for m in o.data.materials if m})}
    return res


def verify_anims(source_rig_samples):
    res = {}
    for name, (frames, ref) in source_rig_samples.items():
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.fbx(filepath=str(OUT / f"Emma_Anim_{name}.fbx"))
        arm = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"][0]
        act = arm.animation_data.action
        off = int(act.frame_range[0]) - frames[0]
        got = bone_samples(arm, act, [f + off for f in frames])
        dpos = drot = 0.0
        for a, b in zip(ref, got):
            for bone, m in a.items():
                m2 = b[bone]
                dpos = max(dpos, (m.translation - m2.translation).length)
                drot = max(drot, math.degrees(m.to_quaternion().rotation_difference(m2.to_quaternion()).angle))
        res[name] = {"frames": len(frames), "max_bone_move_mm": round(dpos * 1000, 4),
                     "max_bone_turn_deg": round(drot, 4), "imported_range": tuple(act.frame_range)}
    return res


def verify_glb(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(path))
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.name != "Icosphere"]
    arm = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    return {"triangles": tri_count(meshes), "meshes": len(meshes),
            "bones": len(arm[0].data.bones) if arm else 0,
            "clips": sorted(a.name for a in bpy.data.actions),
            "node_names_ok": all(any(o.name.startswith(n) for o in meshes)
                                 for n in ("Chest / luminous ring", "Head / pearl shell")),
            "white_light": any(m.name == "White light" for m in bpy.data.materials)}


def material_table():
    out = {}
    for m in bpy.data.materials:
        if not m.node_tree or not m.users:
            continue
        b = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if not b:
            continue
        val = lambda k: b.inputs[k].default_value
        rgb = lambda k: [round(x, 4) for x in val(k)[:3]]
        out[m.name] = {"base_color_linear": rgb("Base Color"), "metallic": round(val("Metallic"), 3),
                       "roughness": round(val("Roughness"), 3), "specular_ior_level": round(val("Specular IOR Level"), 3),
                       "coat_weight": round(val("Coat Weight"), 3), "coat_roughness": round(val("Coat Roughness"), 3),
                       "emission_color_linear": rgb("Emission Color"), "emission_strength": round(val("Emission Strength"), 3)}
    return out


# --------------------------------------------------------------------------- main
t_start = time.time()
objs, originals = make_lowpoly()
# Saved BEFORE the compare pass, which switches the scene to Workbench and recolours parts.
# The original meshes kept for that pass have no users, so they are not written.
lp_blend = OUT / "blend" / "Emma_LP.blend"
bpy.context.scene.frame_set(1)
bpy.ops.wm.save_as_mainfile(filepath=str(lp_blend), compress=True)
export_glb(OUT / "glb" / "Emma_LP.glb")
shutil.copyfile(SRC_GLB, OUT / "glb" / "Emma.glb")
if not args.no_render:
    render_compare(objs, originals)

# Reference bone motion from the untouched source, in metres, for the FBX round trip.
open_source()
REPORT["materials"] = material_table()
ref = {}
for name in ("idle", "look"):
    act = bpy.data.actions[name]
    frames = list(range(int(act.frame_range[0]), int(act.frame_range[1]) + 1))
    r = bpy.data.objects[RIG]
    for t in r.animation_data.nla_tracks:
        t.mute = True
    ref[name] = (frames, bone_samples(r, act, frames))
orig_tris = REPORT["triangles_original"]
export_fbx_set("Emma", with_anims=True)
bpy.ops.wm.open_mainfile(filepath=str(lp_blend))
export_fbx_set("Emma_LP", with_anims=False)

ring_tris = REPORT["meshes"][RING]
REPORT["verify"] = {
    "fbx_original": verify_fbx("Emma", orig_tris - ring_tris["triangles_before"]),
    "fbx_lowpoly": verify_fbx("Emma_LP", REPORT["triangles_lowpoly"] - ring_tris["triangles_after"]),
    "fbx_anims": verify_anims(ref),
    "glb_original": verify_glb(OUT / "glb" / "Emma.glb"),
    "glb_lowpoly": verify_glb(OUT / "glb" / "Emma_LP.glb"),
}
REPORT["seconds"] = round(time.time() - t_start, 1)
(OUT / "report.json").write_text(json.dumps(REPORT, indent=2, ensure_ascii=False), encoding="utf-8")


def write_readme():
    r, v = REPORT, REPORT["verify"]
    views = r.get("render_compare", {}).get("views", {})
    seg_moved = sum(x["seg_changed_off_edge"] for x in views.values())
    zebra_p999 = max((x["zebra_interior_p999"] for x in views.values()), default=None)
    zebra_48 = sum(x["zebra_interior_over_48"] for x in views.values())
    anim = v["fbx_anims"]
    mats = "\n".join(
        f"| {n} | {m['base_color_linear']} | {m['metallic']} | {m['roughness']} | "
        f"{m['coat_weight']} / {m['coat_roughness']} | "
        f"{m['emission_strength'] if m['emission_strength'] else '-'} |"
        for n, m in r["materials"].items())
    lp = r["triangles_lowpoly"]
    text = f"""# Emma สำหรับ Unreal Engine 5

สร้างโดย `scripts/export-emma-ue5.py` จาก `{r['source_blend']}` (ตัว 162k ที่อนุมัติแล้ว)
รันใหม่ได้ทุกเมื่อ โฟลเดอร์นี้ไม่อยู่ใน git

## ไฟล์

| ไฟล์ | คืออะไร |
|---|---|
| `Emma_SK.fbx` | ตัวต้นฉบับ {r['triangles_original'] - r['meshes'][RING]['triangles_before']:,} สามเหลี่ยม + โครงกระดูก 6 ชิ้น (ไม่มีแอนิเมชัน) |
| `Emma_LP_SK.fbx` | ตัวลดโพลี {lp - r['meshes'][RING]['triangles_after']:,} สามเหลี่ยม โครงกระดูกเดียวกัน |
| `Emma_Anim_idle.fbx` | ท่าพัก 90 เฟรม @ 30 fps (ใช้ได้กับทั้งสองตัว) |
| `Emma_Anim_look.fbx` | ท่าหันมอง 140 เฟรม @ 30 fps |
| `Emma_HoverRing.fbx` / `Emma_LP_HoverRing.fbx` | วงแสงใต้ตัว เป็น static mesh แยก **ไม่ผูกกับกระดูก** (บนเว็บก็ไม่ขยับตามตัว) |
| `glb/Emma.glb` / `glb/Emma_LP.glb` | ทุกอย่างในไฟล์เดียว (mesh + rig + 2 คลิป) สำหรับ importer glTF ของ UE |
| `blend/Emma_LP.blend` | ตัวลดโพลีเป็นไฟล์ Blender เผื่อแก้ต่อ |
| `compare/*.png` | ภาพเทียบต้นฉบับกับตัวลดโพลี 21 มุม |
| `report.json` | ตัวเลขทุกชิ้น |

## ลดโพลีได้แค่ไหนโดยไม่เปลี่ยนหน้าตา

**{r['triangles_original']:,} → {lp:,} สามเหลี่ยม (−{100 * (1 - lp / r['triangles_original']):.0f}%)** — เกือบทั้งหมดมาจาก
ผิวที่ฝังอยู่ในชิ้นอื่นของกระดูกเดียวกัน ({r['triangles_original'] - r['triangles_after_hidden']:,} สามเหลี่ยม) ซึ่งไม่มีกล้องมุมไหนเห็นในท่าไหนเลย

ผลตรวจจากการเรนเดอร์ 21 มุม (ทั้งตัว 10 มุม, ใกล้หัว/ตา/ปาก/อก/แขน/หลัง, ท่า look):
ชิ้นส่วนเลื่อนที่ ≥2 px = **{seg_moved} px** · แสงสะท้อนแบบ zebra (ไวต่อ normal ที่สุด) เปลี่ยนสูงสุด
p99.9 = **{zebra_p999}/255**, เกิน 48/255 รวม {zebra_48} px

ลดมากกว่านี้ไม่ได้ถ้าจะให้เหมือนเดิม — วัดแล้ว: ปล่อยให้ผิวเคลื่อนได้ 0.5 มม. ตัดได้อีก ~23k แต่ลายสะท้อนโครเมียม
ที่วงอกแตกเป็นฟันเลื่อยจนเห็นด้วยตา, ยอมให้ normal เพี้ยน 2° ได้เพิ่มแค่ ~1k แต่วงอกก็เริ่มเพี้ยนแล้ว ผิวทุกชิ้นเป็น
โค้งมันเงา สามเหลี่ยมคือสิ่งที่ประกอบเป็นเงาสะท้อน (บทเรียนเดียวกับตัว 68k ที่ทำขอบแถบหัวขาด 29 ก.ย.)

ถ้าต้องการตัวเบากว่านี้สำหรับตอนอยู่ไกล ให้ใช้ LOD ของ UE เอง (Skeletal Mesh → LOD Settings → Number of LODs)
— UE สลับไปตัวหยาบเฉพาะตอนตัวเล็กบนจอ ซึ่งความต่างมองไม่เห็นอยู่แล้ว

## เอาเข้า UE5 (ทาง FBX)

1. ลาก `Emma_SK.fbx` (หรือ `Emma_LP_SK.fbx`) เข้า Content Browser → Skeletal Mesh, Skeleton = สร้างใหม่,
   Normals = **Import Normals** (ไม่ใช่ Compute — normal ในไฟล์คือสิ่งที่ทำให้เงาเหมือนเดิม)
2. ลาก `Emma_Anim_idle.fbx` และ `Emma_Anim_look.fbx` → เลือก Skeleton ตัวที่เพิ่งสร้าง
3. ลาก `Emma_HoverRing.fbx` → Static Mesh วางใต้ตัวใน Blueprint
4. หน่วยในไฟล์เป็นเซนติเมตรแล้ว (FBX scale = 1) ตัวสูง ~3.6 ม. (รวมวงใต้ตัว ~3.9 ม.) ตามไฟล์ต้นฉบับ — ย่อใน UE ตามขนาดจริงได้
5. armature ชื่อ `Armature` จึงไม่มีกระดูกเกินมาหัวแถว กระดูกเริ่มที่ `Root`

หรือใช้ `glb/Emma.glb` ไฟล์เดียว ผ่าน importer glTF ของ UE (หน่วยเมตรของ glTF UE แปลงเอง)

**ยังไม่ได้ลองเปิดใน UE จริง** — ที่ตรวจแล้วคือเปิดไฟล์ที่ส่งออกกลับเข้า Blender:
FBX ตัวต้นฉบับ {v['fbx_original']['sk']['triangles']:,}/{v['fbx_original']['sk']['expected_triangles']:,} สามเหลี่ยม,
ตัวลดโพลี {v['fbx_lowpoly']['sk']['triangles']:,}/{v['fbx_lowpoly']['sk']['expected_triangles']:,},
กระดูก {len(v['fbx_original']['sk']['bones'])} ชิ้นตามลำดับเดิม, แอนิเมชันคลาดสูงสุด
idle {anim['idle']['max_bone_move_mm']} มม. / {anim['idle']['max_bone_turn_deg']}° ·
look {anim['look']['max_bone_move_mm']} มม. / {anim['look']['max_bone_turn_deg']}°
(ส่งออกโดยปิดการลดคีย์ของ exporter — ค่า default ของมันเขียนคีย์ใหม่)

## วัสดุ (สร้างใหม่ใน UE)

ทุกชิ้นเป็น Principled BSDF ธรรมดา ไม่มี texture ค่าด้านล่างคือของจริงในไฟล์ (สีเป็น linear)
ช่อง coat ใช้ shading model Clear Coat ของ UE

| วัสดุ | Base color | Metallic | Roughness | Coat / coat roughness | Emissive |
|---|---|---|---|---|---|
{mats}

`White light` คือไฟกลางอกกับวงใต้ตัว บนเว็บสีของมันถูกเปลี่ยนจากโค้ดตามโหมด (`mascot.js` → `glowMaterial`)
ใน UE ควรทำเป็น Material Instance ที่มี parameter สี

## แอนิเมชันที่ไม่ได้อยู่ในไฟล์

ไฟล์มีแค่ `idle` กับ `look` ส่วนท่าตามสถานะบนหน้าเว็บ (ฟัง/คิด/พูด/ทัก/ดีใจ/ขอบคุณ/error) **ไม่ใช่คลิป**
เป็นสูตรใน `client/assets/emma-3d-lab/mascot.js` (`STATE_MOTION`) ที่บวกลอย/เอียง/หันหัว/ยกแขนทับ idle ทุกเฟรม
ใน UE ต้องทำซ้ำใน Animation Blueprint หรือให้อบสูตรนั้นเป็นคลิป FBX แยกต่อสถานะ
"""
    (OUT / "README.md").write_text(text, encoding="utf-8")


write_readme()
log("verify", json.dumps(REPORT["verify"], ensure_ascii=False))
log(f"done in {REPORT['seconds']}s -> {OUT}")
