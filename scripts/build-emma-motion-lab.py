"""Author the isolated Emma motion study; never writes production Emma files.

Uses the installed Rive CLI schema, real bones and short articulated graphite arms.
Original Emma art is read-only; alternative hand sprites belong to this lab only.
"""

from __future__ import annotations

import math
import base64
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
from emma_lab_geometry import dome_point, glove_point, FINGERS

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "client/rive/emma-motion-lab"
SCALE, CX, CY = 0.86, 330, 295
R = ET.Element("Rive", version="1", kind="fragment")


def el(parent, tag, **attrs):
    return ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})


def node(parent, name, ident=None, **attrs):
    if ident:
        attrs["id"] = ident
    return el(parent, "Node", name=name, **attrs)


def paint(shape, color, stroke=0):
    p = el(shape, "Stroke", name="Stroke", thickness=stroke) if stroke else el(shape, "Fill", name="Fill")
    el(p, "SolidColor", name="Color", colorValue=color)


def shape(parent, name, x, y, w, h, color, ident=None, kind="Rectangle", stroke=0, **attrs):
    s = el(parent, "Shape", name=name, x=x, y=y, **({"id": ident} if ident else {}), **attrs)
    el(s, kind, name="Path", width=w, height=h, **({"cornerRadiusTL": 12} if kind == "Rectangle" else {}))
    paint(s, color, stroke)
    return s


def text(parent, content, x, y, size, color="FFDFE6ED", ident=None):
    t = el(parent, "Text", name=content, x=x, y=y)
    sid = f"0:{text.counter}"
    text.counter += 1
    style = el(t, "TextStylePaint", name="Inter", fontSize=size, fontAssetId="0:1109", id=sid)
    paint(style, color)
    el(t, "TextValueRun", name="Text", text=content, styleId=sid, **({"id": ident} if ident else {}))
    return t


text.counter = 20000


def image(parent, asset, name, **attrs):
    return el(parent, "Image", name=name, assetId=f"0:{asset}", **attrs)


def quad(parent, asset, name, box, pivot, scale=1, rotation=0, ident=None, opacity=1, texture_size=(1536,1024)):
    """A UV window into an existing atlas, without creating/editing a bitmap."""
    x0, y0, x1, y1 = box
    px, py = pivot
    img = image(parent, asset, name, scaleX=scale, scaleY=scale, rotation=rotation, opacity=opacity,
                **({"id": ident} if ident else {}))
    mesh = el(img, "Mesh", name=name + " UV", triangleIndexBytes="AAECAAID")
    for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
        el(mesh, "ContourMeshVertex", name="UV", x=x-px, y=y-py, u=x/texture_size[0], v=y/texture_size[1])
    return img


def grid_mesh(parent, asset, name, box, pivot, texture_size, start_id, nx=10, ny=12):
    """Author UV geometry only. Source pixels remain untouched."""
    x0, y0, x1, y1 = box
    coords = [(x0+(x1-x0)*i/nx, y0+(y1-y0)*j/ny)
              for j in range(ny+1) for i in range(nx+1)]
    # Contour vertices are clockwise, so the editable outline also round-trips.
    boundary = (list(range(nx+1)) + [j*(nx+1)+nx for j in range(1,ny+1)]
                + [ny*(nx+1)+i for i in range(nx-1,-1,-1)]
                + [j*(nx+1) for j in range(ny-1,0,-1)])
    order = boundary + [i for i in range(len(coords)) if i not in boundary]
    remap = {old:new for new,old in enumerate(order)}
    indices = []
    for j in range(ny):
        for i in range(nx):
            a = j*(nx+1)+i
            indices.extend(remap[k] for k in (a,a+1,a+nx+2,a,a+nx+2,a+nx+1))
    packed = bytearray()
    for index in indices:
        while index >= 128:
            packed.append((index & 127) | 128)
            index >>= 7
        packed.append(index)
    img = image(parent, asset, name)
    mesh = el(img,"Mesh",name=name+" deformation",triangleIndexBytes=base64.b64encode(packed).decode())
    vertices = []
    for n,k in enumerate(order):
        x,y = coords[k]
        ident = start_id+n
        el(mesh,"ContourMeshVertex" if n<len(boundary) else "MeshVertex",name=f"v{n}",id=f"0:{ident}",
           x=x-pivot[0], y=y-pivot[1],u=x/texture_size[0],v=y/texture_size[1])
        vertices.append((ident,x-pivot[0],y-pivot[1],x,y))
    return vertices


