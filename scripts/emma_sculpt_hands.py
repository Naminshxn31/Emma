"""Four-digit cartoon gloves, modelled open and posed on a mechanical wrist.

The sheet (client/assets/emma/emma-character-concept-v1.png) shows thumb +
three fingers in every assembled panel. Each finger uses six short deforming
links so a soft glove curls without a sharp three-hinge crease. The same
welded surface is used in every pose; no hand swaps or photos.
Canonical hand space: Z towards fingers, -Y palm, +X thumb before mirroring.

Measurements (2026-09-23, hands pass) are on the raw panel crops in
scratchpad/emma-ref (full_front 128x266, head 106.5 px = 2.32 u, so
1 px = 0.0218 u; full_side head 101.5 px, 1 px = 0.0229 u), read as white
(>150) / object (>60) runs per row, plus the x6/x8 crops in
scratchpad/measure-hands. Rows are counted from the head top (row 22 =
z 4.40).
"""
import math

import bpy
from mathutils import Matrix, Quaternion, Vector


def clamp(x):
    return max(0., min(1., x))


def smooth(x):
    x = clamp(x)
    return x*x*(3-2*x)


def nearest(p, a, b):
    v = b-a
    t = clamp((p-a).dot(v)/v.length_squared)
    return (p-a-v*t).length, t


# Rest-pose hang: the glove's centre line drifts outboard from the ring
# (full_front rows 130-134: x 16..27 px, centre .91 u) to the fingertips
# (row 172: x 14..17, centre 1.04 u) over 0.92 u of drop -> 8 deg; the
# dorsal edge bows to x 1.25 at mid-height (rows 146-162, x 6), so the
# hand axis leans 10 deg and the palm lumps sit .03 dorsal of it.
HANG_TILT = math.radians(10)
# Palm facing: front view shows the glove edge-on (13 px = .28 u just below
# the ring, 19-20 px = .42-.44 u at mid-height) while the side view shows the
# broad back of the hand (rows 146-148: x 52..90 = 38 px = .83 u) with the
# thumb lump on the front edge (rows 152-156, x 79..87) -> palm faces the
# torso, thumb forward. 8 deg back-turn: the front-view finger cluster is
# stacked nearly edge-on in the sheet (z 1.35: x .99..1.25, one finger wide);
# every degree of turn spreads the three roots across world x (12 deg put
# the index .10 u medial of the ring finger in build 2 and filled the
# sheet's gap between thumb lump and fingers). 8 deg with a .19 u palm
# y-radius projects the mid-height width to .39 (sheet .39-.44).
PALM_TURN = math.radians(8)


