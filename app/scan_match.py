"""Where the robot is, from its lidar against a map OUR lidar drew.

The owner's question (19 ต.ค. 2026): we know how the robot moved and we know
the distances the laser reads -- can't we compare the two and know where it
is? Yes: guess from the wheels, then find the pose near that guess where this
frame's returns land on what the lidar has seen there before. The board does
the same thing (AMCL) and we had to switch it off, because it matches against
the floor PLAN, and a furnished room is not its plan: in ฝ่ายอาคาร the plan is
an empty box and the lidar sees cabinets and desks, so the match has no peak
(the board pulled the docked robot 0.33 m south twice; matching the plan here
puts the docked robot ~0.37 m south too -- the same failure, measured).

So the map is not the plan. It is built from the checkpoint frames drive_route
already logs (data/robot/runs/*.jsonl: a lidar frame + the anchor-fixed pose
at every stop), and a cell counts only when frames from at least two runs saw
it -- a person walking past is in one run, the cabinet in all of them.

What `scripts/eval_scan_localization.py` measured on the 6-10 ต.ค. runs
(each day matched against a map of the OTHER days, 248 frames, 19 ต.ค.):

* vs the anchor fix on the axes an anchor measured: x p90 2.1 cm (odometry
  8.8, plan 9.3), y p90 6.0 cm (odometry 18.8, plan 18.6), heading p90 0.8°
  (3.3 / 2.8). Axes it claimed but > 5 cm off: 9 of 292 (plan 85 of 240).
* started 0.25 m + 7° off the wheels' guess: 98% come back within 3 cm/1°
  in every area (plan in ฝ่ายอาคาร: 76%, 19% land somewhere else).
* the axis nobody measured: on 7 ต.ค. afternoon the logs said the docked robot
  was at y 1.43 (set by hand) -- this said 1.24-1.28, and the first stop that
  could measure y later corrected it by -12 to -15 cm, every run.
* the "misses" of the first round were the reference being wrong: until
  8 ต.ค. mr_north/D11 sat 17 cm off in the route files. Re-run with today's
  anchors, they went away.
* NOT measured: frames taken while moving (only stopped checkpoints log a
  scan), and how long a search takes on the robot's own CPU (PC: 0.13 s
  median, 0.20 s worst, ~300 points after `voxel`).

The rule from robot_lanes stays: an axis the surfaces in view do not pin is
not corrected. `Match.sharp` says per axis how much the score drops 12 cm /
4° away from the peak -- ~0 along a bare corridor.

Building a map needs scipy (distance transform) and runs on the PC; the
robot has numpy only (scripts/robot_python.py installs nothing else), so
the PC saves the finished likelihood field (`Field.save`) and the robot only
`Field.load`s and `search`es. Coordinates are the map frame of the head-office
plan (metres, x east, y north, yaw CCW from east); scan points robot frame,
x forward, y left, as in robot_lanes.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

#: Grid cell. 1 cm: the search's fine steps are one cell, and a wall's face
#: lands where it is instead of on a 5 cm cell edge (the board's map is 5 cm
#: and its walls came out 12.5 cm thicker a side).
RES = 0.01
#: Width of the likelihood around a surface. 4 cm: wider and a cabinet face
#: 6 cm in front of the wall scores like the wall; narrower and one frame's
#: smear (the lidar spins while the base settles) misses its own surface.
SIGMA = 0.04
#: A cell is map only when this many different runs saw it (people move,
#: furniture does not -- or not between two runs).
MIN_RUNS = 2
#: Returns further than this are dropped: the far end of a corridor at 10+ m
#: is a few points per metre and its error grows with range.
MAX_RANGE = 10.0
#: Margin round the frames' extent when the grid is sized.
MARGIN = 1.0
#: Peak sharpness at or above which an axis counts as measured. Along a bare
#: corridor it is ~0; across it 0.1-0.4. 0.05 claimed 100% of the axes the
#: anchors measured on the 6-10 ต.ค. runs, ~3% of them more than 5 cm away
#: from the anchor fix -- and those were mostly spots where the anchor itself
#: is a plan value (see the eval script's notes).
SHARP_MIN = 0.05


@dataclass(frozen=True)
class Grid:
    x0: float
    y0: float
    nx: int
    ny: int
    res: float = RES

    def cells(self, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(row, col) of world points; may be out of range -- callers clip."""
        return (np.round((w[..., 1] - self.y0) / self.res).astype(np.int64),
                np.round((w[..., 0] - self.x0) / self.res).astype(np.int64))


