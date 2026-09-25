"""Continuous, bounded surface poses for the isolated Rive motion study.

These functions move native mesh vertices; they never alter source image pixels.
"""
import math


def dome_point(x, y, yaw):
    """Project one shallow rigid dome, limited to eleven degrees of yaw."""
    angle = yaw * .19
    depth = 82 * math.sqrt(max(0, 1-(x/205)**2-(y/195)**2))
    return x*math.cos(angle)+depth*math.sin(angle), y


# Knuckles, tips and half widths, measured on palm-open-v4.png.
FINGERS = (
    ((452, 766), (198, 584), 130),
    ((551, 570), (452, 262), 116),
    ((707, 548), (705, 191), 114),
    ((844, 619), (930, 316), 106),
    ((940, 748), (1080, 478), 100),
)


def glove_point(u, v, curl, yaw, finger_index=None):
    """Bend the same five fingers through two joints, then project hand yaw.

    The cuff is outside the finger weights. Palm-to-knuckle weights are smooth,
    so closing the hand doesn't change its image, opacity, or attachment point.
    """
    x, y, z = u-660, v-1060, 0.0
    candidates = []
    for index, (base, tip, radius) in enumerate(FINGERS):
        if index != finger_index:
            continue
        dx, dy = tip[0]-base[0], tip[1]-base[1]
        length = math.hypot(dx, dy)
        ax, ay = dx/length, dy/length
        distance = (u-base[0])*ax+(v-base[1])*ay
        lateral = abs((u-base[0])*ay-(v-base[1])*ax)
        if distance > 0:
            w = min(1, distance/45)
            # tip is the centre of the rounded cap, not the last mesh row.
            # Include that cap in the articulated length to avoid folding its
            # outer rows back through the fingertip during the shrug.
            candidates.append((w, index, distance, length+radius+20, ax, ay))
    if candidates and curl:
        w, index, distance, length, ax, ay = max(candidates)
        bend = min(.64, max(0, curl)) * (.66 if index==0 else 1)
        joint = length*.48
        first, second = min(distance,joint), max(0,distance-joint)
        a1, a2 = bend*1.05, bend*1.35
        if bend > .0001:
            theta = a1*first/joint
            projected = joint*math.sin(theta)/a1
            depth = joint*(1-math.cos(theta))/a1
            if second:
                theta2 = a1+a2*second/(length-joint)
                projected += (length-joint)*(math.sin(theta2)-math.sin(a1))/a2
                depth += (length-joint)*(math.cos(a1)-math.cos(theta2))/a2
            x += (projected-distance)*ax*w
            y += (projected-distance)*ay*w
            z += depth*w
    if finger_index == 0 and curl:
        # Oppose the thumb at its base while the other fingers curl forward.
        bx,by = FINGERS[0][0][0]-660,FINGERS[0][0][1]-1060
        dx,dy = x-bx,y-by
        weight = min(1,max(0,math.hypot(dx,dy)/180))
        angle = curl*.90*weight
        x=bx+dx*math.cos(angle)-dy*math.sin(angle)
        y=by+dx*math.sin(angle)+dy*math.cos(angle)
    # The palm has a little volume; the wrist center stays pinned to the cuff.
    palm_depth = 32*max(0,1-(x/430)**2-((y+270)/360)**2)
    x = x*math.cos(yaw)+(z+palm_depth)*math.sin(yaw)
    return x, y
