"""Serve the current room-agent code so a deployed agent can self-update.

Read-only and LAN-bound: it hands out a zip of `app/` + `run_room_agent.py` --
the agent's CODE, which holds no secret (no `.env`, no tokens, no `data/`). A
room agent whose `.env` has ROOM_AGENT_UPDATE_URL pointing here pulls this when
the operator clicks "อัปเดต" in the console, then restarts itself with the new
code. No more rebuild-and-reinstall per PC for a code change.

Run it on the management PC alongside the console:

    python scripts/agent_update_server.py

It binds 0.0.0.0 by default because the room PCs must reach it (the console
stays on 127.0.0.1). It serves CODE only -- nothing writable, nothing sensitive
-- but it is still an endpoint that hands out runnable code, so keep it on the
trusted LAN. The safety that matters is on the AGENT side: an agent only pulls
from the ONE url baked into its own .env, so this server cannot push code at a
machine that was not told to trust it.
"""

from __future__ import annotations

import hashlib
import io
import os
import zipfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

ROOT = Path(__file__).resolve().parent.parent
HOST = os.getenv("AGENT_UPDATE_HOST", "0.0.0.0").strip() or "0.0.0.0"
PORT = int(os.getenv("AGENT_UPDATE_PORT", "8799"))

app = FastAPI(title="Emma agent update", docs_url=None, redoc_url=None)


def build_bundle() -> bytes:
    """A DETERMINISTIC zip of the agent's code: run_room_agent.py + the app/ tree
    (no pyc, no secrets). Matches what make_room_package ships, so a self-update
    lands the same files an install would.

    Deterministic on purpose: the same code tree must always produce the same
    bytes, so its sha256 is stable. The console reads that hash over loopback and
    the room agent checks the bytes it pulls over the LAN against it -- a rogue
    host answering this server's IP cannot match a hash of code it does not have.
    A normal zip embeds each file's mtime and would defeat that; here every entry
    gets a fixed timestamp and the files are written in a fixed order."""
    files = [("run_room_agent.py", ROOT / "run_room_agent.py")]
    for path in (ROOT / "app").rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if "__pycache__" in rel or rel.endswith(".pyc"):
            continue
        files.append((rel, path))
    files.sort(key=lambda t: t[0])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, path in files:
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    return buf.getvalue()


def bundle_sha256() -> str:
    return hashlib.sha256(build_bundle()).hexdigest()


@app.get("/agent-bundle")
async def agent_bundle() -> Response:
    return Response(content=build_bundle(), media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename=agent.zip",
                             "Cache-Control": "no-store"})


@app.get("/agent-bundle.sha256")
async def agent_bundle_sha256() -> Response:
    """The hash of exactly what /agent-bundle would serve. The console reads this
    over loopback and passes it to the agent so a substituted LAN bundle fails."""
    return Response(content=bundle_sha256(), media_type="text/plain",
                    headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "serves": "room-agent code bundle (read-only)"})


def main() -> None:
    import uvicorn

    print(f"Agent update server on http://{HOST}:{PORT}/agent-bundle  (serves app/ code, read-only)")
    if HOST not in {"127.0.0.1", "localhost"}:
        print("  bound to the LAN so room PCs can pull -- it serves code, keep it on the trusted network.")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
