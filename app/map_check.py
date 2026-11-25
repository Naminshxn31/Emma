"""Does the robot's map still match what its lidar sees? (map check)

Owner, 31 Oct 2026, after the head's lidar/camera cross-check: "แล้วการทำแผนที่
ผังละ". Two map failures so far were found only by hand:

* 28 Oct, floor 1: a `--map` run through the multimedia room wrote people and
  the open door leaf into the saved map. In the doorway the board's pose then
  jumped 0.2 m (lidar on map 53% against 88-90% on the map before), the
  robot could not turn at the jamb, and the map was put back from a copy.
* 5 Oct, floor 2: the plan map had none of ฝ่ายอาคาร's furniture, so the lidar
  had nothing right to lock onto (the overlay showed it, nothing else did).

And the event (25-26 Nov) needs the floor-1 map redone with the tables set --
with staff walking about, which is exactly what `--map` records.

Every lidar beam is a line the robot saw THROUGH up to the thing it hit. Laid
on the map, from frames the robot already records (walk frames, board_walk
--record), per walk:

* **phantom** -- the map has something where beams went through, in at least
  MIN_RUNS separate walks, and no walk hit it: a person or an open door leaf
  the map kept, furniture since moved.
* **new** -- the map has free floor where beams stopped, in at least MIN_RUNS
  walks and never seen through: event tables, furniture the plan lacked. One
  walk is never enough -- a person stands in one.
* **changing** -- hit in some walks, seen through in others: chairs, people,
  a door. Reported, never written.
* **line** -- a phantom that is a long thin stroke: glass (the lidar sees
  through it, and the glass wall the map keeps is what stops the planner
  driving into it) or a wall drawn off where it really is (the plan maps are
  ~12.5 cm fat per side). Reported, never cleared.

The frames are placed by the board's own pose, refined a little against the
map first (REFINE_*): the walk was localised on this map, and a 5 cm pose
error must not read as a 5 cm phantom.

It proposes; it never writes to the board. The cleaned grid is a file, the
pictures say what each finding is (scripts/map_check.py draws them), and
uploading is a separate step on the owner's go -- PUT maps/stcm resets the
pose and renumbers the map's artifacts.

numpy only: the robot has no scipy (scan_match's rule).

The robot's own numbers (the lidar's trusted band, the floor its body stood on, the board's
quality gate) come from a robot profile (app/robot_profile.py; owner 20 Nov: our maps may
serve a serving robot too). Emma is the default and the constants below are hers -- the
same bytes out as before the profile existed (CHANGELOG 20 Nov).
"""
from __future__ import annotations

import math
import struct
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

#: The board's cells (int8): -127 occupied, 127 free, 0 unknown; scans grade
#: in between. Same cut as board_walk.MapFit.
OCC_BELOW = -10
FREE_ABOVE = 10
OCC_VALUE = -127
FREE_VALUE = 127

#: Beams used: nearer is the robot's own body/bumper, further is past where
#: this lidar's returns are trusted (analyze_runs, 28 Oct: 0.15-8 m).
MIN_RANGE_M = 0.15
MAX_RANGE_M = 8.0
#: A frame's beams are thinned to about this many.
MAX_BEAMS = 720

#: Pose refinement around the board's pose: +-REFINE_M in steps, +-REFINE_DEG.
REFINE_M = 0.15
REFINE_STEP_M = 0.025
REFINE_DEG = 3.0
REFINE_STEP_DEG = 0.75
FIT_TOL_M = 0.08
#: Below this the frame's pose is not known well enough to judge the map by
#: it -- the doorway of 28 Oct read 0.53 on the broken map, so this is lower.
FIT_MIN = 0.40
MIN_QUALITY = 40
#: A walk whose poses sit in another frame than the map's: floor 2's
#: drive_route sets its own pose against walls it measured, the board's map
#: there is the plan -- 4 Nov, ~25 cm and ~2 deg apart, past REFINE_M, so every
#: frame of the dock room stopped at the edge of its search (fit 0.63) and the
#: furniture was drawn that far off. One shift for the whole walk first, from
#: a sample of its frames; then each frame's own REFINE_M around it. Taken
#: only for a clear gain (a corridor would slide along itself for nothing).
WALK_SHIFT_M = 0.8
WALK_SHIFT_STEP_M = 0.04
WALK_SHIFT_DEG = 6.0
WALK_SHIFT_STEP_DEG = 1.0
WALK_SHIFT_SAMPLE = 24
WALK_SHIFT_GAIN = 0.05

