"""PHASE A-D only: source-contour head, no old model, rig, clips or export.

Run after prepare-emma-head-reference.py:
  blender --background --python scripts/build-emma-head-match.py
Reference-driven front silhouette is lofted into a solid. Unknown depth is
explicitly provisional. Modifiers stay editable until visual approval.
"""
from pathlib import Path
import json
import math
import sys

import bpy
from mathutils import Vector
from mathutils.geometry import delaunay_2d_cdt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from emma_head_reference_data import VIEWS
OUT = ROOT/'models/emma/head-match'
VALIDATION = OUT/'validation'
VALIDATION.mkdir(parents=True, exist_ok=True)
measurements = json.loads((OUT/'measurements.json').read_text(encoding='utf-8'))
N = measurements['comparison']['size']
SCALE = measurements['comparison']['ortho_scale']
DEPTH = measurements['construction_assumptions']['head_depth']

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
for item in list(bpy.data.collections):
    bpy.data.collections.remove(item)
for item in list(bpy.data.materials):
    bpy.data.materials.remove(item)
for item in list(bpy.data.actions):
    bpy.data.actions.remove(item)

def collection(name, parent):
    result=bpy.data.collections.new(name)
    parent.children.link(result)
    return result

EMMA=collection('EMMA',bpy.context.scene.collection)
GEO=collection('GEO',EMMA)
HEAD=collection('HEAD',GEO)
FACE=collection('FACE',GEO)
for name in ('TORSO','ARM_L','ARM_R','HANDS','HOVER'):
    collection(name,GEO)
for name in ('MATERIALS','RIG'):
    collection(name,EMMA)
REF=collection('REF_IMAGES',EMMA)
CAMERAS=collection('CAMERAS',EMMA)
LIGHTING=collection('LIGHTING',EMMA)

def material(name,color,metallic=0,roughness=.3,emission=0):
    m=bpy.data.materials.new(name)
    m.diffuse_color=(*color,1)
    m.use_nodes=True
    p=m.node_tree.nodes.get('Principled BSDF')
    p.inputs['Base Color'].default_value=(*color,1)
    p.inputs['Metallic'].default_value=metallic
    p.inputs['Roughness'].default_value=roughness
    p.inputs['Emission Color'].default_value=(*color,1)
    p.inputs['Emission Strength'].default_value=emission
    return m

WHITE=material('MAT_WHITE_GLOSS',(.8,.8,.8),0,.22)
CHROME=material('MAT_CHROME',(.65,.65,.65),1,.12)
BLACK=material('MAT_BLACK',(.008,.008,.008),0,.28)
EMISSION=material('MAT_EMISSION_WHITE',(1,1,1),0,.3,2)

def mesh(name,verts,faces,mat,col):
    data=bpy.data.meshes.new(name)
    data.from_pydata(verts,[],faces)
    data.update()
    obj=bpy.data.objects.new(name,data)
    col.objects.link(obj)
    data.materials.append(mat)
    for p in data.polygons:p.use_smooth=True
    bpy.context.view_layer.objects.active=obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.select_set(False)
    return obj

def contour(view,key):
    d=VIEWS[view];cx,cy=d['center'];w=d['width']
    return [((x-cx)/w,(cy-y)/w) for x,y in d['contours'][key]]

def contains(x,z,poly):
    inside=False
    for (ax,az),(bx,bz) in zip(poly,poly[1:]+poly[:1]):
        if (az>z)!=(bz>z) and x<(bx-ax)*(z-az)/(bz-az)+ax:inside=not inside
    return inside

def ray_radius(poly,angle,center):
    dx,dz=math.cos(angle),math.sin(angle)
    hits=[]
    for a,b in zip(poly,poly[1:]+poly[:1]):
        ax,az=a[0]-center[0],a[1]-center[1]
        ex,ez=b[0]-a[0],b[1]-a[1]
        den=dx*ez-dz*ex
        if abs(den)<1e-10:continue
        r=(ax*ez-az*ex)/den
        t=(ax*dz-az*dx)/den
        if r>0 and -.00001<=t<=1.00001:hits.append(r)
    if not hits:raise ValueError(f'Contour is not radial about {center}: angle {angle}')
    return max(hits)

