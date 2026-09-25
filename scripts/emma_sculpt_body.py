"""Sculpted solid panels from the Emma source-sheet proportions.

The front view fixes proportions; depth is an interpretation of the three-quarter
and rear drawings. Curves below are symmetric construction curves, not claimed
orthographic recovery of the perspective hero image.
"""
import math
import os


#: Opt-in extra geometry for the projection study: vertical oval ring eyes that
#: sit exactly where the arch eyes sit, on the same bone, so they inherit every
#: head turn and pose for free (owner, 2026-09-24: "ตำแหน่งการหมุนทุกอย่างเหมือน
#: ตาเดิม"). Off unless EMMA_RING_EYES=1, so the shipping model is unchanged
#: byte for byte when anyone rebuilds it.
RING_EYES = os.environ.get("EMMA_RING_EYES") == "1"
#: Same single level-of-detail knob the build script reads. The heavy meshes
#: (the teardrop body, the chrome wave, the fins) are built HERE, so scaling
#: only the build script's primitives left two thirds of the triangles in
#: place — measured: 162,380 -> 109,016 with this module untouched.
# Separate from the build script's knob on purpose: reducing the SCULPTED
# surfaces (the teardrop body, the rolled edges) damaged them — the render came
# back with dark speckles across the body — while reducing the smooth
# primitives did not. Two knobs so the safe half can be used without the other.
LOD = float(os.environ.get("EMMA_SCULPT_LOD", os.environ.get("EMMA_LOD", "1")) or 1)


def _n(count, floor=6):
    return max(floor, int(round(count / LOD)))
#: A SOLID capsule, not a ring (owner, 2026-09-24: "เอาเป็นแบบทึบไปเลย ไม่ต้อง
#: มีพื้นที่ [ตรงกลาง]"), and it must clear the chrome wave.
#:
#: The clearance is measured off the built model, not guessed: in the GLB the
#: chrome saddle's top sits at y 2.894 and the first ring eye reached down to
#: 2.870 — 24 thousandths INTO it, which is exactly what the owner saw. The
#: arch eyes stop at 2.930, so that is the floor this shape respects too.
#: Half-width, half-height, centre height (build frame, GLB y = z + 3.190).
EYE_W, EYE_H, EYE_Z = .125, .205, -.045


def build_body(api):
    for name in ("bpy", "PI", "mesh", "ellipsoid", "ring", "segment", "tube",
                 "bezier", "patch", "face_y", "HZ", "RX", "RZ", "DEPTH",
                 "CHROME", "PEARL", "EYE", "DARK", "LIGHT", "material"):
        globals()[name] = api[name]
    build_head()
    build_torso()


def curve(*points, count=32):
    return bezier(*points, count=count)


def through(points, count):
    """Uniform Catmull-Rom curve THROUGH the given (x, z) points, both ends included.

    The W wave and the chin cup are traced from the sheet as edge samples, so the
    curve has to pass through the measured points instead of through bezier
    handles guessed to look like them.
    """
    P = [(float(x), float(z)) for x, z in points]
    ext = [(2*P[0][0]-P[1][0], 2*P[0][1]-P[1][1])] + P + [(2*P[-1][0]-P[-2][0], 2*P[-1][1]-P[-2][1])]
    segs = len(P)-1
    out = []
    for i in range(count):
        f = segs*i/(count-1)
        k = min(int(f), segs-1); u = f-k
        p0, p1, p2, p3 = ext[k], ext[k+1], ext[k+2], ext[k+3]
        out.append(tuple(.5*(2*p1[d] + (-p0[d]+p2[d])*u + (2*p0[d]-5*p1[d]+4*p2[d]-p3[d])*u*u
                             + (-p0[d]+3*p1[d]-3*p2[d]+p3[d])*u*u*u) for d in (0, 1)))
    return out


def smoothstep(q):
    q = max(0.0, min(1.0, q))
    return q*q*(3-2*q)


