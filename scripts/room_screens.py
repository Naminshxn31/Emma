"""Set up the room screens the robot opens itself (app/room_screens.py).

    python scripts/room_screens.py list
    python scripts/room_screens.py add "ห้องขาย 2" http://192.168.1.52:8777
    python scripts/room_screens.py check "ห้องขาย 2"
    python scripts/room_screens.py here "ห้องขาย 2" [--radius 2.0]
    python scripts/room_screens.py forget "ห้องขาย 2"
    python scripts/room_screens.py display "ห้องขาย 2" DISPLAY2   (which of its screens; `check` lists them)

`add` writes data/room-screens.json (gitignored) with a fresh key and prints
the three lines that PC's agent .env needs. `here` stores where the robot is
standing right now as that screen's position -- drive the robot to the spot
in front of the screen first; it reads the robot through `robot_server.py
forward` (https://127.0.0.1:18001), so that has to be running. Then
`robot_server.py secrets` + `restart` puts the table on the robot.

The pose is the chassis board's: only as right as the map it is localized
on. Record positions on the map the robot will use there.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "data" / "room-screens.json"
FORWARD = "https://127.0.0.1:18001"
ACTIONS = ("emma_screen_open", "emma_screen_close")


def load() -> dict:
    if not TABLE.is_file():
        return {"screens": []}
    data = json.loads(TABLE.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("screens"), list):
        sys.exit("%s must be {\"screens\": [...]}" % TABLE)
    return data


def save(data: dict) -> None:
    TABLE.parent.mkdir(parents=True, exist_ok=True)
    TABLE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find(data: dict, name: str) -> dict:
    for entry in data["screens"]:
        if str(entry.get("name", "")).casefold() == name.strip().casefold():
            return entry
    sys.exit("no screen named %r -- have: %s" % (name, ", ".join(e.get("name", "?") for e in data["screens"]) or "none"))


def _env_value(key: str) -> str:
    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines() if env.is_file() else []:
        m = re.match(r"\s*%s\s*=\s*(.*)$" % re.escape(key), line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


def cmd_list(data: dict) -> None:
    for e in data["screens"]:
        near = e.get("near")
        where = ("x=%.2f y=%.2f r=%.1f m" % (near["x"], near["y"], near.get("radius", 2.0))
                 if isinstance(near, dict) else "no position (opens only on request)")
        print("%-20s %-32s %s" % (e.get("name"), e.get("agent"), where))
    if not data["screens"]:
        print("no screens yet -- `add`")


def cmd_add(data: dict, name: str, agent: str) -> None:
    if not re.match(r"^https?://[^/]+$", agent.rstrip("/")):
        sys.exit("agent must look like http://192.168.1.52:8777")
    if any(str(e.get("name", "")).casefold() == name.casefold() for e in data["screens"]):
        sys.exit("%r is already there (forget it first)" % name)
    key = secrets.token_urlsafe(24)
    data["screens"].append({"name": name, "agent": agent.rstrip("/"), "token": key})
    save(data)
    port = agent.rstrip("/").rsplit(":", 1)[-1] if agent.count(":") == 2 else "8777"
    print("added %r. On THAT PC, the room agent's .env needs:" % name)
    print("  ROOM_AGENT_TOKEN=%s" % key)
    print("  ROOM_AGENT_ALLOW=%s" % ",".join(ACTIONS))
    print("  ROOM_AGENT_HOST=0.0.0.0")
    print("  ROOM_AGENT_PORT=%s" % port)
    print("(and the Windows firewall must let the robot reach that port)")


def cmd_check(data: dict, name: str) -> None:
    e = find(data, name)
    try:
        with urllib.request.urlopen(e["agent"] + "/health", timeout=6) as r:
            health = json.load(r)
    except Exception as exc:
        sys.exit("%s did not answer from this PC: %s" % (e["agent"], exc))
    enabled = set(health.get("enabled") or [])
    print("agent %r armed=%s, Emma screen: %s" % (
        health.get("room"), health.get("armed"),
        "ready" if set(ACTIONS) <= enabled else "NOT enabled (ROOM_AGENT_ALLOW)"))
    for d in health.get("displays") or []:
        mm = d.get("mm") or [0, 0]
        size = ("%.0f-inch" % ((mm[0] ** 2 + mm[1] ** 2) ** 0.5 / 25.4)) if mm[0] else "size not reported"
        print("  %-9s %s %dx%d at (%d,%d), %s%s" % (
            d.get("device"), "primary  " if d.get("primary") else "secondary", d.get("width", 0), d.get("height", 0),
            d.get("x", 0), d.get("y", 0), size, "   <- Emma goes here" if d.get("device") == e.get("display")
            or (not e.get("display") and d.get("primary")) else ""))


def cmd_here(data: dict, name: str, radius: float) -> None:
    e = find(data, name)
    token = _env_value("WS_TOKEN")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE       # the forward's own LAN cert, on loopback
    url = FORWARD + "/robot/state?token=" + urllib.parse.quote(token)
    try:
        with urllib.request.urlopen(url, timeout=8, context=ctx) as r:
            answer = json.load(r)
    except Exception as exc:
        sys.exit("could not read the robot at %s (is `robot_server.py forward` running?): %s" % (FORWARD, exc))
    state = answer.get("state") if answer.get("ok") else None
    pose = (state or {}).get("pose") or {}
    if not isinstance(pose.get("x"), (int, float)) or not isinstance(pose.get("y"), (int, float)):
        sys.exit("the robot gave no position: %s" % (answer.get("error") or answer))
    e["near"] = {"x": round(pose["x"], 3), "y": round(pose["y"], 3), "radius": radius}
    save(data)
    print("%r is at x=%.2f y=%.2f (radius %.1f m, localization quality %s)" % (
        name, pose["x"], pose["y"], radius, (state or {}).get("localization_quality")))
    print("now: python scripts/robot_server.py secrets  (no restart needed for a position change)")


def cmd_display(data: dict, name: str, display: str) -> None:
    e = find(data, name)
    if display.lower() in ("", "primary", "none"):
        e.pop("display", None)
    else:
        e["display"] = display
    save(data)
    print("%r shows Emma on %s -- now: python scripts/robot_server.py secrets" % (name, e.get("display", "its primary display")))


def cmd_forget(data: dict, name: str) -> None:
    e = find(data, name)
    data["screens"].remove(e)
    save(data)
    print("forgot %r" % name)


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="what", required=True)
    sub.add_parser("list")
    a = sub.add_parser("add"); a.add_argument("name"); a.add_argument("agent")
    c = sub.add_parser("check"); c.add_argument("name")
    h = sub.add_parser("here"); h.add_argument("name"); h.add_argument("--radius", type=float, default=2.0)
    f = sub.add_parser("forget"); f.add_argument("name")
    d = sub.add_parser("display"); d.add_argument("name"); d.add_argument("display")
    args = ap.parse_args(argv)
    data = load()
    if args.what == "list":
        cmd_list(data)
    elif args.what == "add":
        cmd_add(data, args.name, args.agent)
    elif args.what == "check":
        cmd_check(data, args.name)
    elif args.what == "here":
        cmd_here(data, args.name, args.radius)
    elif args.what == "display":
        cmd_display(data, args.name, args.display)
    else:
        cmd_forget(data, args.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