def symmetric(poly,center=(0,0),count=256):
    # This preserves the measured contour in metadata while making the model
    # symmetric. The comparison is ALWAYS against the original unsymmetrized mask.
    result=[]
    for i in range(count):
        a=2*math.pi*i/count
        r=(ray_radius(poly,a,center)+ray_radius(poly,math.pi-a,center))/2
        result.append((center[0]+r*math.cos(a),center[1]+r*math.sin(a)))
    return result

front_outline=symmetric(contour('front','head'))
back_outline=symmetric(contour('back','head'))
# One orthographic solid has the same outline from front and back. The artwork
# has different aspect ratios; share the residual between the two measured
# contours instead of altering either reference image or camera scale.
outer=[((a[0]+b[0])/2,(a[1]+b[1])/2) for a,b in zip(front_outline,back_outline)]
zmin=min(p[1] for p in outer);zmax=max(p[1] for p in outer)

def width_at(z):
    intersections=[]
    for (ax,az),(bx,bz) in zip(outer,outer[1:]+outer[:1]):
        if min(az,bz)<=z<=max(az,bz) and abs(bz-az)>1e-10:
            intersections.append(ax+(bx-ax)*(z-az)/(bz-az))
    return max(.00001,max(intersections,default=0))

def depth_at(z):
    # Loft width comes from measured pixels. Depth is one declared provisional
    # variable, never silently presented as a recovered side measurement.
    return DEPTH/2*(max(0,width_at(z))/.5)**.94

def surface(x,z,back=False):
    w=width_at(z)
    y=depth_at(z)*math.sqrt(max(.000001,1-(x/w)**2))
    return y if back else -y

verts=[(0,0,zmin)]
rings=144;segments=160
for j in range(1,rings):
    z=zmin+(zmax-zmin)*j/rings
    w=width_at(z);d=depth_at(z)
    for i in range(segments):
        a=2*math.pi*i/segments
        verts.append((w*math.cos(a),d*math.sin(a),z))
top=len(verts);verts.append((0,0,zmax))
faces=[]
for i in range(segments):faces.append((0,1+(i+1)%segments,1+i))
for j in range(rings-2):
    for i in range(segments):
        a=1+j*segments+i;b=1+j*segments+(i+1)%segments
        faces.append((a,b,b+segments,a+segments))
for i in range(segments):faces.append((top,1+(rings-2)*segments+i,1+(rings-2)*segments+(i+1)%segments))
shell=mesh('HEAD_OUTER_CHROME',verts,faces,CHROME,HEAD)
shell['construction']='Horizontal loft from equal-weight measured front/back radial contours; not a UV sphere.'
shell['depth_status']='PROVISIONAL: no calibrated side reference'