# ---------------------------------------------------------------------------
# Head constants (2026-09-23 head pass). Measured on the sheet panels head_front,
# head_side, head_back at x4 bicubic; unit 1 HW = head width in the same view =
# 2.32 lab (RX 1.16, frozen). Lab z is anchored at the sheet's EQUATOR: the centre
# of the circle fitted to the upper 65 % of the head_front outline (D = 0.997 HW,
# rms 0.01 HW). That centre sits 0.0224 HW below the whole-outline circle the head
# report quoted its v values from, so report v -> lab z = (v + 0.0224) * SIL.
# SIL = 2.371: the sheet's HW is its SILHOUETTE (rim included) and so is the
# render's in every measuring pipeline (compare tool anchor, mh_*.py: 402 px =
# 2.367 u); the lab silhouette is the band crest 2 x 1.1855, not the 2.32 shell.
# Converting with 2.32 put every face feature 2 % small and inward (build 1:
# eye centre u 0.237 vs sheet 0.242).
# ---------------------------------------------------------------------------
SIL = 2.371
# Lower hemisphere. The sheet head is a sphere whose lower half is pulled up: the
# outline sits 0.042 HW (head_front) / 0.036 HW (head_back) above the fitted
# sphere at the bottom, the shift growing smoothly from ~0.015 HW at the equator
# (rows measured every 8 px, all three panels). Modelled as a lower half-
# superellipse (r/RX)^2 + (|z|/RZ_LOW)^P_LOW = 1 (upper RZ 1.11 stays frozen):
# RZ_LOW 1.045 puts the shell end at -1.045 (PROPORTIONS: -1.01..-1.05; sheet
# bottom in the SIL frame: full_front chin -1.064, head_front -1.083). P_LOW
# sets how full the lower sides are; the two panels disagree (SIL frame half-
# widths at z -0.71 / -0.84 / -0.93: head_front 0.850 / 0.679 / 0.559, head_back
# 0.903 / 0.736 / 0.643; the front panel's lower sides are partly the dark
# chrome cheeks dropping out of the mask). Build 2 (ellipse 1.04) gave 0.861 /
# 0.694 / 0.51 and fit the front (box IoU .966 / side .958 / back .953); build 3
# (1.05, P 2.3) gave 0.907 / 0.746 / 0.569 and fit the back (.9576) but lost
# .01 on the front and side. P 2.15 is the middle: 0.885 / 0.721 / 0.543.
# Band crest -1.051 -> head height 1.134 + 1.051 = 2.185 = 0.922 HW (sheet
# head_front 0.955, full_front 0.927); chin-to-torso-top gap 3.26 - 1.051 -
# 2.12 = 0.089 u = 0.038 HW (sheet 0.047: the frozen RZ makes the upper head
# 0.044 u shorter than the sheet's sphere, and that comes out of this gap).
RZ_LOW = 1.045
P_LOW = 2.15
# Chrome band. head_side (a ~21 deg three-quarter view, corrected): projected
# 0.28 SW at the equator, widening to 0.36 SW at the top crossing and narrowing to
# 0.22 SW at the bottom -> true half-widths ~.33 / .40 / .26. The sides use .36
# instead of .33 because the FRONT rim has to read 0.046 HW (head_front, 0/180 deg)
# and the crest is capped: rim = crest - front edge = 1.1855 - 1.1554*sqrt(1-(hw/1.08)^2)
# = .096 lab = 0.041 HW at hw .36 (0.035 at .33, 0.043 at .37; build 1 at .37 read
# 0.053/0.065 HW vs the sheet's 0.060/0.063 with the chrome-mask extractor, and
# 0.31 HW wide from the side vs the sheet's 0.28: .36 splits the two panels).
# Top .42 with the tilt below: front rim 1.134 - 1.003 = .131 = 0.055 HW (sheet
# 0.055), back rim 1.134 - 1.032 = .102 = 0.043 HW (sheet 0.049; build 1 at
# tilt -.05 / top .40 read 0.061 front and only 0.03 back).
BAND_HW = .36
BAND_HW_TOP = .42      # uz = +1
BAND_HW_BOTTOM = .27   # uz = -1
# Tilt: the sheet's top crossing is +0.02..+0.03 SW in FRONT of the pole and the
# bottom crossing -0.015 behind it (2.5 +- 1.5 deg, top toward the face). Was
# +.13 = 7.4 deg the other way. -.03 = 1.7 deg (y = tilt*RZ*uz, front = -y);
# -.05 starved the back rim at the top (see BAND_HW_TOP).
BAND_TILT = -.03
# Bulge: .026 is the PROPORTIONS crest cap (RX*(.996+.026) = 1.1855 <= 1.185, the
# silhouette stays 2.37). The head report's .06 would grow the unit by 3 %, so the
# wider band does the rim's work instead. Below the equator the bulge fades to
# .008 at the bottom pole because the front rim under the chin is a hairline on
# the sheet (225-315 deg: 0.005 HW) while the band must still show from the side.
BAND_BASE = .996
BAND_BULGE = .026
BAND_BULGE_BOTTOM = .008
# White face plate and chin cup end AT the band's front edge (head_side: white
# meets the band directly, transition <= 2 native px; no separate chrome frame).
# Build 1 had them 1 % inside and the plate's .026 proudness made a dark seam
# 0.06 u in front of the band in side.png; now the edge is on the band line and
# the plate's proudness fades out over the last 0.07 of its radius (PLATE_FADE).
FACE_INSET = 1.0
PLATE_FADE = (.93, .07)


def lower_z(frac):
    """z of the lower shell where the normalised horizontal radius is sqrt(1-frac^2).

    frac = |sin(elevation)| * (ellipsoid scale), i.e. |z|/RZ on the frozen
    ellipsoid; the superellipse maps it to -RZ_LOW*frac^(2/P_LOW).
    """
    return -RZ_LOW*max(0.0, frac)**(2/P_LOW)


def shell_z_scale(z):
    """Vertical remap for a point of the frozen ellipsoid (head frame)."""
    return z if z >= 0 else lower_z(-z/RZ)


def head_shell_y(x, z):
    """Front surface of the HEAD shell (head frame), proud by .026 like face_y.

    Named head_* because the torso section below defines its own shell_y for
    the body; the merge of the head and torso passes (2026-09-23) would
    otherwise let the later torso definition silently replace this one.
    """
    if z >= 0: depth = 1-(x/RX)**2-(z/RZ)**2
    else: depth = 1-(x/RX)**2-(-z/RZ_LOW)**P_LOW
    return -DEPTH*math.sqrt(max(0.025, depth)) - 0.026


def band_hw(uz):
    """Band half-width along the x-z great circle (uz = sin of the elevation)."""
    return BAND_HW + (BAND_HW_TOP-BAND_HW)*max(0, uz) + (BAND_HW-BAND_HW_BOTTOM)*min(0, uz)


def band_bulge(uz):
    return BAND_BULGE_BOTTOM + (BAND_BULGE-BAND_BULGE_BOTTOM)*smoothstep((uz+.8)/.6)


def band_edge(t, side=-1):
    """(x, z) of the band's front (-1) or back (+1) edge at angle t in the x-z plane."""
    ux, uz = math.cos(t), math.sin(t)
    y = side*band_hw(uz) + BAND_TILT*RZ*uz
    s = BAND_BASE*math.sqrt(max(0, 1-(y/DEPTH)**2))
    return RX*ux*s, (RZ*uz*s if uz >= 0 else lower_z(-uz*s))


def band_edge_angle(z_target):
    """Angle t in [-pi/2, 0] (right side, below the equator) where the inset front edge is at z."""
    lo, hi = -PI/2, 0.0
    for _ in range(60):
        mid = (lo+hi)/2
        if band_edge(mid)[1]*FACE_INSET < z_target: lo = mid
        else: hi = mid
    return (lo+hi)/2


