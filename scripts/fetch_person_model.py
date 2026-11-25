#!/usr/bin/env python3
"""
Download the person detector (one time, 3.7 MB): YOLOX-nano, Apache-2.0.

    python scripts/fetch_person_model.py

Not committed, like every other model here: one command restores it. On the robot
`robot_server.py deploy` fetches the same bytes (FETCH_ON_ROBOT), so run this on the PC
first. The hash is pinned in app/person_vision.py; another file is refused, here and there.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import person_vision  # noqa: E402


def main() -> int:
    target = person_vision.MODEL
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == person_vision.SHA256:
        print(f"already there: {target}")
        return 0
    print(f"downloading {person_vision.URL} ...")
    with urllib.request.urlopen(person_vision.URL) as resp:
        payload = resp.read()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != person_vision.SHA256:
        print(f"refused: got sha256 {digest}, pinned {person_vision.SHA256}")
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(payload)
    tmp.replace(target)
    print(f"done: {target} ({len(payload) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