for i, name in enumerate(["head", "body", "arm-left", "arm-right", "eye-left", "eye-right", "mouth-smile", "mouth-open", "ring"]):
    el(R, "ImageAsset", file=f"../emma/assets/{name}.png", name=name, id=f"0:{1100+i}")
el(R, "FontAsset", file="assets/Inter.ttf", name="Inter", id="0:1109")
el(R, "ScriptAsset", file="gaze.luau", name="Damped gaze", id="0:1113")
el(R,"ImageAsset",file="assets/palm-open-v4.png",name="Palm facing camera",id="0:1115")

actions = [("idle", "Rest / follow cursor", "Move the pointer around Emma"),
           ("wave", "Say hello", "A small bow, then a friendly wave"),
           ("search", "Find something", "Looking closer, with a little curiosity"),
           ("deny", "Really, Emma?", "A playful no, followed by a shrug"),
           ("scan", "Take a closer look", "Reading a floating holographic card"),
           ("shy", "You look lovely", "A soft lean and a bashful thank-you"),
           ("showcase", "Play all motions", "A continuous tour of the five gestures")]

vm = el(R, "ViewModel", name="EmmaMotionLab", id="0:4000", defaultInstanceId="0:4001")
for i, (name, _, _) in enumerate(actions):
    el(vm, "ViewModelPropertyTrigger", name=name, id=f"0:{4010+i}")
instance = el(vm, "ViewModelInstance", name="Default", exports="true", id="0:4001")
for i in range(len(actions)):
    el(instance, "ViewModelInstanceTrigger", propertyValue=0, viewModelPropertyId=f"0:{4010+i}")
for i,name in enumerate(("lookX","lookY")):
    el(vm,"ViewModelPropertyNumber",name=name,id=f"0:{4050+i}")
    el(instance,"ViewModelInstanceNumber",propertyValue=0,viewModelPropertyId=f"0:{4050+i}")

A = el(R, "Artboard", width=1180, height=780, name="Emma Motion Lab", id="0:2",
       defaultStateMachineId="0:900", viewModelId="0:4000", viewModelInstanceId="0:4001", styleId="0:3")
el(A, "LayoutComponentStyle", name="Stage style", id="0:3")
input_layer = el(A,"LayoutComponent",width=1180,height=780,styleId="0:180",name="Gaze input")
el(input_layer,"LayoutComponentStyle",id="0:180",name="Fill input",layoutWidthScaleType=1,
   layoutHeightScaleType=1,widthUnitsValue=3,heightUnitsValue=3)
gaze_script = el(input_layer,"ScriptedLayout",scriptAssetId="0:1113",name="Damped head direction")
for ident,name,value in ((181,"follow",1),(182,"poseX",0),(183,"poseY",0)):
    el(gaze_script,"ScriptInputNumber",name=name,id=f"0:{ident}",propertyValue=value)

# Front-to-back drawing: controls and character first, background last.
text(A, "EMMA  /  MOTION STUDY", 58, 40, 13, "FF8CCDCB")
text(A, "A little more alive.", 58, 70, 29, "FFF2F5F7")
text(A, "EXPERIMENT 05", 939, 42, 12, "FF85919F")
text(A, "Rive rig · separate preview", 899, 64, 12, "FF85919F")
text(A, "TRY A CONVERSATION", 690, 181, 12, "FF8CCDCB")
for i, (_, title, desc) in enumerate(actions):
    y = 249 + (i-1)*72 if 1 <= i <= 5 else 652
    x, w = (875, 370) if 1 <= i <= 5 else ((770, 160) if i == 0 else (960, 200))
    if i in (0, 6):
        text(A, "Rest" if i == 0 else "Play all motions", x-w/2+21, y-9, 14)
    else:
        text(A, f"0{i}", x-w/2+19, y-16, 12, "FF7BC4C0")
        text(A, title, x-w/2+57, y-20, 17)
        text(A, desc, x-w/2+57, y+6, 10, "FFA1ADB9")
    shape(A, "Selected " + title, x, y, w, 58 if i not in (0, 6) else 44, "FF164345", f"0:{600+i}", opacity=0)
    shape(A, title, x, y, w, 58 if i not in (0, 6) else 44, "FF18242F", f"0:{500+i}")
text(A, "IDLE", 70, 680, 12, "FF9BDFD6", "0:710")
text(A, "Hover to meet her gaze. Choose a prompt to see her react.", 70, 704, 12, "FF91A0AF", "0:711")
text(A, "Local motion study · no microphone or robot connection", 690, 718, 10, "FF748392")