def build_arms(api):
    bones, hands, model = api['BONES'], api['HANDS'], api['MODEL']
    mesh, ellipsoid, segment = api['mesh'], api['ellipsoid'], api['segment']
    chrome, dark, glove = api['CHROME'], api['DARK'], api['GLOVE']
    definitions = {}

    def revolve(name, centre, axis, profile, mat, bone, n=96):
        """Closed surface of revolution: profile is a closed (along, r) loop."""
        axis = Vector(axis).normalized()
        u = axis.cross(Vector((0,1,0))).normalized()
        v = axis.cross(u)
        vertices, faces = [], []
        for along, r in profile:
            for i in range(n):
                angle = i*2*math.pi/n
                vertices.append(tuple(centre+axis*along+r*(u*math.cos(angle)+v*math.sin(angle))))
        m = len(profile)
        for j in range(m):
            k = (j+1) % m
            for i in range(n):
                faces.append((j*n+i, j*n+(i+1)%n, k*n+(i+1)%n, k*n+i))
        return mesh(name, vertices, faces, mat, bone)

    for side, suffix in [(-1,'L'),(1,'R')]:
        # Proportions lock (2026-09-23, frozen): ball (+-.68, 0, 2.01) r .14,
        # socket r .11, elbow (+-.80,-.02,2.03) inside the ball, wrist = glove
        # heel swivel (+-.92,-.06,2.05). The hands pass keeps all three.
        shoulder = Vector((side*.68,0,2.01))
        elbow = Vector((side*.80,-.02,2.03))
        # wrist z 2.05 -> 2.03 (2026-09-23 owner review). Scale-aligned
        # side-by-side of full_front and front.png at 402 px head width:
        # the sheet glove spans z 1.05..2.00 and its top tucks UNDER the
        # ball, while the render sat at 1.02..2.08 with the cuff exposed
        # above the white. Dropping the heel .05 puts white beside the
        # ball and leaves the ring half-covered, as the sheet draws it.
        # The full .05 drop (sheet glove top 2.00) pushed the glove BOTTOM
        # to .97 against the sheet's 1.05 and cost .012 of full_front IoU:
        # our glove is .11 longer than the sheet's, so it cannot match at
        # both ends. .03 splits the error between top and bottom.
        wrist = Vector((side*.92,-.060,2.03))
        mirror = -side
        # Both frames are proper rotations. Only the mesh/guide X coordinate is
        # mirrored; no reflected matrix is ever converted into a quaternion.
        # Hand Z = down, leaning HANG_TILT outboard; hand -Y = palm normal,
        # pointing at the torso and turned PALM_TURN towards the back; hand +X
        # (thumb) then points forward and slightly inboard (the inner-front
        # thumb lump of full_front rows 156-160). The old basis
        # (side*.68,-.733,0) faced the palm 47 deg between medial and back,
        # which showed the wide face in the front view (glove .55 u wide
        # edge-on instead of the sheet's .42).
        zaxis = Vector((side*math.sin(HANG_TILT),0,-math.cos(HANG_TILT)))
        yraw = Vector((side*math.cos(PALM_TURN),-math.sin(PALM_TURN),0))
        yaxis = (yraw-zaxis*yraw.dot(zaxis)).normalized()
        xaxis = yaxis.cross(zaxis)
        basis = Matrix((xaxis,yaxis,zaxis)).transposed()
        def world(p):
            p = Vector(p)
            p.x *= mirror
            return wrist+basis@p
        # Palm bone spans the cushion up to the knuckle line (finger roots at
        # hand z .40-.43, see chains below).
        palm = world((0,0,.18))
        base = world((0,0,.42))
        for name,a,b,parent in [('Shoulder',shoulder,elbow,'Body'),
                                ('Forearm',elbow,wrist,f'Shoulder.{suffix}'),
                                ('Wrist',wrist,palm,f'Forearm.{suffix}'),
                                ('Palm',palm,base,f'Wrist.{suffix}')]:
            bones[f'{name}.{suffix}'] = (tuple(a),tuple(b),parent)

        # Reference: a single chrome ball joint at the shoulder, then a flat
        # chrome cuff ring the glove heel turns inside. The ball turns with the
        # Shoulder bone; its dark socket stays on the body so the seam never
        # shows a gap when lifted.
        # Ball r .14 -> .17 (2026-09-23 owner review). measure:hands read the
        # sheet ball as .33-.37 dia (r .165-.185); the proportions lock had cut
        # it to .14, which both undersized it and left a .04 dark gap to the
        # torso rim (r(z 2.01) = .50 after the torso dome). .17 seats the ball
        # on the rim as in full_front, where ball and torso touch.
        ellipsoid(f'Shoulder / recessed socket {suffix}',shoulder,(.13,.13,.13),dark,'Body')
        ellipsoid(f'Shoulder / chrome ball joint {suffix}',shoulder,(.17,.17,.17),chrome,f'Shoulder.{suffix}',segments=64,rings=40)

        # Cuff = a flat chrome ring, not a barrel. full_front rows 125.5-130.5
        # (z 2.15..2.04) hold a thin chrome band .11 u tall at the top of the
        # glove, level with the ball top (2.15) and 5 px = .11 u above the white
        # heel; in the front view it spans x 17..26 px = .81..1.01 u beyond
        # the ball (the inboard half is hidden in the ball), i.e. outer dia
        # ~.33-.40 centred near the wrist x (build 1 at R .20 reached x 1.11
        # in the render against the sheet's 1.01-1.03, R .175 1.09 -> R .165,
        # outer edge 1.075 with the .03 lift of the tilted outboard end). The palm
        # panels draw the same ring
        # as .755 of the palm width with a bright top lip, dark reflective
        # side and a thin black recess where the white heel enters (hero_hand_R
        # x6); their .60 u would stick .2 u past the heel in the full views, so
        # the full-view size wins (silhouette first). Ring axis vertical (the
        # sheet band is level; a ring on the 10 deg hand axis lifted its
        # outboard end to z 2.19 in builds 1-5, above the ball top, and moved
        # the compare tool's head split), centre z 2.09 -> top 2.14 just
        # under the ball top 2.15, bottom 2.04, white glove from z 2.03
        # (sheet ring rows 125.5-130.5 = z 2.15..2.04, white from row 131).
        # The heel on the tilted hand axis stays inside the bore: offset
        # .016 at the ring's top plane where its r is .05, .002 at the
        # bottom plane where its r is .12 against the .13 bore.
        # 2026-09-23 owner review: a vertical ring .04 above the heel rendered
        # as a horizontal chrome PLATE cantilevered outboard of the ball with a
        # flat mirror top face and open air under its outer half - the single
        # most off-model element in front.png. In full_front x5 the chrome sits
        # ON the glove top, wrapping it, with white heel around and below it.
        # Ring now rides the hand axis (so the glove's dorsal bulge covers its
        # lower-outboard quarter) at hand-local z -.015, R .165 -> .15.
        # Outboard end z = 2.068 + .15*sin(HANG_TILT) = 2.094, still clear of
        # the ball top 2.18 - the reason build 1-5 kept the axis vertical.
        cuff_axis = basis@Vector((0,0,1))
        cuff_centre = world((0,-.01,-.015))
        # Slimmer than build 6 (.15/.12/.042): at 402 px head width the
        # sheet's chrome at the glove top is .20 u of visible width and
        # .11 u tall with the ball over its inboard end, so a fat ring
        # with a flat mirror top read as a machined bar.
        R, RI, T = .135, .115, .032
        cuff_profile = [(-T+.015,R),(-T+.006,R-.006),(-T,R-.018),
                        (-T,RI+.012),(-T+.006,RI+.003),(-T+.014,RI),
                        (T-.014,RI),(T-.006,RI+.003),(T,RI+.012),
                        (T,R-.018),(T-.006,R-.006),(T-.015,R)]
        revolve(f'Wrist / flat chrome cuff ring {suffix}',cuff_centre,cuff_axis,
                cuff_profile,chrome,f'Forearm.{suffix}')
        # Black recess filling the ring's bore: the heel (r .12 at the ring's
        # bottom plane) is narrower than the bore (.13), so a dark gap shows
        # between chrome and white as in the hero close-up (BLACK DETAIL).
        segment(f'Wrist / black cuff recess {suffix}',cuff_centre-cuff_axis*(T-.004),
                cuff_centre+cuff_axis*(T-.004),.132,dark,f'Forearm.{suffix}')
        parts = []

        def lump(p, radii):
            obj = ellipsoid('glove-sculpt',world(p),radii,glove,segments=48,rings=32)
            obj.rotation_mode = 'QUATERNION'
            obj.rotation_quaternion = basis.to_quaternion()
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            bpy.ops.object.transform_apply(location=False,rotation=True,scale=True)
            parts.append(obj)

        # Glove body (hand space). Heel: full_front row 134 (3 px under the
        # ring) is 13 px = .28 u wide -> r .135 at the ring, top hidden inside
        # the bore; its r at the ring's bottom plane (z -.009) is .12 against
        # the .13 bore, a thin black gap. Palm: side view .78-.83 u across
        # the back of the hand at mid-height, front view .39-.44 u edge-on
        # (rows 146-162: x 6..25/26 = .84..1.25) -> knuckle lump x .39,
        # y .19 (with the 8 deg turn that projects to .39; build 1 at y .205
        # and 12 deg rendered .46 and put the inner edge at .77 vs the
        # sheet's .84).
        # The dorsal side bows outboard (front view: straight outer edge at
        # x 1.25 from row 146 to 162 while the centre line drifts only to
        # 1.04, and the inner edge recedes from .77 at z 1.85 to .84 at
        # z 1.60-1.70), so the palm and knuckle lumps sit .07 dorsal (+y) of
        # the heel axis with y-radius .18: outer edge axis+.25 = 1.24, inner
        # edge axis-.11-.03 = .85 at z 1.65. Build 3 had a separate thenar
        # lump on the palm side that held the inner edge at .77 down to
        # z 1.5; the palm panels show one smooth cushion, so it is gone.
        # Build 4 (heel y 0, palm .07, knuckle .07) put the glove top at
        # x .85..1.21 vs the sheet's .77..1.16 (z 1.85) and full_back reads
        # the gloves .06-.08 further inboard than full_front (x .68/.75 ..
        # 1.11/1.19 vs .77..1.25, head 108.5 px), so the offsets grade from
        # -.02 at the heel through .04 at the palm to .08 at the knuckles:
        # the sheet's inner edge bulges medially at the top (.77 at z 1.85)
        # and the dorsal edge stays at 1.25 lower down.
        # Heel-to-knuckle .42 u: sheet cuff-bottom (row 131) to where the
        # fingers separate (row ~156) is 25 px = .54 u, of which the webbing
        # and rest curl take ~.1.
        lump((0,-.02,.06),(.135,.125,.16))
        lump((0,.04,.27),(.29,.19,.24))
        lump((-.03,.08,.41),(.39,.17,.17))

        def capsule(a, b, radius, tip_radius):
            a, b = Vector(a), Vector(b)
            axis = (b-a).normalized()
            ux = axis.cross(Vector((0,1,0))).normalized()
            uy = axis.cross(ux)
            length = (b-a).length
            profile = []
            for i in range(9):
                angle = -math.pi/2+(math.pi/2)*i/8
                profile.append((radius*math.sin(angle),max(.0005,radius*math.cos(angle))))
            for i in range(1,17):
                t = i/16
                profile.append((length*t,radius*(1-t)+tip_radius*t))
            for i in range(1,9):
                angle = math.pi*i/16
                profile.append((length+tip_radius*math.sin(angle),max(.0005,tip_radius*math.cos(angle))))
            vertices, faces, n = [], [], 32
            for along, r in profile:
                for i in range(n):
                    angle = 2*math.pi*i/n
                    vertices.append(tuple(world(a+axis*along+r*(ux*math.cos(angle)+uy*math.sin(angle)))))
            for j in range(len(profile)-1):
                for i in range(n):
                    faces.append((j*n+i,j*n+(i+1)%n,(j+1)*n+(i+1)%n,(j+1)*n+i))
            faces += [tuple(range(n-1,-1,-1)),tuple((len(profile)-1)*n+i for i in range(n))]
            parts.append(mesh('glove-sculpt',vertices,faces,glove))

        # Fingers: constant-width sausages with hemispherical tips (palm_l/r:
        # tip if anything fuller than the base -> no taper). Width: palm panels
        # 24-25 px of a 73.5 px palm = .33 PW (.27 u at .80), full_side rows
        # 166-168 show 6-7 px = .14-.16 u; r .12 is the midpoint. Lengths from
        # palm_l (middle 50 px = .68 PW = .54 u, index ~.87 and ring ~.78 of
        # it) scaled so the tips land at z 1.11 (full_front row 173) from the
        # white top at 2.03: hand z .95 for the middle. Rest hang nearly
        # parallel (full views): index +6 / ring -6 deg fan, which opens the
        # .08 u mid-length gap of palm_l (7 px) at the tips; the palm panel's
        # wide ring-finger splay is a pose (see apply_hand_pose). Roots on the
        # dorsal line (y .05): full_front z 1.15-1.35 has the finger cluster
        # at x .99..1.25 (outer half of the glove) with the thumb lump inboard
        # of a gap; build 1 (roots at y 0, curl .24) rendered it at .79..1.08,
        # build 3 (y .05, curl .16) .90..1.21 with the tips right (.98..1.14
        # vs .99..1.12), so the roots go to y .10 (fingers emerge from the
        # dorsal half of the knuckle line) and the curl to .20 to hold the
        # tips. Roots sit .05 behind the heel axis (hand -x): full_side rows
        # 166-168 put the three fingers at x 58..78 px, centred 4.5 px =
        # .10 u behind the glove's centre (52..90), thumb on the front edge.
        R_F = .12
        def fan(start, length, degrees):
            a = math.radians(degrees)
            start = Vector(start)
            return start, start+Vector((math.sin(a),.012,math.cos(a)))*length
        chains = []
        for label,(start,end),radius in [
            ('Index',fan((.20,.10,.41),.47-R_F,6),R_F),
            ('Middle',fan((-.05,.10,.43),.52-R_F,0),R_F),
            ('Ring',fan((-.30,.10,.40),.42-R_F,-6),R_F),
            # Thumb: root on the forward side of the palm (palm_l: 0.45 PW
            # from the axis, 0-0.4 palm heights below the knuckle line),
            # hanging 20 deg out in the palm plane and 15 deg towards the
            # palm (45 deg in build 1 reached world y -.71, .15 ahead of the
            # torso's front line; full_side rows 152-156 keep the thumb lump
            # at x 79..87 px, just inside the torso's front edge at 88-90).
            # In the rest pose the tip is the sheet's distinct inner lump
            # below the palm: full_front rows 156-160 = z 1.40-1.48, x 19..27
            # px = .79-.97, separated from the fingers (1.01-1.25) by a gap;
            # this root/angle puts the tip centre at world x .87, z 1.43.
            # Root y -.01 (build 5 -.03 held the inner edge at .77 for
            # z 1.5-1.6, sheet .81). Length .31 (palm_l 28-30 px = .395 PW,
            # foreshortened), same diameter as a finger.
            ('Thumb',((.27,-.01,.35),
                      (.27+.19*.330,-.01-.19*.259,.35+.19*.908)),R_F),
        ]:
            segments = 4 if label=='Thumb' else 6
            start, end = Vector(start),Vector(end)
            local_points = [start.lerp(end,i/segments) for i in range(segments+1)]
            points = [world(p) for p in local_points]
            names = []
            for j in range(segments):
                name = f'{label}.{j+1:02}.{suffix}'
                bones[name] = (tuple(points[j]),tuple(points[j+1]),names[-1] if names else f'Palm.{suffix}')
                names.append(name)
            capsule(start,end,radius,radius)
            chains.append({'label':label,'names':names,'points':points,'local_points':local_points,'radius':radius})

        bpy.ops.object.select_all(action='DESELECT')
        for obj in parts:
            obj.select_set(True)
            model.remove(obj)
        bpy.context.view_layer.objects.active = parts[0]
        bpy.ops.object.join()
        hand = bpy.context.object
        bpy.ops.object.transform_apply(location=True,rotation=True,scale=True)
        remesh = hand.modifiers.new('Continuous glove surface','REMESH')
        # Voxel .0065 -> .008: the glove is ~2.4x the old surface area; this
        # keeps the vertex count near the previous 37k per hand.
        remesh.mode='VOXEL';remesh.voxel_size=.008;remesh.use_smooth_shade=True
        bpy.ops.object.modifier_apply(modifier=remesh.name)
        soften = hand.modifiers.new('Round finger webs','SMOOTH')
        soften.factor=.7;soften.iterations=20
        bpy.ops.object.modifier_apply(modifier=soften.name)
        api['finish'](hand,f'Glove.{suffix} / continuous skinned surface',glove)
        definition = {'suffix':suffix,'side':side,'mirror':mirror,'basis':basis,
                      'wrist':wrist,'palm':palm,'elbow':elbow,'chains':chains}
        hands[hand.name]=definition
        definitions[suffix]=definition
    return definitions


