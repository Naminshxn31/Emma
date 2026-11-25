"""What a robot IS, for the map tools: its body, sensors, motion, planner, dock and board.

Owner, 20 Nov 2026: "ระบบแผนที่ของเราอาจจะต้องเอาไปใช้กับหุ่นเสิร์ฟด้วย อาจจะต้องทำระบบเผื่อไว้".
Until today the floor simulator (scripts/floor_sim.py) and the map check (app/map_check.py)
had Emma's numbers written in as module constants. A serving robot -- brand, model and size
not known yet -- must be able to use our maps, spots and checks without anything Emma relies
on moving. So the numbers that belong to a ROBOT (not to a room, not to a night) live here,
and the tools take a profile; Emma is the default and gives the same bytes as before (proved
by comparing the outputs of both tools on the same inputs, CHANGELOG 20 Nov).

What is NOT here, on purpose:

* board_walk.py's own constants (how Emma walks, docks, creeps). That is Emma's walking code;
  a serving robot brings its vendor's navigation. The simulator runs board_walk's Walker for
  Emma only; another profile is walked as plain planner legs (floor_sim `profile`).
* the room: furniture, glass, the dock's place, hazards -- those are the map package's
  (app/map_package.py), in the map's frame, for any robot.
* how much of the furniture the lidar returns (floor_sim.FURNITURE_RETURNS): fitted on one
  robot's lidar on one night; another lidar at another height is unknown, said in `unknown`.

Every number carries where it came from. A field that nobody measured says so: a serving
robot's profile is an EXAMPLE until the vendor's sheet is in (`example=True`), and the tools
print that at the top of every report.

Pure Python (no numpy): the robot imports this too.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Body:
    #: along the heading (front to back), across, and across at the widest point at ANY height
    #: (Emma's arms; a serving robot's tray if it overhangs). Rectangles centred on the pose.
    length_m: float
    width_m: float
    reach_width_m: float
    #: the widest point's height band, when it matters (a tray at table height passes over a
    #: chair seat and into a table top): None = not known
    reach_from_m: float | None = None
    reach_to_m: float | None = None
    #: a tray (serving robots): its top above the floor, and how far it sticks out in front of
    #: the body's own front (0 = flush)
    tray_height_m: float | None = None
    tray_overhang_m: float = 0.0
    #: where nothing can be because the robot stood there (map_check: a passer-by in a doorway
    #: in two walks must not become a wall across the door the robot drove through)
    stood_floor_r_m: float = 0.25

    @property
    def turn_radius_m(self) -> float:
        """The corner of the widest rectangle: what a turn on the spot sweeps."""
        return math.hypot(self.length_m / 2 + self.tray_overhang_m, self.reach_width_m / 2)


@dataclass(frozen=True)
class Lidar:
    #: the arc it sees, robot frame, + = left; nothing outside it (Emma: nothing behind)
    from_deg: float
    to_deg: float
    #: how far the simulator casts, and the band whose returns the map check trusts
    sim_range_m: float = 10.0
    trust_min_m: float = 0.15
    trust_max_m: float = 8.0
    noise_m: float = 0.008
    bin_deg: float = 0.25
    #: the plane's height above the floor: None = never measured. Everything a map built from
    #: one robot's lidar holds is AT THAT HEIGHT -- a table top above it is not in the map.
    height_m: float | None = None


@dataclass(frozen=True)
class Camera:
    hfov_deg: float | None = None
    mirrored: bool = False


@dataclass(frozen=True)
class Motion:
    #: the fastest any walk of ours asks of it (not a vendor limit)
    max_speed_mps: float = 0.35
    spot_turn_rad_s: float = 0.38
    accel_mps2: float = 0.4


@dataclass(frozen=True)
class Planner:
    """The robot's own path planner (its board's MoveTo), as far as we know it."""
    #: what it keeps clear of round its centre when it plans
    inflate_m: float
    #: it refuses a target nearer than this to its map / to a thing its lidar sees
    refuse_map_m: float
    refuse_seen_m: float
    measured: bool = False


@dataclass(frozen=True)
class Docking:
    #: "reverse_onto_contacts" (Emma: our own code backs her on), "vendor" (the robot docks
    #: itself; we only know where its dock stands), "none"
    style: str
    contact_half_m: float | None = None
    max_aside_m: float | None = None
    max_crooked_deg: float | None = None


@dataclass(frozen=True)
class RobotProfile:
    id: str
    name: str
    body: Body
    lidar: Lidar
    camera: Camera
    motion: Motion
    planner: Planner
    docking: Docking
    #: the navigation board's API: "slamware" (Slamtec REST + SDK 1445) or what the vendor has
    board_api: str = "slamware"
    #: the map formats it takes / gives: "stcm" (Slamtec), "ros_pgm_yaml", "vendor"
    map_formats: tuple[str, ...] = ("stcm",)
    #: the localisation quality gate on its board's scale (Slamtec Q 0-100); None = no such number
    min_quality: int | None = 40
    #: the arms' rectangle may come no nearer than this to anything solid (the pre-flight's bound)
    min_gap_m: float = 0.05
    #: fields no one has measured, said in every report that uses this profile
    unknown: tuple[str, ...] = ()
    #: an example until the vendor's numbers are in
    example: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["map_formats"] = list(self.map_formats)
        d["unknown"] = list(self.unknown)
        return d


#: Emma (ZC-3588A on the Slamtec board). Every value is the constant the tools had before
#: 20 Nov -- tests/test_robot_profile.py holds them to those constants so they cannot part.
EMMA = RobotProfile(
    id="emma",
    name="Emma (Aobo Astronaut, ZC-3588A, Slamtec SDP board)",
    body=Body(
        length_m=0.60,           # base, taped 6 Oct (robot_lanes.ROBOT_BASE_LENGTH)
        width_m=0.70,            # base (robot_lanes.ROBOT_BASE_WIDTH)
        reach_width_m=0.78,      # with the arms (robot_lanes.ROBOT_WIDTH); 28 Oct an arm hit a table
        stood_floor_r_m=0.25,    # map_check.BODY_R_M
    ),
    lidar=Lidar(
        from_deg=-101.0, to_deg=108.0,   # 16 Nov frames: nothing behind
        sim_range_m=10.0, trust_min_m=0.15, trust_max_m=8.0,   # analyze_runs 28 Oct: 0.15-8 m
        noise_m=0.008, bin_deg=0.25,
        height_m=None,           # "knee height" -- never measured
    ),
    camera=Camera(hfov_deg=19.0, mirrored=True),   # 4 Nov: base spot turns against odometry
    motion=Motion(
        max_speed_mps=0.45,      # the fastest our route files ask (floor1.json); the board's own limit unknown.
                                 # 0.35 -> 0.45 on 20 Nov (owner "ให้หุ่นเดินไวกว่านี้"): rehearsal_back_home's MoveTo
                                 # legs at 0.45 ran on the robot 7 times that evening, the same 0-4 cm at the stops
        spot_turn_rad_s=0.38,    # 16 Nov frames
        accel_mps2=0.4,
    ),
    planner=Planner(
        inflate_m=0.36,          # ASSUMED (floor_sim.BOARD_INFLATE_M): never measured
        refuse_map_m=0.45,       # upper bounds from targets the real board took (floor_sim)
        refuse_seen_m=0.30,
        measured=False,
    ),
    docking=Docking(style="reverse_onto_contacts", contact_half_m=0.12, max_aside_m=0.05, max_crooked_deg=2.0),
    board_api="slamware",
    map_formats=("stcm", "explore_blob"),
    min_quality=40,
    min_gap_m=0.05,
    unknown=("lidar.height_m", "planner.inflate_m (assumed 0.36)", "planner.refuse_* (upper bounds only)",
             "motion.max_speed_mps (what we ask, not the board's limit)"),
    notes="the default of every map tool; board_walk.py walks her",
)

#: A serving robot as most vendors' sheets read (~0.5 x 0.5 m, a tray at table height, a lidar
#: with a wide arc). AN EXAMPLE: no brand chosen, nothing measured. Replace with the vendor's
#: sheet (`--profile-file`) before believing any number it gives.
SERVING_EXAMPLE = RobotProfile(
    id="serving_example",
    name="serving robot (EXAMPLE -- vendor and model not chosen)",
    body=Body(length_m=0.50, width_m=0.50, reach_width_m=0.50, reach_from_m=0.70, reach_to_m=1.10,
              tray_height_m=0.90, tray_overhang_m=0.0, stood_floor_r_m=0.20),
    lidar=Lidar(from_deg=-135.0, to_deg=135.0, sim_range_m=10.0, trust_min_m=0.10, trust_max_m=8.0,
                noise_m=0.01, bin_deg=0.25, height_m=None),
    camera=Camera(hfov_deg=None, mirrored=False),
    motion=Motion(max_speed_mps=0.6, spot_turn_rad_s=0.6, accel_mps2=0.5),
    planner=Planner(inflate_m=0.30, refuse_map_m=0.35, refuse_seen_m=0.30, measured=False),
    docking=Docking(style="vendor"),
    board_api="vendor",
    map_formats=("ros_pgm_yaml", "vendor"),
    min_quality=None,
    min_gap_m=0.05,
    unknown=("EVERYTHING: an example, no vendor chosen", "lidar.height_m", "planner.*", "body.tray_*",
             "docking (the vendor's own)", "which map format its software imports"),
    example=True,
    notes="for trying the tools only -- not a robot anyone has measured",
)

PROFILES = {p.id: p for p in (EMMA, SERVING_EXAMPLE)}


def get(name: str | None = None) -> RobotProfile:
    """A profile by id; None/"" = Emma. Unknown id = KeyError (never a silent Emma)."""
    if not name:
        return EMMA
    if name not in PROFILES:
        raise KeyError("no robot profile %r (have: %s; or give a JSON file)" % (name, ", ".join(sorted(PROFILES))))
    return PROFILES[name]


_PARTS = {"body": Body, "lidar": Lidar, "camera": Camera, "motion": Motion, "planner": Planner, "docking": Docking}


def from_dict(doc: dict[str, Any], *, base: RobotProfile | None = None) -> RobotProfile:
    """A profile from JSON (a vendor's sheet typed in). Keys not given come from `base`
    (default SERVING_EXAMPLE, so nothing is silently Emma's) -- and every part left to the base
    is added to `unknown`. Unknown keys are an error (a typo must not fall back quietly)."""
    base = base or SERVING_EXAMPLE
    if not isinstance(doc, dict) or not doc.get("id"):
        raise ValueError("a robot profile needs an `id`")
    known = {f for f in RobotProfile.__dataclass_fields__}
    bad = sorted(set(doc) - known - {"about"})
    if bad:
        raise ValueError("robot profile %s: unknown key(s) %s" % (doc["id"], ", ".join(bad)))
    kw: dict[str, Any] = {}
    unknown = list(doc.get("unknown") or [])
    for key in known:
        if key in _PARTS:
            part = getattr(base, key)
            given = doc.get(key)
            if given is None:
                if key not in ("camera",):
                    unknown.append("%s (all: the %s example's)" % (key, base.id))
                kw[key] = part
                continue
            fields = set(part.__dataclass_fields__)
            extra = sorted(set(given) - fields)
            if extra:
                raise ValueError("robot profile %s: %s has unknown key(s) %s" % (doc["id"], key, ", ".join(extra)))
            kw[key] = replace(part, **given)
            missing = sorted(fields - set(given))
            unknown += ["%s.%s (the %s example's)" % (key, f, base.id) for f in missing
                        if getattr(part, f) is not None and f not in ("noise_m", "bin_deg", "sim_range_m")]
        elif key in doc and key != "unknown":
            v = doc[key]
            kw[key] = tuple(v) if key == "map_formats" else v
    kw.setdefault("example", bool(doc.get("example", False)))
    kw["unknown"] = tuple(dict.fromkeys(unknown))
    for key in ("id", "name", "board_api", "map_formats", "min_quality", "min_gap_m", "notes"):
        kw.setdefault(key, getattr(base, key) if key != "id" else doc["id"])
    return RobotProfile(**kw)


def load(name_or_path: str | None) -> RobotProfile:
    """`emma`, `serving_example`, or a path to a JSON profile."""
    if name_or_path and (name_or_path.endswith(".json") or Path(name_or_path).is_file()):
        doc = json.loads(Path(name_or_path).read_text(encoding="utf-8"))
        return from_dict(doc)
    return get(name_or_path)


def describe(p: RobotProfile) -> list[str]:
    """Lines for the top of a report: who, how big, and what nobody measured."""
    b = p.body
    out = ["ROBOT: %s [%s]%s" % (p.name, p.id, "  -- AN EXAMPLE, NOT A MEASURED ROBOT" if p.example else ""),
           "  body %.2f x %.2f m (widest %.2f m%s), turns in %.2f m round its centre; lidar %+.0f..%+.0f deg%s; "
           "planner keeps %.2f m%s; dock: %s; board: %s" % (
               b.length_m, b.width_m, b.reach_width_m,
               "" if b.tray_height_m is None else ", tray at %.2f m" % b.tray_height_m,
               b.turn_radius_m, p.lidar.from_deg, p.lidar.to_deg,
               "" if p.lidar.height_m is None else " at %.2f m" % p.lidar.height_m,
               p.planner.inflate_m, "" if p.planner.measured else " (assumed)",
               p.docking.style, p.board_api)]
    if p.unknown:
        out.append("  not measured: " + "; ".join(p.unknown))
    return out