def rim_surface(name, boundary_a, boundary_b, yfunc, offset, bulge, bone):
    """Rolled surface of varying width, rather than a constant-diameter tube."""
    verts, faces = [], []
    count = len(boundary_a)
    rows = _n(16, 6)
    for j in range(rows + 1):
        t = j / rows
        for a, b in zip(boundary_a, boundary_b):
            x = a[0] * (1-t) + b[0] * t
            z = a[1] * (1-t) + b[1] * t
            verts.append((x, yfunc(x, z)-bulge*math.sin(math.pi*t), z+offset))
    for j in range(rows):        # rows, not a hardcoded 16: the vertex loop
        for i in range(count-1):  # above builds rows+1 rings, and pointing
            a = j*count+i         # faces past them crashed Blender outright
            faces.append((a, a+1, a+count+1, a+count))
    obj = mesh(name, verts, faces, CHROME, bone)
    mod = obj.modifiers.new("Rolled metal wall", "SOLIDIFY")
    mod.thickness = .018
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)
    return obj


def side_band(name):
    """Chrome strip following the egg around the x-z great circle.

    Points with |y - tilt*RZ*uz| < band_hw(uz), raised by a rounded bulge that
    returns flush at both edges so the strip meets the pearl without a step.
    The cross-section is cos^0.7: a flatter crest with a quicker roll-off at
    the edges (hero: a rolled bezel with a crest highlight, not a dome).
    """
    verts, faces = [], []
    n, m = 256, 12
    for j in range(m+1):
        s = -1+2*j/m
        profile = math.cos(PI*s/2)**.7
        for i in range(n):
            t = 2*PI*i/n
            ux, uz = math.cos(t), math.sin(t)
            lift = BAND_BASE+band_bulge(uz)*profile
            y = band_hw(uz)*s + BAND_TILT*RZ*uz
            scale = math.sqrt(max(0, 1-(y/DEPTH)**2))
            z = RZ*uz*scale if uz >= 0 else lower_z(-uz*scale)
            verts.append((RX*ux*scale*lift, y, HZ+z*lift))
    for j in range(m):
        for i in range(n):
            a = j*n+i; b = j*n+(i+1)%n
            faces.append((a, b, b+n, a+n))
    return mesh(name, verts, faces, CHROME, "Head")