def skin_hand(obj, d):
    suffix=d['suffix']
    names=[f'Wrist.{suffix}',f'Palm.{suffix}']+[n for c in d['chains'] for n in c['names']]
    groups={n:obj.vertex_groups.new(name=n) for n in names}
    inverse=d['basis'].transposed()
    for vertex in obj.data.vertices:
        p=inverse@(vertex.co-d['wrist'])
        p.x*=d['mirror']
        candidates=[]
        for chain in d['chains']:
            pts=chain['local_points']
            distances=[nearest(p,a,b) for a,b in zip(pts,pts[1:])]
            j=min(range(len(distances)),key=lambda i:distances[i][0])
            candidates.append((distances[j][0]/chain['radius'],chain,j,distances[j][1]))
        _,chain,j,t=min(candidates,key=lambda item:item[0])
        ns=chain['names']
        weights={ns[j]:1.}
        if t<.35:
            previous=ns[j-1] if j else f'Palm.{suffix}'
            blend=.5*smooth((.35-t)/.35)
            weights={ns[j]:1-blend,previous:blend}
        elif t>.65 and j+1<len(ns):
            blend=.5*smooth((t-.65)/.35)
            weights={ns[j]:1-blend,ns[j+1]:blend}
        # Digit influence ramps in along the digit's own axis from its root
        # (the thumb used a plain x threshold when its root was buried
        # mid-palm; with the root on the palm's edge the thenar swelling
        # would otherwise follow the thumb).
        root=chain['local_points'][0]
        along=(p-root).dot((chain['local_points'][1]-root).normalized())
        if chain['label']=='Thumb':
            influence=smooth((along-.05)/.10)
        else:
            influence=smooth((along-.015)/.080)
        weights={n:w*influence for n,w in weights.items()}
        weights[f'Palm.{suffix}']=weights.get(f'Palm.{suffix}',0)+1-influence
        # Heel (inside and just under the cuff ring) belongs to the Wrist bone
        # so palm_turn spins it inside the ring; blend into the palm by z .15.
        wrist=1-smooth((p.z-.02)/.13)
        weights={n:w*(1-wrist) for n,w in weights.items()}
        weights[f'Wrist.{suffix}']=wrist
        weights={n:w for n,w in weights.items() if w>1e-6}
        total=sum(weights.values())
        for n,w in weights.items():groups[n].add([vertex.index],w/total,'REPLACE')