#: Agreement: separate walks that must agree, and how many beams a walk must
#: put through a cell before it counts as seen through there.
MIN_RUNS = 2
MIN_PASS = 2
#: Hits this close (cells) count as hitting the cell, and a cell this close
#: to a walk's hits is never "seen through" by that walk: pose and range noise,
#: and map walls drawn up to 10 cm off (scan maps are 5 cm cells, plan maps
#: worse). A margin along the beam instead was tried and is not the same --
#: an oblique beam's last 10 cm runs only a few cm off the surface.
HIT_TOL_CELLS = 2
#: "new" must be this far (cells) from anything the map already has.
NEW_GAP_CELLS = 2
#: Findings smaller than this (cells) are noise.
MIN_CELLS = 3
#: Where the robot itself stood nothing can be: the base is 0.70 x 0.60 m
#: (taped 6 Oct), so 0.25 m round each placed pose is floor, whatever the map
#: says -- a passer-by who stood in a doorway in both walks must not become a
#: wall across the door the robot just drove through.
BODY_R_M = 0.25
#: A phantom at least this long and at most this wide is a "line".
LINE_MIN_M = 1.2
LINE_MAX_W_M = 0.25


@dataclass
class Grid:
    cells: np.ndarray          # int8 (h, w); row 0 = the bottom (y = oy)
    ox: float
    oy: float
    res: float
    header: bytes = b""        # the explore blob's 36-byte header, kept as read

    @property
    def h(self) -> int:
        return int(self.cells.shape[0])

    @property
    def w(self) -> int:
        return int(self.cells.shape[1])

    def occ(self) -> np.ndarray:
        return self.cells < OCC_BELOW

    def free(self) -> np.ndarray:
        return self.cells > FREE_ABOVE

    def world(self, row, col):
        return self.ox + (np.asarray(col) + 0.5) * self.res, self.oy + (np.asarray(row) + 0.5) * self.res


def parse_explore(blob: bytes) -> Grid:
    """The board's `GET /api/core/slam/v1/maps/explore` (board_walk.MapFit's reading)."""
    ox, oy, w, h, res = struct.unpack_from("<ffIIf", blob, 0)
    (size,) = struct.unpack_from("<I", blob, 32)
    if size != w * h or len(blob) < 36 + size or not res > 0:
        raise ValueError("explore map: size %d for %d x %d, %d bytes" % (size, w, h, len(blob)))
    cells = np.frombuffer(blob[36:36 + size], dtype=np.int8).reshape(h, w).copy()
    return Grid(cells, float(ox), float(oy), float(res), bytes(blob[:36]))


def explore_bytes(grid: Grid) -> bytes:
    if len(grid.header) != 36:
        raise ValueError("no explore header to write this grid with")
    return grid.header + grid.cells.astype(np.int8).tobytes()


def _stcm_layers(blob: bytes):
    """(key/values, payload offset, layer end) per layer of a Slamtec .stcm
    (memory "stcm-plan-maps": u16 header length at 4, u32 layer count at 0x0C;
    each layer = u32 length incl. itself, u16 key count, u16-length strings)."""
    if blob[:4] != b"STCM":
        raise ValueError("not an STCM file")
    (n,) = struct.unpack_from("<I", blob, 0x0C)
    (off,) = struct.unpack_from("<H", blob, 4)
    for _ in range(n):
        (length,) = struct.unpack_from("<I", blob, off)
        (k,) = struct.unpack_from("<H", blob, off + 4)
        p = off + 6
        kv = {}
        for _ in range(k):
            (a,) = struct.unpack_from("<H", blob, p)
            key = blob[p + 2:p + 2 + a].decode("utf-8", "replace")
            p += 2 + a
            (a,) = struct.unpack_from("<H", blob, p)
            kv[key] = blob[p + 2:p + 2 + a].decode("utf-8", "replace")
            p += 2 + a
        yield kv, p, off + length
        off += length


