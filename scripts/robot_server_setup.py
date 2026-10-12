"""Stage the on-robot server runtime, ON the robot (run by robot_server.py install).

Runs as the adb shell user under the route controller's Python (the one
scripts/robot_python.py installed in /data/local/tmp/emma-nav): it is the only
Python on the box before this runs. Downloads every artifact named in the lock
(the robot fetches at ~5.8 MB/s, adb push from the PC manages ~0.3 MB/s --
measured 2026-10-09), refuses any byte that is not the pinned sha256, and
builds runtime/ under --stage. Root then copies the finished tree into place;
nothing here runs as root.

    stage/dl/<file>         downloads (kept, so a rerun only fetches what changed)
    stage/runtime/python    python-build-standalone (CPython 3.14, aarch64 glibc)
    stage/runtime/glibc     Debian libc/libstdc++/libgcc/zlib, loader included
    stage/runtime/site      every wheel, unpacked
    stage/runtime/py        the launcher (glibc's loader, then python)
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import lzma
import os
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

LAUNCHER = """#!/system/bin/sh
# Debian glibc + python-build-standalone on Android: start through glibc's own loader.
# Written by scripts/robot_server_setup.py (robot_server.py install).
R={runtime}
G=$R/glibc
export PYTHONHOME=$R/python
export PYTHONPATH=$R/site
export PYTHONUTF8=1
export PYTHONDONTWRITEBYTECODE=1
# Android has no /usr/share/zoneinfo: a POSIX zone, so logs carry Bangkok time like the PC's
export TZ=ICT-7
export HOME={home}
export TMPDIR={tmp}
exec $G/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1 \\
  --library-path $G/lib/aarch64-linux-gnu:$G/usr/lib/aarch64-linux-gnu:$R/python/lib \\
  $R/python/bin/python{pyver} "$@"
"""

_LIB_DIRS = ("./lib/aarch64-linux-gnu/", "./usr/lib/aarch64-linux-gnu/")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(item: dict, dl: Path) -> Path:
    import httpx

    dst = dl / item["file"]
    if dst.exists() and sha256_file(dst) == item["sha256"]:
        return dst
    part = dst.with_name(dst.name + ".part")
    h = hashlib.sha256()
    with httpx.stream("GET", item["url"], follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with open(part, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                h.update(chunk)
                f.write(chunk)
    if h.hexdigest() != item["sha256"]:
        part.unlink()
        raise SystemExit("sha256 mismatch for %s -- refusing it" % item["file"])
    part.replace(dst)
    return dst


def ar_members(data: bytes):
    if data[:8] != b"!<arch>\n":
        raise SystemExit("not an ar archive")
    i = 8
    while i + 60 <= len(data):
        name = data[i:i + 16].decode().strip().rstrip("/")
        size = int(data[i + 48:i + 58].decode().strip())
        yield name, data[i + 60:i + 60 + size]
        i += 60 + size + (size & 1)


def unpack_deb(blob: bytes, dest: Path) -> None:
    """Only the shared-library directories of a .deb's data.tar.*."""
    for name, member in ar_members(blob):
        if not name.startswith("data.tar"):
            continue
        raw = lzma.decompress(member) if name.endswith(".xz") else member
        with tarfile.open(fileobj=io.BytesIO(raw)) as t:
            for m in t.getmembers():
                if not m.name.startswith(_LIB_DIRS):
                    continue
                target = dest / m.name[2:]
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif m.issym():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.is_symlink() or target.exists():
                        target.unlink()
                    os.symlink(m.linkname, target)
                elif m.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with t.extractfile(m) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
                    os.chmod(target, 0o755)


def unpack_wheel(path: Path, site: Path) -> None:
    with zipfile.ZipFile(path) as z:
        for zi in z.infolist():
            name = zi.filename
            if name.endswith("/") or name.startswith("/") or ".." in Path(name).parts:
                continue
            target = site / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(zi) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            mode = (zi.external_attr >> 16) & 0o777
            if mode & 0o111:
                os.chmod(target, 0o755)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--runtime", required=True, help="where root will put runtime/ (for the launcher)")
    ap.add_argument("--home", required=True)
    ap.add_argument("--tmp", required=True)
    a = ap.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import robot_dns

    robot_dns.install()
    lock = json.loads(Path(a.lock).read_text(encoding="utf-8"))
    stage = Path(a.stage)
    dl = stage / "dl"
    dl.mkdir(parents=True, exist_ok=True)
    rt = stage / "runtime"
    if rt.exists():
        shutil.rmtree(rt)
    rt.mkdir(parents=True)

    items = [lock["python"]] + lock["debs"] + lock["wheels"]
    for n, item in enumerate(items, 1):
        fetch(item, dl)
        print("fetched %d/%d %s" % (n, len(items), item["file"]), flush=True)

    with tarfile.open(dl / lock["python"]["file"]) as t:    # python/...
        t.extractall(rt, filter="tar") if hasattr(tarfile, "data_filter") else t.extractall(rt)
    for item in lock["debs"]:
        unpack_deb((dl / item["file"]).read_bytes(), rt / "glibc")
    site = rt / "site"
    site.mkdir()
    for item in lock["wheels"]:
        unpack_wheel(dl / item["file"], site)
    launcher = rt / "py"
    launcher.write_text(LAUNCHER.format(runtime=a.runtime, home=a.home, tmp=a.tmp,
                                        pyver=lock["python"]["version"]), encoding="utf-8")
    os.chmod(launcher, 0o755)
    print(json.dumps({"ok": True, "artifacts": len(items), "runtime": str(rt)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