# Open-hand splay (palm_l/palm_r, angles from the palm axis, + = thumb side):
# index +10, middle +5, ring -50, thumb +45 vs the rest mesh's +6 / 0 / -6 /
# +45 (rest thumb hangs 20 deg out, so +25 in the open pose). The extra fan
# is applied at the first link when the fingers are fully open (wave, grip
# frames 30/118) and fades out by the rest curl.
OPEN_SPLAY = {'Index':4.,'Middle':5.,'Ring':-40.,'Thumb':25.}


def apply_hand_pose(api, definitions, wave=0, curl=.20, palm_turn=0):
    rig=api['rig']
    for suffix,d in definitions.items():
        side=d['side'];w=clamp(wave) if suffix=='R' else 0
        turn=max(w,clamp(palm_turn))
        shoulder=rig.pose.bones[f'Shoulder.{suffix}']
        forearm=rig.pose.bones[f'Forearm.{suffix}']
        wrist=rig.pose.bones[f'Wrist.{suffix}']
        def q_world(bone,axis,angle):
            return Quaternion(bone.bone.matrix_local.to_quaternion().inverted()@Vector(axis),angle)
        lift=smooth((w-.10)/.90)
        shoulder.rotation_quaternion=q_world(shoulder,(0,0,1),-side*.32*lift)@q_world(shoulder,(0,1,0),-side*.12*lift)
        forearm.rotation_quaternion=q_world(forearm,(0,1,0),-side*1.20*lift)
        wrist.rotation_quaternion=Quaternion()
        bpy.context.view_layer.update()
        if turn:
            # Put the palm outward with an absolute orientation. The spherical
            # glove heel turns inside its metal bearing, retaining its volume.
            up=Vector((side*.60,0,.80)) if w else Vector((side*.10,0,-.995)).normalized()
            yaxis=Vector((0,1,0))
            xaxis=yaxis.cross(up).normalized()
            target=Matrix((xaxis,yaxis,up)).transposed()
            rotation=(target@d['basis'].transposed()).to_quaternion()
            rest=wrist.bone.matrix_local.to_quaternion()
            parent_rest=forearm.bone.matrix_local.to_quaternion()
            before=forearm.matrix.to_quaternion()@parent_rest.inverted()@rest
            desired=before.inverted()@rotation@rest
            if desired.w<0:desired.negate()
            wrist.rotation_quaternion=Quaternion().slerp(desired,smooth(turn))

        # Rest curl .20 (was .32): full_front rows 162-172 show the tips
        # converging under the outer half of the glove (x 1.04-1.12) with the
        # outer edge straight to z 1.35, i.e. a light curl of ~22 deg in
        # total; .32 gave 41 deg and .24 (build 1) pulled the tips to x .97,
        # .16 (build 3) with roots at y .05 left the tips right but the
        # fingers .08 inboard at z 1.35; with roots at y .10 the extra 6 deg
        # holds the tips. The open splay is fully off by close .06 (rest .108).
        close=clamp((curl-.06)/(1.35-.06))*(1-w)
        spread=smooth((.06-close)/.06)
        hinge=d['basis']@Vector((1,0,0))
        opposition=d['basis']@Vector((0,1,0))
        for chain in d['chains']:
            for j,name in enumerate(chain['names']):
                bone=rig.pose.bones[name]
                inverse=bone.bone.matrix_local.to_quaternion().inverted()
                if chain['label']=='Thumb':
                    bend=(.23,.23,.35,.35)[j]*close
                    oppose=(-d['mirror']*.80*close) if j<2 else 0
                    if j==0 and spread:
                        oppose+=math.radians(OPEN_SPLAY['Thumb'])*spread*d['mirror']
                    bone.rotation_quaternion=Quaternion(inverse@opposition,oppose)@Quaternion(inverse@hinge,bend)
                else:
                    angle=(.50,.50,.80,.80,.50,.50)[j]*close
                    q=Quaternion(inverse@hinge,angle)
                    if j==0 and spread:
                        fan=math.radians(OPEN_SPLAY[chain['label']])*spread*d['mirror']
                        q=Quaternion(inverse@opposition,fan)@q
                    bone.rotation_quaternion=q


