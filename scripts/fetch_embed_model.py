#!/usr/bin/env python3
"""Fetch the ONNX export of the local embedding model (one time, ~480 MB).

    python scripts/fetch_embed_model.py           # skip files already verified
    python scripts/fetch_embed_model.py --force   # download again anyway

Puts `onnx/model.onnx` + `tokenizer.json` of
sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2, at the pinned
commit, into data/models/paraphrase-multilingual-MiniLM-L12-v2/ (gitignored --
weights that one command restores, like the wake and face models). Every file
is checked against its sha256 in app/embed_onnx.py before it replaces anything;
a mismatch leaves nothing behind.

What it is for: semantic slide search on a machine without torch -- the robot,
which downloads the same pinned files itself during
`python scripts/robot_server.py deploy` (FETCH_ON_ROBOT, ~6 MB/s on the robot vs
~0.3 MB/s over adb). Deploy only ships what this PC has, so run this once here
first. On a PC with sentence-transformers it is optional: `auto` keeps using
sentence-transformers there; `EMBED_LOCAL_BACKEND=onnx` runs this export instead
(how the equivalence in CLAUDE.md is re-measured).

Same vectors as sentence-transformers (measured, see app/embed_onnx.py), so the
existing embeddings.npz stays valid -- no rebuild.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import embed_onnx  # noqa: E402  (pins only; stdlib at import)


def fetch(name: str, dest: Path, force: bool) -> None:
    _path, digest, size = embed_onnx.FILES[name]
    target = dest / name
    if not force and target.is_file() and embed_onnx.sha256_file(target) == digest:
        print("already there (sha256 ok): %s" % target)
        return
    url = embed_onnx.url(name)
    print("downloading %s (%.0f MB)" % (url, size / 1e6))
    # Beside and rename, verified first: a half-written model is worse than
    # a missing one -- missing is "lexical search, logged"; half-written was a
    # protobuf error from onnxruntime.
    part = target.with_name(target.name + ".part")
    h = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=120) as r, open(part, "wb") as out:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            out.write(chunk)
    if h.hexdigest() != digest:
        part.unlink(missing_ok=True)
        raise SystemExit("sha256 mismatch for %s: got %s, pinned %s -- nothing replaced"
                         % (name, h.hexdigest(), digest))
    part.replace(target)
    print("  ok %s (%s bytes, sha256 verified)" % (target, format(target.stat().st_size, ",")))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--force", action="store_true", help="download even if a verified copy exists")
    args = ap.parse_args()
    dest = embed_onnx.DIR
    dest.mkdir(parents=True, exist_ok=True)
    for name in embed_onnx.FILES:
        fetch(name, dest, args.force)
    print("done: %s" % dest)
    print("robot: python scripts/robot_server.py deploy  (the robot downloads the same pinned files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