def build_head():
    # The shell is pearl, not chrome (2026-09-23 review). With a chrome shell
    # every gap between the front and rear enamel projections showed as metal:
    # a coin rim from the front and a 36%-wide band from the side. The
    # reference head is a white ball with ONE chrome band over the side.
    shell = ellipsoid("Head / pearl shell", (0,0,HZ), (RX,DEPTH,RZ), PEARL, "Head",128,80)
    # Lower hemisphere -> superellipse (see the constants block).
    for v in shell.data.vertices:
        if v.co.z < 0: v.co.z = shell_z_scale(v.co.z)
    shell.data.update()
    side_band("Head / chrome side band")

    # White face plate: bounded by the band's front edge (inset 1 %) from the
    # left W end over the top to the right W end. head_front inner rim radius
    # (white starts): 90 deg 0.419 HW = .972 lab, 45 deg .438 = 1.016,
    # 0/180 deg .455 = 1.056; this edge gives .973 / 1.005 / 1.075.
    # The W's upper edge meets the rim at v -0.20 HW (chrome cheek 205-218 deg)
    # = z -0.421 lab (SIL frame).
    t_arc = band_edge_angle(-0.421)
    arc = []
    for i in range(129):
        t = (PI-t_arc) + (t_arc-(PI-t_arc))*i/128     # left end -> top -> right end
        x, z = band_edge(t)
        arc.append((x*FACE_INSET, z*FACE_INSET))
    # Chrome cheek: the saddle's lower edge runs on to the band edge at z -0.611
    # (head_front: chrome to the outline for |u| > 0.36, v -0.20..-0.28).
    t_cup = band_edge_angle(-0.611)
    corner = tuple(c*FACE_INSET for c in band_edge(t_cup))
    # W wave edges, sheet head_front samples averaged left/right (u, v in HW ->
    # x = SIL*u, z = SIL*(v+0.0224)); upper edge / lower edge:
    #   u 0     -0.285/-0.353   u .07 -0.269/-0.338   u .105 -0.242/-0.314
    #   u .14   -0.204/-0.274   u .21 -0.147/-0.203   u .245 -0.136/-0.190 (hump)
    #   u .28   -0.139/-0.194   u .315 -0.158/-0.229  u .35  -0.184/-0.266
    #   u .37-.39 upper -0.193/-0.197, then the rim.
    # Dip 0.157 HW thick, humps 0.127, x .325 0.161, ends 0.192 (thickening).
    K = SIL/2.32   # the lists below were first written in the 2.32 frame
    # dz: the hairline tubes (r .009) on both boundaries read as chrome, so the
    # rendered saddle measured .018 thicker than its boundary in build 2, with
    # the hump tops 0.008 HW and the ends 0.013 HW above the sheet (dip exact):
    # upper edge down .009 (+ .01 at the humps, + .02 at the ends), lower edge up .009.
    upper_r = [arc[-1]] + [(x*K, z*K-dz) for x, z, dz in
               [(0.905,-0.405,.029), (0.812,-0.374,.029), (0.731,-0.314,.019), (0.650,-0.270,.019),
                (0.568,-0.263,.019), (0.487,-0.290,.019), (0.406,-0.350,.014), (0.325,-0.422,.009),
                (0.244,-0.508,.009), (0.162,-0.572,.009), (0.081,-0.599,.009), (0,-0.610,.009)]]
    lower_r = [corner] + [(x*K, z*K+.009) for x, z in
               [(0.812,-0.566), (0.731,-0.480), (0.650,-0.399), (0.568,-0.389),
                (0.487,-0.418), (0.406,-0.500), (0.325,-0.583), (0.244,-0.677),
                (0.162,-0.733), (0.081,-0.764), (0,-0.768)]]
    def mirrored(right):
        return right + [(-x, z) for x, z in reversed(right[:-1])]
    top = through(mirrored(upper_r), 129)      # right rim end -> left rim end
    bottom = through(mirrored(lower_r), 129)   # right corner -> left corner
    front = arc + top
    ax = max(x for x, z in arc); az = max(z for x, z in arc)
    def plate_y(x, z):
        # .026 proud in the middle, 0.004 proud at the arc edge (rho ~ 1), so the
        # plate meets the band's sunk edge without a wall.
        rho = math.sqrt((x/ax)**2+(max(z, 0)/az)**2)
        return head_shell_y(x, z)+.022*smoothstep((rho-PLATE_FADE[0])/PLATE_FADE[1])
    patch("Face / convex pearl enamel",front,(0,.10),PEARL,yfunc=plate_y)
    tube("Face / fine rolled edge",[(x,plate_y(x,z)-.003,HZ+z) for x,z in front+[front[0]]],.009,CHROME,"Head")

    # Chin cup: its sides ARE the band's front edge (head_front rows z -0.78..-1.0:
    # white half-widths .70/.59/.47/.30 = the outline inset 0.02-0.03 HW, i.e. the
    # rim) down to z -0.95; below that it leaves the band edge and wraps UNDER the
    # chin to z -1.048 (head_side: at the bottom crossing "the white chin cup sits
    # in front of the band's front part"; head_front 225-315 deg: a 0.005 HW
    # hairline of chrome under the cup). Builds 1-3 ended the cup at the band
    # edge's bottom (-1.024) and showed 0.021-0.035 HW of chrome under it.
    def outline_x(z):
        return RX*math.sqrt(max(0, 1-(-z/RZ_LOW)**P_LOW))
    side_r = []
    for i in range(41):
        t = t_cup + (-PI/2-t_cup)*i/40
        bx, bz = band_edge(t)
        w = smoothstep((-0.95-bz)/0.08)
        side_r.append((bx*FACE_INSET*(1-w)+(outline_x(bz)-.02)*w, bz))
    for z in (-1.031, -1.038, -1.043, -1.046):
        side_r.append((outline_x(z)-.02, z))
    side_r.append((0, -1.048))
    chin = bottom + [(-x, z) for x, z in side_r[1:]] + [(x, z) for x, z in reversed(side_r[1:-1])]
    # Cache vertical cross-sections of the actual cup outline. The additional
    # crown must meet the chrome at zero height all along the irregular W edge.
    cup_min=min(x for x,z in chin); cup_max=max(x for x,z in chin)
    cup_sections=[]
    for i in range(801):
        x=cup_min+(cup_max-cup_min)*i/800
        crossings=[]
        for a,b in zip(chin,chin[1:]+chin[:1]):
            if min(a[0],b[0])<=x<=max(a[0],b[0]) and abs(b[0]-a[0])>1e-8:
                crossings.append(a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0]))
        cup_sections.append((min(crossings),max(crossings)) if crossings else (-.7,-.7))
    def chin_y(x,z):
        # The reference lower cup has its own padded crown. Merely cutting a
        # second white region out of the main ellipsoid looked like a decal.
        sample=max(0,min(799.999,(x-cup_min)/(cup_max-cup_min)*800))
        i=int(sample); t=sample-i
        low=cup_sections[i][0]*(1-t)+cup_sections[i+1][0]*t
        high=cup_sections[i][1]*(1-t)+cup_sections[i+1][1]*t
        u=max(0,min(1,(z-low)/max(.001,high-low)))
        pad=math.sin(PI*u)*max(0,1-(x/.95)**2)
        return head_shell_y(x,z)-.068*pad**1.5
    patch("Chin / full lower enamel cup",chin,(0,-.90),PEARL,yfunc=chin_y)
    tube("Chin / fine rolled edge",[(x,chin_y(x,z)-.003,HZ+z) for x,z in chin+[chin[0]]],.009,CHROME,"Head")
    # Hero: a half-round rolled chrome ridge with a crest highlight and a soft
    # shadow under it; bulge .053 -> .065 for the fuller roll.
    rim_surface("Smile / broad sculpted chrome saddle",top,bottom,head_shell_y,HZ,.065,"Head")

    # Eyes (head_front, both eyes averaged, SIL frame): bbox u +-(0.126..0.358)
    # -> x +-(0.298..0.848), centre +-.574, half-width .276; v -0.128 (tips) ..
    # +0.049 (top) -> z -0.250..+0.169, centre -0.04, height .42. Stroke: top
    # 0.071-0.075 HW, legs 0.055, tips 0.046-0.048 ROUNDED (hero dark core:
    # uniform 0.066-0.077 HW along the whole arc, blunt tips); inner opening
    # 0.119 HW = .28 wide, .25 deep. Modelled as a stroke along an elliptical
    # arch centreline with semicircular caps: A .219, B .343, z0 -.260, tips at
    # phi0 12 deg, half-thickness .058 + .028*sin(phi)^1.3 (.086 top / .069
    # legs / .062 tips: 0.006 u over the sheet because the bead's highlight
    # thins the dark reading; build 1 at .080/.065/.058 read legs 0.046 vs
    # 0.055, area 0.018 vs 0.022). The old crescent tapered to a point (tips
    # 0.012 HW, area 2.2x too small).
    for side in (-1,1):
        cx, A, B, z0, phi0 = side*.574, .219, .343, -.260, math.radians(12)
        def centre(phi, cx=cx):
            return cx+A*math.cos(phi), z0+B*math.sin(phi)
        def normal(phi):
            nx, nz = B*math.cos(phi), A*math.sin(phi)
            nl = math.hypot(nx, nz)
            return nx/nl, nz/nl
        def width(phi):
            return .058+.028*math.sin(phi)**1.3
        N = 72
        phis = [phi0+(PI-2*phi0)*i/N for i in range(N+1)]
        boundary = []
        for phi in phis:                                  # outer edge, right tip -> left tip
            (px, pz), (nx, nz), w = centre(phi), normal(phi), width(phi)
            boundary.append((px+w*nx, pz+w*nz))
        def cap(phi, direction):
            (px, pz), (nx, nz), w = centre(phi), normal(phi), width(phi)
            tx, tz = -A*math.sin(phi), B*math.cos(phi)     # tangent of increasing phi
            tl = math.hypot(tx, tz); tx, tz = tx/tl*direction, tz/tl*direction
            pts = []
            for k in range(1, 12):
                a = PI*k/12
                pts.append((px+w*(math.cos(a)*nx+math.sin(a)*tx), pz+w*(math.cos(a)*nz+math.sin(a)*tz)))
            return pts
        boundary += cap(phis[-1], +1)                     # left tip: outer -> down -> inner
        for phi in reversed(phis):                        # inner edge, left tip -> right tip
            (px, pz), (nx, nz), w = centre(phi), normal(phi), width(phi)
            boundary.append((px-w*nx, pz-w*nz))
        boundary += [(x, z) for x, z in reversed(cap(phis[0], -1))]   # right tip
        def eye_y(x, z, cx=cx):
            phi = math.atan2((z-z0)/B, (x-cx)/A)
            phi = max(phi0, min(PI-phi0, phi))
            px, pz = cx+A*math.cos(phi), z0+B*math.sin(phi)
            u = min(1.0, math.hypot(x-px, z-pz)/width(phi))
            # Half-round glossy bead over the plate: convex everywhere, so it
            # catches one highlight along its upper edge (hero).
            return head_shell_y(x, z)-.005-.055*math.sqrt(max(0, 1-u*u))
        patch("Eye / rounded arch "+str(side),boundary,(cx,z0+B*.5),EYE,yfunc=eye_y,spacing=.007)

    if RING_EYES:
        build_solid_eyes()


