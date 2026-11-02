#!/usr/bin/env python3
"""Download the offline-mode speech recognizers (one time, ~0.9 GB of archives
-> ~310 MB kept). docs/emma-offline-mode.md.

    python scripts/fetch_offline_models.py              # both, into OFFLINE_ASR_DIR
    python scripts/fetch_offline_models.py --only thai

The two archives are pinned by sha256 (app/offline/asr.py MODELS) and checked
BEFORE anything is extracted; only the pinned files are taken out of them,
each re-hashed after extraction, and a member whose path leaves the model's
own folder (absolute, "..", a link) is refused -- an archive from the network
is untrusted data. Not committed: one command restores them (the wake model's
rule). The robot runs this same script itself (scripts/robot_server.py
deploy, only with OFFLINE_FALLBACK on): 0.9 GB over adb Wi-Fi would take most
of an hour, the robot downloads at ~6 MB/s.

Stdlib only (+ certifi when present, for a Python with no system CA store --
the robot's glibc runtime has none).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import ssl
import sys
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath


class Refused(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _context() -> ssl.SSLContext | None:
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def download(url: str, dest: Path, sha256: str, size: int | None = None) -> Path:
    """`dest`, verified. An existing file with the right hash is kept; a
    wrong one is replaced. Streams to .part and renames: a broken download
    never looks like a finished one."""
    if dest.is_file() and sha256_file(dest) == sha256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    got = 0
    with urllib.request.urlopen(url, timeout=120, context=_context()) as resp, part.open("wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            out.write(chunk)
            got += len(chunk)
    if h.hexdigest() != sha256 or (size is not None and got != size):
        part.unlink(missing_ok=True)
        raise Refused("sha256/size mismatch for %s -- nothing extracted" % url)
    os.replace(part, dest)
    return dest


def _member_ok(name: str, top: str) -> str | None:
    """The file name inside `top/` this member is, or None when it is not a
    plain file directly in the model's folder (traversal, absolute, nested)."""
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or "\\" in name:
        return None
    if len(p.parts) != 2 or p.parts[0] != top:
        return None
    return p.parts[1]


def extract_pinned(archive: Path, top: str, files: dict[str, str], dest: Path) -> list[str]:
    """Take exactly the pinned `files` out of `top/` in the (already verified)
    archive into `dest` (the model folder), re-hashing each. Returns the
    names written. Refuses links, special files and any path outside `top/`
    for the files it takes; everything else in the archive is skipped."""
    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    with tarfile.open(archive, "r:*") as tar:
        for member in tar:
            base = _member_ok(member.name, top)
            if base is None or base not in files:
                continue
            if not member.isreg():
                raise Refused("%s is not a plain file in the archive" % member.name)
            src = tar.extractfile(member)
            if src is None:
                raise Refused("%s could not be read" % member.name)
            out = dest / base
            part = out.with_name(base + ".part")
            h = hashlib.sha256()
            with part.open("wb") as o:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    h.update(chunk)
                    o.write(chunk)
            if h.hexdigest() != files[base]:
                part.unlink(missing_ok=True)
                raise Refused("%s does not match its pin" % base)
            os.replace(part, out)
            written.append(base)
    missing = sorted(set(files) - set(written))
    if missing:
        raise Refused("not in the archive: %s" % ", ".join(missing))
    return written


def _pins(path: str | None) -> dict:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.offline import asr

    return asr.fetch_map()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", choices=["thai", "whisper"])
    ap.add_argument("--dest", help="model root (default: OFFLINE_ASR_DIR)")
    ap.add_argument("--stage", help="where the archives are kept (default: <dest>/.download)")
    ap.add_argument("--pins", help="pins JSON (the robot: written by robot_server.py deploy)")
    ap.add_argument("--keep-archives", action="store_true")
    a = ap.parse_args(argv)
    if a.pins and os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "robot_dns.py")):
        # on the robot: glibc has no resolver config (scripts/robot_dns.py)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            import robot_dns

            robot_dns.install()
        except Exception:
            pass
    pins = _pins(a.pins)
    if a.dest:
        dest = Path(a.dest)
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.offline import asr

        dest = asr.base_dir()
    stage = Path(a.stage) if a.stage else dest / ".download"
    for name, pin in pins.items():
        if a.only and name != a.only:
            continue
        folder = dest / pin["dir"]
        if all((folder / f).is_file() and sha256_file(folder / f) == h for f, h in pin["files"].items()):
            print("already there: %s" % folder)
            continue
        archive = stage / (pin["dir"] + ".tar.bz2")
        if archive.is_file() and sha256_file(archive) == pin["sha256"]:
            print("using the verified archive %s" % archive, flush=True)
        else:
            print("downloading %s (%.0f MB) ..." % (pin["url"], (pin.get("size") or 0) / 1e6), flush=True)
            download(pin["url"], archive, pin["sha256"], pin.get("size"))
        written = extract_pinned(archive, pin["dir"], pin["files"], folder)
        print("ok %s: %s" % (pin["dir"], ", ".join(written)), flush=True)
        if not a.keep_archives:
            archive.unlink(missing_ok=True)
    if stage.is_dir() and not a.keep_archives and not any(stage.iterdir()):
        shutil.rmtree(stage, ignore_errors=True)
    print("offline models ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refused as exc:
        print("REFUSED:", exc, file=sys.stderr)
        raise SystemExit(1)
