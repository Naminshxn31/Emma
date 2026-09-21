"""What do the bytes in the chassis's explore map mean? Measure, don't guess.

Read-only. Fetches the map, the current lidar frame and the pose from the
running server (`/robot/map`, `/robot/laserscan`, `/robot/state`), projects
every lidar return onto the grid, and tabulates the byte value found

  * at the robot's own cell (must be free — it is standing there),
  * at the cell each lidar return ends in (an obstacle, by definition),
  * along the ray just short of each return (free, by definition).

Whichever value group lights up under "hit" is occupied and whichever lights
up under "ray" is free. The SLAMTEC spec says only "one byte per cell"; the
2026-09-12 frame showed two ladders (20..127 and 129..196) plus 0, and this
script exists so the threshold in `robot_chassis` is a measurement.

    python scripts/probe_map_semantics.py [--server https://127.0.0.1:8001]

The WS token is read from .env and never printed.
"""
from __future__ import annotations

import argparse
import base64
import math
import sys
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def token_from_env() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("WS_TOKEN="):
            return line.split("=", 1)[1].strip()
    return ""


def fetch(client: httpx.Client, path: str, token: str) -> dict:
    out = client.get(path, params={"token": token}).json()
    if not out.get("ok"):
        sys.exit("%s -> %s" % (path, out.get("error")))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="https://127.0.0.1:8001")
    parser.add_argument("--max", type=int, default=400, help="lidar points to use")
    args = parser.parse_args()

    token = token_from_env()
    client = httpx.Client(base_url=args.server, verify=False, timeout=30)
    grid = fetch(client, "/robot/map", token)["map"]
    scan = fetch(client, "/robot/laserscan", token)
    state = fetch(client, "/robot/state", token)["state"]

    cells = base64.b64decode(grid["cells_b64"])
    w, h, res = grid["width"], grid["height"], grid["resolution"]
    ox, oy = grid["origin_x"], grid["origin_y"]

    def at(x: float, y: float) -> int | None:
        col = int(math.floor((x - ox) / res))
        row = int(math.floor((y - oy) / res))
        if 0 <= col < w and 0 <= row < h:
            return cells[row * w + col]
        return None

    pose = scan.get("pose") or state["pose"]
    px, py, pyaw = float(pose["x"]), float(pose["y"]), float(pose["yaw"])
    print("map %dx%d @ %.3f m, origin (%.3f, %.3f); pose (%.3f, %.3f, yaw %.3f); "
          "localization_quality=%s" % (w, h, res, ox, oy, px, py, pyaw,
                                       state.get("localization_quality")))
    print("all cells      :", sorted(Counter(cells).items()))
    print("robot's own cell:", at(px, py))

    hit: Counter = Counter()
    ray: Counter = Counter()
    off_map = 0
    for p in scan["points"]:
        if not p["valid"] or p["distance"] <= 0:
            continue
        a = pyaw + p["angle"]
        d = p["distance"]
        v = at(px + d * math.cos(a), py + d * math.sin(a))
        if v is None:
            off_map += 1
            continue
        hit[v] += 1
        # Sample the free stretch: from 3 cells past the robot to 3 cells
        # short of the hit, so neither end contaminates the other.
        steps = int(d / res)
        for k in range(3, max(3, steps - 3)):
            rv = at(px + k * res * math.cos(a), py + k * res * math.sin(a))
            if rv is not None:
                ray[rv] += 1
    print("lidar returns used: %d (off map: %d)" % (sum(hit.values()), off_map))
    print("value at HIT cells (obstacle by definition):", sorted(hit.items()))
    print("value along RAYS   (free by definition)     :", sorted(ray.items()))


if __name__ == "__main__":
    main()