def build_solid_eyes():
    """Two solid lit capsules, where the arch eyes are, on the same bone.

    Solid, so there is no hole and no inner mesh to keep in step. A capsule
    (straight sides, semicircular caps) rather than an ellipse: the reference
    the owner picked reads as a rounded bar, and an ellipse of the same
    proportions looks pinched at top and bottom next to the chrome.

    One closed loop, so `patch()` triangulates it directly — the ring needed a
    keyhole slit to make its hole, and the slit was visible on the outline.
    """
    # Emission 6.0, the same strength as the chest lamp ("White light"). The
    # first pass used 1.0 and the eyes came out as pale slivers on a pearl
    # head — measured in the GLB: the chest material carried
    # KHR_materials_emissive_strength and this one did not, which is the whole
    # difference between a lamp and a light-grey patch.
    eye_light = material("Eye light", (0.72, 0.90, 1.0), metallic=0.0,
                         roughness=0.25, emission=6.0, coat=0.30, coat_roughness=0.10)
    straight = EYE_H - EYE_W                       # half-length of the flat sides
    for side in (-1, 1):
        cx = side*.574
        # A stadium: the two caps ARE the loop, and the straight sides are the
        # edges that close it. The first attempt walked the right half of the
        # top circle and then the left half of the bottom one, which is not a
        # capsule at all — it rendered as a comma, and that is what the owner
        # was looking at when he said the eyes were not pretty.
        boundary, N = [], 24
        top, bot = EYE_Z + straight, EYE_Z - straight
        for i in range(N+1):                        # over the top, right -> left
            a = PI*i/N
            boundary.append((cx + EYE_W*math.cos(a), top + EYE_W*math.sin(a)))
        for i in range(N+1):                        # under the bottom, left -> right
            a = PI + PI*i/N
            boundary.append((cx + EYE_W*math.cos(a), bot + EYE_W*math.sin(a)))
        def eye_y(x, z, cx=cx):
            # A shallow dome so the lit face still catches a highlight instead
            # of reading as a flat sticker.
            u = min(1.0, math.hypot((x-cx)/EYE_W, (z-EYE_Z)/EYE_H))
            return head_shell_y(x, z) - .026 - .018*math.sqrt(max(0, 1-u*u))
        patch("Eye / solid "+str(side), boundary, (cx, EYE_Z), eye_light,
              yfunc=eye_y, spacing=.006)


# ---------------------------------------------------------------------------
# Torso (torso pass, 2026-09-23). Every number below was measured on the concept
# sheet crops (scratchpad/emma-ref, x4 bicubic; torso_front W = 580 px = 1.30 lab
# units, torso_side W_side = 592 px, torso_back W_back = 601 px; full_front and
# full_back at 106.5 / 109.25 px per head width). Frozen by PROPORTIONS.md and
# NOT touched: shell z .79 (tip) .. 2.12 (top), max half-width .655 at z ~1.63,
# BODY_DEPTH .88 at the widest (shell y +-.577), collar ring positions and
# diameters (z 2.115 r .375, z 2.18 r .355, cup r .345), chest light z 1.62.
#
# Front silhouette, half-width in W at 13 rows from the shell top edge (z 2.078)
# to the tip (z .79), torso_front x4:
#   sheet .303 .397 .460 .491 .499 .488 .458 .419 .370 .311 .247 .164 .022
# The old profile matched rows 2-12 within +-.013 u but closed to a point at
# the top (.230 / .458 u at rows 0-1 vs .393 / .516 u): the sheet shell is a
# broad dome that the collar cup sits in. New rows 0-1 give .414 / .539 u,
# i.e. +.02 u over the torso panel, on purpose: full_front reads the torso
# edge at the shoulder-ball row (z 2.01) as .58 u and PROPORTIONS.md asks for
# r >= .55 there so the ball at x .68 (r .14) seats; .50 at z 2.01 is the
# midpoint of the two panels. Rows 6-9 (z 1.43-1.11) were +.011-.013 u wide
# in build 1 (compare tool width profile, rows 13-19: +.04 anchor after the
# half-row offset), so those knots came in by .008-.010; the tip knots went
# up .006 because rows 11-12 read .010 narrow (sheet tip radius .15-.21 W).
# The flat cap r .372 at z 2.12 hides under the lower collar ring (inner edge
# .351 at z 2.091-2.139) and the cup (r .345).
PROFILE = [(.79,.024),(.83,.118),(.94,.248),(1.12,.410),(1.38,.577),(1.63,.655),
           (1.79,.630),(1.97,.540),(2.08,.412),(2.12,.372)]