def patch(name,poly,mat,col,back=False,relief=0,offset=.002):
    # Constrained triangulation respects concave W edges. It does not draw a fan
    # across the empty areas or turn the chrome signature into a tube.
    unique=[]
    for point in poly:
        if not unique or (Vector(point)-Vector(unique[-1])).length>1e-6:unique.append(point)
    if (Vector(unique[0])-Vector(unique[-1])).length<1e-6:unique.pop()
    edges=[(i,(i+1)%len(unique)) for i in range(len(unique))]
    points=[Vector(p) for p in unique]
    count=len(points);step=.009
    xmin,xmax=min(p[0] for p in unique),max(p[0] for p in unique)
    low,high=min(p[1] for p in unique),max(p[1] for p in unique)
    for j in range(1,math.ceil((high-low)/step)):
        z=low+j*step
        for i in range(1,math.ceil((xmax-xmin)/step)):
            x=xmin+(i+.5*(j%2))*step
            if contains(x,z,unique):points.append(Vector((x,z)))
    coords,_,triangles,_,_,_=delaunay_2d_cdt(points,edges,[list(range(count))],1,1e-7,False)
    def boundary_distance(p):
        best=1e10
        for a,b in zip(unique,unique[1:]+unique[:1]):
            a,b=Vector(a),Vector(b);v=b-a
            t=max(0,min(1,(p-a).dot(v)/max(1e-12,v.length_squared)))
            best=min(best,(p-a-v*t).length)
        return best
    vertices=[]
    for p in coords:
        pad=relief*math.sin(min(1,boundary_distance(p)/.035)*math.pi/2) if relief else 0
        y=surface(p.x,p.y,back)+(offset+pad)*(1 if back else -1)
        vertices.append((p.x,y,p.y))
    obj=mesh(name,vertices,[tuple(reversed(t)) for t in triangles] if back else triangles,mat,col)
    # Explicit analytical normals on the dome; solidify stays editable.
    normals=[]
    for v in obj.data.vertices:
        x,z=v.co.x,v.co.z;e=.0001
        dx=(surface(x+e,z,back)-surface(x-e,z,back))/(2*e)
        dz=(surface(x,z+e,back)-surface(x,z-e,back))/(2*e)
        normal=Vector((dx,-1,dz)).normalized()*(-1 if back else 1)
        normals.append(tuple(normal))
    if not relief:obj.data.normals_split_custom_set_from_vertices(normals)
    solid=obj.modifiers.new('Editable panel thickness','SOLIDIFY')
    solid.thickness=.004;solid.offset=-1
    obj['source_view']='back' if back else 'front'
    obj['source_contour']='Raw source pixels recorded in measurements.json'
    return obj

# The narrow concave band need not be star-shaped: reflect its raw vertices,
# align left/right by horizontal cross-sections in a separate helper below.
def symmetric_rows(poly,step=.003):
    left=-min(x for x,z in poly);right=max(x for x,z in poly)
    width=(left+right)/2
    count=max(64,math.ceil(width/step))
    xs=[width*i/count for i in range(count+1)]
    lower=[];upper=[]
    for x in [-a for a in xs[:0:-1]]+xs:
        pairs=[]
        for sign in (-1,1):
            # Pair equal fractions of each measured half-width. Abruptly
            # dropping one side at its shorter endpoint creates border spikes.
            cross=[];xx=min(1-1e-8,abs(x)/width)*(right if sign>0 else -left)
            for (ax,az),(bx,bz) in zip(poly,poly[1:]+poly[:1]):
                if min(ax,bx)<=xx<=max(ax,bx) and abs(bx-ax)>1e-8:
                    cross.append(az+(bz-az)*(xx-ax)/(bx-ax))
            if cross:pairs.append((min(cross),max(cross)))
        if pairs:
            lower.append((x,sum(p[0] for p in pairs)/len(pairs)))
            upper.append((x,sum(p[1] for p in pairs)/len(pairs)))
    return upper+lower[::-1]
# Use the same cross-section operation at all shared boundaries. Mixing radial
# face fitting with column-based wave fitting used to overlap their edges.
face=symmetric_rows(contour('front','face'))
chin=symmetric_rows(contour('front','chin'))
wave=symmetric_rows(contour('front','chrome_wave'))
def section(poly,x):
    hits=[]
    for (ax,az),(bx,bz) in zip(poly,poly[1:]+poly[:1]):
        if min(ax,bx)-1e-8<=x<=max(ax,bx)+1e-8 and abs(bx-ax)>1e-8:
            hits.append(az+(bz-az)*(x-ax)/(bx-ax))
    return (min(hits),max(hits)) if hits else None

