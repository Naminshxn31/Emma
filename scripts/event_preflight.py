#!/usr/bin/env python3
"""Go / no-go for the event day (25–26 Nov 2026, floor 1) -- read-only, every morning.

    python scripts/event_preflight.py                              # this PC's files
    python scripts/event_preflight.py --serial 192.168.79.11:5555  # + the robot: spots it recorded, deploy/secrets drift

Why (audit 19 Nov): every check below already existed, each behind its own command, and the
state they would have shown that night was "nothing is spoken on stage and the screen gets no
film" -- all 29 ceremony cues CHANGED since their approval, no recording marked listened, the
slides file a draft -- without anything failing loudly: a cue that is not approved is simply not
servable, a draft slides file simply sends no slide. One page that says GO or NO-GO.

It reads and prints; it signs, deploys, moves and writes nothing. Exit 0 = GO, 1 = NO-GO,
2 = a file is missing (nothing could be checked -- which is not a GO).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: where the robot's server keeps what it writes itself (scripts/robot_server.py layout)
ROBOT_CODE = "/data/local/emma-server/code"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []      # (verdict, what, detail)

    def add(self, ok: bool | None, what: str, detail: str = "") -> None:
        self.rows.append(("OK " if ok else ("-- " if ok is None else "NO "), what, detail))

    @property
    def go(self) -> bool:
        return not any(v == "NO " for v, _, _ in self.rows)


def check_ceremony(rep: Report, path: Path) -> None:
    from app import event_script

    doc = event_script.load(path)
    if doc is None:
        rep.add(False, "ceremony script", "%s is not a ceremony script" % path)
        return
    aes = _module("approve_event_script")
    aes._SPEAK["lang"] = event_script.speak(doc)
    lines = [c for c in event_script.cues(doc) if c.get("th")]
    not_ready, not_heard, no_voice = [], [], []
    for c in lines:
        if not event_script.approved(c) or event_script._problems_in(c, aes._SPEAK["lang"]):
            not_ready.append(c["id"])
        voice = aes._voice_state(c)
        if voice == "recording NOT listened":
            not_heard.append(c["id"])
        elif voice == "no recording":
            no_voice.append(c["id"])
    rep.add(not not_ready, "ceremony cues approved, nothing breaking (%d/%d)" % (len(lines) - len(not_ready), len(lines)),
            ("not servable -- NOT SPOKEN on stage: %s · owner: `python scripts/approve_event_script.py --status`, "
             "then `--by <name>`" % ", ".join(not_ready)) if not_ready else "")
    rep.add(not not_heard, "recorded voices listened (%d with a recording)" % (len(lines) - len(no_voice)),
            ("played as Gemini's reading instead of the recording: %s · owner listens "
             "(data/ceremony/voice/listen_en.html), then `approve_event_script.py --by <name> --listened`"
             % ", ".join(not_heard)) if not_heard else "")
    if no_voice:
        rep.add(None, "cues with no recording (Gemini reads them)", ", ".join(no_voice))
    try:
        probs = event_script.order_problems(doc)
    except Exception as exc:                       # noqa: BLE001 -- a check that cannot run is reported
        probs = ["order check failed: %s" % exc]
    rep.add(not probs, "ceremony running order", "; ".join(probs)[:400])


def check_slides(rep: Report, path: Path) -> None:
    from app import ceremony_slides

    sp = ceremony_slides.path()
    doc = ceremony_slides.load(sp) if sp is not None and sp.is_file() else None
    if doc is None:
        rep.add(False, "ceremony slides file", "missing: %s" % sp)
        return
    broken = [r["id"] for r in ceremony_slides.status(doc) if r["problems"]]
    rep.add(not broken, "slides: every cue's slide passes its checks", ", ".join(broken))
    rep.add(ceremony_slides.approved(doc), "slides file signed (a draft sends NO film to the screen)",
            "" if ceremony_slides.approved(doc) else ("CHANGED since approval" if doc.get("approved_by") else "draft")
            + " · owner, after the last edit: `python scripts/approve_ceremony_slides.py --by <name>`, then deploy")


def _reachable(serial: str, adb: str) -> bool:
    """The robot answers over adb. 19 Nov, the first run of this script: the robot was off,
    and `robot_server.py deploy --dry-run` against it read "1896 to push, robot has 0 keys" --
    an empty robot, not an unreachable one. Nothing about the robot is believed without this."""
    try:
        subprocess.run([adb, "connect", serial], capture_output=True, timeout=15)
        out = subprocess.run([adb, "-s", serial, "shell", "echo emma-ok"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=15).stdout
        return "emma-ok" in out
    except (OSError, subprocess.SubprocessError):
        return False


def _robot_spots(serial: str, adb: str) -> dict | None:
    """The spots the staff recorded on the robot ({} = none recorded yet); None = could not read."""
    name = "%s/data/events/floor1_event.spots.json" % ROBOT_CODE
    try:
        out = subprocess.run([adb, "-s", serial, "shell", "su 0 sh -c 'cat %s 2>/dev/null || echo NOFILE'" % name],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if out.strip() == "NOFILE":
        return {}
    try:
        doc = json.loads(out)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def _spot_names(event: dict, ceremony: dict | None) -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    for s in event.get("scenes") or []:
        for name in [s.get("spot")] + list(s.get("stops") or []) + list((s.get("roam") or {}).get("stops") or []):
            if isinstance(name, str):
                used.setdefault(name, []).append("scene " + s.get("id", "?"))
    for part in (ceremony or {}).get("order") or []:
        for st in part.get("steps") or [part]:
            if st.get("do") == "walk" and isinstance(st.get("spot"), str):
                used.setdefault(st["spot"], []).append("ceremony part %s" % part.get("n", "?"))
    return used


def check_spots(rep: Report, event_path: Path, ceremony_path: Path | None, robot: dict | None) -> None:
    event = json.loads(event_path.read_text(encoding="utf-8"))
    ceremony = json.loads(ceremony_path.read_text(encoding="utf-8")) if ceremony_path and ceremony_path.is_file() else None
    spots = event.get("spots") or {}
    measured = (robot or {}).get("spots", robot or {}) if isinstance(robot, dict) else {}
    missing = []
    for name, where in sorted(_spot_names(event, ceremony).items()):
        spot = spots.get(name) or {}
        has = bool(spot.get("dock") or spot.get("pose") or spot.get("steps")
                   or (isinstance(measured.get(name), dict) and measured[name].get("pose")))
        if not has:
            missing.append("%s (%s)" % (name, ", ".join(sorted(set(where)))))
    seen = "this PC's event file" + (" + the robot's recorded spots" if robot is not None else
                                     " only -- pass --serial to read the spots recorded on the robot")
    rep.add(not missing, "every spot a scene or the ceremony walks to has a position", ("no position: " + "; ".join(missing)
            + " · record them on floor 1 (บันทึกตำแหน่ง), walk each twice, `floor_sim.py preflight --spots`") if missing else seen)


def check_recorded(rep: Report, event_path: Path) -> None:
    event = json.loads(event_path.read_text(encoding="utf-8"))
    bad = []
    for s in event.get("scenes") or []:
        rec = s.get("recorded")
        if not isinstance(rec, dict):
            continue
        f = ROOT / str(rec.get("file", ""))
        try:
            sha = hashlib.sha256(f.read_bytes()).hexdigest()
        except OSError:
            sha = None
        if sha != rec.get("sha256"):
            bad.append("%s (%s)" % (s.get("id"), "missing" if sha is None else "changed"))
    rep.add(not bad, "recorded scene lines match their pins", ", ".join(bad))
    if any(isinstance(s.get("recorded"), dict) for s in event.get("scenes") or []):
        # CLAUDE.md, 10 Nov: the takes pinned so far were made on ElevenLabs' free plan
        rep.add(None, "recorded scene lines: licence", "the pinned takes were made on the free plan = no commercial "
                "use; re-record on a paid plan (`scripts/voice_line.py`), pin, deploy -- or drop `recorded`")


def _robot_env() -> dict:
    """The robot's .env as `robot_server.py secrets` composes it: this PC's .env minus what never leaves
    it, robot.env on top (check_drift says whether the robot has it yet)."""
    rs = _module("robot_server")
    pc = ROOT / ".env"
    text = pc.read_text(encoding="utf-8") if pc.is_file() else ""
    return dict(rs.parse_env(rs.compose_env(text, rs.OVERRIDES.read_text(encoding="utf-8"))))


def _robot_reaches(serial: str, adb: str, host: str) -> tuple[bool | None, str]:
    """From the robot's own Wi-Fi: two pings of `host` (the hub, the controller), and which subnet the
    robot is on. None = could not ask. A ping answered is not a command taken (the hub's own auth is the
    robot's morning card) -- but no answer from the robot is the subnet problem, found before the day."""
    cmd = ("ping -c 2 -W 2 %s >/dev/null 2>&1 && echo REACH-UP || echo REACH-DOWN; "
           "ip -4 -o addr show wlan0 2>/dev/null" % host)
    try:
        out = subprocess.run([adb, "-s", serial, "shell", cmd], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None, "could not ask the robot"
    mine = re.search(r"inet (\d+\.\d+\.\d+)\.\d+", out)
    net = ("robot on %s.x, " % mine.group(1)) if mine else ""
    theirs = ".".join(host.split(".")[:3]) if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host) else None
    if "REACH-UP" in out:
        return True, "%s%s answers" % (net, host)
    if "REACH-DOWN" not in out:
        return None, "could not ask the robot"
    other = (" -- ANOTHER SUBNET (%s.x): put it on the robot's Wi-Fi (Broadlink is 2.4 GHz only)" % theirs
             if mine and theirs and theirs != mine.group(1) else "")
    return False, "%s%s does not answer%s" % (net, host, other)


def check_venue(rep: Report, serial: str = "", adb: str = "", robot_up: bool = False) -> None:
    """The venue's lights and curtains (app/venue_devices.py), as the ROBOT will be set up: its composed
    .env says whether it drives them; the devices file and the learned codes are this PC's (deploy sends
    them). With the robot up, it pings each hub / controller from the robot's own Wi-Fi."""
    from app import venue_devices

    try:
        cfg = venue_devices.Config.from_env(_robot_env())
    except (OSError, ValueError) as exc:
        rep.add(False, "venue lights/curtains: the robot's settings", "could not compose: %s" % exc)
        return
    if not cfg.enabled:
        rep.add(None, "venue lights/curtains", "VENUE_DEVICES off on the robot: staff do the lights and the "
                "curtain (the ceremony's manual ticks, as before)")
        return
    for ok, th, detail, _dev in venue_devices.review(cfg):
        rep.add(ok, "venue: " + th, detail)
    # the ceremony order's device steps against the ROBOT's devices file (event_script.order_problems
    # checks them only where this PC drives devices itself -- review 20 Nov)
    from app import event_script

    bad = []
    for sc in event_script.order(event_script.load()) or []:
        for st in sc["steps"]:
            if st["do"] == "device":
                problem = venue_devices.step_problem(st.get("device"), st.get("action"), cfg)
                if problem:
                    bad.append("scene %s: %s" % (sc["n"], problem))
    rep.add(not bad, "venue: the ceremony's device steps name devices the robot has", "; ".join(bad))
    hosts = venue_devices.targets(cfg)
    if not hosts:
        return
    if not robot_up:
        rep.add(None, "venue: the robot reaches the hub / controller",
                "not checked -- pass --serial (or read the phone's morning card, which asks from the robot)")
        return
    for adapter, host in hosts:
        ok, detail = _robot_reaches(serial, adb, host)
        rep.add(ok, "venue: the robot reaches the %s at %s" % (adapter, host), detail)


def check_drift(rep: Report, serial: str) -> None:
    py = sys.executable
    for what, args, good in (("deploy", ["deploy", "--dry-run"], re.compile(r"\b0 to push\b.*\b0 to delete\b")),
                             ("secrets", ["secrets", "--dry-run"], re.compile(r"added: -; removed: -; changed: -"))):
        try:
            out = subprocess.run([py, str(ROOT / "scripts" / "robot_server.py")] + args + ["--serial", serial],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            rep.add(False, "robot %s matches this PC" % what, "could not ask: %s" % exc)
            continue
        line = next((x for x in out.splitlines() if "to push" in x or "secrets --dry-run: .env" in x), out.strip()[-160:])
        rep.add(bool(good.search(out)), "robot %s matches this PC" % what, line.strip()[:200])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--serial", default="", help="the robot's adb serial: also read its recorded spots and drift")
    ap.add_argument("--adb", default=str(ROOT / "tools" / "android" / "platform-tools" / "adb.exe"))
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    from app.config import settings

    ceremony = ROOT / (settings.ceremony_file or "data/ceremony/floor1_ceremony.json")
    event = ROOT / (settings.event_file or "data/events/floor1_event.json")
    if not ceremony.is_file() or not event.is_file():
        print("missing: %s" % ", ".join(str(p) for p in (ceremony, event) if not p.is_file()))
        return 2
    rep = Report()
    check_ceremony(rep, ceremony)
    check_slides(rep, ceremony)
    robot, up = None, False
    if args.serial:
        up = _reachable(args.serial, args.adb)
        rep.add(up, "robot reachable over adb", args.serial + ("" if up else
                " does not answer -- off, or on another network (find it: `adb mdns services`); robot checks skipped"))
        if up:
            robot = _robot_spots(args.serial, args.adb)
            if robot is None:
                rep.add(False, "robot's recorded spots readable", "could not read them")
    check_spots(rep, event, ceremony, robot)
    check_recorded(rep, event)
    check_venue(rep, args.serial, args.adb, robot_up=up)
    if robot is not None:
        check_drift(rep, args.serial)
    for verdict, what, detail in rep.rows:
        print("%s %s%s" % (verdict, what, ("\n      " + detail) if detail else ""))
    print("\n%s" % ("GO" if rep.go else "NO-GO"))
    return 0 if rep.go else 1


if __name__ == "__main__":
    raise SystemExit(main())