def to_world(pts: np.ndarray, pose: Sequence[float]) -> np.ndarray:
    x, y, t = pose
    c, s = math.cos(t), math.sin(t)
    p = np.asarray(pts, dtype=float).reshape(-1, 2)
    return np.c_[x + p[:, 0] * c - p[:, 1] * s, y + p[:, 0] * s + p[:, 1] * c]


def scan_points(angle: Iterable[float], distance: Iterable[float], max_range: float = MAX_RANGE) -> np.ndarray:
    """Robot-frame points from a checkpoint's logged scan (`angle`, `distance`
    lists). Anything under 5 cm is the robot's own shell."""
    a = np.asarray(list(angle), dtype=float)
    r = np.asarray(list(distance), dtype=float)
    ok = (r > 0.05) & (r < max_range)
    return np.c_[r[ok] * np.cos(a[ok]), r[ok] * np.sin(a[ok])]


def voxel(pts: np.ndarray, size: float = 0.03) -> np.ndarray:
    """One point per `size` square, in scan order. Close up the lidar puts ten
    returns in a centimetre; unthinned, the wall next to the robot outvotes
    everything else in the frame."""
    p = np.asarray(pts, dtype=float).reshape(-1, 2)
    if not len(p):
        return p
    k = np.floor(p / size).astype(np.int64)
    _, idx = np.unique(k[:, 0] * 1_000_003 + k[:, 1], return_index=True)
    return p[np.sort(idx)]


def grid_for(frames: Iterable[tuple[np.ndarray, Sequence[float]]], margin: float = MARGIN) -> Grid:
    lo = np.array([np.inf, np.inf]); hi = -lo
    for pts, pose in frames:
        w = to_world(pts, pose)
        if len(w):
            lo = np.minimum(lo, w.min(axis=0)); hi = np.maximum(hi, w.max(axis=0))
        lo = np.minimum(lo, pose[:2]); hi = np.maximum(hi, pose[:2])
    if not np.all(np.isfinite(lo)):
        raise ValueError("no frames to size a grid from")
    lo -= margin; hi += margin
    return Grid(float(lo[0]), float(lo[1]), int(math.ceil((hi[0] - lo[0]) / RES)) + 1,
                int(math.ceil((hi[1] - lo[1]) / RES)) + 1)


def occupancy(grid: Grid, frames: Iterable[tuple[str, np.ndarray, Sequence[float]]],
              min_runs: int = MIN_RUNS) -> np.ndarray:
    """Cells the lidar saw in `min_runs` or more runs. `frames` = (run id,
    robot-frame points, pose). Each run's hits are grown one cell first so
    two runs that saw the same face a centimetre apart still agree."""
    from scipy import ndimage as nd      # PC only -- the robot never builds a map
    count = np.zeros((grid.ny, grid.nx), np.uint16)
    by_run: dict[str, list[tuple[np.ndarray, Sequence[float]]]] = {}
    for run, pts, pose in frames:
        by_run.setdefault(run, []).append((pts, pose))
    for items in by_run.values():
        hit = np.zeros((grid.ny, grid.nx), bool)
        for pts, pose in items:
            r, c = grid.cells(to_world(pts, pose))
            ok = (r >= 0) & (r < grid.ny) & (c >= 0) & (c < grid.nx)
            hit[r[ok], c[ok]] = True
        count += nd.binary_dilation(hit, iterations=1)
    return count >= min_runs


#: A saved field: likelihood 0-1 stored as 0-255 (the search compares
#: scores, a step of 1/255 is far under any difference it acts on).
FIELD_FORMAT = 1