def shared_wave(face,chin,wave):
    # Reuse every white-boundary vertex: independent symmetry operations have
    # different half-widths and otherwise leave a visible ~2-source-pixel seam.
    fw=max(x for x,z in face);cw=max(x for x,z in chin);ww=max(x for x,z in wave)
    xs=sorted(set(x for poly in (face,chin,wave) for x,z in poly if abs(x)<=ww))
    upper=[];lower=[]
    fdelta=section(face,fw-1e-7)[0]-section(wave,fw-1e-7)[1] if fw<ww else 0
    cdelta=section(chin,cw-1e-7)[1]-section(wave,cw-1e-7)[0] if cw<ww else 0
    for x in xs:
        wr=section(wave,x)
        if wr is None:continue
        low,high=wr
        fr=section(face,x);cr=section(chin,x)
        if fr:high=fr[0]
        elif abs(x)>fw:high+=fdelta*(ww-abs(x))/max(1e-8,ww-fw)
        if cr:low=cr[1]
        elif abs(x)>cw:low+=cdelta*(ww-abs(x))/max(1e-8,ww-cw)
        upper.append((x,high));lower.append((x,low))
    return upper+lower[::-1]
wave=shared_wave(face,chin,wave)
patch('HEAD_SHELL_WHITE',face,WHITE,HEAD,offset=.008)
patch('FACE_CHIN_WHITE',chin,WHITE,FACE,relief=.01,offset=.008)
patch('FACE_LOWER_CHROME',wave,CHROME,FACE,relief=.003,offset=.004)
rear=symmetric(contour('back','back_enamel'))
patch('HEAD_SHELL_WHITE_BACK',rear,WHITE,HEAD,back=True,offset=.008)
for key,name in [('eye_l','EYE_HAPPY_L'),('eye_r','EYE_HAPPY_R')]:
    # The source overrides generic symmetry: recentering the eyes on the outer
    # dome displaced both by ~5 native pixels. Keep the directly measured
    # feature projections; flag their asymmetry for review, not as approved CAD.
    eye=patch(name,contour('front',key),BLACK,FACE,relief=.012,offset=.013)
    eye['symmetry_status']='Measured projected source placement; slightly turned illustration, not verified orthographic symmetry'

scene=bpy.context.scene
scene.render.engine='CYCLES';scene.cycles.samples=24
scene.cycles.use_denoising=True
scene.render.resolution_x=N;scene.render.resolution_y=N;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA'
scene.render.film_transparent=True
scene.world.use_nodes=True
scene.world.node_tree.nodes['Background'].inputs[0].default_value=(.35,.35,.35,1)
scene.world.node_tree.nodes['Background'].inputs[1].default_value=.5
scene.view_settings.view_transform='Standard'
scene.view_settings.look='Medium High Contrast' if False else 'None'
scene.view_settings.exposure=0;scene.view_settings.gamma=1
scene['phase']='A-D / HEAD ONLY'
scene['geometry_status']='DRAFT / awaiting visual review; SIDE depth not verified'
scene['no_rig_or_animation']=True

def aim(obj):obj.rotation_euler=(-obj.location).to_track_quat('-Z','Y').to_euler()
locations={'front':(0,-4,0),'side':(4,0,0),'back':(0,4,0),
           'quarter':(4*math.sin(math.radians(55)),-4*math.cos(math.radians(55)),0)}
cameras={}
for view,location in locations.items():
    data=bpy.data.cameras.new('CAM_COMPARE_'+view.upper());data.type='ORTHO';data.ortho_scale=SCALE
    camera=bpy.data.objects.new(data.name,data);CAMERAS.objects.link(camera)
    camera.location=location;aim(camera);cameras[view]=camera
    if view=='quarter':camera['calibration']='Uncalibrated illustration angle; not an acceptance camera'
    if view in ('front','back','quarter'):
        ref=bpy.data.objects.new('REF_'+view.upper(),None);REF.objects.link(ref)
        ref.empty_display_type='IMAGE'
        ref.data=bpy.data.images.load(str(OUT/'references'/f'{view}.png'),check_existing=True)
        ref.data.pack();ref.empty_display_size=SCALE
        ref.color=(1,1,1,.5);ref.empty_image_depth='BACK';ref.hide_render=True
        ref.rotation_euler=camera.rotation_euler
        ref.location=-Vector(location).normalized()*.65
        ref['alignment']='Uniform vertical-extent scale; uncalibrated' if view=='quarter' else 'Uniform source head-width scale, no anisotropic stretching'