# ---------------------------------------------------------------------------
# Simple flipper arms (owner's reference image, 2026-09-23).
#
# After the glove rig above was judged off-model at every joint ("ตั้งแต่ส่วนไหล่
# ไม่เหมือนสักอันเลย"), the owner sent a second reference: a torso with two soft
# one-piece arms - no fingers, no wrist, no shoulder ball, nothing chrome. They
# read as rounded paddles that grow straight out of the body, bow outboard at
# about a third of their length and end in a round tip.
#
# Proportions read off that image (torso width = 1.0, arms measured on the
# visible silhouette, +-0.02): arm widest 0.26 of the torso width, length 0.62,
# top of the arm level with the collar, tip at about 0.55 of the torso height,
# outer edge standing ~0.08 proud of the torso's widest point. In lab units with
# torso width 1.31 that is 0.34 wide and 0.81 long.
#
# The root sits INSIDE the torso (x .40 against a torso half-width of ~.48 at
# z 2.03) so the flat cap at the top never shows and no seam ring is needed -
# the old arm needed a chrome ball exactly because its root was outside the body.
# ---------------------------------------------------------------------------
FLIPPER = [(0.00,.170),(0.10,.228),(0.22,.256),(0.36,.265),(0.50,.263),
           (0.62,.252),(0.74,.230),(0.84,.199),(0.92,.157),(0.97,.099),(1.00,.0)]