rig = node(A, "Emma Root", "0:10", x=CX, y=CY, scaleX=SCALE, scaleY=SCALE)
scan = node(rig, "Scan Hologram", "0:130", x=13, y=238, opacity=0)
shape(scan, "Scan sweep", 0, -45, 190, 3, "FFC9FFFF", "0:131")
for j, w in enumerate([62, 86, 86, 60]):
    shape(scan, "Document line", -7 if j == 0 else 5, -38+j*19, w, 3, "99DFFFFF")
shape(scan, "Document", 0, 0, 124, 130, "775CD8E0", stroke=1.5)
shape(scan, "Glass border", 0, 0, 204, 150, "BB8CCFCF", stroke=1.5)
shape(scan, "Glass", 0, 0, 204, 150, "33248293")

heart = node(rig, "Warm heart", "0:140", x=0, y=-193, opacity=0)
# Two circles and a rotated square form a small soft heart.
shape(heart, "Heart point", 0, 1, 23, 23, "FFFFC0CE", rotation=math.pi/4)
shape(heart, "Heart left", -9, -8, 24, 24, "FFFFC0CE", kind="Ellipse")
shape(heart, "Heart right", 9, -8, 24, 24, "FFFFC0CE", kind="Ellipse")

head = el(rig, "RootBone", name="Head pivot at neck", id="0:20", x=0, y=170, length=170, rotation=-math.pi/2)
head_art = node(head, "Head Artwork Space", x=170, rotation=math.pi/2)
head_art = node(head_art,"Head pitch space","0:28")
features = node(head_art, "Face Features", "0:30")
for i, x in enumerate([-87, 87]):
    eye_surface = node(features,f"Eye surface {i}",f"0:{38+i}",x=x,y=-17)
    expression = node(eye_surface, f"Eye expression {i}", f"0:{36+i}")
    e = node(expression, f"Eye blink {i}", f"0:{34+i}")
    image(e, 1104+i, "Chrome eye", scaleX=.68, scaleY=.88)
# One source and one topology throughout the turn. A small rigid dome
# projection preserves the shell silhouette without changing angle images.
head_surface = node(head_art,"Continuous head surface","0:26")
head_vertices = grid_mesh(head_surface,1100,"Original chrome dome",(0,0,585,550),
                          (292.5,275),(585,550),65000,nx=24,ny=22)
for vertex in head_surface.iter():
    if vertex.tag in ("MeshVertex","ContourMeshVertex"):
        vertex.set("x",str(float(vertex.get("x"))*.68))
        vertex.set("y",str(float(vertex.get("y"))*.68))
head_vertices = [(i,x*.68,y*.68,u,v) for i,x,y,u,v in head_vertices]

body = node(rig, "Body and shoulders", "0:50")


def graphite(parent, name, x, y, w, h, kind="Rectangle", ident=None, path_id=None):
    """Short dark links with a narrow reflection, not long white chrome sleeves."""
    s = el(parent, "Shape", name=name, x=x, y=y,**({"id":f"0:{ident}"} if ident else {}))
    el(s, kind, name="Contour", width=w, height=h,
       **({"cornerRadiusTL": h/2} if kind == "Rectangle" else {}),
       **({"id":f"0:{path_id}"} if path_id else {}))
    grad = el(el(s, "Fill", name="Graphite"), "LinearGradient", name="Soft black reflection",
              startX=0, startY=-h/2, endX=0, endY=h/2)
    for pos, color in [(0,"FF06080C"),(.24,"FF191E26"),(.39,"FF59616D"),(.50,"FF262B33"),(1,"FF05070A")]:
        el(grad, "GradientStop", position=pos, colorValue=color)


hand_vertices = {}
HAND_REST_CURL = .27


def index_bytes(indices):
    packed=bytearray()
    for value in indices:
        while value>=128:
            packed.append((value&127)|128); value>>=7
        packed.append(value)
    return base64.b64encode(packed).decode()


def glove_mesh(parent, name, points, indices, contour_count, start_id, finger=None):
    mesh=el(image(parent,1115,name),"Mesh",name=name+" mesh",
            triangleIndexBytes=index_bytes(indices))
    verts=[]
    for i,(u,v) in enumerate(points):
        x,y=glove_point(u,v,HAND_REST_CURL,0,finger)
        ident=start_id+i
        el(mesh,"ContourMeshVertex" if i<contour_count else "MeshVertex",name=f"v{i}",
           id=f"0:{ident}",x=x,y=y,u=u/1254,v=v/1254)
        verts.append((ident,u,v,finger))
    return verts


