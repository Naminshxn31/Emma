"""What the room-PC installer must and must not do.

The package is the thing that lands on a machine the public walks past, so the
two failures worth a test are the two that are invisible until too late:

  A secret rides along. The repo has a .env with the Gemini key and the
  Supabase service-role key; the package must carry a fresh .env with only the
  room's own settings and never the real one.

  The agent gets installed to be concealed. "Runs in the background" is a
  legitimate request and this installer honours it -- no console window,
  auto-start -- but background must not shade into hidden. A service that can
  drive a PC has to stay findable and stoppable by whoever owns that PC: a
  fixed standard location, a NAMED scheduled task, and an uninstall beside it.
  That is the line between an IT agent and a backdoor, and it is a property of
  the generated files, so it is tested on the generated files.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "make_room_package", ROOT / "scripts" / "make_room_package.py")
mrp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrp)


@pytest.fixture
def package(tmp_path):
    out = tmp_path / "room1"
    # bundle_python=False so the suite never reaches out to python.org for 25 MB;
    # the bundling path has its own tests that seed a fake cache.
    mrp.build("ห้องขาย 1", out, host="0.0.0.0", port=8777,
              allow="all", programs="all", token="test-token-xyz", bundle_python=False)
    return out


def test_the_package_carries_only_the_rooms_own_env(package):
    env = (package / ".env").read_text(encoding="utf-8")
    assert "ROOM_AGENT_NAME=ห้องขาย 1" in env
    assert "ROOM_AGENT_TOKEN=test-token-xyz" in env
    # None of the real machine's secrets. These are the keys in the repo .env.
    for secret in ("GEMINI_API_KEY", "OPENAI_API_KEY", "SUPABASE", "WS_TOKEN",
                   "INVENTORY_SUPABASE"):
        assert secret not in env, secret
    # And no copy of the real .env rode along in a subfolder.
    strays = [p for p in package.rglob(".env") if p != package / ".env"]
    assert not strays, strays


def test_input_control_stays_off_even_with_everything_allowed(package):
    """ALLOW=all opens the catalogue; keyboard/mouse is still its own switch,
    and the packaged default must be the safe one."""
    env = (package / ".env").read_text(encoding="utf-8")
    assert "ROOM_AGENT_INPUT=false" in env


def test_the_cmd_files_are_plain_ascii_crlf_without_a_bom(package):
    """A UTF-8 BOM breaks batch on the first line, and a Thai console message
    renders as boxes; both read as 'the installer is broken'."""
    for name in ("install.cmd", "uninstall.cmd", "start-room-agent.cmd"):
        raw = (package / name).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{name} has a BOM"
        raw.decode("ascii")                         # non-ASCII would raise
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), name


def test_the_installer_is_findable_and_reversible_not_hidden(package):
    """Background, not concealed. Encodes the line the owner's 'ที่ลับๆ'
    request was declined on, so a later edit toward stealth fails here."""
    install = (package / "install.cmd").read_text(encoding="ascii")
    uninstall = (package / "uninstall.cmd").read_text(encoding="ascii")

    # A standard, fixed location and a NAMED task -- both discoverable.
    assert r"C:\ProgramData\EmmaRoomAgent" in install
    assert "EmmaRoomAgent" in install
    assert "Task Scheduler" in install               # it tells the installer where to look

    # It must NOT hide the files or masquerade the process.
    lowered = install.lower()
    assert "attrib +h" not in lowered and "attrib +s" not in lowered
    assert "+h +s" not in lowered

    # Reversible: the uninstaller removes the task and the firewall rule.
    assert "schtasks /delete" in uninstall
    assert "firewall delete rule" in uninstall


def test_the_agent_runs_as_the_user_not_as_admin_or_system(package):
    """The GUI capabilities only work in the interactive desktop session, and
    least privilege wants the agent unelevated even though the INSTALLER needs
    admin. RunLevel Limited + an at-logon trigger is that shape."""
    install = (package / "install.cmd").read_text(encoding="ascii")
    assert "-RunLevel Limited" in install
    assert "AtLogOn" in install
    # SYSTEM/session-0 would give black screenshots -- guard against a drift to it.
    assert "RunLevel Highest" not in install
    assert "-User SYSTEM" not in install


def test_the_port_the_installer_opens_matches_the_env(package):
    """The firewall line reads the port back from the .env that shipped, so a
    package built on a non-default port cannot open the wrong hole."""
    install = (package / "install.cmd").read_text(encoding="ascii")
    assert 'if /i "%%A"=="ROOM_AGENT_PORT" set "PORT=%%B"' in install
    assert "localport=%PORT%" in install


def test_the_target_keeps_its_own_uninstaller(package):
    """install.cmd copies uninstall.cmd to the install dir, so a machine can be
    cleaned up later even after the original package folder is gone -- and so a
    remote deploy can run it over WinRM. Without this the uninstaller only ever
    exists in the staging copy, which the deploy script deletes."""
    install = (package / "install.cmd").read_text(encoding="ascii")
    assert 'copy /y "%SRC%uninstall.cmd" "%DEST%\\"' in install


def test_the_installers_do_not_hang_when_run_headless(package):
    """A remote WinRM deploy runs install.cmd/uninstall.cmd with no console, so
    a bare `pause` would block the deploy forever. Every pause in these two must
    be guarded by ROOM_AGENT_SILENT, which the deploy sets. (start-room-agent.cmd
    is the interactive runner and may pause.)"""
    for name in ("install.cmd", "uninstall.cmd"):
        text = (package / name).read_text(encoding="ascii")
        for line in text.splitlines():
            assert line.strip() != "pause", f"{name} has an unguarded pause"
        assert "if not defined ROOM_AGENT_SILENT pause" in text


def test_the_installer_provisions_python_when_it_is_missing(package):
    """Deploying to many PCs must not mean hand-installing Python on each. When
    no Python is found, install.cmd downloads the official one and installs a
    PRIVATE copy into the agent's folder, used by full path -- so it needs
    nothing on PATH and never fights a system Python. The manual link stays as a
    fallback for a machine with no internet, and the scheduled task uses the
    resolved interpreter, not a bare PATH lookup."""
    install = (package / "install.cmd").read_text(encoding="ascii")
    assert "www.python.org/ftp/python/" in install          # official download
    assert "curl.exe" in install
    assert r"EmmaRoomAgent\python" in install               # private target dir
    assert 'TargetDir="%PRIV%"' in install
    assert "https://www.python.org/downloads/" in install   # manual fallback kept
    # the task and the immediate start use the resolved interpreter
    assert "-Execute '%PYW%'" in install
    assert '"%PY%" -m pip install' in install
    # no PATH lookup for the task any more (it is resolved once, above)
    assert "for /f \"delims=\" %%P in ('where pythonw') do set" not in install


def test_the_installer_prefers_a_bundled_python_over_downloading(package):
    """The target often has no route to python.org, so downloading there stalls.
    install.cmd must run the Python that shipped in the package first, and treat
    the download as a bounded fallback -- not the first move."""
    install = (package / "install.cmd").read_text(encoding="ascii")
    assert 'set "PYSETUP=%SRC%python-' in install
    assert 'if exist "%PYSETUP%" goto run_python_setup' in install
    assert "--connect-timeout" in install and "--max-time" in install   # bounded, cannot hang
    assert install.index("%SRC%python-") < install.index("curl.exe")     # bundle checked first


def test_python_is_bundled_from_cache_without_a_network_call(tmp_path, monkeypatch):
    """The management machine fetches the installer ONCE (cached) and carries it
    in the package, so nothing hits the network at deploy time."""
    fake_cache = tmp_path / "cache" / mrp.PY_INSTALLER
    fake_cache.parent.mkdir(parents=True)
    fake_cache.write_bytes(b"MZ fake python installer")
    monkeypatch.setattr(mrp, "PY_CACHE", fake_cache)
    monkeypatch.setattr(mrp.urllib.request, "urlretrieve",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("hit the network")))

    out = tmp_path / "pkg"
    built = mrp.build("ห้องขาย 9", out, host="0.0.0.0", port=8777,
                      allow="all", programs="all", token="t", bundle_python=True)

    assert built["python"] == mrp.PY_INSTALLER
    assert (out / mrp.PY_INSTALLER).read_bytes() == b"MZ fake python installer"


def test_a_bundle_failure_never_fails_the_build(tmp_path, monkeypatch):
    """No cache and no internet -> the package still builds and stays usable;
    install.cmd falls back to downloading, then to a manual message."""
    monkeypatch.setattr(mrp, "PY_CACHE", tmp_path / "cache" / mrp.PY_INSTALLER)
    monkeypatch.setattr(mrp.urllib.request, "urlretrieve",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no internet")))

    out = tmp_path / "pkg"
    built = mrp.build("ห้องขาย 9", out, host="0.0.0.0", port=8777,
                      allow="all", programs="all", token="t", bundle_python=True)

    assert built["python"] is None
    assert (out / "install.cmd").is_file()      # still a working package


def test_the_task_runs_as_the_interactive_user_not_the_installer(package):
    """No task principal meant the task bound to whoever ran install.cmd -- over a
    WinRM deploy, the IT admin, who is never logged on at the room PC, so after a
    reboot the agent came up in a session that does not exist. A Users-group
    principal runs it in the logged-on user's own interactive session (needed for
    screen/keyboard capabilities). Found by the red-team 2026-09-25."""
    install = (package / "install.cmd").read_text(encoding="ascii")
    assert "New-ScheduledTaskPrincipal" in install
    assert r"BUILTIN\Users" in install
    assert "-RunLevel Limited" in install


def test_the_installer_covers_screenshots_perms_and_a_loud_pip_failure(package):
    """pillow for screenshots; icacls so the limited-user auto-start can write its
    logs/shots under ProgramData; and a pip failure exits loudly (goto pip_failed)
    before the task is registered, so a half-installed agent cannot masquerade as
    installed. The success path must stop (exit /b 0) before the failure label."""
    install = (package / "install.cmd").read_text(encoding="ascii")
    assert "pillow" in install
    assert "icacls" in install and "S-1-5-32-545" in install
    assert "goto pip_failed" in install and ":pip_failed" in install
    assert "exit /b 0" in install
    assert install.index("exit /b 0") < install.index(":pip_failed")


def test_reinstall_stops_the_old_agent_before_starting_the_new(package):
    """An upgrade must replace the running agent. If it does not, the old process
    keeps port 8777, the new agent cannot bind and exits, and the machine keeps
    serving the OLD code and OLD token -- a new capability/token silently never
    takes effect (seen on a real reinstall 2026-09-25). install.cmd stops the
    task and the agent's own pythonw before installing, and does it early enough
    to also free any locked files."""
    install = (package / "install.cmd").read_text(encoding="ascii")
    assert "schtasks /end" in install
    assert "run_room_agent.py" in install and "Stop-Process" in install
    # before the Python install / file copy, not after the new one is started
    assert install.index("Stop-Process") < install.index("Copying files")
    assert install.index("Stop-Process") < install.index("Start it now")