# Depth factor .88 at the widest (proportions lock, frozen): full_side torso
# depth 55.25 px vs front width 58 = 0.95; torso_side/torso_front .845-.874
# (500 px of W_side 592 excl. the light puck = 1.10 u). .88 -> 1.154 u total.
BODY_DEPTH = .88


def depth_at(z):
    # torso_side x4 vs the build-1 side render (compare tool width profile,
    # torso-only, rows 15-22 = z ~1.35 down to ~.85): the sheet is deeper
    # than the front-width * .88 scaling by 5% at z 1.35, 12% at 1.1, 21% at
    # 1.0 and 32% at .9 (sheet .60 u deep at z 1.0 where it is .56 wide; the
    # side tip is a rounded nose .20 u wide in its last row, the front tip a
    # point). The factor therefore rises below the widest row only; the
    # widest (z 1.63) and the light (z 1.62) keep the frozen .88. Build 2
    # with +.17 still read -.02..-.05 anchor at rows 20-22, hence +.24.
    t=max(0,min(1,(1.5-z)/(1.5-.79)))
    return BODY_DEPTH+.24*(t*t*(3-2*t))


def lean_at(z):
    # torso_side x4: the lower third of the outline sits forward of the collar
    # axis (front edge further out than the rear by .02-.06 u, tip centre
    # ~.035 u forward; rear silhouette near-vertical over z 1.45-1.75). A
    # forward shift of the section centre that starts below z 1.45 reproduces
    # both without touching the front-view silhouette or the frozen extents.
    # Build 2 at -.035 left the front edge .025-.054 u short at rows 20-22
    # and the rear .007-.015 u short, so -.05 here.
    t=max(0,min(1,(1.45-z)/(1.45-.79)))
    return -.05*(t*t*(3-2*t))


# Plan section (new). torso_side x4: the white front panel reaches 71% of the
# depth from the front (band front edge x 450 of 96..595 for rows 235-350),
# the chrome band is 0.13-0.18 W (.17-.23 u) wide at mid height, and the rear
# panel is a thin sliver (x 530-585, 11-13%). torso_back shows the rear panel
# filling the same outline as the front with the same rim (.027-.033 W): only
# a section that is domed at the front and flat at the back, with its widest
# point BEHIND mid-depth, is consistent with all three panels; an ellipse puts
# the band at 50% of the depth and, with a .035 rim, makes it 32% wide.
# The section is two cubic Beziers per side in units of the half-width r:
# apex (0,-D) -> corner (1,S) -> back centre (0,D), D = BODY_DEPTH, so the
# y extents +-D*r are the frozen ones. S .35 puts the widest point at 70% of
# the depth (sheet ~79%); the corner control lengths (.20 front, .14 back)
# give a corner radius ~.33 u so the band comes out 21% of the depth (sheet
# 17%) while the front stays a smooth dome (sheet hero: soft highlight, no
# facet); a tighter corner (13% band) needed straight flanks. Measured with
# scratchpad/torso-work/explore.py: front panel 56% / band 21% / rear 23%.
SECTION_SHIFT = .35
SECTION_FRONT = (.62, .20)   # (apex tangent length, corner tangent length)
SECTION_BACK = (.14, .75)    # (corner tangent length, back-centre tangent length)


def radius_at(z):
    for i in range(len(PROFILE)-1):
        if z <= PROFILE[i+1][0]:
            z0,r0=PROFILE[i];z1,r1=PROFILE[i+1]
            left=PROFILE[max(0,i-1)];right=PROFILE[min(len(PROFILE)-1,i+2)]
            m0=(r1-left[1])/(z1-left[0]);m1=(right[1]-r0)/(right[0]-z0)
            t=max(0,(z-z0)/(z1-z0))
            return max(.008,(2*t**3-3*t*t+1)*r0+(t**3-2*t*t+t)*(z1-z0)*m0
                       +(-2*t**3+3*t*t)*r1+(t**3-t*t)*(z1-z0)*m1)
    return PROFILE[-1][1]


def _cubic(p0, p1, p2, p3, count):
    pts=[]
    for i in range(count+1):
        t=i/count; u=1-t
        pts.append((u**3*p0[0]+3*u*u*t*p1[0]+3*u*t*t*p2[0]+t**3*p3[0],
                    u**3*p0[1]+3*u*u*t*p1[1]+3*u*t*t*p2[1]+t**3*p3[1]))
    return pts


def _section_quarters(count):
    D,S=BODY_DEPTH,SECTION_SHIFT
    kf1,kf2=SECTION_FRONT; kb1,kb2=SECTION_BACK
    front=_cubic((0,-D),(kf1,-D),(1,S-kf2*(S+D)),(1,S),count)      # apex -> corner
    back=_cubic((1,S),(1,S+kb1*(D-S)),(kb2,D),(0,D),count)         # corner -> back centre
    return front,back


# Unit loop of the plan section, 128 points, apex first, going +x then round
# the back: the shell rings are this loop scaled by radius_at(z) in x and by
# radius_at(z)*depth_at(z)/BODY_DEPTH in y, then shifted by lean_at(z).
_QF,_QB=_section_quarters(32)
UNIT_LOOP=(_QF[:-1]+_QB[:-1]+[(-x,y) for x,y in reversed(_QB[1:])]
           +[(-x,y) for x,y in reversed(_QF[1:])])
# Dense lookup tables (x monotonic in both quarters) for y at a given x/r.
_TF,_TB=_section_quarters(400)
_TB=list(reversed(_TB))   # x increasing


def _lookup(table, u):
    lo,hi=0,len(table)-1
    while hi-lo>1:
        mid=(lo+hi)//2
        if table[mid][0]<=u: lo=mid
        else: hi=mid
    (x0,y0),(x1,y1)=table[lo],table[hi]
    t=0 if x1==x0 else (u-x0)/(x1-x0)
    return y0+(y1-y0)*max(0,min(1,t))