def articulated_glove(parent, base):
    verts=[]
    for finger,(origin,tip,radius) in enumerate(FINGERS):
        ax,ay=tip[0]-origin[0],tip[1]-origin[1]
        length=math.hypot(ax,ay); ax/=length; ay/=length
        cols,rows=4,12
        coords=[]
        for j in range(rows+1):
            distance=-30+(length+radius+45)*j/rows
            for i in range(cols+1):
                width=radius*(2*i/cols-1)
                coords.append((origin[0]+ax*distance-ay*width,origin[1]+ay*distance+ax*width))
        boundary=(list(range(cols+1))+[j*(cols+1)+cols for j in range(1,rows+1)]
                  +[rows*(cols+1)+i for i in range(cols-1,-1,-1)]
                  +[j*(cols+1) for j in range(rows-1,0,-1)])
        order=boundary+[i for i in range(len(coords)) if i not in boundary]
        remap={old:new for new,old in enumerate(order)}
        triangles=[]
        for j in range(rows):
            for i in range(cols):
                a=j*(cols+1)+i
                triangles.extend(remap[k] for k in (a,a+1,a+cols+2,a,a+cols+2,a+cols+1))
        verts.extend(glove_mesh(parent,f"Finger {finger+1}",[coords[k] for k in order],triangles,
                               len(boundary),30000+base*100+finger*70,finger))
    # Palm and cuff remain one rigid surface. Finger roots overlap this surface,
    # and no fingertip pixels are stretched across the gaps between fingers.
    boundary=[(430,650),(446,550),(565,505),(625,520),(772,525),(887,578),
              (950,662),(1005,739),(1025,796),(988,875),(893,977),(904,1180),
              (420,1180),(410,972),(315,842),(342,777),(395,720)]
    points=boundary+[(680,820)]
    triangles=[k for i in range(len(boundary)) for k in (i,(i+1)%len(boundary),len(boundary))]
    verts.extend(glove_mesh(parent,"Palm and cuff",points,triangles,len(boundary),30000+base*100+400))
    return verts


def arm(side, offset_x, base):
    """Fixed-length shoulder/forearm chain; the same glove in every pose."""
    g = node(body,side+" Arm Space",x=offset_x,y=214,
             scaleX=-.68 if side=="Left" else .68,scaleY=.68)
    mounting = node(g,side+" shoulder seating",rotation=.65)
    upper = el(mounting,"RootBone",name=side+" shoulder",id=f"0:{base}",length=50)
    fore = el(upper,"Bone",name=side+" elbow",id=f"0:{base+1}",length=28,rotation=.5)
    wrist = node(fore,side+" wrist",f"0:{base+4}",x=28,rotation=2.0)
    glove = node(wrist,side+" continuous glove",scaleX=-.16,scaleY=.16)
    hand_vertices[base] = articulated_glove(glove,base)
    if side=="Right":
        # An independent prop behind the persistent fingers, never a baked fist.
        prop=node(wrist,"Magnifier held at palm","0:120",x=20,y=-143,opacity=0)
        shape(prop,"Chrome outer rim",0,0,112,112,"FFE6F2F6",kind="Ellipse",stroke=5)
        shape(prop,"Dark rim",0,0,104,104,"FF111D26",kind="Ellipse",stroke=3)
        shape(prop,"Glass highlight",0,0,99,99,"FFACDEE5",kind="Ellipse",stroke=1)
        shape(prop,"Clear lens",0,0,99,99,"1823B5CA",kind="Ellipse")
        shape(prop,"Handle edge reflection",-3,97,2,80,"FFC7D5DE")
        graphite(prop,"Magnifier handle",0,97,12,80)
    graphite(fore,side+" fixed forearm",14,0,40,30)
    graphite(upper,side+" elbow bearing",50,0,36,36,kind="Ellipse")
    graphite(upper,side+" fixed upper link",25,0,58,36)
    graphite(g,side+" inset shoulder axle",-6,0,24,34)
    return 0