def _flipper_radius(t):
    for (t0,r0),(t1,r1) in zip(FLIPPER, FLIPPER[1:]):
        if t <= t1:
            q = (t-t0)/(t1-t0)
            return r0+(r1-r0)*(q*q*(3-2*q))
    return 0.0


def build_flipper_arms(api):
    """Two soft paddle arms, one rigid mesh each on its own bone."""
    bones, mesh, glove = api['BONES'], api['mesh'], api['GLOVE']
    for side, suffix in [(-1,'L'), (1,'R')]:
        root = Vector((side*.46, -.040, 1.97))
        tip = Vector((side*.82, -.080, 1.02))
        bones[f'Arm.{suffix}'] = (tuple(root), tuple(tip), 'Body')
        faces = []
        rings, n = 44, 40
        verts = []
        for j in range(rings+1):
            t = j/rings
            centre = root.lerp(tip, t) + Vector((side*.050*math.sin(math.pi*t**.85), 0, 0))
            axis = (tip-root).normalized()
            u = Vector((0,1,0)).cross(axis).normalized()
            v = axis.cross(u)
            r = _flipper_radius(t)
            for i in range(n):
                angle = 2*math.pi*i/n
                # Round section, not flattened (owner, 2026-09-23: "เอาแบนออก").
                # The .80 squash read as a thin blade from the side.
                p = centre + u*(r*math.cos(angle)) + v*(r*math.sin(angle))
                verts.append(tuple(p))
        for j in range(rings):
            for i in range(n):
                a = j*n+i
                faces.append((a, j*n+(i+1)%n, (j+1)*n+(i+1)%n, (j+1)*n+i))
        faces.append(tuple(range(n-1,-1,-1)))
        faces.append(tuple(rings*n+i for i in range(n)))
        mesh(f'Arm / soft paddle {suffix}', verts, faces, glove, f'Arm.{suffix}')
    return {}


