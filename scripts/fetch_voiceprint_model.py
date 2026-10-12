#!/usr/bin/env python3
"""Fetch the speaker-embedding model for voice prints (one time, ~28 MB).

    python scripts/fetch_voiceprint_model.py           # skip if a verified copy exists
    python scripts/fetch_voiceprint_model.py --force   # download again anyway

Puts 3D-Speaker CAM++ (zh+en, 16 kHz; Apache-2.0) from the sherpa-onnx
`speaker-recongition-models` release into data/models/voiceprint/ (gitignored
-- weights one command restores, like the wake, VAD and face models), where
app/voiceprint.py looks for it when VOICEPRINT_MODEL is empty. Checked against
the sha256 pinned in app/voiceprint.py before it replaces anything; a mismatch
leaves nothing behind.

Why this model and not another: measured, see the app/voiceprint.py docstring
(real Thai/English/Chinese speech, 0.8-3 s windows). THRESHOLD there is only
true for these weights -- another file means re-measuring.

The robot downloads the same pinned file itself during
`python scripts/robot_server.py deploy` (FETCH_ON_ROBOT), and deploy only pins
what this PC has -- so run this once here first.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import voiceprint  # noqa: E402  (pins; numpy/sherpa only inside functions)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--force", action="store_true", help="download even if a verified copy exists")
    args = ap.parse_args()

    target = voiceprint.DEFAULT_MODEL
    if not args.force and target.is_file() and voiceprint._sha256(target) == voiceprint.MODEL_SHA256:
        print("already there (sha256 ok): %s" % target)
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    print("downloading %s (%.0f MB)" % (voiceprint.MODEL_URL, voiceprint.MODEL_BYTES / 1e6))
    # Beside and rename, verified first: a half-written model is worse than a
    # missing one -- missing is a logged "voice prints off", half-written was
    # a protobuf error inside the worker.
    part = target.with_name(target.name + ".part")
    h = hashlib.sha256()
    with urllib.request.urlopen(voiceprint.MODEL_URL, timeout=120) as r, open(part, "wb") as out:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            out.write(chunk)
    if h.hexdigest() != voiceprint.MODEL_SHA256:
        part.unlink(missing_ok=True)
        print("sha256 mismatch: got %s, pinned %s -- nothing replaced"
              % (h.hexdigest(), voiceprint.MODEL_SHA256))
        return 1
    part.replace(target)
    print("ok %s (%s bytes, sha256 verified)" % (target, format(target.stat().st_size, ",")))
    print("robot: python scripts/robot_server.py deploy  (the robot downloads the same pinned file)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