def shell_y(x, z, front=True):
    """Surface y of the torso shell at (x, z) on the front (-y) or rear face."""
    r=radius_at(z)
    u=min(1,abs(x)/r)
    return r*_lookup(_TF if front else _TB, u)*depth_at(z)/BODY_DEPTH+lean_at(z)


def rim_inset(z):
    # torso_front x4: white panel inset inside the chrome outline .028-.038 W
    # at six angles (mean .033 W = .043 u) and .050 W (.065 u) at the tip;
    # torso_back the same (.027-.033 W sides, .05 W tip). The rolled edge
    # tube (r .009) sits on the panel edge, so the panel boundary is set
    # .036 inside the outline (chrome seen = .036 + .009 = .045; build 1 with
    # .035 measured .035-.041 on the render) and .062 at the tip (build 1
    # with .056 measured a .053 chrome tip, sheet .065). The flare starts
    # only below z .95: rows 225/315 deg read .028/.031 W, no wider than the
    # sides, and build 1 read .047 at z 1.1-1.2 with a flare from z 1.0.
    t=max(0,min(1,(.95-z)/(.95-.79)))
    return .036+.026*(t*t*(3-2*t))


def eroded_half_width(z):
    """Half-width of the front-view outline eroded by rim_inset(z) at height z.

    Exact offset of the outline r(z) (a disc of radius d around the point must
    stay inside the outline), so the tip rounds itself and no bezier has to be
    hand-fitted to the profile: the old front patch pulled .086-.144 u away
    from the outline below z 1.3 (sheet .036-.049) because its curve was drawn
    by eye.
    """
    d=rim_inset(z)
    if z-d<.79 or z+d>2.12: return -1
    best=1e9
    for k in range(-48,49):
        zp=z+d*k/48
        best=min(best,radius_at(zp)-math.sqrt(max(0,d*d-(zp-z)**2)))
    return best


def panel_outline():
    # Top edge at z 2.085: the lower collar ring (z 2.115, tube r .024) starts
    # at 2.091 and the rolled edge tube (r .009) reaches 2.094, so panel and
    # ring meet with no chrome strip between (sheet: the white panel runs up
    # to the junction rim of the cup). Below that the outline is the eroded
    # silhouette down to where the erosion closes above the tip (z ~.85;
    # sheet patch bottom .855 u).
    right=[]
    z=2.085
    while z>.79:
        e=eroded_half_width(z)
        if e>=.004: right.append((e,z))
        elif right: break
        z-=.006
    return right+[(-x,z) for x,z in reversed(right)]


def revolved(name, z, profile, mat, n=96):
    n = _n(n, 32)
    """Closed surface of revolution about the y axis through (0, y, z).

    profile: list of (radius, y) points forming a closed loop; y may be a
    callable (x, z) -> y so a ring can follow the curved body; a radius-0
    point becomes a single apex vertex with a triangle fan to its neighbours."""
    verts,faces,rings=[],[],[]
    for rr,yy in profile:
        if rr<1e-9:
            rings.append(len(verts)); verts.append((0,yy(0,z) if callable(yy) else yy,z))
        else:
            rings.append(len(verts))
            for i in range(n):
                a=2*PI*i/n; x,zz=rr*math.cos(a),z+rr*math.sin(a)
                verts.append((x,yy(x,zz) if callable(yy) else yy,zz))
    m=len(profile)
    for j in range(m):
        k=(j+1)%m
        aj,ak=profile[j][0]<1e-9,profile[k][0]<1e-9
        if aj and ak: continue
        for i in range(n):
            i1=(i+1)%n
            if aj: faces.append((rings[j],rings[k]+i1,rings[k]+i))
            elif ak: faces.append((rings[j]+i,rings[j]+i1,rings[k]))
            else: faces.append((rings[j]+i,rings[j]+i1,rings[k]+i1,rings[k]+i))
    return mesh(name,verts,faces,mat,"Body")


def bump_annulus(name, z, r_in, r_out, offset, mat, steps=8):
    steps = _n(steps, 4)
    """Ring (or disc, r_in 0) standing offset(r) in front of the body shell,
    closed at the back .05 inside the shell."""
    front=[]
    for i in range(steps+1):
        rr=r_out-(r_out-r_in)*i/steps
        front.append((rr,(lambda o: (lambda x,zz: shell_y(x,zz,True)-o))(offset(rr))))
    back=lambda x,zz: shell_y(x,zz,True)+.05
    return revolved(name,z,front+[(r_in,back),(r_out,back)],mat)


