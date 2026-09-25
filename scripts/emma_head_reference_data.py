"""Measured head contours in ORIGINAL sheet pixels, not model coordinates.

These are manually traced closed cubic paths, sampled at 24 steps per segment.
Coordinates use image x-right/y-down. They deliberately retain source asymmetry.
No depth, camera angle, material or rig measurement is implied by these pixels.
The illustration called SIDE is a three-quarter view, not a 90-degree elevation.
"""

SOURCE = {
    "path": "client/assets/emma/emma-pose-library-reference-v1.png",
    "size": (1501, 1008),
    "sha256": "f360f4d50a276f5715e6c73e45d4d487fe19364dd1ab77d70a30856b9a182878",
}

# Each path is [start, (control_1, control_2, end), ...]. All are closed.
# RAW controls are retained so a reviewer can audit the trace against the source.
BEZIERS = {
    "front": {
        "head": [(634, 109),
            ((666, 107.5), (693, 125), (708, 154)),
            ((716, 169), (720.5, 186), (719, 201)),
            ((718, 224), (708, 243), (691, 256)),
            ((674, 269), (648, 275), (627, 272.5)),
            ((604, 271), (582, 260), (568, 244)),
            ((554, 229), (548.5, 207), (550, 187)),
            ((552, 157), (571, 132), (595, 118)),
            ((607, 111), (622, 108.5), (634, 109))],
        "face": [(637, 120),
            ((604, 119), (577, 141), (566, 169)),
            ((559, 189), (558, 207), (565, 219)),
            ((573, 215), (582, 211), (591, 213)),
            ((611, 215), (623, 243), (638, 242)),
            ((653, 242), (664, 216), (682, 214)),
            ((693, 212), (704, 219), (706, 221)),
            ((713, 209), (713, 193), (709, 177)),
            ((704, 150), (685, 130), (662, 123)),
            ((653, 120), (645, 119), (637, 120))],
        "chin": [(580, 226),
            ((591, 221), (599, 225), (608, 235)),
            ((619, 247), (629, 256), (637, 256)),
            ((649, 256), (661, 239), (672, 231)),
            ((681, 224), (687, 225), (691, 230)),
            ((699, 239), (686, 254), (669, 262)),
            ((650, 272), (626, 273), (605, 265)),
            ((587, 258), (576, 247), (576, 236)),
            ((575, 231), (577, 228), (580, 226))],
        "eye_l": [(584, 203),
            ((583, 197), (587, 187), (594, 184)),
            ((601, 181), (608, 184), (612, 191)),
            ((615, 195), (616, 201), (614, 204)),
            ((612, 206), (610, 200), (604, 198)),
            ((597, 194), (591, 198), (587, 203)),
            ((586, 205), (584, 205), (584, 203))],
        "eye_r": [(664, 203),
            ((663, 197), (668, 187), (675, 184)),
            ((682, 181), (689, 186), (692, 192)),
            ((695, 197), (695, 202), (693, 204)),
            ((691, 205), (689, 200), (683, 198)),
            ((677, 195), (671, 199), (667, 203)),
            ((666, 205), (664, 205), (664, 203))],
        # Actual broad W-shaped metal region. This is a surface outline, not
        # a constant-radius smile tube. Its inner edge is the chin's top edge.
        "chrome_wave": [(565, 219),
            ((573, 215), (582, 211), (591, 213)),
            ((611, 215), (623, 243), (638, 242)),
            ((653, 242), (664, 216), (682, 214)),
            ((693, 212), (704, 219), (706, 221)),
            ((713, 230), (707, 242), (699, 248)),
            ((700, 240), (696, 233), (691, 230)),
            ((687, 225), (681, 224), (672, 231)),
            ((661, 239), (649, 256), (637, 256)),
            ((629, 256), (619, 247), (608, 235)),
            ((599, 225), (591, 221), (580, 226)),
            ((575, 230), (574, 235), (576, 243)),
            ((566, 239), (561, 230), (560, 226)),
            ((561, 223), (563, 221), (565, 219))],
    },
    "back": {
        "head": [(994, 108),
            ((1028, 106), (1053, 132), (1065, 161)),
            ((1078, 190), (1077, 214), (1064, 236)),
            ((1054, 254), (1029, 263), (1001, 264)),
            ((970, 266), (945, 254), (932, 235)),
            ((919, 218), (915, 196), (920, 174)),
            ((924, 151), (940, 129), (958, 118)),
            ((968, 111), (982, 107), (994, 108))],
        "back_enamel": [(994, 120),
            ((1020, 119), (1042, 137), (1054, 159)),
            ((1064, 177), (1068, 195), (1060, 214)),
            ((1054, 233), (1037, 244), (1013, 246)),
            ((998, 248), (972, 248), (957, 242)),
            ((937, 236), (927, 220), (926, 202)),
            ((922, 181), (931, 157), (945, 141)),
            ((958, 127), (976, 119), (994, 120))],
    },
    "quarter": {
        "head": [(824, 109),
            ((843, 107), (849, 113), (855, 120)),
            ((876, 128), (890, 150), (894, 171)),
            ((900, 193), (895, 220), (884, 238)),
            ((882, 249), (877, 256), (863, 263)),
            ((852, 272), (839, 274), (829, 269)),
            ((808, 268), (789, 257), (778, 247)),
            ((757, 238), (752, 216), (752, 196)),
            ((750, 166), (767, 137), (790, 121)),
            ((801, 114), (813, 110), (824, 109))],
        "front_enamel": [(841, 118),
            ((864, 117), (886, 143), (892, 169)),
            ((899, 195), (892, 221), (877, 239)),
            ((872, 244), (868, 239), (865, 231)),
            ((855, 214), (844, 211), (833, 215)),
            ((820, 219), (813, 225), (808, 220)),
            ((795, 214), (797, 190), (803, 168)),
            ((809, 142), (824, 121), (841, 118))],
        "rear_enamel": [(794, 120),
            ((775, 135), (769, 159), (770, 188)),
            ((771, 209), (777, 227), (775, 236)),
            ((773, 241), (766, 235), (761, 227)),
            ((747, 204), (751, 176), (761, 155)),
            ((768, 140), (780, 128), (794, 120))],
        "chin": [(840, 225),
            ((850, 223), (857, 232), (858, 246)),
            ((859, 251), (862, 254), (867, 255)),
            ((860, 265), (847, 272), (836, 269)),
            ((823, 265), (816, 258), (817, 247)),
            ((818, 236), (830, 227), (840, 225))],
    },
}