LEFT = arm("Left", -82, 60)
RIGHT = arm("Right", 108, 70)
torso = node(body, "Torso", x=13, y=277)
body_image = image(torso, 1101, "Approved body", scaleX=.68, scaleY=.68)
# The body crop also contains its neighbour's head rim above the actual neck.
# Keep only source rows 20..305 so that rim cannot remain behind a tilted head.
el(body_image, "ClippingShape", sourceId="0:51", name="Exclude atlas neighbours")
body_mask = el(torso, "Shape", id="0:51", name="Torso artwork bounds", y=3.4)
el(body_mask, "Rectangle", name="Bounds", width=220, height=193.8)
# The same articulated arm can pass in front of the cheek; head occlusion must
# not amputate a finger in the middle of the wave.
rig.remove(body)
rig.insert(list(rig).index(head),body)

ground = node(A, "Landing light", "0:82", x=341, y=645)
image(ground, 1108, "Approved ring", scaleX=.63, scaleY=.63)
glow = shape(ground, "Floor glow", 0, 7, 440, 440, "00FFFFFF", "0:83", kind="Ellipse", scaleY=.24)
glow.remove(list(glow)[1])
gradient = el(el(glow, "Fill", name="Glow"), "RadialGradient", name="Falloff", startX=0, startY=0, endX=215, endY=0)
for pos, color in [(0,"558EDDE0"),(.55,"225BBFCA"),(1,"004699AA")]:
    el(gradient, "GradientStop", position=pos, colorValue=color)
look = el(A,"Joystick",name="Curved surface yaw and pitch",xId="0:290",yId="0:291")
for prop,ident in ((299,4050),(300,4051)):
    el(look,"DataBindContext",sourcePathIds=f"0:4000-0:{ident}",propertyKey=prop)

shape(A, "Rule", 617, 413, 1, 482, "FF243541")
bg = shape(A, "Stage background", 590,390,1180,780,"FF09131D", "0:160")

# Each animation keys all pose channels to prevent stale transforms on interruption.
defaults = {(10,13):CX, (10,14):CY, (10,15):0, (10,16):SCALE, (10,17):SCALE,
            (20,15):-math.pi/2, (50,15):0,
            (60,15):LEFT, (61,15):.5, (70,15):RIGHT, (71,15):.5,
            (181,243):1, (182,243):0, (183,243):0,
            (30,13):0, (30,14):0, (34,15):0, (35,15):0,
            (36,17):1, (37,17):1,
            (120,18):0, (64,15):2.0, (74,15):2.0,
            (130,18):0, (131,14):-45, (140,18):0, (140,14):-193,
            (82,16):1, (82,17):1}
for i in range(7):
    defaults[(600+i,18)] = 1 if i==0 else 0
for vertices in hand_vertices.values():
    for ident,u,v,finger in vertices:
        x,y = glove_point(u,v,HAND_REST_CURL,0,finger)
        defaults[(ident,24)] = x
        defaults[(ident,25)] = y


def pose_track(values, delta=0):
    return [(frame,value+delta) for frame,value in values]


def keyframes(prop, values, discrete=False):
    for frame, value in values:
        k = el(prop,"KeyFrameDouble",frame=frame,value=round(value,6),interpolationType="hold" if discrete else "cubic")
        if not discrete:
            el(k,"CubicEaseInterpolator",x1=.42,y1=0,x2=.58,y2=1)


for axis,anim_id in (("x",290),("y",291)):
    an = el(A,"LinearAnimation",name="Bounded head "+axis,id=f"0:{anim_id}",duration=120,fps=60)
    if axis=="y":
        kp = el(el(an,"KeyedObject",objectId="0:28"),"KeyedProperty",propertyKey=14)
        keyframes(kp,[(0,-4),(60,0),(120,4)])
        continue
    for ident,x,y,_,_ in head_vertices:
        kp = el(el(an,"KeyedObject",objectId=f"0:{ident}"),"KeyedProperty",propertyKey=24)
        keyframes(kp,[(frame,dome_point(x,y,d)[0]) for frame,d in ((0,-1),(60,0),(120,1))])
    for ident,x in ((38,-87),(39,87)):
        kp=el(el(an,"KeyedObject",objectId=f"0:{ident}"),"KeyedProperty",propertyKey=13)
        keyframes(kp,[(frame,dome_point(x,-17,d)[0]) for frame,d in ((0,-1),(60,0),(120,1))])
        kp=el(el(an,"KeyedObject",objectId=f"0:{ident}"),"KeyedProperty",propertyKey=16)
        keyframes(kp,[(0,.98 if ident==38 else 1),(60,1),(120,1 if ident==38 else .98)])


