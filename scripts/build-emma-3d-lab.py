"""Build a separate, editable Emma mesh/armature study with Blender 4.5 LTS.

Run: blender --background --python scripts/build-emma-3d-lab.py
No production Rive assets or voice configuration are read or modified.
Coordinates: X right, Y back, Z up; the front camera looks along +Y.
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector, Quaternion
from mathutils.geometry import delaunay_2d_cdt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "client/assets/emma-3d-lab"
# `--out <dir>` writes the study somewhere else, which is how the projection
# lab gets its own GLB without touching the one the app loads.
if "--out" in sys.argv:
    OUT = Path(sys.argv[sys.argv.index("--out") + 1])
OUT.mkdir(parents=True, exist_ok=True)
BUILD = OUT / "build"
BUILD.mkdir(exist_ok=True)
PI = math.pi
#: Level of detail. 1.0 is the density this study has always built at; a larger
#: number spaces the tessellation out and drops segment counts by the same
#: factor. Opt-in through EMMA_LOD so the default build is unchanged, and the
#: point of it being ONE number is that a render can be compared against the
#: full-detail one and the difference stated in pixels rather than argued.
LOD = float(os.environ.get("EMMA_LOD", "1") or 1)
#: Restrict the reduction to meshes whose name starts with one of these. The
#: body assembly (teardrop + rolled edges) came back speckled when it was
#: thinned — the shell and its edge tube start intersecting — so the knob has
#: to be aimable rather than global.
LOD_ONLY = [n.strip() for n in os.environ.get("EMMA_LOD_ONLY", "").split(",") if n.strip()]


def _lod_for(name):
    if not LOD_ONLY:
        return LOD
    return LOD if any(str(name).startswith(p) for p in LOD_ONLY) else 1.0


def _seg(n, floor=8):
    """Segment counts scale down with LOD, never below a shape-keeping floor."""
    return max(floor, int(round(n / LOD)))
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
for data in list(bpy.data.materials):
    bpy.data.materials.remove(data)


def material(name, color, metallic=0.0, roughness=0.3, emission=0, coat=0.60, coat_roughness=0.085, specular=0.5):
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*color, 1)
    m.use_nodes = True
    p = m.node_tree.nodes.get("Principled BSDF")
    p.inputs["Base Color"].default_value = (*color, 1)
    p.inputs["Metallic"].default_value = metallic
    p.inputs["Roughness"].default_value = roughness
    p.inputs["Specular IOR Level"].default_value = specular
    p.inputs["Coat Weight"].default_value = coat
    p.inputs["Coat Roughness"].default_value = coat_roughness
    if emission:
        p.inputs["Emission Color"].default_value = (*color, 1)
        p.inputs["Emission Strength"].default_value = emission
    return m


# PART LOOK (2026-09-23): every value below was set against the sheet's MATERIAL
# REFERENCE panel (481x162 crop, background luminance 23; sphere stats inside 0.93 r;
# 8-band vertical luminance profiles top->bottom):
#   WHITE BODY  p50 225, p05 162, 23 % > 240; profile 244 229 226 212 200 209 220 234 =
#               one broad soft highlight upper-left (centroid -0.19,-0.38 r), shading to
#               200 at 60 % down, bounce back to 234 at the bottom. Base build head cap:
#               p50 236, p05 222, profile flat 230..237, highlight upper-RIGHT (+0.50,-0.33)
#               from the 1000 W right strip. -> base roughness .17->.24, coat .60->.50 at
#               .085->.14 (a broad patch, not a pin); the gradient comes from the lights.
#   CHROME      51 % < 40, 25 % > 200, 17 % > 240, histogram 49 % in the darkest eighth
#               and 21 % in the brightest (a mirror of a room with dark and bright bands).
#               Base build shoulder balls: 0.1 % < 40, 55 % > 200 = a washed grey disc.
#               -> roughness .075->.03, no coat; the bands come from the world gradient.
#               Build 2 (base colour .96, graded world) still read balls p50 173-186 with
#               0 % < 40: in Cycles the balls mirror the white head and torso around
#               them, which the sheet's chrome does not. Base colour .96 -> .56 = real
#               chrome F0 (0.55 linear at normal incidence; silver would be .95), so the
#               white body's mirror image drops to ~190 while clipped lights stay 255.
#   BLACK       p50 12, p95 90, max 183, 77 % < 40: satin, one broad dim highlight.
#               Base build neck cup read p50 143 (coat .60 at .085 made it glossy grey);
#               build 2 with roughness .38 / coat .10 read p50 80, p75 153 (softbox lobes)
#               -> roughness .45, specular level .35, coat .06 at .30.
#   LIGHT       flat 254 core; halo 101 at 1.05-1.15 r, 52 at 1.2-1.4 r, 29 at 1.5-1.8 r.
#               -> emission 3.5->6 (viewer ring core read p50 227, sheet 253) plus the
#               compositor bloom below for the Blender renders.
CHROME = material("Polished chrome", (0.56, 0.565, 0.575), 1, 0.03, coat=0.0)
PEARL = material("Pearl enamel", (0.965, 0.962, 0.955), 0.02, 0.24, coat=0.50, coat_roughness=0.14)
GLOVE = material("Soft white glove", (0.96, 0.96, 0.955), 0.0, 0.28, coat=0.45, coat_roughness=0.16)
DARK = material("Graphite joints", (0.010, 0.011, 0.012), 0.0, 0.45, coat=0.06, coat_roughness=0.30, specular=0.35)
EYE = material("Black chrome eyes", (0.008, 0.009, 0.010), 0.48, 0.10, coat=0.60, coat_roughness=0.06)
LIGHT = material("White light", (0.98, 0.985, 1.0), 0.0, 0.25, 6.0, coat=0.30, coat_roughness=0.10)
MODEL = []
DEFORM = {}


def finish(obj, name, mat, bone=None):
    obj.name = name
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    if obj.type == "MESH":
        for face in obj.data.polygons:
            face.use_smooth = True
    MODEL.append(obj)
    if bone:
        DEFORM[obj.name] = bone
    return obj


def mesh(name, verts, faces, mat, bone=None):
    data = bpy.data.meshes.new(name)
    data.from_pydata(verts, [], faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    # Recalculate consistently after constructing closed parametric surfaces.
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    obj.select_set(False)
    return finish(obj, name, mat, bone)


def ellipsoid(name, loc, scale, mat, bone=None, segments=48, rings=24):
    _l = _lod_for(name)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=max(24, int(round(segments/_l))),
                                         ring_count=max(12, int(round(rings/_l))), location=loc)
    obj = bpy.context.object
    obj.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return finish(obj, name, mat, bone)


def tube(name, points, radius, mat, bone=None):
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 2 if LOD <= 1.4 else 1
    curve.bevel_depth = radius
    curve.bevel_resolution = 3 if LOD <= 1.4 else (2 if LOD <= 2.2 else 1)
    curve.use_fill_caps = True
    s = curve.splines.new("POLY")
    s.points.add(len(points) - 1)
    for p, co in zip(s.points, points):
        p.co = (*co, 1)
    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.convert(target="MESH")
    return finish(bpy.context.object, name, mat, bone)


def ring(name, center, normal, radius, thickness, mat, bone=None):
    bpy.ops.mesh.primitive_torus_add(major_segments=_seg(64, 24), minor_segments=_seg(12, 6),
                                   location=center, major_radius=radius, minor_radius=thickness)
    obj = bpy.context.object
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(Vector(normal).normalized())
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return finish(obj, name, mat, bone)


def segment(name, a, b, radius, mat, bone=None):
    a, b = Vector(a), Vector(b)
    bpy.ops.mesh.primitive_cylinder_add(vertices=_seg(32, 12), radius=radius, depth=(b-a).length,
                                      end_fill_type="NGON", location=(a+b)*0.5)
    obj = bpy.context.object
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = Vector((0, 0, 1)).rotation_difference((b-a).normalized())
    bevel = obj.modifiers.new("Rounded machined edge", "BEVEL")
    bevel.width = min(radius*0.35, 0.028)
    bevel.segments = 3
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return finish(obj, name, mat, bone)


def shoulder_cuff(name, a, b, bone):
    """One rounded bearing housing, with rolled edges machined into its profile."""
    a,b=Vector(a),Vector(b)
    axis=(b-a).normalized()
    u=axis.cross(Vector((0,1,0))).normalized();v=axis.cross(u)
    length=(b-a).length
    profile=[(-.06,.09),(-.046,.135),(-.028,.165),(-.010,.177),
             (.010,.179),(.030,.172),(.052,.164),(length-.062,.164),
             (length-.043,.171),(length-.023,.176),(length-.005,.170),
             (length+.010,.148),(length+.013,.115)]
    verts=[];faces=[];n=96
    for along,r in profile:
        for i in range(n):
            angle=2*PI*i/n
            verts.append(tuple(a+axis*along+r*(u*math.cos(angle)+v*math.sin(angle))))
    for j in range(len(profile)-1):
        for i in range(n):faces.append((j*n+i,j*n+(i+1)%n,(j+1)*n+(i+1)%n,(j+1)*n+i))
    faces.extend([tuple(range(n-1,-1,-1)),tuple((len(profile)-1)*n+i for i in range(n))])
    return mesh(name,verts,faces,CHROME,bone)


def bezier(a, b, c, d, count=16):
    a, b, c, d = map(Vector, (a, b, c, d))
    return [tuple((1-t)**3*a + 3*(1-t)**2*t*b + 3*(1-t)*t*t*c + t**3*d)
            for t in (i/count for i in range(count))]


# Proportions lock (2026-09-23, sheet full_front x4, 1 HW = 106.5 px): HZ
# stays 3.26. Measured down from the head top: chin 0.927 HW, torso shell top
# 0.976 HW, tip 1.542 HW. The lab head top is HZ+RZ+.03 (band), the torso top
# 2.12 and the tip .79, so head-top-to-torso-top = 0.983 HW and head-top-to-tip
# 1.556 HW: both within 0.015 HW of the sheet at HZ 3.26 (3.34 would make them
# 1.017 / 1.59). The sheet's 0.047 HW collar gap is NOT reached by moving the
# head: the sheet chin is a sphere bottom flattened by 7 px (0.046-0.066 HW,
# head_front and full_front), i.e. the shell should end ~0.10 u above the
# ellipsoid bottom; that is the head sculptor's chin item (see PROPORTIONS.md).
# HZ 3.26 -> 3.18 with the collar gone (2026-09-23): the chin crest was .089
# above the body dome and that gap was filled by the neck stack. Now the ball
# rests in the dome with .009 of overlap, as the sheet draws it.
HZ = 3.18
# DEPTH was 0.66: from the front the lens-shaped shell read as a flat disc
# (2026-09-23 review). The reference side view is nearly a full sphere.
# DEPTH 1.02 -> 1.08 (proportions lock): full_side head depth 101.5 px vs
# front width 106.5 = 0.953; head_side 136.5/151 = 0.912 (its eye shows, so
# it is a ~21 deg three-quarter view). 1.08/1.16 = 0.931 sits between them.
# RX 1.16 stays the anchor (head width 2.32 = 1 HW); RZ 1.11 kept (head_front
# h/w 0.955; full_front 0.932; the lab render measures 0.948-0.950).
# RZ 1.11 -> 1.154 (2026-09-23 owner review): the head pass measured the frozen
# RZ as the reason the render head reads flat - silhouette h/w 0.898 against
# head_front 0.957 / full_front 0.927. band_edge() and the face plate arc are
# both derived from RZ, so the rim stays proportional; the chin, W and eyes sit
# in the lower half (RZ_LOW) and do not move. New crest ~1.179 -> h/w 0.940,
# the midpoint of the two sheet panels.
RX, RZ, DEPTH = 1.16, 1.154, 1.08


def face_y(x, z):
    return -DEPTH*math.sqrt(max(0.025, 1-(x/RX)**2-(z/RZ)**2)) - 0.026


def patch(name, boundary, center, mat, bulge=0, bone="Head", yfunc=face_y, zoffset=HZ, spacing=None):
    """A curved closed shell, not a camera-facing image plane."""
    # A W-shaped boundary is concave: a radial fan overlaps itself. Tessellate the
    # actual polygon first, subdivide, then project EVERY point onto the dome.
    unique=[]
    for p in boundary:
        if not unique or (Vector(p)-Vector(unique[-1])).length>1e-6:
            unique.append(p)
    if (Vector(unique[0])-Vector(unique[-1])).length<1e-6: unique.pop()
    area=sum(unique[i][0]*unique[(i+1)%len(unique)][1]-unique[(i+1)%len(unique)][0]*unique[i][1] for i in range(len(unique)))
    if area<0: unique.reverse()
    loop=[Vector((x,z)) for x,z in unique]
    edges=[(i,(i+1)%len(loop)) for i in range(len(loop))]
    boundary_count=len(loop)
    # Dense, nearly equilateral triangles avoid long thin fan triangles whose
    # normals made horizontal ridges under a chrome/studio reflection.
    step=(spacing or (.012 if bulge else .033))*_lod_for(name)
    xmin,xmax=min(p[0] for p in unique),max(p[0] for p in unique)
    zmin,zmax=min(p[1] for p in unique),max(p[1] for p in unique)
    def inside(x,z):
        hit=False
        for i,(ax,az) in enumerate(unique):
            bx,bz=unique[(i+1)%len(unique)]
            if (az>z)!=(bz>z) and x<(bx-ax)*(z-az)/(bz-az)+ax: hit=not hit
        return hit
    for j in range(1,math.ceil((zmax-zmin)/step)):
        z=zmin+j*step
        for i in range(1,math.ceil((xmax-xmin)/step)):
            x=xmin+(i+.5*(j%2))*step
            if inside(x,z):loop.append(Vector((x,z)))
    coords,_,faces,_,_,_=delaunay_2d_cdt(loop,edges,[list(range(boundary_count))],1,1e-6,False)
    obj=mesh(name,[(p.x,0,p.y+zoffset) for p in coords],faces,mat,bone)
    for v in obj.data.vertices:
        x,z=v.co.x,v.co.z-zoffset
        bump=0
        if bulge:
            # Rounded lens relief, zero at BOTH crescent borders (no cone apex).
            t=max(0,min(1,(x-center[0]+.225)/.45))
            arc=math.sqrt(max(0,1-(2*t-1)**2))
            lo=center[1]-.20+.115*arc**1.7
            hi=center[1]-.20+.310*arc
            u=max(0,min(1,(z-lo)/max(.001,hi-lo)))
            bump=bulge*math.sin(PI*u)*math.sin(PI*t)**.5
        v.co.y=yfunc(x,z)-bump
    obj.data.update()
    if yfunc(*center)>0:
        bpy.context.view_layer.objects.active=obj
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.flip_normals()
        bpy.ops.object.mode_set(mode="OBJECT")
    solid = obj.modifiers.new("Enamel thickness", "SOLIDIFY")
    solid.thickness = 0.022
    solid.offset = -1
    surface_faces=len(obj.data.polygons)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=solid.name)
    # Thin enamel walls must not drag surface normals sideways. Use the analytic
    # dome normal on the surface and flat wall normals at the closed perimeter.
    if not bulge:
        outward=1 if yfunc(*center)<0 else -1
        normals=[]
        for p in obj.data.polygons:
            for vi in p.vertices:
                if p.index>=2*surface_faces:
                    normals.append(tuple(p.normal));continue
                v=obj.data.vertices[vi].co;x,z=v.x,v.z-zoffset
                eps=.0003
                dx=(yfunc(x+eps,z)-yfunc(x-eps,z))/(2*eps)
                dz=(yfunc(x,z+eps)-yfunc(x,z-eps))/(2*eps)
                n=Vector((dx,-1,dz)).normalized()*outward
                if p.index>=surface_faces:n=-n
                normals.append(tuple(n))
        obj.data.normals_split_custom_set(normals)
    return obj


# Full solid parts calibrated against the source sheet, kept independent of hands.
sys.path.insert(0, str(ROOT / "scripts"))
import emma_sculpt_body
emma_sculpt_body.build_body(globals())


# Armature and rest-space bone definitions, also used to assign hand skin weights.
# Proportions lock: unchanged (HZ stays 3.26). Neck spans the collar (torso
# top 2.12 .. chin 2.15), the Head pivot is 1.04 below the head centre.
BONES = {
    "Root": ((0,0,.45),(0,0,.8),None),
    "Body": ((0,0,1.45),(0,0,2.08),"Root"),
    "Neck": ((0,0,2.05),(0,0,2.14),"Body"),
    "Head": ((0,0,2.14),(0,0,3.32),"Neck"),
}
HANDS = {}


# ARM_STYLE (2026-09-23, owner's call on the merged build: "ตั้งแต่ส่วนไหล่
# ไม่เหมือนสักอันเลย เอาออกทั้งหมด"). The shoulder balls, the cuff rings and both
# gloves all came out off-model against the concept sheet, so the whole arm set
# leaves the study until it is drawn again from the sheet. emma_sculpt_hands.py
# stays on disk with its measurements intact (six builds of fitting against
# full_front); this switch is the only thing that turns it off. The owner then
# sent a second reference - soft one-piece paddle arms, no fingers, no joint -
# so "flipper" is the built style and build_flipper_arms() at the foot of
# emma_sculpt_hands.py is what draws them.
# Turning it back on also means restoring, in the same commit: the wave/grip
# entries in CLIPS below, their two render shots, their buttons in index.html
# and viewer.js, and the glove/arm assertions in verify-emma-3d-lab.py. Those
# assertions now describe the armless model on purpose - a check that still
# asked for two gloves would go green on a model that simply does not build
# them, which is the 'passes because the machine is missing something' trap
# this project has paid for four times.
ARM_STYLE = "fin"   # "fin" | "flipper" | "glove" | None
ARMS = ARM_STYLE == "glove"
import emma_sculpt_hands
if ARM_STYLE == "glove":
    HAND_POSES = emma_sculpt_hands.build_arms(globals())
elif ARM_STYLE == "flipper":
    HAND_POSES = emma_sculpt_hands.build_flipper_arms(globals())
elif ARM_STYLE == "fin":
    HAND_POSES = emma_sculpt_hands.build_fin_arms(globals())
else:
    HAND_POSES = {}

bpy.ops.object.select_all(action="DESELECT")
arm_data=bpy.data.armatures.new("Emma skeleton")
rig=bpy.data.objects.new("Emma_Rig",arm_data)
bpy.context.collection.objects.link(rig)
bpy.context.view_layer.objects.active=rig
rig.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
for name,(head,tail,parent) in BONES.items():
    bone=arm_data.edit_bones.new(name)
    bone.head=head;bone.tail=tail
    if parent: bone.parent=arm_data.edit_bones[parent]
bpy.ops.object.mode_set(mode="OBJECT")
rig.show_in_front=True
rig.data.display_type="OCTAHEDRAL"


def seg_distance(p,a,b):
    d=b-a
    t=max(0,min(1,(p-a).dot(d)/d.length_squared))
    return (p-a-d*t).length,t


for obj in MODEL:
    obj.parent=rig
    mod=obj.modifiers.new("Emma skeleton deformation","ARMATURE")
    mod.object=rig
    # Export uses linear blend skinning, so validate the same deformation in Blender.
    mod.use_deform_preserve_volume=False
    if obj.name not in HANDS:
        group=obj.vertex_groups.new(name=DEFORM.get(obj.name,"Root"))
        group.add(list(range(len(obj.data.vertices))),1,"REPLACE")
        continue
    emma_sculpt_hands.skin_hand(obj, HANDS[obj.name])


def local_world_axis(name,axis):
    return rig.data.bones[name].matrix_local.to_quaternion().inverted()@Vector(axis)


def rotate(name, axis, angle):
    pb=rig.pose.bones[name]
    pb.rotation_mode="QUATERNION"
    pb.rotation_quaternion=Quaternion(local_world_axis(name,axis),angle)


PREVIOUS_QUATERNIONS = {}


# Rest curl .32 -> .20 (hands pass 2026-09-23): full_front rows 162-172 show
# the fingertips converging under the outer half of the glove (x 1.04-1.12)
# with a straight outer edge, a total curl of ~22 deg; .32 gave 41 deg and
# pulled the tips .15 u inboard of the sheet (see emma_sculpt_hands.py).
# Rest curl stays .20 (the hands pass measured it against full_front rows
# 146-162). Owner review tried .42 to smooth the three finger lobes into the
# sheet's comma: it did, but it also pulled the glove up into a stubby fist
# and cost .18 u of the .95 u hanging length the sheet draws. Silhouette
# first (CODEX_MODEL_SPEC), so the groove depth is left as the open item.
def pose(frame, wave=0, curl=.20, yaw=0, pitch=0, palm_turn=0):
    for pb in rig.pose.bones:
        pb.rotation_mode="QUATERNION"
        pb.rotation_quaternion=Quaternion()
        pb.location=(0,0,0)
    rotate("Neck",(0,0,1),yaw)
    rotate("Head",(1,0,0),pitch)
    if ARMS:
        emma_sculpt_hands.apply_hand_pose(globals(), HAND_POSES, wave=wave,
                                           curl=curl, palm_turn=palm_turn)
    for pb in rig.pose.bones:
        # q and -q are the same orientation, but component F-curves between
        # opposite signs cross zero and can flip a wrist through the head.
        q = pb.rotation_quaternion.normalized()
        previous = PREVIOUS_QUATERNIONS.get(pb.name)
        if previous is not None and previous.dot(q) < 0:
            q.negate()
        pb.rotation_quaternion = q
        PREVIOUS_QUATERNIONS[pb.name] = q.copy()
        pb.keyframe_insert("rotation_quaternion",frame=frame,group=pb.name)
        pb.keyframe_insert("location",frame=frame,group=pb.name)


# Bake related poses on ONE skeleton. Clips begin/end with the same relaxed pose.
CLIPS={
    "idle": [(1,{}),(45,{"pitch":.025}),(90,{})],
    "look": [(1,{}),(35,{"yaw":-.6}),(75,{"yaw":.6}),(105,{"pitch":-.18}),(140,{})],
}
if ARMS:
    # Both clips are hand motion only; with ARMS off they would animate nothing
    # and leave two dead buttons in the viewer.
    CLIPS.update({
        "wave": [(1,{}),(18,{"wave":.18}),(40,{"wave":1}),(52,{"wave":.91}),
                 (64,{"wave":1}),(76,{"wave":.91}),(90,{"wave":1}),(125,{})],
        "grip": [(1,{}),(30,{"curl":.06,"palm_turn":1}),(62,{"curl":1.35,"palm_turn":1}),
                 (88,{"curl":1.35,"palm_turn":1}),(118,{"curl":.06,"palm_turn":1}),(150,{})],
    })
for clip,keys in CLIPS.items():
    rig.animation_data_clear()
    PREVIOUS_QUATERNIONS.clear()
    for frame,kwargs in keys: pose(frame,**kwargs)
    action=rig.animation_data.action
    action.name=clip
    action.use_fake_user=True
    # NLA strips preserve separate named actions in glTF and the editable blend.
    track=rig.animation_data.nla_tracks.new();track.name=clip
    strip=track.strips.new(clip,1,action);strip.action_frame_end=keys[-1][0]
    track.mute=True

# Rebuild NLA tracks after each action creation (animation_data_clear removes old tracks).
rig.animation_data_clear();rig.animation_data_create()
for clip in CLIPS:
    action=bpy.data.actions[clip]
    track=rig.animation_data.nla_tracks.new();track.name=clip
    strip=track.strips.new(clip,1,action)
    track.mute=False
    strip.blend_type="REPLACE"

# Hover ring is a separate stationary object, not parented to the moving torso.
# Proportions lock (full_front/full_side/full_back x4): ring bright core
# 56.5-59.75 px wide / 106.5 = 0.545 HW -> outer dia 1.264 u -> major r .585
# (was .575); tube: rear band 3.5-5.75 px = 0.033-0.054 HW, mean 0.043 HW =
# .10 u diameter -> minor r .052 (was .046 = 0.040 HW; within tolerance, set
# to the measured value).
# Height: the sheet draws the ring as an ellipse seen from ~19 deg above while
# the body is straight-on, so the panels give two answers: ellipse top 6.5 px
# below the tip (0.061 HW -> centre z .60) or ellipse centre 15.8 px below
# (0.148 HW -> z .45); the hero shows a larger gap still. z .50 (was .40) is
# the midpoint of the two ortho readings; flagged for the owner in PROPORTIONS.md.
base=ring("Hover / luminous ring",(0,0,.50),(0,0,1),.585,.052,LIGHT)
MODEL.remove(base)
base.parent=None


def point_at(obj, target):
    obj.rotation_euler=(Vector(target)-obj.location).to_track_quat("-Z","Y").to_euler()


scene=bpy.context.scene
scene.render.engine="CYCLES"
scene.cycles.samples=40
scene.cycles.use_denoising=True
scene.render.resolution_x=800;scene.render.resolution_y=900;scene.render.resolution_percentage=100
scene.render.image_settings.file_format="PNG"
# World (PART LOOK): a neutral graded studio instead of flat (.075,.09,.115) x .4.
# Measured on the sheet's CHROME sphere (materials panel, 8 vertical bands top->bottom
# 23 54 153 100 140 120 25 68). A front-lit sphere at band height s mirrors the direction
# with z = 2s*sqrt(1-s^2), so those bands say: dark straight up (23-54), bright around
# +45 deg (153), mid at the horizon (100), bright again around -30 deg (140/120), dark
# straight down (25) with a mid rim (68). Stops are the sheet values as linear radiance
# (sRGB^2.2 / .96 chrome albedo) at position (z+1)/2; the area lights add the clipped
# highlights (sheet chrome 17 % > 240). Probed: TexCoord Generated on the world is the
# view direction (z 0 rendered as linear .5 at a mirror ball's centre).
world=scene.world;world.use_nodes=True;wn=world.node_tree
for node in list(wn.nodes):wn.nodes.remove(node)
w_coord=wn.nodes.new("ShaderNodeTexCoord");w_xyz=wn.nodes.new("ShaderNodeSeparateXYZ")
w_map=wn.nodes.new("ShaderNodeMapRange");w_map.inputs["From Min"].default_value=-1;w_map.inputs["From Max"].default_value=1
w_ramp=wn.nodes.new("ShaderNodeValToRGB");w_bg=wn.nodes.new("ShaderNodeBackground");w_out=wn.nodes.new("ShaderNodeOutputWorld")
WORLD_STOPS=[(0.0,.02),(.075,.06),(.15,.20),(.375,.28),(.5,.13),(.625,.13),(.85,.34),(.925,.05),(1.0,.012)]
w_ramp.color_ramp.interpolation="EASE"
w_ramp.color_ramp.elements[0].position=WORLD_STOPS[0][0];w_ramp.color_ramp.elements[0].color=(WORLD_STOPS[0][1],)*3+(1,)
w_ramp.color_ramp.elements[1].position=WORLD_STOPS[-1][0];w_ramp.color_ramp.elements[1].color=(WORLD_STOPS[-1][1],)*3+(1,)
for pos,val in WORLD_STOPS[1:-1]:
    w_ramp.color_ramp.elements.new(pos).color=(val,val,val,1)
w_bg.inputs["Strength"].default_value=1.0
for a,b in [(w_coord.outputs["Generated"],w_xyz.inputs["Vector"]),(w_xyz.outputs["Z"],w_map.inputs["Value"]),
            (w_map.outputs["Result"],w_ramp.inputs["Fac"]),(w_ramp.outputs["Color"],w_bg.inputs["Color"]),
            (w_bg.outputs["Background"],w_out.inputs["Surface"])]:
    wn.links.new(a,b)
# View transform (PART LOOK): Standard instead of AgX. The sheet is a clipped, high-contrast
# render (WHITE BODY 23 % > 240, LIGHT flat 254, CHROME 17 % > 240); AgX compressed the
# pearl to a flat 230-237 and the emissive ring to 240 (sheet 253). Alpha is unaffected.
scene.view_settings.view_transform="Standard"
scene.render.film_transparent=True
# Compositor bloom (PART LOOK): sheet LIGHT sphere halo 101 / 52 / 29 at 1.05-1.15 /
# 1.2-1.4 / 1.5-1.8 r over bg 23; hover ring halo 214 158 127 101 81 66 56 49 at 2 px
# steps below a 12 px tube. Glare BLOOM adds that to the RGB; the alpha is put back from
# the render layer so front/side/back/wave/grip keep the exact silhouette the compare
# tool reads. The look-* renders composite the same frame over the sheet backdrop
# (hero corners 22,25,26 sRGB).
scene.use_nodes=True;ctree=scene.node_tree
for node in list(ctree.nodes):ctree.nodes.remove(node)
c_layers=ctree.nodes.new("CompositorNodeRLayers")
c_glare=ctree.nodes.new("CompositorNodeGlare");c_glare.glare_type="BLOOM";c_glare.quality="HIGH"
print("GLARE_DEFAULTS",{s.name:(s.default_value if hasattr(s,"default_value") else None) for s in c_glare.inputs if s.type!="RGBA"},flush=True)
# Build 1 (Size .30, Strength .30) read 163 145 133 123 116 109 103 99 94 90 at 2 px steps
# under the ring bar vs the sheet's 158 127 101 81 66 56 49 43 40 38: right at the edge,
# tail twice too long -> Size .12, Strength .45 (build 2: 187 164 148 135 126 117 109 104
# 97 92 87 82 78 74 70 66, first pixels right, tail still 1.6x) -> Size .07, Strength .50.
c_glare.inputs["Threshold"].default_value=1.0;c_glare.inputs["Size"].default_value=.07;c_glare.inputs["Strength"].default_value=.50
c_alpha=ctree.nodes.new("CompositorNodeSetAlpha");c_alpha.mode="REPLACE_ALPHA"
c_backdrop=ctree.nodes.new("CompositorNodeRGB");c_backdrop.outputs[0].default_value=(.0075,.0093,.0102,1)
c_over=ctree.nodes.new("CompositorNodeAlphaOver")
c_out=ctree.nodes.new("CompositorNodeComposite")
for a,b in [(c_layers.outputs["Image"],c_glare.inputs["Image"]),(c_glare.outputs["Image"],c_alpha.inputs["Image"]),
            (c_layers.outputs["Alpha"],c_alpha.inputs["Alpha"]),(c_backdrop.outputs[0],c_over.inputs[1]),
            (c_glare.outputs["Image"],c_over.inputs[2])]:
    ctree.links.new(a,b)
def compositor(mode):
    for link in list(c_out.inputs["Image"].links):ctree.links.remove(link)
    ctree.links.new((c_over if mode=="look" else c_alpha).outputs["Image"],c_out.inputs["Image"])
compositor("transparent")
scene.render.fps=30
# Review cameras are LEVEL true elevations (proportions lock): camera z equals
# the aim z, at the vertical centre of head top (HZ+RZ+band .03 = 4.40) and
# ring bottom (.50-.052 = .448) -> 2.43. The old (0,-9,3.25)->(0,0,2.4) camera
# looked down 5.4 deg, which shifted the torso tip and made the ring an ellipse
# in what the compare tool treats as an orthographic front view.
# ortho_scale 5.3 stays: 900 px = 5.3 u = 169.8 px/u (the number the measuring
# scripts use); the character is 3.99 u tall, so 0.65 u of margin each end.
CAMERA_Z = 2.43
bpy.ops.object.camera_add(location=(0,-9,CAMERA_Z))
cam=bpy.context.object;cam.name="Review camera"
cam.data.type="ORTHO";cam.data.ortho_scale=5.3
point_at(cam,(0,0,CAMERA_Z));scene.camera=cam
# Lights (PART LOOK): key upper-left-front dominant, right strip down to an accent. Sheet
# WHITE BODY highlight centroid (-0.19,-0.38) r = upper-left; base build read (+0.50,-0.33)
# = upper-right because the 1000 W strip on the right out-powered the 800 W key. Total
# 3150 -> 580 W: AgX had been hiding a ~4x overexposure (its 236 on the pearl is linear
# ~4); build 1 at 1870 W under Standard clipped pearl, balls and gloves to a flat 255
# (pearl p05 = 255). Pearl target p50 ~225 / p05 ~162 sRGB = linear .75 / .36, with the
# graded world carrying ~.17 of ambient. Build 2 read pearl p50 222 / 17.6 % > 240 but the
# head cap's top band 215 < its bottom 231 (sheet top 244 > 200 mid): key raised from
# 31 to 43 deg elevation above the head so the crown catches it. Low bounce 38 -> 70:
# gloves read p50 188 (sheet hero gloves are white).
for name,loc,power,size,sy in [("Key softbox",(-3,-3.5,7.5),250,4,5),
                               ("Right strip",(3.5,-2,3.5),60,1.0,4),
                               ("Rim softbox",(-1,3,5),250,3,3),
                               ("Low bounce",(0,-3,.5),70,2,1)]:
    bpy.ops.object.light_add(type="AREA",location=loc)
    light=bpy.context.object;light.name=name;light.data.energy=power
    light.data.shape="RECTANGLE";light.data.size=size;light.data.size_y=sy
    point_at(light,(0,0,2.5))

# Store rig information and an explicit draft designation inside the source file.
rig["design_status"]="3D study / pending visual approval"
rig["source"]="Procedural geometry authored for Emma; no Free3D asset used"
rig["hand_geometry"]="One welded mesh per glove; three fingers plus thumb, 22 deforming finger links per hand"
rig["axis"]="Blender Z up; front is -Y"

bpy.ops.object.select_all(action="DESELECT")
for obj in MODEL+[rig,base]: obj.select_set(True)
bpy.context.view_layer.objects.active=rig
scene.frame_set(1)
bpy.ops.export_scene.gltf(filepath=str(OUT/"emma.glb"),export_format="GLB",use_selection=True,
                          export_animations=True,export_animation_mode="NLA_TRACKS",
                          export_force_sampling=True,export_frame_range=False,
                          export_skins=True,export_def_bones=True,export_materials="EXPORT",
                          export_cameras=False,export_lights=False)
for track in rig.animation_data.nla_tracks: track.mute=True
rig.animation_data.action=bpy.data.actions["idle"]
scene.frame_set(1)
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/"emma.blend"),compress=True)

# Geometry and normalized skin-weight audit in bind space.
from collections import Counter
audit={"blender":bpy.app.version_string,"bones":len(BONES),"clips":list(CLIPS),"meshes":len(MODEL)+1,
       "vertices":sum(len(o.data.vertices) for o in MODEL+[base]),"hands":{}}
for name in HANDS:
    obj=bpy.data.objects[name]
    edges=Counter(tuple(sorted((p.vertices[i],p.vertices[(i+1)%len(p.vertices)])))
                  for p in obj.data.polygons for i in range(len(p.vertices)))
    adjacency=[set() for _ in obj.data.vertices]
    for a,b in edges:adjacency[a].add(b);adjacency[b].add(a)
    unseen=set(range(len(adjacency)));components=0
    while unseen:
        components+=1;pending=[unseen.pop()]
        while pending:
            for neighbor in adjacency[pending.pop()]:
                if neighbor in unseen:unseen.remove(neighbor);pending.append(neighbor)
    audit["hands"][name]={"vertices":len(obj.data.vertices),"connected_components":components,"nonmanifold_edges":sum(n!=2 for n in edges.values()),
        "max_weight_error":max(abs(sum(g.weight for g in v.groups)-1) for v in obj.data.vertices)}
    assert audit["hands"][name]["nonmanifold_edges"]==0
    assert components==1
    assert audit["hands"][name]["max_weight_error"]<1e-4
(OUT/"model-audit.json").write_text(json.dumps(audit,indent=2),encoding="utf-8")
print("EMMA_AUDIT",json.dumps(audit),flush=True)

if "--render" in sys.argv:
    # front/side/back/wave: level elevations (aim z = camera z). grip keeps its
    # slightly raised three-quarter view: it is a hand close-up, not an elevation.
    shots=[("front",(0,-9,CAMERA_Z),(0,0,CAMERA_Z),"idle",1),
           ("side",(9,0,CAMERA_Z),(0,0,CAMERA_Z),"idle",1),
           ("back",(0,9,CAMERA_Z),(0,0,CAMERA_Z),"idle",1)]
    if ARMS:
        shots += [("wave",(0,-9,CAMERA_Z),(0,0,CAMERA_Z),"wave",64),
                  ("grip",(2,-8,3.0),(0,0,2.4),"grip",70)]
    for label,loc,aim,clip,frame in shots:
        rig.animation_data.action=bpy.data.actions[clip]
        scene.frame_set(frame)
        cam.location=loc;point_at(cam,aim)
        scene.render.filepath=str(BUILD/(label+".png"))
        bpy.ops.render.render(write_still=True)
    # Look renders (PART LOOK): the same idle frame over the sheet's dark backdrop with the
    # bloom visible, for judging materials next to the sheet. look-front = the review
    # camera; look-hero = the viewer's reference camera (three.js (-1.8,2.65,9.65) ->
    # (0,2.35,0), 33 deg vertical fov) so Blender and browser can be compared like with
    # like. Extra files only; the five compare-tool renders above are untouched.
    compositor("look")
    for label,loc,aim,persp in [("look-front",(0,-9,CAMERA_Z),(0,0,CAMERA_Z),False),
                                ("look-hero",(-1.8,-9.65,2.65),(0,0,2.35),True)]:
        rig.animation_data.action=bpy.data.actions["idle"];scene.frame_set(1)
        cam.data.type="PERSP" if persp else "ORTHO"
        if persp:cam.data.sensor_fit="VERTICAL";cam.data.angle_y=math.radians(33)
        cam.location=loc;point_at(cam,aim)
        scene.render.filepath=str(BUILD/(label+".png"))
        bpy.ops.render.render(write_still=True)
    cam.data.type="ORTHO";compositor("transparent")