@dataclass
class Field:
    """Likelihood that a return lands at each cell: 1 on a surface, falling
    off over SIGMA. Padded with zeros so lookups past the edge score 0 instead
    of wrapping round to the other side of the building. `meta` = what the map
    was built from (runs, frame id, when) -- saved with it, never guessed."""
    grid: Grid
    L: np.ndarray
    pad: int
    meta: dict = dc_field(default_factory=dict)

    @classmethod
    def from_occupancy(cls, grid: Grid, occ: np.ndarray, sigma: float = SIGMA, pad: int = 64,
                       meta: dict | None = None) -> "Field":
        from scipy import ndimage as nd      # PC only
        if occ.shape != (grid.ny, grid.nx):
            raise ValueError("occupancy shape %s does not match the grid %s" % (occ.shape, (grid.ny, grid.nx)))
        dist = nd.distance_transform_edt(~occ).astype(np.float32) * grid.res
        L = np.exp(-(np.minimum(dist, 1.0) ** 2) / (2 * sigma ** 2)).astype(np.float32)
        return cls(grid, np.pad(L, pad, constant_values=0.0), pad, dict(meta or {}))

    def lookup(self, w: np.ndarray) -> np.ndarray:
        r, c = self.grid.cells(w)
        r = np.clip(r + self.pad, 0, self.L.shape[0] - 1)
        c = np.clip(c + self.pad, 0, self.L.shape[1] - 1)
        v = self.L[r, c]
        return v * (1.0 / 255.0) if v.dtype == np.uint8 else v

    def save(self, path: str | Path) -> None:
        q = np.round(np.clip(self.L, 0.0, 1.0) * 255.0).astype(np.uint8) if self.L.dtype != np.uint8 else self.L
        g = self.grid
        np.savez_compressed(path, L=q, grid=np.array([g.x0, g.y0, g.nx, g.ny, g.res]), pad=np.array(self.pad),
                            meta=np.array(json.dumps({"format": FIELD_FORMAT, **self.meta}, sort_keys=True)))

    @classmethod
    def load(cls, path: str | Path) -> "Field":
        """A field `save` wrote, checked whole: a file that is not exactly that
        shape is refused, not half used (allow_pickle off -- npz can carry code)."""
        with np.load(path, allow_pickle=False) as z:
            L, g, pad = z["L"], z["grid"], int(z["pad"])
            meta = json.loads(str(z["meta"]))
        if meta.get("format") != FIELD_FORMAT or L.dtype != np.uint8 or g.shape != (5,):
            raise ValueError("%s: not a scan_match field (format %r)" % (path, meta.get("format")))
        grid = Grid(float(g[0]), float(g[1]), int(g[2]), int(g[3]), float(g[4]))
        if L.shape != (grid.ny + 2 * pad, grid.nx + 2 * pad):
            raise ValueError("%s: field %s does not match its grid" % (path, L.shape))
        return cls(grid, L, pad, meta)

    def score(self, pts: np.ndarray, pose: Sequence[float]) -> float:
        p = np.asarray(pts, dtype=float).reshape(-1, 2)
        return float(self.lookup(to_world(p, pose)).mean()) if len(p) else 0.0


@dataclass
class Match:
    pose: np.ndarray                 # x, y, yaw at the peak
    score: float                     # mean likelihood of the returns there (0..1)
    sharp: tuple[float, float, float]   # per axis: 1 - best score 12 cm / 4° away / peak
    prior: np.ndarray

    def pinned(self, sharp_min: float = SHARP_MIN) -> tuple[bool, bool, bool]:
        return tuple(s >= sharp_min for s in self.sharp)  # type: ignore[return-value]

    def apply(self, sharp_min: float = SHARP_MIN) -> np.ndarray:
        """The prior, moved only along the axes the surfaces pinned."""
        out = self.prior.astype(float).copy()
        for ax, ok in enumerate(self.pinned(sharp_min)):
            if ok:
                out[ax] = self.pose[ax]
        return out


def _sharpness(cube: np.ndarray, offs: tuple[np.ndarray, np.ndarray, np.ndarray],
               best: tuple[float, float, float]) -> tuple[float, float, float]:
    peak = float(cube.max())
    if peak <= 0:
        return (0.0, 0.0, 0.0)
    ox, oy, ot = offs
    profiles = (cube.max(axis=(0, 1)), cube.max(axis=(0, 2)), cube.max(axis=(1, 2)))
    out = []
    for prof, o, b, far in zip(profiles, (ox, oy, ot), best, (0.12, 0.12, math.radians(4.0))):
        m = np.abs(o - b) >= far - 1e-9
        out.append(float(1.0 - prof[m].max() / peak) if m.any() else 1.0)
    return tuple(out)  # type: ignore[return-value]