def hand_pose(base, values):
    """Sample joint bends into native vertex tracks: no sprite/opacity changes."""
    tracks={}
    for ident,u,v,finger in hand_vertices[base]:
        poses=[(f,*glove_point(u,v,curl,yaw,finger)) for f,curl,yaw in values]
        tracks[(ident,24)]=[(f,x) for f,x,y in poses]
        tracks[(ident,25)]=[(f,y) for f,x,y in poses]
    return tracks


def fingers(base, values):
    return hand_pose(base,[(f,max(0,min(1,HAND_REST_CURL+a*.5)),0) for f,a in values])


def animation(name, ident, duration, tracks, label, subtitle, loop=False):
    an = el(A,"LinearAnimation",name=name,id=f"0:{ident}",duration=duration,fps=60,loopValue="loop" if loop else "oneShot")
    for (obj,key), value in defaults.items():
        kp = el(el(an,"KeyedObject",objectId=f"0:{obj}"),"KeyedProperty",propertyKey=key)
        keyframes(kp, tracks.get((obj,key), [(0,value),(duration,value)]))
    for obj, content in [(710,label),(711,subtitle)]:
        kp = el(el(an,"KeyedObject",objectId=f"0:{obj}"),"KeyedProperty",propertyKey=268)
        el(kp,"KeyFrameString",frame=0,value=content)
    return an


idle = {(10,14):[(0,CY),(105,CY-5),(210,CY)],
        (60,15):pose_track([(0,0),(110,.018),(210,0)],LEFT), (70,15):pose_track([(0,0),(100,.018),(210,0)],RIGHT)}
animation("idle",200,210,idle,"IDLE / CURIOUS","Move the pointer. Emma follows while resting.",True)

motions = {}


def motion(name, duration, overrides, label, subtitle, index):
    tr = {(181,243):[(0,0),(duration,0)], (600,18):[(0,0),(duration,0)],
          (600+index,18):[(0,1),(duration,1)]}
    tr.update(overrides)
    for elbow in (61,71):
        if (elbow,15) in tr:
            tr[(elbow,15)] = [(f,v+.5) for f,v in tr[(elbow,15)]]
    motions[name] = (duration,tr,label,subtitle)
    animation(name,200+index,duration,tr,label,subtitle)


motion("wave",240,{
    (10,17):[(0,SCALE),(18,SCALE*.982),(43,SCALE*1.016),(82,SCALE),(240,SCALE)],
    (10,14):pose_track([(0,0),(18,7),(45,-12),(78,-7),(150,-10),(205,2),(240,0)],CY),
    (20,15):pose_track([(0,0),(20,.06),(46,-.085),(145,-.065),(212,.018),(240,0)],-math.pi/2),
    (70,15):[(0,0),(18,.06),(64,-.55),(165,-.55),(240,0)],
    (71,15):[(0,0),(22,.08),(70,-1.30),(166,-1.30),(240,0)],
    (74,15):[(0,2),(26,2.05),(70,.72),(93,1.08),(118,.51),(144,1.06),(169,.72),(216,1.6),(240,2)],
    (60,15):[(0,0),(45,-.08),(145,-.04),(240,0)],
    (182,243):[(0,0),(50,.24),(170,.24),(225,0),(240,0)],
    (183,243):[(0,0),(22,.22),(60,-.12),(190,0),(240,0)],
    **hand_pose(70,[(0,HAND_REST_CURL,0),(25,.48,0),(70,.02,0),(170,.02,0),(218,.28,0),(240,HAND_REST_CURL,0)]),
},"HELLO / WAVE","A little anticipation. Two wrist waves. A soft return.",1)

motion("search",300,{
    (36,17):[(0,1),(65,.82),(215,.82),(275,1),(300,1)],
    (37,17):[(0,1),(65,1.06),(215,1.06),(275,1),(300,1)],
    (10,13):pose_track([(0,0),(45,10),(110,-8),(190,12),(260,0),(300,0)],CX),
    (20,15):pose_track([(0,0),(45,-.055),(125,.065),(200,-.07),(270,0),(300,0)],-math.pi/2),
    (70,15):[(0,0),(20,.08),(76,-.40),(130,-.48),(200,-.42),(300,0)],
    (71,15):[(0,0),(26,.05),(86,-1.45),(210,-1.45),(300,0)],
    (74,15):[(0,2),(30,1.9),(84,.85),(150,.92),(210,.85),(300,2)],
    (182,243):[(0,0),(82,.60),(141,-.50),(202,.60),(271,0),(300,0)],
    (183,243):[(0,0),(80,-.16),(210,-.16),(280,0),(300,0)],
    (120,18):[(0,0),(42,0),(58,1),(228,1),(252,0),(300,0)],
    **hand_pose(70,[(0,HAND_REST_CURL,0),(30,.2,-.1),(75,.68,-.90),(220,.68,-.90),(266,.3,-.2),(300,HAND_REST_CURL,0)]),
},"SEARCH / CURIOUS","Looking left, looking right... let me check that.",2)

