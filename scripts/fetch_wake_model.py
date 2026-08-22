#!/usr/bin/env python3
"""
Download the wake-word model (one time, ~15MB).

    python scripts/fetch_wake_model.py

The model is not committed: 30MB of ONNX weights that one command restores
do not belong in history (same rule as the slide images). This puts it where
`WAKE_MODEL_DIR` expects it; after that, `WAKE_ENABLED=true` in .env is all
the wake word needs.
"""
from __future__ import annotations

import io
import sys
import tarfile
import urllib.request
from pathlib import Path

NAME = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{NAME}.tar.bz2"
DEST = Path("data/wake")


def main() -> int:
    target = DEST / NAME
    if (target / "tokens.txt").exists():
        print(f"already there: {target}")
        return 0
    DEST.mkdir(parents=True, exist_ok=True)
    print(f"downloading {URL} ...")
    with urllib.request.urlopen(URL) as resp:
        payload = resp.read()
    print(f"got {len(payload) / 1e6:.1f} MB, extracting ...")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:bz2") as tar:
        tar.extractall(DEST, filter="data")
    if not (target / "tokens.txt").exists():
        print("extraction finished but tokens.txt is missing — archive layout changed?")
        return 1
    print(f"done: {target}")
    print("now set WAKE_ENABLED=true in .env and restart the server")
    return 0


if __name__ == "__main__":
    sys.exit(main())