def build_torso():
    # Materials that exist only on the torso (the sheet MATERIAL REFERENCE
    # row: BLACK DETAIL is a glossy black sphere with sharp reflections, and the
    # collar cup / chest ring read the same way in torso_front, gray 0-30 with
    # bright wall reflections 90-165). Same recipe as the black chrome eyes
    # (metallic .48): build 1 with a dielectric black + the helper's coat read
    # gray 80-130 on the flat chest ring. The chest centre disc is dark grey
    # with a soft gradient (gray 40 at the centre, 75 in full_front), not
    # chrome; build 1's .11 base read 160-178, so .045 here.
    # Build 2 (metallic .48) still averaged gray 67-85 on the chest ring and
    # 102-108 on the disc under the four softboxes, so both go more metallic
    # (reflection tinted by the near-black base) and the disc base darker.
    black_gloss=material("Black gloss detail",(0.004,0.005,0.006),0.70,0.10)
    # Centre disc: builds 3-5 read gray 198 / 169 / 166 at the centre (sheet
    # 40 at the centre, 75 in full_front, soft gradient). Measured cause: the
    # review scene's "Low bounce" card sits 12.5 deg under the camera axis, in
    # the specular lobe of any surface facing the camera (roughness .14 or .4
    # give the same lobe value there); the black ring escapes only because
    # its dome tilts the normals. So the disc is a dielectric with low
    # specular and almost no coat: what remains is its diffuse dark grey.
    # Build 6 then read 133-154 with base .040: under the four softboxes a
    # .04 albedo is mid grey and the .008 joints read 69, so the sheet's
    # 40-75 is a .010 albedo in this scene (viewer lighting differs anyway).
    graphite=material("Graphite satin",(0.010,0.011,0.012),0.0,0.35)
    bsdf=graphite.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Coat Weight"].default_value=0.06
    bsdf.inputs["Specular IOR Level"].default_value=0.2

    verts,faces=[],[]
    # The single heaviest mesh in the model: a 129 x 96 grid on a smooth blob,
    # 24,828 triangles, 24% of the whole thing (measured 2026-09-25). Both
    # directions follow the LOD knob now — the shape is settled, so the grid
    # only has to be fine enough to keep the silhouette and the chrome
    # highlight, not fine enough to sculpt with.
    loop = UNIT_LOOP if LOD <= 1.2 else UNIT_LOOP[::max(1, int(round(LOD)))]
    n,m=len(loop),_n(96,24)
    for j in range(m+1):
        z=.79+(2.12-.79)*j/m
        r=radius_at(z); dy=depth_at(z)/BODY_DEPTH; ly=lean_at(z)
        for ux,uy in loop: verts.append((r*ux,r*uy*dy+ly,z))
    for j in range(m):
        for i in range(n):faces.append((j*n+i,j*n+(i+1)%n,(j+1)*n+(i+1)%n,(j+1)*n+i))
    faces.extend([tuple(range(n-1,-1,-1)),tuple(m*n+i for i in range(n))])
    mesh("Body / rounded chrome teardrop",verts,faces,CHROME,"Body")

    outline=panel_outline()
    # Panels sit .006 proud of the shell with no crown, the rolled edge tube
    # (r .009) standing .003 past them: PROPORTIONS.md measured the old +.045
    # crown adding .09 u to the body depth in the side view, and build 1's
    # .012 panels still put the render .07 u deeper than torso_side.
    front_y=lambda x,z: shell_y(x,z,True)-.006
    rear_y=lambda x,z: shell_y(x,z,False)+.006
    patch("Body / rounded front enamel",outline,(0,1.55),PEARL,bone="Body",yfunc=front_y,zoffset=0)
    patch("Body / rounded rear enamel",outline,(0,1.55),PEARL,bone="Body",yfunc=rear_y,zoffset=0)
    tube("Body / fine front rolled edge",[(x,front_y(x,z)-.001,z) for x,z in outline+[outline[0]]],.009,CHROME,"Body")
    tube("Body / fine rear rolled edge",[(x,rear_y(x,z)+.001,z) for x,z in outline+[outline[0]]],.009,CHROME,"Body")

    # Neck collar: positions and diameters frozen (proportions lock: full_front
    # dark band 34 px = .32 HW, full_side .36 HW, torso_front cup .566 W ->
    # dia .69-.80 u). Finish per torso_front/back x4: ONE black glossy cup
    # (interior gray 0-30, sharp wall reflections) with a chrome rolled lip on
    # top; the lower ring is the junction rim where the shell meets the cup.
    # NO NECK (2026-09-23, owner on the dimensioned sheet: "ในแบบไม่มีคอนะ").
    # The sheet's FULL CHARACTER VIEWS show the ball sitting straight in the
    # body dome with only a contact shadow between them - no collar ring, no
    # black cup, no chrome lip. Two earlier passes kept shrinking that stack
    # instead of deleting it. The head is lowered (HZ) so the chin meets the
    # dome; the Neck bone stays because the look clip yaws on it.

    # Chest light: one lens set into the body, not a stack and not a cylinder.
    # torso_front x4 radial profile (r in px of W 580): chrome outer 147
    # (.507 W = .655 u), thin chrome edge 10 px (.022 u), glossy black ring
    # to 141 px (.486 W = .62 u) with a highlight along its top, luminous
    # ring outer 93 / inner 50 px (.415 / .21 u, peak at 64 px = .143 u),
    # centre disc = luminous inner (.21 u) DARK GREY. torso_side: the
    # front-most point is .05-.06 u in front of the body curve at the centre
    # rows only and the outline .3 u above/below the centre is ~.10 u behind
    # it, i.e. the body curve itself: the chrome rim FOLLOWS the body (a
    # rolled edge tube r .014 on the surface) and the face is a low dome
    # rising .043 over r .30 on top of a .012 step. Build 1 (flat face .055
    # proud, cylinder wall) read +.04-.09 anchor in side rows 5-9 and build 2
    # (short wall + dome) still +.03-.06. Height z 1.62 kept (full_front
    # 1.628, full_back 1.624; the torso panel alone says 1.47).
    z=1.62
    rim_pts=[]
    for i in range(129):
        a=2*PI*i/128; x,zz=.314*math.cos(a),z+.314*math.sin(a)
        rim_pts.append((x,shell_y(x,zz,True)-.010,zz))
    tube("Chest / chrome rim",rim_pts,.014,CHROME,"Body")
    dome=lambda r: .012+.043*(1-(r/.30)**2)
    bump_annulus("Chest / black glossy ring",z,.207,.300,dome,black_gloss)
    bump_annulus("Chest / luminous ring",z,.105,.200,lambda r:dome(r)+.004,LIGHT)
    bump_annulus("Chest / graphite centre disc",z,0,.105,lambda r:dome(.105)+.002,graphite)   # flat, level with the ring's inner edge

    # Rear service hatch: torso_back Hough circle r 138.4 px of W_back 601 =
    # d .46 W = .60 u; full_back circle fit .593 u; centred, z 1.624 in
    # full_back (kept at the light z 1.62). Faint seam ~2 px at x4: tube .004,
    # laid on the rear PANEL (build 1 put it on the shell, under the panel).
    hatch=[]
    for i in range(129):
        a=2*PI*i/128
        x,zz=.30*math.cos(a),z+.30*math.sin(a)
        hatch.append((x,rear_y(x,zz)+.003,zz))
    tube("Back / service hatch",hatch,.004,CHROME,"Body")