# ---------------------------------------------------------------------------
# Flat rounded fins, from the owner's dimensioned sheet
# "EMMA - 3D MODELING REFERENCE (Based on Original Concept, No Redesign)",
# 2026-09-23. Its DESIGN NOTES are the whole specification of this part:
#   1 No shoulders, no arm joints.      4 Connected directly to body.
#   2 Arms are flat, rounded fins.      5 Keep proportions as original concept.
#   3 Same shape for L/R (mirrored).    6 Simple and clean silhouette.
#
# The sheet dimensions this part is built from (unit = head height 1.00):
#   Body Width 0.68 · Total Width (Arms Open) 1.28
# so each fin's outer extreme sits at 0.64 from the centre line, i.e. total
# width / body width = 1.882. Anchoring on OUR body width (1.3136 lab, measured
# from the concept sheet in the torso pass and unchanged here) gives
# 1 sheet unit = 1.932 lab and an outer extreme at 1.234 lab.
#
# Everything else is read off the FRONT VIEW and the FIN DETAILS close-ups:
# an almond a little over 2.5:1 in the front view, a thin lens from the side
# (thickness ~0.35 of its width), long axis leaning 40 deg off vertical, top-
# inner end overlapping the body so the fin grows straight out of the shell
# with no joint, bottom-outer tip free. Flat means flat in DEPTH (y) only - the
# earlier round paddle was rejected, and so was a version squashed the other way.
# ---------------------------------------------------------------------------
# The sheet's ARM / FIN: a symmetric flat almond, same shell material as the body.
# 2026-09-23 history, so nobody re-derives it: the owner sent three renders of
# another companion robot and asked for a teardrop "ประมาณนี้"; three builds of
# that (wing, cone, stubby bulb) came back "แย่กว่าเดิม" and the almond was
# restored. Then the restored almond rendered with visible horizontal RIPPLES.
# Cause: the revert kept the sweep from the teardrop attempt, whose radius came
# from a 15-point table interpolated with smoothstep. Smoothstep is C1 but not
# C2, so curvature jumps at every table point and a mirror-finish shell shows
# each jump as a band. An ellipsoid has an exact radius, so the table is gone:
# r(t) = sqrt(1-(2t-1)^2) is evaluated per ring. Do not reintroduce a sampled
# profile on a glossy part without checking the render for banding.
# DETACHED (2026-09-23, owner: "ให้มันไม่ติดตัวหุ่นดีไหม"). The arm used to sink
# .110 into the shell, which read as one melted lump at the junction. It now
# floats with a real gap, which suits a character that already hovers over a
# separate ring and has no joints anywhere.
# Moving it out alone would have blown the width: a .03 gap at the old size puts
# the outer edge at 1.40 = 2.08 body widths against the sheet's 1.882. Solved as
# a pair instead - scale .80 and centre x .917 hold the outer edge at 1.2366
# (exactly 1.882 body widths) WHILE the nearest point clears the shell by .042,
# about 7 px in an 800 px render. z 1.70 -> 1.80 buys part of that clearance for
# free, because the shell narrows as it climbs towards the dome.
FIN_HALF_LENGTH = .416      # half the arm's length, along its own axis
FIN_HALF_WIDTH = .228       # max half-width across it, in the front view plane
FIN_HALF_THICK = .084       # max half-thickness through it; .37 of the width
FIN_TILT = math.radians(40) # off vertical, per the sheet's FRONT VIEW
FIN_CENTRE = (.917, .16, 1.80)
# y .16 is the dark reflected band measured on web-side.png, not the geometric
# mid-depth. Sheet note 4 says "connected directly to body"; the owner overrode
# it after seeing the junction, and note 1 (no joints) still holds either way.


