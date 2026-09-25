"""Build a static, reviewable hanging-hand pose; preserve the existing core.

blender --background --python scripts/build-emma-rest-hands.py -- --render
Source .blend is read only. Output stays DRAFT, with no new rig or animations.
"""
from pathlib import Path
import hashlib
import json
import math
import sys

import bpy
import bmesh
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'client/assets/emma-3d-lab/emma.blend'
OUT = ROOT / 'models/emma/rest-pose'
OUT.mkdir(parents=True, exist_ok=True)
VALID = OUT / 'validation'
VALID.mkdir(exist_ok=True)
source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
bpy.ops.wm.open_mainfile(filepath=str(SOURCE))
scene = bpy.context.scene
scene.frame_set(1)
bpy.context.view_layer.update()


def arm_part(obj):
    return obj.name.startswith(('Shoulder /', 'Wrist /', 'Glove.'))


def world_vertices(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    data = evaluated.to_mesh()
    result = [tuple(evaluated.matrix_world @ v.co) for v in data.vertices]
    evaluated.to_mesh_clear()
    return result


# Snapshot evaluated geometry, not the bind-pose mesh. This freezes the existing
# resting core without changing its silhouette, transforms, or material slots.
locked = {o.name: world_vertices(o) for o in scene.objects
          if o.type == 'MESH' and not arm_part(o)}
removed = [o.name for o in scene.objects if arm_part(o)]
for obj in list(scene.objects):
    if arm_part(obj):
        bpy.data.objects.remove(obj, do_unlink=True)
    elif obj.type == 'MESH':
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        data = bpy.data.meshes.new_from_object(evaluated)
        matrix = evaluated.matrix_world.copy()
        obj.modifiers.clear()
        obj.parent = None
        obj.animation_data_clear()
        obj.data = data
        obj.matrix_world = matrix
        obj.vertex_groups.clear()
        obj['review_scope'] = 'Locked existing core: evaluated source frame 1'
for obj in list(bpy.data.objects):
    if obj.type == 'ARMATURE':
        bpy.data.objects.remove(obj, do_unlink=True)
for action in list(bpy.data.actions):
    bpy.data.actions.remove(action)
bpy.context.view_layer.update()
delta = max((Vector(a)-Vector(b)).length for name, points in locked.items()
            for a, b in zip(points, world_vertices(bpy.data.objects[name])))
assert delta < 1e-6, delta

HANDS = bpy.data.collections.new('REST_HANDS_DRAFT')
scene.collection.children.link(HANDS)
GUIDES = bpy.data.collections.new('HAND_CONSTRUCTION_GUIDES')
scene.collection.children.link(GUIDES)
GUIDES.hide_render = True
GUIDES.hide_viewport = True
WHITE = bpy.data.materials['Soft white glove']
CHROME = bpy.data.materials['Polished chrome']
DARK = bpy.data.materials['Graphite joints']


def finish(obj, name, material):
    obj.name = name
    for col in list(obj.users_collection):
        col.objects.unlink(obj)
    HANDS.objects.link(obj)
    obj.data.materials.clear()
    obj.data.materials.append(material)
    for poly in obj.data.polygons:
        poly.use_smooth = True
    return obj


def ellipsoid(name, center, radii, material):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=24, location=center)
    obj = bpy.context.object
    obj.scale = radii
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return finish(obj, name, material)


def tube(name, points, radius, material):
    data = bpy.data.curves.new(name, 'CURVE')
    data.dimensions = '3D'
    data.bevel_depth = radius
    data.bevel_resolution = 4
    data.use_fill_caps = True
    spline = data.splines.new('POLY')
    spline.points.add(len(points)-1)
    for p, co in zip(spline.points, points):
        p.co = (*co, 1)
    obj = bpy.data.objects.new(name, data)
    HANDS.objects.link(obj)
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.convert(target='MESH')
    return finish(bpy.context.object, name, material)


def bezier(a, b, c, d, count=32):
    a, b, c, d = map(Vector, (a, b, c, d))
    return [tuple((1-t)**3*a+3*(1-t)**2*t*b+3*(1-t)*t*t*c+t**3*d)
            for t in (i/count for i in range(count+1))]