placeholder=bpy.data.objects.new('REF_SIDE',None);REF.objects.link(placeholder)
placeholder.empty_display_type='PLAIN_AXES';placeholder.hide_render=True
placeholder['status']='MISSING calibrated 90-degree side; REF_QUARTER is not a substitute'
for name,loc,power,size in [('Clay key',(-2,-3,3),110,4),('Clay fill',(2,1,2),85,3)]:
    data=bpy.data.lights.new(name,'AREA');data.energy=power;data.shape='DISK';data.size=size
    obj=bpy.data.objects.new(name,data);LIGHTING.objects.link(obj);obj.location=loc;aim(obj)
scene.camera=cameras['front']
# Save physical base material names, references and editable geometry first.
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'EMMA_HEAD_MATCH.blend'),compress=True)

# Neutral clay comparison: suppress mirror highlights rather than polishing
# lighting to camouflage silhouette errors. Exactly four base materials remain.
saved={}
for m,c in [(WHITE,(.68,.68,.68,1)),(CHROME,(.18,.18,.18,1)),(BLACK,(.025,.025,.025,1))]:
    p=m.node_tree.nodes.get('Principled BSDF')
    saved[m.name]=(tuple(p.inputs['Base Color'].default_value),p.inputs['Metallic'].default_value,p.inputs['Roughness'].default_value)
    p.inputs['Base Color'].default_value=c;p.inputs['Metallic'].default_value=0;p.inputs['Roughness'].default_value=.7
for index,(view,camera) in enumerate(cameras.items(),1):
    scene.camera=camera;scene.render.filepath=str(VALIDATION/f'{index:02}_{view}.png')
    bpy.ops.render.render(write_still=True)
objects=[o for o in bpy.data.objects if o.type=='MESH']
scene.camera=cameras['front']
for label,name in [('face','HEAD_SHELL_WHITE'),('chin','FACE_CHIN_WHITE'),
                   ('chrome_wave','FACE_LOWER_CHROME'),('eye_l','EYE_HAPPY_L'),('eye_r','EYE_HAPPY_R')]:
    for obj in objects:obj.hide_render=obj.name!=name
    scene.render.filepath=str(VALIDATION/f'front_{label}_geometry.png')
    bpy.ops.render.render(write_still=True)
for obj in objects:obj.hide_render=False
for name,(color,metal,rough) in saved.items():
    p=bpy.data.materials[name].node_tree.nodes.get('Principled BSDF')
    p.inputs['Base Color'].default_value=color;p.inputs['Metallic'].default_value=metal;p.inputs['Roughness'].default_value=rough

meshes=[o for o in bpy.data.objects if o.type=='MESH']
audit={'phase':'A-D','status':'draft','meshes':[o.name for o in meshes],
       'vertices':sum(len(o.data.vertices) for o in meshes),
       'armatures':sum(o.type=='ARMATURE' for o in bpy.data.objects),'actions':len(bpy.data.actions),
       'materials':sorted(m.name for m in bpy.data.materials),
       'cameras':{k:{'type':v.data.type,'scale':v.data.ortho_scale} for k,v in cameras.items()},
       'front_bounds':[min(v.co.x for v in shell.data.vertices),max(v.co.x for v in shell.data.vertices),
                       min(v.co.z for v in shell.data.vertices),max(v.co.z for v in shell.data.vertices)],
       'provisional_depth':DEPTH,
       'unapplied_modifiers':{o.name:[m.type for m in o.modifiers] for o in meshes},
       'side_acceptance':'unverified: missing calibrated side reference'}
assert audit['armatures']==0 and audit['actions']==0
assert len(audit['materials'])==4
(OUT/'model-audit.json').write_text(json.dumps(audit,indent=2)+'\n',encoding='utf-8')
print('HEAD_MATCH_AUDIT',json.dumps(audit),flush=True)