def parse_stcm(blob: bytes) -> Grid:
    for kv, p, end in _stcm_layers(blob):
        if kv.get("usage") == "explore":
            w, h = int(kv["dimension_width"]), int(kv["dimension_height"])
            if p + w * h > end:
                raise ValueError("stcm explore layer shorter than %d x %d" % (w, h))
            cells = np.frombuffer(blob[p:p + w * h], dtype=np.int8).reshape(h, w).copy()
            return Grid(cells, float(kv["origin_x"]), float(kv["origin_y"]), float(kv["resolution_x"]))
    raise ValueError("stcm has no explore layer")


def stcm_with(blob: bytes, grid: Grid) -> bytes:
    """The same .stcm with its explore grid replaced -- docks, virtual walls
    and forbidden areas untouched. Refuses a grid of another size/origin."""
    for kv, p, _end in _stcm_layers(blob):
        if kv.get("usage") == "explore":
            w, h = int(kv["dimension_width"]), int(kv["dimension_height"])
            same = (w, h) == (grid.w, grid.h) and all(
                abs(float(kv[k]) - v) < 1e-4 for k, v in
                (("origin_x", grid.ox), ("origin_y", grid.oy), ("resolution_x", grid.res)))
            if not same:
                raise ValueError("grid %dx%d at (%.3f, %.3f) does not fit this stcm's %dx%d at (%s, %s)"
                                 % (grid.w, grid.h, grid.ox, grid.oy, w, h, kv["origin_x"], kv["origin_y"]))
            out = bytearray(blob)
            out[p:p + w * h] = grid.cells.astype(np.int8).tobytes()
            return bytes(out)
    raise ValueError("stcm has no explore layer")


@dataclass
class Frame:
    run: str
    n: int
    pose: tuple[float, float, float]
    scan: np.ndarray                       # (k, 2): angle rad, distance m -- robot frame
    q: int | None = None
    picture: str | None = None
    head_deg: float = 0.0
    extra: dict = field(default_factory=dict)
    # the head's nod, in the same recorded units as head_deg (+ = down)
    tilt_deg: float = 0.0


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return mask.copy()
    h, w = mask.shape
    out = mask.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if (dx == 0 and dy == 0) or dx * dx + dy * dy > r * r:
                continue
            ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
            xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
            out[ys, xs] |= mask[yd, xd]
    return out


