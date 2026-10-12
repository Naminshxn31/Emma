"""Fetch the GTCRN speech-enhancement model (~535 KB, once) for ENHANCE_ENABLED.

sherpa-onnx's release asset, pinned by sha256: a model file that changed
under the same URL is a different model, and the numbers in app/enhance.py
were measured on this one.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
       "speech-enhancement-models/gtcrn_simple.onnx")
SHA256 = "e77603ac0c23dac3227dd2d7135b3a585cbee2679048aecfa886657d3ae1b534"


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.config import settings

    dest = Path(settings.enhance_model).expanduser()
    if dest.is_file():
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        print(f"already there: {dest} ({'sha256 ok' if digest == SHA256 else 'SHA256 DIFFERS: ' + digest})")
        return 0 if digest == SHA256 else 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    print(f"downloading {URL}")
    urllib.request.urlretrieve(URL, tmp)
    digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
    if digest != SHA256:
        tmp.unlink()
        print(f"sha256 mismatch: got {digest}, want {SHA256} — not installed")
        return 1
    tmp.replace(dest)
    print(f"saved {dest} ({dest.stat().st_size:,} bytes, sha256 ok)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