motion("deny",270,{
    (36,17):[(0,1),(33,.57),(112,.57),(162,1),(270,1)],
    (37,17):[(0,1),(33,.57),(112,.57),(162,1),(270,1)],
    (20,15):pose_track([(0,0),(145,0),(205,-.025),(270,0)],-math.pi/2),
    (182,243):[(0,0),(30,-.78),(61,.78),(94,-.66),(126,.60),(158,0),(270,0)],
    (10,14):pose_track([(0,0),(35,5),(120,0),(166,-10),(215,-6),(270,0)],CY),
    (60,15):pose_track([(0,0),(120,0),(161,-.72),(218,-.72),(270,0)],LEFT),
    (70,15):pose_track([(0,0),(120,0),(161,-.82),(218,-.82),(270,0)],RIGHT),
    (61,15):[(0,0),(135,0),(167,-.25),(218,-.25),(270,0)],
    (71,15):[(0,0),(135,0),(167,-.25),(218,-.25),(270,0)],
    (34,15):[(0,0),(38,.10),(110,.10),(150,0),(270,0)],
    (35,15):[(0,0),(38,-.10),(110,-.10),(150,0),(270,0)],
    **fingers(60,[(0,0),(125,0),(172,.75),(218,.75),(265,0),(270,0)]),
    **fingers(70,[(0,0),(125,0),(172,.75),(218,.75),(265,0),(270,0)]),
},"REALLY? / PLAYFUL","A gentle head shake, then a small shrug.",3)

motion("scan",330,{
    (36,17):[(0,1),(50,.76),(246,.76),(300,1),(330,1)],
    (37,17):[(0,1),(50,.76),(246,.76),(300,1),(330,1)],
    (20,15):pose_track([(0,0),(50,.055),(245,.055),(300,0),(330,0)],-math.pi/2),
    (183,243):[(0,0),(52,.72),(242,.72),(300,0),(330,0)],
    (60,15):pose_track([(0,0),(50,.18),(250,.18),(300,0),(330,0)],LEFT),
    (70,15):pose_track([(0,0),(50,.18),(250,.18),(300,0),(330,0)],RIGHT),
    (130,18):[(0,0),(26,0),(46,1),(263,1),(291,0),(330,0)],
    (131,14):[(0,-55),(50,-55),(112,55),(170,-55),(232,55),(280,-55),(330,-55)],
},"SCAN / FOCUSED","A soft hologram and a downward glance.",4)

motion("shy",300,{
    (36,17):[(0,1),(57,.58),(210,.58),(270,1),(300,1)],
    (37,17):[(0,1),(57,.58),(210,.58),(270,1),(300,1)],
    (10,17):[(0,SCALE),(25,SCALE*.985),(60,SCALE*1.01),(125,SCALE),(300,SCALE)],
    (10,14):pose_track([(0,0),(28,8),(62,-7),(140,-4),(218,-5),(273,2),(300,0)],CY),
    (20,15):pose_track([(0,0),(60,-.095),(160,-.095),(230,-.055),(280,0),(300,0)],-math.pi/2),
    (50,15):[(0,0),(70,.025),(216,.025),(300,0)],
    (60,15):pose_track([(0,0),(50,.24),(215,.24),(275,0),(300,0)],LEFT),
    (70,15):pose_track([(0,0),(53,.44),(215,.44),(275,0),(300,0)],RIGHT),
    (61,15):[(0,0),(68,.28),(210,.28),(275,0),(300,0)],
    (71,15):[(0,0),(68,.28),(210,.28),(275,0),(300,0)],
    (140,18):[(0,0),(36,0),(58,1),(213,1),(252,0),(300,0)],
    (140,14):[(0,-182),(58,-195),(145,-206),(230,-215),(300,-193)],
    (182,243):[(0,0),(65,-.32),(190,-.32),(270,0),(300,0)],
    (183,243):[(0,0),(65,.65),(190,.65),(270,0),(300,0)],
    **fingers(60,[(0,0),(68,-.30),(210,-.30),(275,0),(300,0)]),
    **fingers(70,[(0,0),(68,-.30),(210,-.30),(275,0),(300,0)]),
},"THANK YOU / SHY","A tiny lean, hands drawn in, and a warm thank-you.",5)

