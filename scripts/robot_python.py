"""Run the route controller on the robot itself, not on a PC over Wi-Fi.

    python scripts/robot_python.py install   # once: Python 3.13 + numpy/httpx -> the robot
    python scripts/robot_python.py deploy    # after every change to the controller or a route
    python scripts/robot_python.py check     # read-only: imports work, the board answers
    python scripts/robot_python.py run data/robot/routes/floor2_bigroom_roundtrip.json [drive_route args]
    python scripts/robot_python.py tail      # follow the run started on the robot
    python scripts/robot_python.py stop      # Ctrl-C for that run: the board's action is cancelled
    python scripts/robot_python.py nav-status  # read-only: what the robot's launcher says is running
    python scripts/robot_python.py pull      # copy the robot's run logs into data/robot/runs/

Why on the robot (8 ต.ค.): from the PC every board read crosses Wi-Fi and an
adb relay -- a lidar frame took median 105 ms / worst 195 ms, on the robot
66 / 85 ms, a pose 10 / 57 ms against 3 / 21. Run 31's first corner went wide
because the loop ticked at 0.6 s, and every LinkLost so far was the Wi-Fi leg.
The board (192.168.11.1) sits inside the robot; the controller belongs there.

Android has no glibc, so the robot gets python-build-standalone (a glibc
build) plus Debian bookworm's libc6/libgcc-s1/libstdc++6/zlib1g, started
through Debian's own loader (`ld-linux-aarch64.so.1 --library-path ...`).
Nothing is installed system-wide: everything lives in /data/local/tmp/emma-nav
and `rm -r` takes it all back. Every download is checked against a hash
pinned here -- the robot runs whatever these bytes are, next to people.

A run started here keeps going if adb or the Wi-Fi drops (nohup on the
robot); `stop` and `tail` reconnect to it. The robot never moves from
`install`, `deploy`, `check`, `tail` or `pull`.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import lzma
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".tmp" / "robot-python"
REMOTE = "/data/local/tmp/emma-nav"
BOARD = "192.168.11.1"

PYTHON = ("https://github.com/astral-sh/python-build-standalone/releases/download/20261003/"
          "cpython-3.13.16%2B20261003-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz",
          "6e9641400f8debd9b7924b27b5ff0662c372852382e291a76c450c7b67414cf6")
DEBIAN = "https://deb.debian.org/debian/"
DEBS = {   # bookworm arm64; hashes from that release's Packages.xz
    "pool/main/g/glibc/libc6_2.36-9+deb12u14_arm64.deb":
        "01f4330719fd4f65580e16ea5a0527f372fca750e8f588d26deaf09f2d3b1cf4",
    "pool/main/g/gcc-12/libgcc-s1_12.2.0-14+deb12u1_arm64.deb":
        "576926b283613db80168ddf76380a3bd877602778cf0d226caa7bfbfa71eacf3",
    "pool/main/g/gcc-12/libstdc++6_12.2.0-14+deb12u1_arm64.deb":
        "26e138c677a985775331373828a6c286c551ff397cb735d00e2383cb273d1cb2",
    # numpy's _multiarray_umath needs libz.so.1 -- found by running it, not by reading
    "pool/main/z/zlib/zlib1g_1.2.13.dfsg-1_arm64.deb":
        "52b8b8a145bbe1956bba82034f77022cbef0c3d0885c9e32d9817a7932fe1913",
}
WHEELS = {
    "numpy-2.5.1-cp313-cp313-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl":
        "1c6759f538fb912fc46de0a6b1758ccf7b57bc7c7ebebc23974fdac3de8db0cd",
    "httpx-0.28.1-py3-none-any.whl": "d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad",
    "httpcore-1.0.9-py3-none-any.whl": "2d400746a40668fc9dec9810239072b40b4484b640a8c38fd654a024c7a1bf55",
    "h11-0.16.0-py3-none-any.whl": "63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86",
    "anyio-4.15.1-py3-none-any.whl": "6152fdbbf9a77fdec97731721bebf7c4c44f7c29b424b0065826173efc7ed101",
    "idna-3.20-py3-none-any.whl": "ab7ae7122974553370f0bdb919e1a960b2cd1bc1ef0276416d896db81c14582c",
    "certifi-2026.7.22-py3-none-any.whl": "62f22742b58a1a33014a2b6b706588a8d7e2a88ae7bd1a6ebe8c992928483775",
    "typing_extensions-4.16.0-py3-none-any.whl":
        "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8",
}
# What drive_route and the robot's own launcher (emma_nav.py, design
# 2026-10-08 §2e) import, and nothing else of the app (tests check this list
# against the imports, so a new module can't silently stay on the PC).
CODE = ["app/__init__.py", "app/robot_lanes.py", "app/slamware_sdk.py",
        "scripts/drive_route.py", "scripts/analyze_run.py",
        "app/robot_contracts.py", "app/robot_places.py", "scripts/emma_nav.py",
        "app/scan_match.py"]
#: The places table the launcher reads (tracked; data/robot/ is not).
PLACES_GLOB = "data/robot-places/*.json"
#: Our lidar map, built on the PC (scripts/eval_scan_localization.py
#: --save-field; numpy-only to load -- the robot has no scipy) and matched by
#: drive_route at every checkpoint for the log only (SHADOW_FIELD). Pushed
#: when the PC has one; a robot without it just logs no shadow_fix rows.
MAPS_GLOB = "data/robot/maps/*_field.npz"

LAUNCHER = """#!/system/bin/sh
# Debian glibc + python-build-standalone on Android: start through glibc's own loader.
# Written by scripts/robot_python.py install.
R={remote}
G=$R/glibc
export PYTHONHOME=$R/python
export PYTHONPATH=$R/site
export PYTHONUTF8=1
export PYTHONDONTWRITEBYTECODE=1
# Android has no /usr/share/zoneinfo: a POSIX zone, so run logs carry Bangkok time like the PC's
export TZ=ICT-7
exec $G/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1 \\
  --library-path $G/lib/aarch64-linux-gnu:$G/usr/lib/aarch64-linux-gnu:$R/python/lib \\
  $R/python/bin/python3.13 "$@"