def cuff(name, start, end, radius, material):
    a, b = Vector(start), Vector(end)
    axis = (b-a).normalized()
    u = axis.cross(Vector((0, 1, 0))).normalized()
    v = axis.cross(u)
    length = (b-a).length
    profile = [(0, .80), (.009, .96), (.021, 1),
               (length-.021, 1), (length-.009, .96), (length, .80)]
    n = 80
    verts = [tuple(a+axis*z+radius*r*(u*math.cos(2*math.pi*i/n)+v*math.sin(2*math.pi*i/n)))
             for z, r in profile for i in range(n)]
    faces = [(j*n+i, j*n+(i+1)%n, (j+1)*n+(i+1)%n, (j+1)*n+i)
             for j in range(len(profile)-1) for i in range(n)]
    faces += [tuple(range(n-1, -1, -1)), tuple((len(profile)-1)*n+i for i in range(n))]
    data = bpy.data.meshes.new(name)
    data.from_pydata(verts, [], faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    HANDS.objects.link(obj)
    return finish(obj, name, material)


# Right hand is authored once, then mirrored in geometry. Fingers run mostly
# across depth: edge-of-hand in front view, broad back-of-hand in side view.
parts = []
parts.append(ellipsoid('glove construction palm', (1.008, .004, 1.590), (.142, .191, .205), WHITE))
parts.append(ellipsoid('glove construction heel', (.947, 0, 1.775), (.125, .126, .166), WHITE))
paths = {
    'Index': ((1.028, -.131, 1.535), (1.108, -.185, 1.405), (1.063, -.205, 1.294), (.995, -.205, 1.282)),
    'Middle': ((1.047, .012, 1.503), (1.127, .007, 1.332), (1.064, -.002, 1.219), (.990, -.008, 1.226)),
    'Outer': ((1.027, .148, 1.516), (1.101, .185, 1.368), (1.055, .205, 1.282), (.998, .205, 1.275)),
    'Thumb': ((.937, -.123, 1.689), (.840, -.189, 1.653), (.803, -.202, 1.560), (.830, -.177, 1.535)),
}
for label, controls in paths.items():
    points = bezier(*controls)
    radius = .076 if label != 'Thumb' else .083
    parts.append(tube('glove construction '+label, points, radius, WHITE))
    parts.append(ellipsoid('glove construction tip', points[-1], (radius,)*3, WHITE))

# Retain the editable pre-union construction in a hidden collection.
for obj in parts:
    copy = obj.copy()
    copy.data = obj.data.copy()
    GUIDES.objects.link(copy)
    copy.hide_render = True
    copy['purpose'] = 'Editable source volumes for rest-hand reconstruction'
bpy.ops.object.select_all(action='DESELECT')
for obj in parts:
    obj.select_set(True)
bpy.context.view_layer.objects.active = parts[0]
bpy.ops.object.join()
hand = bpy.context.object
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
remesh = hand.modifiers.new('Weld relaxed glove', 'REMESH')
remesh.mode = 'VOXEL'
remesh.voxel_size = .0055
remesh.use_smooth_shade = True
bpy.ops.object.modifier_apply(modifier=remesh.name)
soft = hand.modifiers.new('Soften finger roots', 'SMOOTH')
soft.factor = .65
soft.iterations = 10
bpy.ops.object.modifier_apply(modifier=soft.name)
finish(hand, 'HAND_REST_R', WHITE)
hand['pose'] = 'Static relaxed fingers, hanging alongside torso; not a fist'
hand['status'] = 'DRAFT / awaiting visual review'
left = hand.copy()
left.data = hand.data.copy()
HANDS.objects.link(left)
left.name = 'HAND_REST_L'
for vertex in left.data.vertices:
    vertex.co.x *= -1
bm = bmesh.new()
bm.from_mesh(left.data)
bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
bm.to_mesh(left.data)
bm.free()

for side, suffix in ((1, 'R'), (-1, 'L')):
    def mirror(p):
        return (side*p[0], p[1], p[2])
    ellipsoid('SHOULDER_SOCKET_'+suffix, mirror((.642, 0, 1.985)), (.117, .129, .133), DARK)
    cuff('SHOULDER_RING_'+suffix, mirror((.663, 0, 1.989)), mirror((.732, 0, 1.964)), .148, CHROME)
    cuff('ARM_SHORT_'+suffix, mirror((.721, 0, 1.967)), mirror((.855, 0, 1.905)), .119, DARK)
    cuff('WRIST_CUFF_'+suffix, mirror((.804, 0, 1.932)), mirror((.932, 0, 1.859)), .143, CHROME)

audit = {'status': 'draft', 'pose': 'rest / hands hanging, fingers gently curved',
         'digits_per_hand': 4, 'digit_definition': 'one thumb plus three broad rounded fingers',
         'source_blend': str(SOURCE.relative_to(ROOT)), 'source_sha256': source_hash,
         'locked_core_objects': sorted(locked), 'locked_core_max_vertex_delta': delta,
         'replaced_objects': removed, 'hands': {},
         'armatures': sum(o.type == 'ARMATURE' for o in scene.objects), 'actions': len(bpy.data.actions),
         'reference_note': 'Latest user FULL CHARACTER VIEWS crop sets the pose. Existing project sheet supports inspection; neither provides calibrated side depth.',
         'construction_assumptions': 'Depth and cuff internals are provisional. Latest user close-up explicitly selects four fat glove digits including the thumb.'}
for obj in (hand, left):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    unseen = set(bm.verts)
    components = 0
    while unseen:
        components += 1
        pending = [unseen.pop()]
        while pending:
            for edge in pending.pop().link_edges:
                for vert in edge.verts:
                    if vert in unseen:
                        unseen.remove(vert)
                        pending.append(vert)
    item = {'vertices': len(bm.verts), 'connected_components': components,
            'nonmanifold_edges': sum(not e.is_manifold for e in bm.edges),
            'finite_coordinates': all(math.isfinite(c) for v in bm.verts for c in v.co)}
    bm.free()
    assert item['connected_components'] == 1 and item['nonmanifold_edges'] == 0 and item['finite_coordinates'], item
    audit['hands'][obj.name] = item
assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == source_hash
assert audit['armatures'] == 0 and audit['actions'] == 0

scene['design_status'] = 'DRAFT / static rest-hand review, no visual approval implied'
scene['scope'] = 'Rest hands and compact arm connections only; core frozen from source frame 1'
scene.render.resolution_x = 720
scene.render.resolution_y = 840
scene.render.resolution_percentage = 100
scene.cycles.samples = 32
scene.camera.data.type = 'ORTHO'
scene.camera.data.ortho_scale = 5.3
scene.camera.location = (0, -9, 3.25)
scene.camera.rotation_euler = (Vector((0, 0, 2.4))-scene.camera.location).to_track_quat('-Z', 'Y').to_euler()
bpy.ops.object.select_all(action='DESELECT')
hand.select_set(True)
left.select_set(True)
bpy.context.view_layer.objects.active = hand
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == 'VIEW_3D':
            area.spaces.active.region_3d.view_perspective = 'CAMERA'
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'EMMA_REST_POSE.blend'), compress=True)
(OUT/'model-audit.json').write_text(json.dumps(audit, indent=2)+'\n', encoding='utf-8')
print('REST_POSE_AUDIT', json.dumps(audit), flush=True)

if '--render' in sys.argv:
    for label, location in [('front', (0, -9, 3.25)), ('quarter', (7.4, -5.2, 3.25)),
                             ('side', (9, 0, 3.25)), ('back', (0, 9, 3.25))]:
        scene.camera.location = location
        scene.camera.rotation_euler = (Vector((0, 0, 2.4))-scene.camera.location).to_track_quat('-Z', 'Y').to_euler()
        scene.render.filepath = str(VALID/(label+'.png'))
        bpy.ops.render.render(write_still=True)
    scene.camera.location = (4, -5, 2.4)
    scene.camera.rotation_euler = (Vector((1, 0, 1.6))-scene.camera.location).to_track_quat('-Z', 'Y').to_euler()
    scene.camera.data.ortho_scale = 1.25
    scene.render.filepath = str(VALID/'hand-detail.png')
    bpy.ops.render.render(write_still=True)