def _sample(path, steps=24):
    points = []
    start = path[0]
    for c1, c2, end in path[1:]:
        for index in range(steps):
            t = index / steps
            s = 1 - t
            points.append(tuple(round(s*s*s*start[axis] + 3*s*s*t*c1[axis]
                                      + 3*s*t*t*c2[axis] + t*t*t*end[axis], 5)
                                for axis in range(2)))
        start = end
    return points


VIEWS = {
    "front": {
        "crop": (540, 100, 730, 280),
        "bounds": (549.5, 109, 719, 273),
        "center": (634.25, 191),
        "width": 169.5,
        "uncertainty": [
            "Silhouette trace uncertainty approximately 1-2 native pixels; chrome glare blurs some edges.",
            "Front illustration has a slight turn: left chrome rim is wider than right, and W is shifted right.",
            "Symmetric model construction must be compared against these RAW paths, not against an edited symmetric target.",
        ],
    },
    "back": {
        "crop": (909, 100, 1083, 271),
        "bounds": (918, 108, 1075, 264),
        "center": (996.5, 186),
        "width": 157.0,
        "uncertainty": [
            "Back head/chrome-neck join is partly ambiguous at y261-266; neck is excluded.",
            "Back and front head H/W ratios differ in the drawing; one uniform scale per view is allowed, not independent x/y stretch.",
        ],
    },
    "quarter": {
        "crop": (745, 100, 904, 281),
        "bounds": (751, 109, 896, 271),
        "center": (823.5, 190),
        "width": 145.0,
        "uncertainty": [
            "This is NOT a true side view: a complete eye and substantial front panel remain visible.",
            "Camera yaw and head depth cannot both be uniquely recovered from this drawing.",
            "Do not call a 90-degree model projection matched or approved from this view.",
        ],
    },
}

for _view, _paths in BEZIERS.items():
    VIEWS[_view]["contours"] = {name: _sample(path) for name, path in _paths.items()}

# Exact affine normalization for each independently measured drawing:
# x=(source_x-center[0])/width; z=(center[1]-source_y)/width.
# This fixes the width to 1 and preserves native aspect ratio. It does not assert
# shared camera scale across sheet panels, which the image does not establish.
NORMALIZATION = "x=(px-cx)/width; z=(cy-py)/width; no anisotropic scaling"
DEPTH_MEASUREMENT = None
STATUS = "draft source trace; geometry approval not granted"