"""

# drive_route's defaults point at the PC's relay; on the robot it talks to the board itself,
# and there is no adb to put a forward back.
ON_ROBOT_ARGS = ["--relay", "http://%s:1448" % BOARD, "--sdk", "%s:1445" % BOARD, "--adb-serial", "",
                 # the door middles the launcher's legs remember (emma_nav.build_command): one memory
                 "--door-memory", "../nav/door_memory.json"]


class Fail(Exception):
    pass


# -- staging (pure, tested) ---------------------------------------------------

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ar_members(data: bytes):
    """The members of a .deb (a Unix ar archive)."""
    if data[:8] != b"!<arch>\n":
        raise Fail("not an ar archive")
    i = 8
    while i + 60 <= len(data):
        name = data[i:i + 16].decode().strip().rstrip("/")
        size = int(data[i + 48:i + 58].decode().strip())
        yield name, data[i + 60:i + 60 + size]
        i += 60 + size + (size & 1)


_LIB_DIRS = ("./lib/aarch64-linux-gnu/", "./usr/lib/aarch64-linux-gnu/")


def stage_libs(debs: list[bytes]) -> bytes:
    """One tar of the shared libraries in `debs`, under glibc/. Members are copied
    tar-to-tar so the libraries' symlinks never touch a Windows disk."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as dst:
        for deb in debs:
            for name, blob in ar_members(deb):
                if not name.startswith("data.tar"):
                    continue
                raw = lzma.decompress(blob) if name.endswith(".xz") else blob
                with tarfile.open(fileobj=io.BytesIO(raw)) as src:
                    for m in src.getmembers():
                        if m.isdir() or not m.name.startswith(_LIB_DIRS):
                            continue
                        m.name = "glibc/" + m.name[2:]
                        dst.addfile(m, None if (m.issym() or m.islnk()) else src.extractfile(m))
    return buf.getvalue()


