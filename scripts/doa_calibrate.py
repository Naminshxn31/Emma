"""Offline check and refit of the mic array's lag -> bearing calibration.

The server learns it online (app/doa.py): each time exactly one engaged face
is speaking on camera, the frame's lags and that face's bearing become a
pair, logged to the turn log as a `doa_pair` row (numbers only). This script
answers "how good is it?" from those rows -- k-fold, so every pair is judged
by a model that never saw it -- and can write a fresh calibration from them.

    python scripts/doa_calibrate.py                    # data/logs, last 7 days: accuracy only
    python scripts/doa_calibrate.py --days 30 --write  # ...and write data/doa_calibration.json
    python scripts/doa_calibrate.py --synthetic circle # the model on a simulated array
                                                       # (circle | line | vertical)

--write keeps the fingerprint (rate/channels/device) of the calibration file
already there: pairs from another channel map are another space. Stop the
server first or it overwrites the file with its own at the next save.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import doa  # noqa: E402


def pairs_from_logs(folder: Path, days: int) -> list[tuple[list[float], float, float]]:
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    out = []
    for path in sorted(folder.glob("*.jsonl")):
        if path.stem[:10] < cutoff:          # age from the file NAME (CLAUDE.md: never mtime)
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("event") != "doa_pair":
                continue
            lags, bearing, w = row.get("lags"), row.get("bearing"), row.get("w", 1.0)
            if isinstance(lags, list) and len(lags) == 6 and isinstance(bearing, (int, float)):
                out.append(([float(x) for x in lags], float(bearing), float(w or 1.0)))
    return out


def synthetic_pairs(kind: str, n: int, seed: int = 0, outliers: float = 0.1, noise: float = 0.15):
    """(pairs, positions): faces at -30..30 degrees, 5..25 degrees elevation,
    `outliers` of the pairs voiced by somebody else at a random bearing."""
    rng = np.random.default_rng(seed)
    if kind == "circle":
        r, rot = 0.032, rng.uniform(0, 2 * math.pi)
        pos = [[r * math.cos(rot + k * math.pi / 2), r * math.sin(rot + k * math.pi / 2), 0.0] for k in range(4)]
    elif kind == "line":
        d, ang = 0.035, math.radians(rng.uniform(-20, 20))
        pos = [[(k - 1.5) * d * math.sin(ang), (k - 1.5) * d * math.cos(ang), 0.0] for k in range(4)]
    elif kind == "vertical":
        r, rot = 0.032, rng.uniform(0, 2 * math.pi)
        pos = [[0.0, r * math.cos(rot + k * math.pi / 2), r * math.sin(rot + k * math.pi / 2)] for k in range(4)]
    else:
        raise SystemExit("--synthetic takes circle, line or vertical")
    pairs = []
    for _ in range(n):
        face = rng.uniform(-30, 30)
        voice = rng.uniform(-90, 90) if rng.random() < outliers else face
        lags = doa.farfield_lags(pos, voice, rng.uniform(5, 25)) + rng.normal(0, noise, 6)
        fitted, _ = doa.consistent(lags, [0.7] * 6)
        pairs.append(([float(x) for x in fitted], face, 0.49))
    return pairs, np.array(pos)


def kfold(pairs, folds: int = 5) -> np.ndarray:
    """Bearing error (degrees) of every pair under a model fitted without it."""
    errors = []
    for k in range(folds):
        cal = doa.Calibration({})
        for i, (lags, bearing, w) in enumerate(pairs):
            if i % folds != k:
                cal.pairs.append((tuple(lags), bearing, w))
        cal.fit()
        if cal.c is None:
            continue
        for i, (lags, bearing, _w) in enumerate(pairs):
            if i % folds == k:
                s, _ = cal.predict(lags)
                errors.append(math.degrees(math.asin(max(-1.0, min(1.0, s)))) - bearing)
    return np.array(errors)


def report(pairs) -> doa.Calibration:
    cal = doa.Calibration({})
    for lags, bearing, w in pairs:
        cal.pairs.append((tuple(lags), bearing, w))
    cal.fit()
    print("pairs %d · spread %.1f° · sigma %.3f samples · ident %.1f · inliers %d · ready %s"
          % (len(cal.pairs), cal.spread_deg, cal.sigma, cal.ident, cal.inliers, cal.ready))
    errors = kfold(pairs)
    if errors.size:
        a = np.abs(errors)
        # the pairs include the outliers the fit rejects: the median and p90
        # are what a speaking face gets; the max is somebody else's voice
        print("k-fold bearing error: median %.1f° · p90 %.1f° · rms %.1f° · max %.1f° (n=%d)"
              % (np.median(a), np.percentile(a, 90), math.sqrt(float(np.mean(errors ** 2))), a.max(), a.size))
    return cal


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--logs", type=Path, default=ROOT / "data" / "logs")
    ap.add_argument("--write", action="store_true", help="write data/doa_calibration.json from the pairs")
    ap.add_argument("--synthetic", choices=["circle", "line", "vertical"])
    args = ap.parse_args()
    if args.synthetic:
        pairs, pos = synthetic_pairs(args.synthetic, 200)
        cal = report(pairs)
        if cal.ready:
            rng = np.random.default_rng(99)
            side = []
            for az in (60, 70, 90, -60, -70, -90):
                lags = doa.farfield_lags(pos, az, 15) + rng.normal(0, 0.15, 6)
                s, _ = cal.predict(doa.consistent(lags, [0.7] * 6)[0])
                side.append("%+d->%+.0f" % (az, math.degrees(math.asin(max(-1.0, min(1.0, s))))))
            print("voices from the side read as:", " ".join(side))
        return 0
    pairs = pairs_from_logs(args.logs, args.days)
    if not pairs:
        print("no doa_pair rows in %s for the last %d days" % (args.logs, args.days))
        return 1
    cal = report(pairs)
    if args.write:
        path = doa._calibration_path()
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            cal.fingerprint = old.get("fingerprint") or {}
        except (OSError, ValueError):
            cal.fingerprint = {"rate": doa.RATE, "channels": list(doa.CHANNELS), "device": ""}
        cal.updated = time.strftime("%Y-%m-%dT%H:%M:%S")
        doa._write(cal.to_json())
        print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
