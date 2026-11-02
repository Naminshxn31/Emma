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


def _beams(frame: Frame) -> np.ndarray:
    s = np.asarray(frame.scan, dtype=float).reshape(-1, 2)
    s = s[(s[:, 1] > MIN_RANGE_M) & (s[:, 1] < MAX_RANGE_M) & np.isfinite(s).all(axis=1)]
    return s[:: max(1, len(s) // MAX_BEAMS)] if len(s) else s


class Checker:
    """Lay frames on one map, walk by walk; `findings()` when done."""

    def __init__(self, grid: Grid) -> None:
        self.grid = grid
        self.near = _dilate(grid.occ(), max(1, int(round(FIT_TOL_M / grid.res))))
        self.passed: dict[str, np.ndarray] = {}
        self.hit: dict[str, np.ndarray] = {}
        self.frames: list[dict] = []        # per frame: run, n, pose used, fits, why skipped
        self.body: dict[str, list[tuple[int, int]]] = {}   # per walk: cells the robot stood on

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

    def place(self, frame: Frame) -> tuple[tuple[float, float, float] | None, float, float]:
        """(refined pose or None, fit at the board's pose, fit after)."""
        beams = _beams(frame)
        if len(beams) < 20:
            return None, 0.0, 0.0
        pts = np.stack([beams[:, 1] * np.cos(beams[:, 0]), beams[:, 1] * np.sin(beams[:, 0])], axis=1)
        pts = pts[:: max(1, len(pts) // 400)]
        x, y, yaw = frame.pose
        n_m = int(round(REFINE_M / REFINE_STEP_M))
        n_d = int(round(REFINE_DEG / REFINE_STEP_DEG))
        dxs = np.arange(-n_m, n_m + 1) * REFINE_STEP_M
        dys = dxs
        dyaws = np.radians(np.arange(-n_d, n_d + 1) * REFINE_STEP_DEG)
        fits = self._fit(x + dxs, y + dys, yaw + dyaws, pts)
        before = float(fits[n_d, n_m, n_m])
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
        if frame.q is not None and frame.q < MIN_QUALITY:
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
        passed, hit = self._trace(pose, _beams(frame))
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
        return _dilate(mask, max(1, int(round(BODY_R_M / g.res))))

    def coverage(self) -> int:
        """Cells some walk saw (through or at) -- how much of the map was checked."""
        seen = np.zeros(self.grid.h * self.grid.w, dtype=bool)
        for r in self.passed:
            seen |= (self.passed[r] > 0) | (self.hit[r] > 0)
        return int(seen.sum())


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


def _lens(camera: dict | None) -> tuple[float, float, float]:
    """(field of view deg, lidar_a, lidar_b) -- the walk sheet's lidar strip's
    reading of run.json's camera: assumed 70 deg until the head's lidar check
    held, then what it measured."""
    cam = camera or {}
    hfov = float(cam.get("hfov_from_lidar") or cam.get("hfov_assumed") or 70.0)
    a = cam.get("lidar_a")
    b = cam.get("lidar_b")
    a = float(a) if isinstance(a, (int, float)) and a else 1.0
    b = float(b) if isinstance(b, (int, float)) else 0.0
    return hfov, a, b


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
        hfov, a, b = _lens(cameras.get(fr.run))
        pose = placed.get((fr.run, fr.n))
        if pose is None:
            continue
        x, y, yaw = pose
        d = math.hypot(cx - x, cy - y)
        if d > max_m or d < 0.3:
            continue
        lidar = math.degrees((math.atan2(cy - y, cx - x) - yaw + math.pi) % (2 * math.pi) - math.pi)
        bearing = (lidar - b) / a - fr.head_deg          # + = left of the picture's middle
        if abs(bearing) > hfov / 2 * 0.9:
            continue
        cost = d + abs(bearing) / 20.0
        if best is None or cost < best[0]:
            half = math.tan(math.radians(hfov / 2))
            best = (cost, {"run": fr.run, "n": fr.n, "picture": fr.picture, "distance_m": round(d, 2),
                           "column": round(0.5 - math.tan(math.radians(bearing)) / (2 * half), 3)})
    return None if best is None else best[1]