def _beams(frame: Frame, lidar=None) -> np.ndarray:
    """`lidar`: a robot profile's (its trusted band); None = Emma's (MIN/MAX_RANGE_M)."""
    lo, hi = (MIN_RANGE_M, MAX_RANGE_M) if lidar is None else (lidar.trust_min_m, lidar.trust_max_m)
    s = np.asarray(frame.scan, dtype=float).reshape(-1, 2)
    s = s[(s[:, 1] > lo) & (s[:, 1] < hi) & np.isfinite(s).all(axis=1)]
    return s[:: max(1, len(s) // MAX_BEAMS)] if len(s) else s


class Checker:
    """Lay frames on one map, walk by walk; `findings()` when done. `profile`: the robot the
    walks were made with (app/robot_profile.py); None = Emma."""

    def __init__(self, grid: Grid, profile=None) -> None:
        from app import robot_profile

        self.grid = grid
        self.profile = profile or robot_profile.EMMA
        # Emma's are this module's constants (the same floats); another robot's come with it
        self.min_quality = self.profile.min_quality
        self.body_r = self.profile.body.stood_floor_r_m
        self.near = _dilate(grid.occ(), max(1, int(round(FIT_TOL_M / grid.res))))
        self.passed: dict[str, np.ndarray] = {}
        self.hit: dict[str, np.ndarray] = {}
        self.frames: list[dict] = []        # per frame: run, n, pose used, fits, why skipped
        self.body: dict[str, list[tuple[int, int]]] = {}   # per walk: cells the robot stood on
        self.shift: dict[str, tuple[float, float, float]] = {}   # per walk: (dx, dy, dyaw) laid on first

    # -- placing a frame -------------------------------------------------
    def _fit(self, xs, ys, yaws, pts) -> np.ndarray:
        """Fit (fraction of returns on the map) for every (x, y, yaw) combination:
        shape (len(yaws), len(ys), len(xs))."""
        g = self.grid
        px, py = pts[:, 0], pts[:, 1]
        out = np.zeros((len(yaws), len(ys), len(xs)))
        for i, yaw in enumerate(yaws):
            c, s = math.cos(yaw), math.sin(yaw)
            rx, ry = px * c - py * s, px * s + py * c
            col = np.floor((xs[:, None] + rx[None, :] - g.ox) / g.res).astype(int)     # (nx, k)
            row = np.floor((ys[:, None] + ry[None, :] - g.oy) / g.res).astype(int)     # (ny, k)
            okc = (col >= 0) & (col < g.w)
            okr = (row >= 0) & (row < g.h)
            colc, rowc = np.clip(col, 0, g.w - 1), np.clip(row, 0, g.h - 1)
            hits = self.near[rowc[:, None, :], colc[None, :, :]] & okr[:, None, :] & okc[None, :, :]
            out[i] = hits.mean(axis=2)
        return out

    def align(self, frames: Iterable[Frame]) -> dict[str, tuple[float, float, float]]:
        """The shift (WALK_SHIFT_*) that lays the walks in `frames` not aligned
        yet best on this map: ONE shift for all of them -- walks from the same
        dock are in the same frame -- unless a walk lies better by
        WALK_SHIFT_GAIN somewhere else (a walk that really is in another frame).
        4 Nov afternoon, floor 2: the map lays a walk about as well 32 cm away
        in y (0.50-0.53 at both, every walk); four morning walks all picked one,
        the two afternoon surveys (mostly spot turns at the room's ends) the
        other, each walk by 0.01 -- and their findings would have stood 32 cm
        off the rest, agreeing with each other."""
        by_run: dict[str, list[Frame]] = {}
        for f in frames:
            by_run.setdefault(f.run, []).append(f)
        scores = {run: self._shift_scores(fs) for run, fs in by_run.items() if run not in self.shift}
        scores = {run: s for run, s in scores.items() if s is not None}
        for run in by_run:
            if run not in self.shift and run not in scores:
                self.shift[run] = (0.0, 0.0, 0.0)
        if not scores:
            return dict(self.shift)
        ds, ts = _shift_axes()
        pooled = sum(scores.values()) / len(scores)
        common = _best_shift(pooled, ds, ts)
        for run, total in scores.items():
            own = _best_shift(total, ds, ts)
            if _score_at(total, own, ds, ts) - _score_at(total, common, ds, ts) >= WALK_SHIFT_GAIN:
                self.shift[run] = own
            else:
                self.shift[run] = common
        return dict(self.shift)

    def _walk_shift(self, frames: list[Frame]) -> tuple[float, float, float]:
        """One walk alone (the old rule; align() pools walks)."""
        total = self._shift_scores(frames)
        if total is None:
            return (0.0, 0.0, 0.0)
        ds, ts = _shift_axes()
        return _best_shift(total, ds, ts)

    def _shift_scores(self, frames: list[Frame]) -> np.ndarray | None:
        """Mean fit of a sample of the walk's frames at every shift on the
        WALK_SHIFT_* grid, shape (yaws, dys, dxs); None without usable frames."""
        usable = [f for f in frames if f.q is None or self.min_quality is None or f.q >= self.min_quality]
        sample = usable[:: max(1, len(usable) // WALK_SHIFT_SAMPLE)][:WALK_SHIFT_SAMPLE]
        n_m = int(round(WALK_SHIFT_M / WALK_SHIFT_STEP_M))
        n_d = int(round(WALK_SHIFT_DEG / WALK_SHIFT_STEP_DEG))
        ds = np.arange(-n_m, n_m + 1) * WALK_SHIFT_STEP_M
        ts = np.radians(np.arange(-n_d, n_d + 1) * WALK_SHIFT_STEP_DEG)
        total, n = np.zeros((len(ts), len(ds), len(ds))), 0
        for f in sample:
            beams = _beams(f, self.profile.lidar)
            if len(beams) < 20:
                continue
            pts = np.stack([beams[:, 1] * np.cos(beams[:, 0]), beams[:, 1] * np.sin(beams[:, 0])], axis=1)
            pts = pts[:: max(1, len(pts) // 200)]
            x, y, yaw = f.pose
            total += self._fit(x + ds, y + ds, yaw + ts, pts)
            n += 1
        if not n:
            return None
        return total / n

    def place(self, frame: Frame) -> tuple[tuple[float, float, float] | None, float, float]:
        """(refined pose or None, fit at the board's pose, fit after) -- the
        search is round the board's pose moved by its walk's shift (`align`)."""
        beams = _beams(frame, self.profile.lidar)
        if len(beams) < 20:
            return None, 0.0, 0.0
        pts = np.stack([beams[:, 1] * np.cos(beams[:, 0]), beams[:, 1] * np.sin(beams[:, 0])], axis=1)
        pts = pts[:: max(1, len(pts) // 400)]
        bx, by, byaw = frame.pose
        sx, sy, syaw = self.shift.get(frame.run, (0.0, 0.0, 0.0))
        x, y, yaw = bx + sx, by + sy, byaw + syaw
        n_m = int(round(REFINE_M / REFINE_STEP_M))
        n_d = int(round(REFINE_DEG / REFINE_STEP_DEG))
        dxs = np.arange(-n_m, n_m + 1) * REFINE_STEP_M
        dys = dxs
        dyaws = np.radians(np.arange(-n_d, n_d + 1) * REFINE_STEP_DEG)
        fits = self._fit(x + dxs, y + dys, yaw + dyaws, pts)
        before = float(fits[n_d, n_m, n_m])
        if (sx, sy, syaw) != (0.0, 0.0, 0.0):        # "before" is the board's own pose
            before = float(self._fit(np.array([bx]), np.array([by]), np.array([byaw]), pts)[0, 0, 0])
        # Best fit; ties go to the smallest move (a flat corridor must not slide).
        best = fits.max()
        k, j, i = np.nonzero(fits >= best - 1e-9)
        move = (dxs[i] ** 2 + dys[j] ** 2) + (dyaws[k] * 0.5) ** 2
        b = int(np.argmin(move))
        pose = (x + float(dxs[i[b]]), y + float(dys[j[b]]), yaw + float(dyaws[k[b]]))
        return pose, before, float(best)

    # -- seeing through ----------------------------------------------------
    def _trace(self, pose, beams) -> tuple[np.ndarray, np.ndarray]:
        g = self.grid
        x, y, yaw = pose
        ang = beams[:, 0] + yaw
        dist = beams[:, 1]
        c, s = np.cos(ang), np.sin(ang)
        step = g.res * 0.5
        # Seen through right up to the return: the cells just before it are
        # the hit's neighbours, which findings() never counts as seen through.
        counts = np.floor((dist - 0.2) / step).astype(int) + 1
        counts = np.where(dist > 0.2, counts, 0)
        total = int(counts.sum())
        if total:
            beam = np.repeat(np.arange(len(dist)), counts)
            first = np.repeat(np.cumsum(counts) - counts, counts)
            r = 0.2 + (np.arange(total) - first) * step
            sx, sy = x + r * c[beam], y + r * s[beam]
            col = np.floor((sx - g.ox) / g.res).astype(int)
            row = np.floor((sy - g.oy) / g.res).astype(int)
            ok = (col >= 0) & (col < g.w) & (row >= 0) & (row < g.h)
            passed = np.unique(row[ok] * g.w + col[ok])
        else:
            passed = np.zeros(0, dtype=int)
        hx, hy = x + dist * c, y + dist * s
        col = np.floor((hx - g.ox) / g.res).astype(int)
        row = np.floor((hy - g.oy) / g.res).astype(int)
        ok = (col >= 0) & (col < g.w) & (row >= 0) & (row < g.h)
        hit = np.unique(row[ok] * g.w + col[ok])
        return passed, hit

    def add(self, frame: Frame) -> dict:
        """One frame. Returns its row (also kept in `frames`)."""
        row = {"run": frame.run, "n": frame.n, "board": [round(v, 3) for v in frame.pose],
               "q": frame.q, "picture": frame.picture}
        if frame.q is not None and self.min_quality is not None and frame.q < self.min_quality:
            row["skipped"] = "quality %d" % frame.q
            self.frames.append(row)
            return row
        pose, before, after = self.place(frame)
        row["fit_board"], row["fit"] = round(before, 3), round(after, 3)
        if pose is None or after < FIT_MIN:
            row["skipped"] = "pose not on this map (fit %.2f)" % after
            self.frames.append(row)
            return row
        row["pose"] = [round(v, 3) for v in pose]
        passed, hit = self._trace(pose, _beams(frame, self.profile.lidar))
        size = self.grid.h * self.grid.w
        p = self.passed.setdefault(frame.run, np.zeros(size, dtype=np.uint16))
        h = self.hit.setdefault(frame.run, np.zeros(size, dtype=np.uint16))
        p[passed] += 1
        self.body.setdefault(frame.run, []).append(
            (int(math.floor((pose[1] - self.grid.oy) / self.grid.res)),
             int(math.floor((pose[0] - self.grid.ox) / self.grid.res))))
        h[hit] += 1
        self.frames.append(row)
        return row

    # -- the verdict ---------------------------------------------------------
    def findings(self, min_runs: int = MIN_RUNS) -> tuple[list[dict], dict[str, np.ndarray]]:
        """(findings, masks). Each finding: id, kind, cells, centre, size,
        runs (walks that saw it the way its kind says)."""
        g = self.grid
        shape = (g.h, g.w)
        occ, free = g.occ(), g.free()
        runs = sorted(self.passed)
        if not runs:
            empty = np.zeros(shape, dtype=bool)
            return [], {"phantom": empty, "new": empty, "changing": empty}
        hit_near = {r: _dilate(self.hit[r].reshape(shape) > 0, HIT_TOL_CELLS) for r in runs}
        cleared = {r: ((self.passed[r].reshape(shape) >= MIN_PASS) & ~hit_near[r]) | self._stood(r)
                   for r in runs}
        n_cleared = sum(cleared[r].astype(np.int16) for r in runs)
        n_hit = sum(hit_near[r].astype(np.int16) for r in runs)
        hit_cells = np.zeros(shape, dtype=bool)
        for r in runs:
            hit_cells |= self.hit[r].reshape(shape) > 0

        phantom = occ & (n_cleared >= min_runs) & (n_hit == 0)
        away = free & ~_dilate(occ, NEW_GAP_CELLS)
        new = away & hit_cells & (n_hit >= min_runs) & (n_cleared == 0)
        changing = away & hit_cells & (n_hit >= 1) & (n_cleared >= 1)
        masks = {"phantom": phantom, "new": new, "changing": changing}

        out: list[dict] = []
        for kind, mask in masks.items():
            support = n_cleared if kind == "phantom" else n_hit
            for cells in _components(mask):
                if len(cells) < MIN_CELLS:
                    continue
                rows = np.array([c[0] for c in cells])
                cols = np.array([c[1] for c in cells])
                wx, wy = g.world(rows, cols)
                length, width = _extent(wx, wy, g.res)
                k = kind
                if kind == "phantom" and length >= LINE_MIN_M and width <= LINE_MAX_W_M:
                    k = "line"
                out.append({"kind": k, "cells": len(cells),
                            "centre": [round(float(wx.mean()), 3), round(float(wy.mean()), 3)],
                            "box": [round(float(wx.min()), 2), round(float(wy.min()), 2),
                                    round(float(wx.max()), 2), round(float(wy.max()), 2)],
                            "length_m": round(length, 2), "width_m": round(width, 2),
                            "runs": int(support[rows, cols].max()),
                            "rc": [rows.tolist(), cols.tolist()]})
        order = {"phantom": 0, "new": 1, "line": 2, "changing": 3}
        out.sort(key=lambda f: (order[f["kind"]], -f["cells"]))
        for i, f in enumerate(out, 1):
            f["id"] = i
        return out, masks

    def _stood(self, run: str) -> np.ndarray:
        g = self.grid
        mask = np.zeros((g.h, g.w), dtype=bool)
        for r, c in self.body.get(run, []):
            if 0 <= r < g.h and 0 <= c < g.w:
                mask[r, c] = True
        return _dilate(mask, max(1, int(round(self.body_r / g.res))))

    def coverage(self) -> int:
        """Cells some walk saw (through or at) -- how much of the map was checked."""
        seen = np.zeros(self.grid.h * self.grid.w, dtype=bool)
        for r in self.passed:
            seen |= (self.passed[r] > 0) | (self.hit[r] > 0)
        return int(seen.sum())


def _shift_axes() -> tuple[np.ndarray, np.ndarray]:
    n_m = int(round(WALK_SHIFT_M / WALK_SHIFT_STEP_M))
    n_d = int(round(WALK_SHIFT_DEG / WALK_SHIFT_STEP_DEG))
    return (np.arange(-n_m, n_m + 1) * WALK_SHIFT_STEP_M,
            np.radians(np.arange(-n_d, n_d + 1) * WALK_SHIFT_STEP_DEG))


def _best_shift(total: np.ndarray, ds: np.ndarray, ts: np.ndarray) -> tuple[float, float, float]:
    """The best (dx, dy, dyaw) of a score grid -- none unless it beats no
    shift by WALK_SHIFT_GAIN; ties go to the smallest."""
    n_d, n_m = len(ts) // 2, len(ds) // 2
    best = total.max()
    if best - total[n_d, n_m, n_m] < WALK_SHIFT_GAIN:
        return (0.0, 0.0, 0.0)
    k, j, i = np.nonzero(total >= best - 1e-9)
    b = int(np.argmin(ds[i] ** 2 + ds[j] ** 2 + (ts[k] * 0.5) ** 2))
    return (float(ds[i[b]]), float(ds[j[b]]), float(ts[k[b]]))


def _score_at(total: np.ndarray, shift: tuple[float, float, float], ds: np.ndarray, ts: np.ndarray) -> float:
    i = int(np.argmin(np.abs(ds - shift[0])))
    j = int(np.argmin(np.abs(ds - shift[1])))
    k = int(np.argmin(np.abs(ts - shift[2])))
    return float(total[k, j, i])


def _components(mask: np.ndarray) -> Iterable[list[tuple[int, int]]]:
    """8-connected components of a boolean grid (no scipy on the robot)."""
    seen = np.zeros_like(mask, dtype=bool)
    h, w = mask.shape
    for r0, c0 in zip(*np.nonzero(mask)):
        if seen[r0, c0]:
            continue
        seen[r0, c0] = True
        comp, todo = [], deque([(int(r0), int(c0))])
        while todo:
            r, c = todo.popleft()
            comp.append((r, c))
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < h and 0 <= cc < w and mask[rr, cc] and not seen[rr, cc]:
                        seen[rr, cc] = True
                        todo.append((rr, cc))
        yield comp


def _extent(wx: np.ndarray, wy: np.ndarray, res: float) -> tuple[float, float]:
    """Length and width (m) along the cells' own main axis."""
    if len(wx) < 2:
        return res, res
    pts = np.stack([wx - wx.mean(), wy - wy.mean()], axis=1)
    _, _, vt = np.linalg.svd(pts, full_matrices=False)
    along, across = pts @ vt[0], pts @ vt[1]
    return float(np.ptp(along) + res), float(np.ptp(across) + res)


def cleaned(grid: Grid, findings: list[dict], *, keep: Iterable[int] = (),
            clear: Iterable[int] = ()) -> tuple[Grid, list[int]]:
    """The grid with phantoms cleared and new things added. `keep` = ids a
    person looked at and said leave alone; `clear` = "line"/"changing" ids a
    person looked at and said go ahead. Returns (grid, ids applied)."""
    keep, clear = set(keep), set(clear)
    cells = grid.cells.copy()
    applied = []
    for f in findings:
        if f["id"] in keep:
            continue
        rows, cols = (np.asarray(v, dtype=int) for v in f["rc"])
        if f["kind"] == "phantom" or (f["kind"] == "line" and f["id"] in clear):
            cells[rows, cols] = FREE_VALUE
        elif f["kind"] == "new" or (f["kind"] == "changing" and f["id"] in clear):
            cells[rows, cols] = OCC_VALUE
        else:
            continue
        applied.append(f["id"])
    return Grid(cells, grid.ox, grid.oy, grid.res, grid.header), applied


def blocked(grid: Grid, spots: Iterable[tuple[float, float, float]]) -> Grid:
    """Mark (x, y, radius) as an obstacle: a thing under the laser's plane, which no walk
    can ever find, so the board's planner keeps off it. A few cells the lidar does not see
    cost the fit nothing against the thousands it does."""
    cells = grid.cells.copy()
    rows, cols = np.mgrid[0:grid.h, 0:grid.w]
    wx, wy = grid.world(rows, cols)
    for x, y, radius in spots:
        cells[np.hypot(wx - x, wy - y) <= radius] = OCC_VALUE
    return Grid(cells, grid.ox, grid.oy, grid.res, grid.header)


def lens(camera: dict | None) -> dict:
    """How a walk's picture columns map to the lidar's bearings, from
    run.json's camera: {"hfov", "a", "b", "head"} -- lidar = a * (picture
    bearing + head) + b, the picture bearing from `hfov`, `head(deg)` the
    recorded head angle in those same units.

    The head's lidar check, once it held, is fitted in the units the head loop
    uses (the ASSUMED field of view) and carries any mirroring in `a`: then
    everything stays in those units. Without it: the field of view measured on
    the robot (`hfov_measured` -- 4 Nov, 19 deg from the base's spot turns, not
    the 70 assumed) and `mirrored` (the head camera's picture is left-right
    flipped: a = -1); the head angles were recorded in the assumed units and
    are carried over to the measured ones by the small-angle ratio `k`, LINEARLY:
    the recorded angle is the sweep's small step scaled up with the pulse, and
    the servo turns linearly with the pulse. (4 Nov afternoon: `atan(tan(h) k)`
    held for the 35 % scan but reads a 75 % pan, 70.8 recorded, as 34 deg --
    the head turns ~17.) `tilt` is the nod in real degrees, + = down.
    Old walks with neither: the assumed lens, a = 1, as before."""
    cam = camera or {}
    assumed = float(cam.get("hfov_assumed") or 70.0)
    a, b = cam.get("lidar_a"), cam.get("lidar_b")
    hfov = float(cam.get("hfov_measured") or assumed)
    k = math.tan(math.radians(hfov / 2.0)) / math.tan(math.radians(assumed / 2.0))
    if isinstance(a, (int, float)) and not isinstance(a, bool) and a:
        return {"hfov": assumed, "a": float(a), "b": float(b) if isinstance(b, (int, float)) else 0.0,
                "head": lambda h: float(h), "tilt": lambda t: float(t) * k}
    return {"hfov": hfov, "a": -1.0 if cam.get("mirrored") else 1.0, "b": 0.0,
            "head": lambda h: float(h) * k, "tilt": lambda t: float(t) * k}


#: A picture for a finding (a wall, a cabinet at the lidar's height) comes
#: from a frame that looks across the room, not at the floor in front: the
#: walk scan nods to ~36 deg for the floor (4 Nov afternoon), and a frame that
#: steep shows the floor 1-2 m out, whatever the finding's bearing.
PICTURE_MAX_TILT_DEG = 25.0


def picture_for(finding: dict, frames: list[Frame], placed: dict[tuple[str, int], tuple],
                cameras: dict[str, dict | None] | None = None, max_m: float = 5.0) -> dict | None:
    """The frame whose camera looked at a finding (nearest, nearest the
    picture's middle), and where in the picture -- column fraction from the
    left. `cameras`: run -> run.json's camera (each walk its own head check)."""
    cameras = cameras or {}
    cx, cy = finding["centre"]
    best = None
    for fr in frames:
        if not fr.picture:
            continue
        ln = lens(cameras.get(fr.run))
        hfov, a, b = ln["hfov"], ln["a"], ln["b"]
        pose = placed.get((fr.run, fr.n))
        if pose is None:
            continue
        x, y, yaw = pose
        d = math.hypot(cx - x, cy - y)
        if d > max_m or d < 0.3:
            continue
        tilt = ln["tilt"](fr.tilt_deg)
        if tilt > PICTURE_MAX_TILT_DEG:
            continue
        lidar = math.degrees((math.atan2(cy - y, cx - x) - yaw + math.pi) % (2 * math.pi) - math.pi)
        bearing = (lidar - b) / a - ln["head"](fr.head_deg)   # + = left of the picture's middle
        if abs(bearing) > hfov / 2 * 0.9:
            continue
        # level first: a desk-height nod (~18 deg) only where nothing level saw it
        cost = d + abs(bearing) / 20.0 + max(tilt, 0.0) / 20.0
        if best is None or cost < best[0]:
            half = math.tan(math.radians(hfov / 2))
            best = (cost, {"run": fr.run, "n": fr.n, "picture": fr.picture, "distance_m": round(d, 2),
                           "column": round(0.5 - math.tan(math.radians(bearing)) / (2 * half), 3)})
    return None if best is None else best[1]
