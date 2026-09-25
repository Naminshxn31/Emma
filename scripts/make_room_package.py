"""Build the folder to copy onto one sales-room PC.

    python scripts/make_room_package.py "ห้องขาย 1" --out D:\\room1

Why this is a script and not a paragraph in a document: the room PC needs a
precise and non-obvious subset of this repo, and the two ways of getting it
wrong both look fine at the time.

**Too little.** `app/tools/__init__.py` imports `app.config`, which resolves
the slide directory through `data/registry/source_registry.json` at IMPORT
time, and that reaches `data/projects/<id>/project.json`. The room agent
imports `app.tools` LATE, so a machine missing those two files starts
perfectly, answers /health, runs list_windows and screenshot, and fails the
first time somebody says "open the browser" — in front of a customer.
Measured on a bare copy, 2026-09-25; the agent now says so at boot too.

**Too much.** The obvious way to get a working folder is to copy the repo, and
the repo has `.env` in it: the Gemini key and the Supabase service-role key,
on a machine in a room the public walks through. This script copies a fixed
list and writes a fresh `.env` containing only the room's own settings, so the
real one cannot arrive by accident.

`PROJECT_NAME` is in that written `.env` because `Settings.__post_init__`
refuses to load when it does not match `PROJECT_ID` — it is the project's
display name, not a secret.
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Everything a room PC needs, and nothing else. Anything absent here is
#: absent on purpose; add to this list only with a reason.
FILES = ("run_room_agent.py",)
TREES = ("app",)
DATA = ("data/registry/source_registry.json",)

START_CMD = """@echo off
REM Start Emma's room agent on this PC. Leave this window open.
cd /d "%~dp0"
python run_room_agent.py
pause
"""


def project_identity() -> tuple[str, str, Path]:
    registry = json.loads((ROOT / "data" / "registry" / "source_registry.json").read_text(encoding="utf-8"))
    for source in registry["sources"]:
        if source.get("id") == "project_identity":
            path = ROOT / source["location"]
            identity = json.loads(path.read_text(encoding="utf-8"))
            return identity["project_id"], identity["display_name"], path
    raise SystemExit("no project_identity in the source registry")


def build(room: str, out: Path, host: str, port: int, allow: str,
          programs: str, token: str | None) -> dict:
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty — pick an empty folder or delete it first")
    out.mkdir(parents=True, exist_ok=True)

    for tree in TREES:
        shutil.copytree(ROOT / tree, out / tree,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in FILES:
        shutil.copy(ROOT / name, out / name)
    for rel in DATA:
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, out / rel)

    project_id, display_name, identity_path = project_identity()
    rel_identity = identity_path.relative_to(ROOT)
    (out / rel_identity).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(identity_path, out / rel_identity)

    token = token or secrets.token_urlsafe(24)
    (out / ".env").write_text(
        "# This machine's room agent. No API key belongs in this file.\n"
        f"PROJECT_ID={project_id}\n"
        f"PROJECT_NAME={display_name}\n"
        f"ROOM_AGENT_NAME={room}\n"
        f"ROOM_AGENT_TOKEN={token}\n"
        f"ROOM_AGENT_HOST={host}\n"
        f"ROOM_AGENT_PORT={port}\n"
        f"ROOM_AGENT_ALLOW={allow}\n"
        f"ROOM_AGENT_PROGRAMS={programs}\n"
        "# Keyboard and mouse stay off unless this says true, even when ALLOW=all.\n"
        "ROOM_AGENT_INPUT=false\n",
        encoding="utf-8")
    (out / "start-room-agent.cmd").write_text(START_CMD, encoding="utf-8")

    assert not (out / ".env.example").exists()
    leaked = [p for p in out.rglob("*") if p.is_file() and p.name == ".env" and p.parent != out]
    assert not leaked, leaked
    return {"room": room, "token": token, "port": port, "out": str(out)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("room", help='the room name, exactly as the server will say it')
    parser.add_argument("--out", required=True, type=Path, help="an empty folder to build into")
    parser.add_argument("--host", default="0.0.0.0",
                        help="0.0.0.0 so the server can reach it (default); 127.0.0.1 to test alone")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--allow", default="all",
                        help='capabilities this room turns on; "all" is the whole catalogue')
    parser.add_argument("--programs", default="",
                        help='programs this room may open; "all" lets the Start Menu decide')
    parser.add_argument("--token", default=None, help="reuse an existing token instead of a new one")
    args = parser.parse_args(argv)

    built = build(args.room, args.out, args.host, args.port, args.allow, args.programs, args.token)

    print(f"built {built['out']}")
    print("\n1. copy that folder to the room PC")
    print("2. on the room PC:  pip install fastapi uvicorn python-dotenv")
    print("3. open the port once, as administrator:")
    print(f'   netsh advfirewall firewall add rule name="Emma room agent" '
          f'dir=in action=allow protocol=TCP localport={built["port"]}')
    print("4. run start-room-agent.cmd and leave it open")
    print("\n5. on THIS machine add the room to data/rooms.json:")
    print(json.dumps({built["room"]: {"url": f"http://<the room PC's IP>:{built['port']}",
                                      "token": built["token"]}}, ensure_ascii=False, indent=1))
    print(f'\n6. check it:  python scripts/room_control.py health "{built["room"]}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