def build_fin_arms(api):
    """Two flat almond arms joined straight to the body; no shoulder, no joint."""
    bones, mesh, pearl = api['BONES'], api['mesh'], api['PEARL']
    cx, cy, cz = FIN_CENTRE
    for side, suffix in [(-1,'L'), (1,'R')]:
        centre = Vector((side*cx, cy, cz))
        along = Vector((side*math.sin(FIN_TILT), 0, -math.cos(FIN_TILT)))
        across = Vector((side*math.cos(FIN_TILT), 0, math.sin(FIN_TILT)))
        through = Vector((0, 1, 0))
        root = centre - along*FIN_HALF_LENGTH
        tip = centre + along*FIN_HALF_LENGTH
        # The bone runs the arm's own axis so a later pose can swing it without
        # a joint mesh; note 1 of the sheet forbids drawing one.
        bones[f'Arm.{suffix}'] = (tuple(root), tuple(tip), 'Body')
        verts, faces = [], []
        rings, n = 64, 64
        for j in range(rings+1):
            # Ring positions follow the ellipse's own angle, so the rings crowd
            # at the two ends where the surface turns fastest. Even spacing in t
            # left long thin quads there and a faceted tip.
            phi = math.pi*j/rings
            a, r = -math.cos(phi), math.sin(phi)
            base = centre + along*(FIN_HALF_LENGTH*a)
            for i in range(n):
                theta = 2*math.pi*i/n
                p = (base + across*(FIN_HALF_WIDTH*r*math.cos(theta))
                     + through*(FIN_HALF_THICK*r*math.sin(theta)))
                verts.append(tuple(p))
        for j in range(rings):
            for i in range(n):
                b = j*n+i
                faces.append((b, j*n+(i+1)%n, (j+1)*n+(i+1)%n, (j+1)*n+i))
        # Same shell material as the body (owner: "วัสดุเดียวกันกับตัวหุ่น").
        # GLOVE was a separate, softer white kept for the old four-digit gloves;
        # on a part that reads as one piece with the shell it showed as a duller
        # patch next to the body's pearl.
        mesh(f'Arm / flat fin {suffix}', verts, faces, pearl, f'Arm.{suffix}')
    return {}
    return {}