def search(field: Field, pts: np.ndarray, prior: Sequence[float], *,
           window_xy: tuple[float, float] = (0.4, 0.4), window_yaw: float = math.radians(10.0),
           step: float = 0.04, step_yaw: float = math.radians(1.0)) -> Match:
    """Best pose within the window round `prior`: every 4 cm / 1°, then every
    1 cm / 0.25° round the best. Brute force on purpose -- a gradient method
    started in the wrong valley stays there, and a 0.8 m window is what the
    wheels have been off by after a turn (0.2-0.3 m, 6 ต.ค.).

    Never searched: the whole building. A global search is how the board
    flipped the docked robot 180° in a room that looks the same both ways."""
    p = np.asarray(pts, dtype=float).reshape(-1, 2)
    prior = np.asarray(prior, dtype=float)
    if len(p) < 20:
        return Match(prior.copy(), 0.0, (0.0, 0.0, 0.0), prior)
    x0, y0, t0 = prior
    kx = int(round(window_xy[0] / step)); ky = int(round(window_xy[1] / step))
    kt = int(round(window_yaw / step_yaw))
    ox = np.arange(-kx, kx + 1) * step
    oy = np.arange(-ky, ky + 1) * step
    ot = np.arange(-kt, kt + 1) * step_yaw
    shifts = np.stack(np.meshgrid(ox, oy), -1)[:, :, None, :]          # (Y, X, 1, 2)
    cube = np.zeros((len(ot), len(oy), len(ox)), np.float32)
    for i, dt in enumerate(ot):
        w = to_world(p, (x0, y0, t0 + dt))
        cube[i] = field.lookup(w[None, None] + shifts).mean(axis=-1)
    i, j, k = np.unravel_index(int(np.argmax(cube)), cube.shape)
    best = (float(cube[i, j, k]), x0 + ox[k], y0 + oy[j], t0 + ot[i])
    coarse = (ox[k], oy[j], ot[i])
    fine = np.arange(-5, 6) * (step / 4)
    fshift = np.stack(np.meshgrid(fine, fine), -1)[:, :, None, :]
    _, bx, by, bt = best
    for dt in np.arange(-6, 7) * (step_yaw / 4):
        w = to_world(p, (bx, by, bt + dt))
        s = field.lookup(w[None, None] + fshift).mean(axis=-1)
        jj, kk = np.unravel_index(int(np.argmax(s)), s.shape)
        if s[jj, kk] > best[0]:
            best = (float(s[jj, kk]), bx + fine[kk], by + fine[jj], bt + dt)
    pose = np.array(best[1:])
    pose[2] = (pose[2] + math.pi) % (2 * math.pi) - math.pi
    return Match(pose, best[0], _sharpness(cube, (ox, oy, ot), coarse), prior)


def refine(frames: Sequence[tuple[str, np.ndarray]], poses: Mapping, measured: Mapping,
           grid: Grid, *, passes: int = 3, min_score: float = 0.6) -> dict:
    """Make the map frames agree with each other on the axes no anchor
    measured. `frames` = (run, points) keyed like `poses`/`measured`
    (key -> (x?, y?, yaw?) booleans). The runs are split in two halves; each
    half is matched against a map of the other (a frame matched against a map
    that holds its own returns finds itself, and nothing moves). Measured axes
    never move -- they are the route's frame."""
    keys = list(poses)
    runs = sorted({frames[k][0] for k in keys})                       # type: ignore[index]
    halves = (set(runs[0::2]), set(runs[1::2]))
    out = {k: np.asarray(poses[k], dtype=float).copy() for k in keys}
    for _ in range(passes):
        for h in (0, 1):
            other = [(frames[k][0], frames[k][1], out[k]) for k in keys if frames[k][0] in halves[1 - h]]
            if not other:
                continue
            field = Field.from_occupancy(grid, occupancy(grid, other))
            for k in keys:
                run, pts = frames[k]
                if run not in halves[h]:
                    continue
                mx, my, myaw = measured[k]
                m = search(field, voxel(pts), out[k], window_xy=(0.0 if mx else 0.32, 0.0 if my else 0.32),
                           window_yaw=math.radians(0.0 if myaw else 8.0))
                if m.score < min_score:
                    continue
                new = out[k].copy()
                for ax, meas in enumerate((mx, my, myaw)):
                    if not meas:
                        new[ax] = m.pose[ax]
                out[k] = new
    return out