def stage_site(wheels: list[bytes]) -> bytes:
    """One tar of the unpacked wheels, under site/ (a wheel is a zip of its site-packages)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as dst:
        for whl in wheels:
            with zipfile.ZipFile(io.BytesIO(whl)) as z:
                for zi in z.infolist():
                    if zi.is_dir():
                        continue
                    data = z.read(zi)
                    ti = tarfile.TarInfo("site/" + zi.filename)
                    ti.size = len(data)
                    shared = zi.filename.endswith(".so") or ".so." in zi.filename
                    ti.mode = 0o755 if shared else 0o644    # a zip's own modes vary by who built it
                    dst.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def launcher(remote: str = REMOTE) -> str:
    return LAUNCHER.format(remote=remote)


#: The su the face app's NavLauncher runs the launcher through (SU_BINARY).
SU = "/system/xbin/su"
#: Seconds `stop` gives drive_route's own cleanup before emma_nav escalates
#: (scripts/emma_nav.py STOP_WAIT_S; the app's own STOP waits 2 s).
STOP_WAIT_S = 10


def as_root(line: str) -> str:
    """`line` as root when the robot has su -- the way the face app runs the
    launcher -- else as the adb shell's own uid (review finding 2). A walk the
    app started is root's: from the shell uid the SIGINT got EPERM and `stop`
    said "none" while the robot walked, and a root-only run.pid/status.json
    read as idle. The inner line is quoted whole: its `$p` and `$(...)` are
    for the inner shell, not this one."""
    import shlex
    return "if [ -x %s ]; then %s 0 sh -c %s; else %s; fi" % (SU, SU, shlex.quote(line), line)


#: What the pre-emma_nav branches print when there is no run.pid -- an
#: answer, unlike silence (see stop_command).
NO_RUN_PID = "no-run-pid"
#: The robot printed nothing: it was not asked (stop_command).
NO_ANSWER = ("no answer from the robot: su refused the adb shell, or the launcher died "
             "before answering (`adb shell %s 0 sh -c id` to check)" % SU)


def stop_command(remote: str = REMOTE) -> str:
    """`stop` = `emma_nav.py stop --wait --hold` on the robot, as root: SIGINT
    whatever run.pid names (this run or one the face app started -- one lock
    for both; through su, so a root run can actually be signalled), and a
    launcher still in its checks; a drive_route that has not ended after
    STOP_WAIT_S is killed and the board's action cancelled (review finding
    1); with nothing on disk, a walk the app is still preparing is refused
    when it reaches the launcher (--hold, review 2 finding 8). An emma_nav
    deployed before a flag existed gets the flags it knows (each is grepped
    by its constant's name): an unknown flag is an argparse exit that stops
    nothing. Before emma_nav is deployed at all, the old kill -INT: a stop
    must not depend on a deploy having happened.

    Every branch prints an answer, so EMPTY stdout can only mean the robot
    was not asked: su exists but refused the shell uid, or emma_nav died
    before it printed (both go to stderr, which adb's shell keeps apart).
    The integrator's check of finding 2 found that read as "no run going".
    The emma_nav test is a path, not a `cd code && ...` first (review 2
    finding 19): before the first deploy there is no code/, the cd failed,
    and that silence read as "su refused" -- `run` then refused to start."""
    return as_root(
        "if [ -f %(r)s/code/scripts/emma_nav.py ]; then cd %(r)s/code && "
        "if grep -q STOP_HOLD_S scripts/emma_nav.py; then ../py scripts/emma_nav.py stop --wait %(w)d --hold; "
        "elif grep -q STOP_WAIT_S scripts/emma_nav.py; then ../py scripts/emma_nav.py stop --wait %(w)d; "
        "else ../py scripts/emma_nav.py stop; fi; "
        "elif p=$(cat %(r)s/run.pid 2>/dev/null); then "
        "kill -INT $p && echo sent SIGINT to $p || echo kill -INT $p failed; "
        "else echo %(n)s; fi" % {"r": remote, "w": STOP_WAIT_S, "n": NO_RUN_PID})


def stop_report(out: str) -> tuple[str, bool]:
    """`stop`'s answer -> (what to print, whether it stopped or nothing ran).
    A stop that did not happen is never printed as one (review findings 1, 2)."""
    out = (out or "").strip()
    if not out:
        # Silence is "could not ask", never "nothing ran" (stop_command).
        return "NOT STOPPED: %s -- press STOP on the robot's screen" % NO_ANSWER, False
    if out == NO_RUN_PID:
        return "no run going", True
    try:
        doc = json.loads(out.splitlines()[-1])
    except ValueError:
        if out.startswith("sent SIGINT to "):       # the old kill -INT line
            return out, True
        return "NOT STOPPED: unexpected stop answer: %s" % out[:200], False
    what, pids = doc.get("stop"), doc.get("pids") or []
    if what == "none":
        # Review 2 finding 8: "none" is only what is on the robot's disk. A
        # walk the face app accepted is in its memory alone while it rests the
        # arms (up to 15 s) -- "no run going" here, and the robot walked.
        hold = doc.get("hold_s")
        if isinstance(hold, (int, float)) and not isinstance(hold, bool) and hold > 0:
            return ("no run on the robot -- a walk its screen is still preparing (resting the arms) is "
                    "refused if it starts within %d s; if the robot moves anyway, press STOP on its screen"
                    % hold), True
        return ("no run on the robot's disk (run.pid, status.json) -- but a walk its screen is still "
                "preparing (resting the arms, up to ~15 s) is not on disk yet: check the robot's screen"), True
    if what == "stopped":
        return "stopped: drive_route %s ended after SIGINT (board action cancelled by it)" % pids, True
    if what == "sent":
        return "SIGINT sent to %s -- `tail` to see it end" % pids, True
    if what == "escalated":
        return ("drive_route %s did NOT end within %d s of SIGINT: killed (kill -9), board action %s"
                % (pids, STOP_WAIT_S, doc.get("board"))), doc.get("board") == "cancelled"
    if what == "denied":
        return ("NOT STOPPED: the run %s belongs to another user (root) and su is missing or refused -- "
                "press STOP on the robot's screen" % pids), False
    if what == "not_stopped":
        return "NOT STOPPED: drive_route %s ignored SIGINT and the kill was refused (%s)" % (
            pids, doc.get("kill")), False
    return "unexpected stop answer: %s" % out[:200], False


def busy_command(remote: str = REMOTE) -> str:
    """What `run` checks first. With the launcher deployed, its `status`: a
    run the face app started belongs to root (su), and the shell's own
    `kill -0` on a root pid fails with EPERM -- it read as "no run going",
    and a second run would have started under the first. The launcher's
    probe counts EPERM as alive, and also sees a launcher still in its checks.
    As root (review finding 2): a root-only status.json is otherwise "idle"."""
    # Every branch answers (as stop_command): empty stdout = the robot was not
    # asked, and `run` must not read that as "nothing going". The test is a
    # path, not a `cd code` first (review 2 finding 19): before the first
    # deploy there is no code/, and `run` refused with "su refused".
    return as_root("if [ -f %(r)s/code/scripts/emma_nav.py ]; then cd %(r)s/code && "
                   "../py scripts/emma_nav.py status; "
                   "elif p=$(cat %(r)s/run.pid 2>/dev/null) && kill -0 $p 2>/dev/null; then echo $p; "
                   "else echo %(n)s; fi" % {"r": remote, "n": NO_RUN_PID})


def run_going(out: str) -> str | None:
    """busy_command's answer -> what is going (for the message), or None."""
    out = (out or "").strip()
    if not out:
        return NO_ANSWER                            # cannot tell = do not start a second
    if out == NO_RUN_PID:
        return None
    if not out.startswith("{"):
        return out                                  # the old probe: a pid
    try:
        st = json.loads(out.splitlines()[-1])
    except ValueError:
        return "unreadable status: %s" % out[:80]   # cannot tell = do not start a second
    if st.get("state") in ("arrived", "stopped", "refused", "failed", "idle"):
        return None
    return "%s run, %s (pid %s)" % (st.get("origin"), st.get("state"), st.get("child_pid") or st.get("launcher_pid"))


def nav_status_command(remote: str = REMOTE) -> str:
    # as root: the launcher's files can be root-only (review finding 2)
    return as_root("cd %s/code && ../py scripts/emma_nav.py status" % remote)


def deploy_files(routes: list[str] | None = None, root: Path = ROOT) -> list[str]:
    """Everything a deploy pushes, relative to the repo (= to code/ on the
    robot): the code, the places table, the lidar map if built, and the routes."""
    places = sorted(str(q.relative_to(root)).replace("\\", "/") for q in root.glob(PLACES_GLOB))
    maps = sorted(str(q.relative_to(root)).replace("\\", "/") for q in root.glob(MAPS_GLOB))
    rts = routes or [str(q.relative_to(root)).replace("\\", "/")
                     for q in sorted((root / "data" / "robot" / "routes").glob("*.json"))]
    return CODE + places + maps + list(rts)


def manifest(files: dict[str, bytes], deployed_at: str) -> bytes:
    """code/MANIFEST.json: what was deployed and its sha256. `emma_nav.py
    check` compares it with the files on the robot, and the operator screen
    shows `deployed_at` -- the first walk of a leg runs reviewed code only if
    someone can see that it is the reviewed code (design §4, docking row)."""
    doc = {"deployed_at": deployed_at, "files": {f: sha256(b) for f, b in sorted(files.items())}}
    return (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8")


def run_command(route: str, extra: list[str], remote: str = REMOTE) -> str:
    """The robot-side shell line that starts a run detached from adb: a Wi-Fi
    drop must not SIGHUP the thing that is driving.

    Review finding 9: after a leg the face app started (through su), run.out
    and run.pid are root's, and the shell uid cannot write either -- the
    redirect failed in the background child, nothing started, and this still
    printed "started". So: the stale files are unlinked first (the directory
    is the shell's own and not sticky, so that is allowed whoever owns them),
    run.pid is written by rename, and the line answers STARTED only if
    drive_route is still alive a second later.

    Review finding 12: `--pid-file` makes drive_route remove run.pid when it
    ends (only while it still names it) -- the dead pid used to outlive the
    run, and a reused pid was then stopped, killed and read as a PC run.
    `$!` stays drive_route's own pid (nohup and ../py both exec), so the
    SIGINT `stop` sends reaches its KeyboardInterrupt cancel path."""
    import shlex
    args = " ".join(shlex.quote(a) for a in [route, *ON_ROBOT_ARGS, "--pid-file", "../run.pid", *extra])
    return ("cd %s/code && rm -f ../run.out ../run.pid.tmp"
            " && (nohup ../py scripts/drive_route.py %s > ../run.out 2>&1 &"
            " echo $! > ../run.pid.tmp && mv -f ../run.pid.tmp ../run.pid)"
            " && sleep 1 && p=$(cat ../run.pid) && kill -0 \"$p\" 2>/dev/null && echo STARTED \"$p\""
            " || { echo NOT STARTED; tail -n 20 ../run.out 2>/dev/null; }"
            % (remote, args))


def run_started(out: str) -> str:
    """run_command's answer -> drive_route's pid, or Fail with what run.out
    says. "started" is printed only for a run that is running."""
    for line in (out or "").splitlines():
        if line.startswith("STARTED "):
            return line.split()[1]
    raise Fail("drive_route is not running a second after the start -- run.out:\n%s" % (out or "").strip())


# -- the robot --------------------------------------------------------------------

def adb_path() -> str:
    for p in (ROOT / "tools" / "android" / "platform-tools" / "adb.exe",
              ROOT / ".tmp" / "android-toolchain" / "sdk" / "platform-tools" / "adb.exe"):
        if p.exists():
            return str(p)
    found = shutil.which("adb")
    if not found:
        raise Fail("adb not found (tools/android/platform-tools/adb.exe)")
    return found


class Robot:
    def __init__(self, serial: str):
        self.adb, self.serial = adb_path(), serial

    def _run(self, *args: str, check: bool = True, timeout: float = 600) -> str:
        r = subprocess.run([self.adb, "-s", self.serial, *args], capture_output=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        if check and r.returncode != 0:
            raise Fail("adb %s: %s" % (args[0], (r.stderr or r.stdout).strip()))
        return r.stdout

    def shell(self, cmd: str, check: bool = True, timeout: float = 600) -> str:
        return self._run("shell", cmd, check=check, timeout=timeout)

    def push(self, local: Path | str, remote: str) -> None:
        self._run("push", str(local), remote)

    def pull(self, remote: str, local: Path) -> None:
        self._run("pull", remote, str(local))

    def put(self, data: bytes, remote: str) -> None:
        """Push bytes and check they arrived whole -- the robot runs these."""
        tmp = CACHE / Path(remote).name
        tmp.write_bytes(data)
        self.push(tmp, remote)
        got = self.shell("sha256sum %s" % remote).split()[0]
        if got != sha256(data):
            raise Fail("%s arrived as %s, sent %s" % (remote, got, sha256(data)))


def fetch(url: str, want: str, name: str | None = None) -> bytes:
    """Download once into .tmp/robot-python/ and refuse anything that is not the pinned bytes."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (name or url.rsplit("/", 1)[-1].replace("%2B", "+"))
    if not path.exists() or sha256(path.read_bytes()) != want:
        print("download", url)
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
        if sha256(data) != want:
            raise Fail("%s: hash %s, pinned %s" % (url, sha256(data), want))
        path.write_bytes(data)
    return path.read_bytes()


def fetch_wheels() -> list[bytes]:
    missing = [w for w, h in WHEELS.items()
               if not (CACHE / w).exists() or sha256((CACHE / w).read_bytes()) != h]
    if missing:
        CACHE.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "pip", "download", "--no-deps", "--only-binary=:all:",
                        "--platform", "manylinux_2_28_aarch64", "--python-version", "3.13",
                        "--implementation", "cp", "-d", str(CACHE),
                        *[w.split("-")[0] + "==" + w.split("-")[1] for w in missing]], check=True)
    out = []
    for w, h in WHEELS.items():
        data = (CACHE / w).read_bytes()
        if sha256(data) != h:
            raise Fail("%s: hash %s, pinned %s" % (w, sha256(data), h))
        out.append(data)
    return out


def install(robot: Robot) -> None:
    py = fetch(*PYTHON)
    libs = stage_libs([fetch(DEBIAN + p, h) for p, h in DEBS.items()])
    site = stage_site(fetch_wheels())
    robot.shell("mkdir -p %s && cd %s && rm -rf python glibc site" % (REMOTE, REMOTE))
    for data, name in ((py, "cpython.tar.gz"), (libs, "glibc.tar"), (site, "site.tar")):
        robot.put(data, "%s/%s" % (REMOTE, name))
    robot.shell("cd %s && tar -xzf cpython.tar.gz && tar -xf glibc.tar && tar -xf site.tar"
                " && rm cpython.tar.gz glibc.tar site.tar" % REMOTE)
    robot.put(launcher().encode(), REMOTE + "/py")
    robot.shell("chmod 755 %s/py" % REMOTE)
    print(robot.shell("%s/py -c 'import sys, numpy, httpx; print(sys.version.split()[0], numpy.__version__, "
                      "httpx.__version__)'" % REMOTE).strip())


def deploy(robot: Robot, routes: list[str], root: Path = ROOT) -> None:
    files = deploy_files(routes, root)
    dirs = sorted({"%s/code/%s" % (REMOTE, f.rsplit("/", 1)[0]) for f in files})
    robot.shell("mkdir -p %s %s/code/data/robot/runs %s/nav" % (" ".join(dirs), REMOTE, REMOTE))
    blobs = {f: (root / f).read_bytes() for f in files}
    for f in files:
        robot.put(blobs[f], "%s/code/%s" % (REMOTE, f))
    # last: a deploy cut off half way leaves the old manifest, which then
    # disagrees with the files -- `check` says so instead of vouching for them
    robot.put(manifest(blobs, time.strftime("%Y-%m-%dT%H:%M:%S%z")), "%s/code/MANIFEST.json" % REMOTE)
    print("deployed %d files (+ MANIFEST.json)" % len(files))


def check(robot: Robot) -> None:
    """Read-only: the runtime starts, the controller imports, the board answers."""
    out = robot.shell(
        "cd %s/code && ../py -c \"import sys; sys.path[:0] = ['.', 'scripts']; import drive_route as d; "
        "b = d.Board('http://%s:1448'); print('battery', b.power().get('batteryPercentage'), "
        "'pose', tuple(round(v, 3) for v in b.pose()))\"" % (REMOTE, BOARD), check=False)
    print(out.strip())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=["install", "deploy", "check", "run", "tail", "stop", "nav-status", "pull"])
    ap.add_argument("rest", nargs=argparse.REMAINDER)
    ap.add_argument("--serial", default="192.168.1.197:5555")
    a = ap.parse_args()
    robot = Robot(a.serial)
    try:
        if a.what == "install":
            install(robot)
        elif a.what == "deploy":
            deploy(robot, a.rest)
        elif a.what == "check":
            check(robot)
        elif a.what == "run":
            if not a.rest:
                raise Fail("run needs a route, e.g. data/robot/routes/floor2_bigroom_roundtrip.json")
            busy = run_going(robot.shell(busy_command(), check=False))
            if busy:
                raise Fail("a run is still going on the robot (pid %s) -- `stop` it first" % busy)
            deploy(robot, [a.rest[0]])
            pid = run_started(robot.shell(run_command(a.rest[0], a.rest[1:]), check=False))
            print("started on the robot (pid %s); `python scripts/robot_python.py tail` to follow, "
                  "`stop` to cancel" % pid)
        elif a.what == "tail":
            subprocess.run([robot.adb, "-s", robot.serial, "shell", "tail -n +1 -f %s/run.out" % REMOTE])
        elif a.what == "stop":
            # SIGINT = drive_route's KeyboardInterrupt path: cancel the board's action, log, summarise
            msg, ok = stop_report(robot.shell(stop_command(), check=False))
            print(msg)
            if not ok:
                return 1
        elif a.what == "nav-status":
            print(robot.shell(nav_status_command(), check=False).strip())
        elif a.what == "pull":
            dst = ROOT / "data" / "robot" / "runs"
            dst.mkdir(parents=True, exist_ok=True)
            robot.pull("%s/code/data/robot/runs/." % REMOTE, dst)
            print("pulled into", dst)
        return 0
    except Fail as e:
        print("FAIL:", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
