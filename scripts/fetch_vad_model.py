"""Fetch the Silero VAD model (~630KB, once) for VAD_MODE=local.

Kept out of the repo for the same reason the wake model is: weights are big
binaries that one command can always restore.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
       "asr-models/silero_vad.onnx")


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.config import settings

    dest = Path(settings.vad_model).expanduser()
    if dest.is_file():
        print(f"already there: {dest}")
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {URL}")
    urllib.request.urlretrieve(URL, dest)
    print(f"saved {dest} ({dest.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