# Build a continuous timed tour from the same gesture tracks, including idle gaps.
tour = {key: [(0,val)] for key,val in defaults.items()}
cursor = 120
tour_labels = [(0,"IDLE / CURIOUS","A tour of Emma's five gestures.")]
for name, (duration,tracks,label,subtitle) in motions.items():
    tour_labels.append((cursor,label,subtitle))
    for key,val in defaults.items():
        tour[key].append((cursor-1,val))
        tour[key].extend((cursor+f,v) for f,v in tracks.get(key,[(0,val),(duration,val)]))
        tour[key].append((cursor+duration+24,val))
    cursor += duration+108
    tour_labels.append((cursor-84,"IDLE / CURIOUS","Back to rest. The next gesture follows shortly."))
for key,val in defaults.items():
    tour[key].append((cursor,val))
tour[(606,18)] = [(0,1),(cursor,1)]
tour[(181,243)] = [(0,0),(cursor,0)]
tour_anim = animation("showcase",206,cursor,tour,"SHOWCASE","A tour of Emma's five gestures.",True)
for obj, label_index in [(710,1),(711,2)]:
    kp = next(o for o in tour_anim if o.get("objectId")==f"0:{obj}")[0]
    kp.clear(); kp.set("propertyKey","268")
    for label in tour_labels:
        el(kp,"KeyFrameString",frame=label[0],value=label[label_index])

blink = el(A,"LinearAnimation",name="Independent blink",id="0:280",duration=273,fps=60,loopValue="loop")
for eye in (34,35):
    kp = el(el(blink,"KeyedObject",objectId=f"0:{eye}"),"KeyedProperty",propertyKey=17)
    keyframes(kp,[(0,1),(150,1),(155,.10),(161,1),(250,1),(255,.10),(261,1),(273,1)])

sm = el(A,"StateMachine",name="EmmaMotionLab",id="0:900")
layer = el(sm,"StateMachineLayer",name="Gesture performance",id="0:910")
any_state = el(layer,"AnyState",x=0,y=-160)
el(layer,"ExitState",x=240,y=-160)
el(el(layer,"EntryState",x=-200,y=0),"StateTransition",stateToId="0:920")
for i,(name,_,_) in enumerate(actions):
    transition = el(any_state,"StateTransition",stateToId=f"0:{920+i}",duration=220)
    cond = el(transition,"TransitionViewModelCondition")
    bind = el(el(cond,"TransitionPropertyViewModelComparator"),"BindablePropertyTrigger")
    el(bind,"DataBindContext",sourcePathIds=f"0:4000-0:{4010+i}",propertyKey=686)
    el(cond,"TransitionValueTriggerComparator")
    state = el(layer,"AnimationState",animationId=f"0:{200+i}",id=f"0:{920+i}",x=i*190,y=0,reset="true")
    if 0<i<6:
        el(state,"StateTransition",stateToId="0:920",duration=280,enableExitTime="true",exitTimeIsPercetange="true",exitTime=100)
    listener = el(sm,"StateMachineListenerSingle",targetId=f"0:{500+i}",listenerTypeValue="click",name=name)
    bind = el(el(listener,"ListenerViewModelChange"),"BindablePropertyTrigger",propertyValue=1)
    el(bind,"DataBindContext",sourcePathIds=f"0:4000-0:{4010+i}",propertyKey=686,direction="true")

bl = el(sm,"StateMachineLayer",name="Blink independent of gestures")
el(bl,"AnyState",x=0,y=-160); el(bl,"ExitState",x=240,y=-160)
el(el(bl,"EntryState",x=-180,y=0),"StateTransition",stateToId="0:981")
el(bl,"AnimationState",animationId="0:280",id="0:981",x=0,y=0)

if __name__ == "__main__":
    PROJECT.mkdir(parents=True,exist_ok=True)
    font = PROJECT / "assets/Inter.ttf"
    if not font.exists():
        candidates = sorted((Path.home()/".rive/versions").glob("*/samples/text_input/Inter.ttf"))
        if not candidates:
            raise SystemExit("Inter.ttf missing: install Rive CLI samples or provide assets/Inter.ttf")
        font.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(candidates[-1],font)
    ET.indent(R,space="    ")
    ET.ElementTree(R).write(PROJECT/"scene.rml",encoding="utf-8",xml_declaration=True)
    print(f"Wrote {PROJECT / 'scene.rml'} ({cursor/60:.1f}s showcase)")
