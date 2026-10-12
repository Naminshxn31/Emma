"""The code bundle the self-update pulls: it must be the agent's CODE and only
that -- never a secret. The whole self-update trust story rests on this bundle
carrying nothing sensitive, so it is the thing worth pinning.
"""

import importlib.util
import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("agent_update_server", ROOT / "scripts" / "agent_update_server.py")
aus = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aus)


def test_bundle_carries_the_agent_code():
    names = zipfile.ZipFile(io.BytesIO(aus.build_bundle())).namelist()
    assert "run_room_agent.py" in names
    assert "app/room_agent.py" in names


def test_bundle_never_carries_a_secret_or_build_junk():
    names = zipfile.ZipFile(io.BytesIO(aus.build_bundle())).namelist()
    # no .env (the repo one holds the Gemini/Supabase keys), no data/, no pyc
    assert not any(n.endswith(".env") or "/.env" in n or n == ".env" for n in names), names
    assert not any(n.startswith("data/") for n in names), names
    assert not any(n.endswith(".pyc") or "__pycache__" in n for n in names), names


def test_bundle_is_deterministic_so_its_hash_is_stable():
    """The integrity check depends on it: the console reads the hash over
    loopback and the agent checks the LAN bytes against it, which only works if
    the same code tree always produces byte-identical output (a plain zip embeds
    mtimes and would not)."""
    assert aus.build_bundle() == aus.build_bundle()
    import hashlib
    assert aus.bundle_sha256() == hashlib.sha256(aus.build_bundle()).hexdigest()


def test_the_sha_endpoint_matches_the_bundle_it_serves():
    from fastapi.testclient import TestClient
    client = TestClient(aus.app)
    served = client.get("/agent-bundle").content
    stated = client.get("/agent-bundle.sha256").text.strip()
    import hashlib
    assert stated == hashlib.sha256(served).hexdigest()
