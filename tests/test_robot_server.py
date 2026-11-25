"""scripts/robot_server.py and scripts/emma_server.sh -- the whole Emma server
runs ON the robot since 9 Oct 2026 (owner: "ทำให้ทุกอย่างรันบนหุ่น ... API key
อยู่บนหุ่นได้ แต่ต้องมีการป้องกัน").

The pure parts (what goes into the robot's .env, which files a deploy ships,
what the robot's copy looks like) and the start script. Nothing here runs adb
or reaches a robot; the one test that RUNS emma_server.sh does it in a temp
directory with a fake su and a fake Python.
"""
from __future__ import annotations

import ast
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import robot_python as rp  # noqa: E402
import robot_server as rs  # noqa: E402

CONF = ROOT / "data" / "robot-server"
SCRIPT = ROOT / "scripts" / "emma_server.sh"


# ---- the robot's .env --------------------------------------------------------------

PC_ENV = (
    "# the PC's .env\r\n"
    "GEMINI_API_KEY=pc-gemini\r\n"
    "OPENAI_API_KEY=sk-should-stay-home\r\n"
    "HOST=0.0.0.0\r\n"
    "\r\n"
    "PRINTER_NAME=Brother\r\n"
    "WS_TOKEN=pc-token\r\n"
    "DEV_RELOAD=true\r\n"
    "ROBOT_ARM_ADB=C:\\adb.exe\r\n"
)
OVERRIDES = "# robot\nHOST=127.0.0.1\nDEV_RELOAD=false\nEMBED_PROVIDER=off\nWEB_STAGE=false\n"


def test_compose_env_drops_pc_only_keys_and_lets_robot_env_win():
    out = rs.compose_env(PC_ENV, OVERRIDES)
    values = dotenv_values(stream=io.StringIO(out))
    for key in rs.ENV_DROP:
        assert key not in values or values[key] == "", key       # never the PC's value
    assert "sk-should-stay-home" not in out and "Brother" not in out and "adb.exe" not in out
    assert values["HOST"] == "127.0.0.1" and values["DEV_RELOAD"] == "false"
    assert values["GEMINI_API_KEY"] == "pc-gemini" and values["WS_TOKEN"] == "pc-token"
    assert "\r" not in out and "#" not in out
    # the PC's order, then the robot-only keys in robot.env's order
    keys = [k for k, _ in rs.parse_env(out)]
    assert keys == ["GEMINI_API_KEY", "HOST", "WS_TOKEN", "DEV_RELOAD", "EMBED_PROVIDER", "WEB_STAGE"]


def test_compose_env_reads_a_repeated_key_the_way_the_server_does():
    """python-dotenv (app/config.py) takes the LAST value of a repeated key.
    compose_env kept the first: the robot ran the line the PC had overridden."""
    pc = "HOST=0.0.0.0\nVAD_MODE=gemini\nPORT=8001\nVAD_MODE=local\n"
    out = rs.compose_env(pc, "")
    assert dotenv_values(stream=io.StringIO(out)) == dotenv_values(stream=io.StringIO(pc))
    assert [k for k, _ in rs.parse_env(out)] == ["HOST", "VAD_MODE", "PORT"]


def test_robot_env_holds_no_secret_and_keeps_the_server_off_the_lan():
    text = (CONF / "robot.env").read_text(encoding="utf-8")
    values = dict(rs.parse_env(text))
    for key, value in values.items():
        if re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", key) and key != "SSL_KEYFILE":
            assert value.strip() == "", key
        assert not re.search(r"AIza[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z]{20,}|eyJ[0-9A-Za-z_-]{20,}", value), key
    assert values["HOST"] == "127.0.0.1"        # off the LAN: only the face app and `forward`
    assert values["PORT"] == "8001"             # the face app's https://127.0.0.1:8001
    assert values["SSL_KEYFILE"] == "certs/lan-key.pem" and values["SSL_CERTFILE"] == "certs/lan-cert.pem"
    assert values["DEV_RELOAD"] == "false"
    # every key is one the server reads -- a typo here is a setting that silently does nothing
    readers = "".join((ROOT / f).read_text(encoding="utf-8") for f in ("app/config.py", "run_server.py"))
    for key in values:
        assert '"%s"' % key in readers, key


def test_secrets_go_only_through_the_secrets_command():
    assert rs.SECRET_FILES == {"certs/lan-key.pem": "certs/lan-key.pem"}
    assert "OPENAI_API_KEY" in rs.ENV_DROP


# ---- what a deploy ships -------------------------------------------------------------

def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _write(root: Path, rel: str, data: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("no git: deploy_files (git ls-files --others) was NOT checked")
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "core.autocrlf", "false")
    _write(root, ".gitignore", "*.pyc\n__pycache__/\n.env\ncerts/\n")
    for rel in ("app/main.py", "client/index.html", "client/rive/runtime.js", "data/robot/legacy.json",
                "data/projects/p/presentations/slides/ew_001.jpg", "run_server.py",
                "scripts/emma_server.py", "scripts/emma_server.sh", "scripts/robot_dns.py"):
        _write(root, rel)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    # after the commit: what a working tree really holds next to it
    for rel in ("app/new_feature.py",                       # untracked, not ignored: the server needs it
                "app/session.py.bak", "app/x.lock", "data/foo.part",   # untracked junk
                "app/main.pyc", "app/__pycache__/main.cpython-314.pyc",
                "data/faces/gallery.npz", "data/faces/gallery.lock", "data/faces/people.json",
                "data/personal-docs/brochure.md", "data/personal-docs/.env.old", "data/personal-docs/api-key.pem",
                "data/projects/p/presentations/slides/index.json",
                "data/projects/p/presentations/slides/ew_002.jpg",
                "data/faces/models/auraface/glintr100.onnx",
                ".env", "certs/lan-cert.pem", "certs/lan-key.pem", "certs/server.key"):
        _write(root, rel)
    return root


def test_deploy_ships_new_files_and_leaves_secrets_junk_and_pictures_behind(repo: Path):
    files = rs.deploy_files(repo)
    for rel in ("app/main.py", "app/new_feature.py", "client/index.html", "run_server.py",
                "scripts/emma_server.py", "scripts/emma_server.sh", "scripts/robot_dns.py",
                "data/faces/gallery.npz", "data/faces/people.json", "data/personal-docs/brochure.md",
                "data/projects/p/presentations/slides/index.json", "certs/lan-cert.pem"):
        assert rel in files, rel
    for rel in ("client/rive/runtime.js", "data/robot/legacy.json",
                "data/projects/p/presentations/slides/ew_001.jpg",       # tracked, still not shipped
                "data/projects/p/presentations/slides/ew_002.jpg",
                "app/session.py.bak", "app/x.lock", "data/foo.part", "data/faces/gallery.lock",
                "app/main.pyc", "app/__pycache__/main.cpython-314.pyc",
                "data/faces/models/auraface/glintr100.onnx",              # the robot downloads it itself
                ".env", "certs/lan-key.pem", "certs/server.key",
                "data/personal-docs/.env.old", "data/personal-docs/api-key.pem"):
        assert rel not in files, rel


def test_answer_pictures_ship_only_the_pinned_set_and_only_when_switched_on(tmp_path):
    """Since 2026-10-11 the robot shows the matching picture with an answer
    (ANSWER_IMAGES in robot.env; owner "ทุกเรื่องที่มีภาพตรง"). Then the
    pictures the asset manifest pins ship — not the rest of the folder (the
    old deck, variants), and nothing at all with the switch off."""
    slides = tmp_path / "data" / "projects" / "p" / "presentations" / "slides"
    slides.mkdir(parents=True)
    for name in ("intro_001.jpg", "intro_002.jpg", "old_deck_017.jpg"):
        (slides / name).write_bytes(b"jpg")
    (tmp_path / "data" / "registry").mkdir(parents=True)
    (tmp_path / "data" / "registry" / "external_asset_manifest.json").write_text(json.dumps({
        "schema_version": 1, "sources": {"slide_assets": {"project_id": "p", "files": [
            {"path": "intro_001.jpg"}, {"path": "intro_002.jpg"}, {"path": "intro_003.jpg"},
            {"path": "../../../../.env"}]}}}), encoding="utf-8")
    env = tmp_path / "data" / "robot-server"
    env.mkdir(parents=True)

    (env / "robot.env").write_text("TOOL_GROUPS=knowledge\nANSWER_IMAGES=false\n", encoding="utf-8")
    assert rs.answer_image_files(tmp_path) == set()

    (env / "robot.env").write_text("TOOL_GROUPS=knowledge\nANSWER_IMAGES=true\n", encoding="utf-8")
    assert rs.answer_image_files(tmp_path) == {
        "data/projects/p/presentations/slides/intro_001.jpg",
        "data/projects/p/presentations/slides/intro_002.jpg"}   # 003 missing here: left out


def test_the_robot_env_turns_answer_pictures_on():
    """The tracked robot.env is where the robot's switch lives (no secret)."""
    values = dotenv_values(ROOT / "data" / "robot-server" / "robot.env")
    assert values.get("ANSWER_IMAGES") == "true"


def test_a_lowered_walk_up_quality_gate_only_ever_rides_with_a_dry_run():
    """9 Nov: the floor-2 dry run lowered FACE_APPROACH_MIN_QUALITY (Q 52 at the dock, the walk-up wants
    60) because nothing moves in a dry run. The day the dry run ends this must not still be low: a walk
    on a map the robot is not sure of is the one thing the gate is for."""
    values = dotenv_values(ROOT / "data" / "robot-server" / "robot.env")
    gate = float(values.get("FACE_APPROACH_MIN_QUALITY") or 60)
    if gate < 60:
        assert (values.get("FACE_APPROACH_DRY_RUN") or "").lower() == "true", "a low gate without a dry run"
        assert (values.get("EVENT_WALK_ENABLED") or "false").lower() != "true", "a low gate with walking on"


def test_recorded_ceremony_lines_ship_only_what_their_index_names(tmp_path, monkeypatch):
    """data/ceremony/voice/ (scripts/render_event_script.py; beside the ceremony app's file since
    9 Nov): the index and the WAVs it names by a bare file name inside the folder -- not a stray
    WAV beside them, not a name that walks out of the folder, not one this PC lacks."""
    folder = tmp_path / "data" / "ceremony" / "voice"
    (folder / "sub").mkdir(parents=True)
    for name in ("a.wav", "stray.wav", "sub/x.wav", "notes.txt"):
        (folder / name).write_bytes(b"RIFF")
    (tmp_path / "data" / "ceremony" / "outside.wav").write_bytes(b"RIFF")
    clips = {"1": {"file": "a.wav"}, "2": {"file": "../outside.wav"}, "3": {"file": "sub/x.wav"},
             "4": {"file": "sub\\x.wav"}, "5": {"file": "notes.txt"}, "6": {"file": "gone.wav"}, "7": "x"}
    (folder / "index.json").write_text(json.dumps({"clips": clips}), encoding="utf-8")
    assert rs.event_voice_files(tmp_path) == {"data/ceremony/voice/index.json", "data/ceremony/voice/a.wav"}
    assert rs.event_voice_files(tmp_path / "nowhere") == set()
    files = sorted(rs.event_voice_files(tmp_path))
    assert rs.event_voice_summary(files, files) == ("ceremony voice: 1 recorded line(s) in data/ceremony/voice, "
                                                    "2 file(s) to push")
    assert rs.event_voice_summary(["app/main.py"], []) is None
    # deploy_files carries exactly those along (the folder is gitignored: nothing else ships it)
    monkeypatch.setattr(rs, "tracked_files", lambda root=None: [])
    shipped = {f for f in rs.deploy_files(tmp_path) if f.startswith("data/ceremony/voice/")}
    assert shipped == {"data/ceremony/voice/index.json", "data/ceremony/voice/a.wav"}
    # ...and nothing from the old place beside the event file any more
    old = tmp_path / "data" / "events" / "voice"
    old.mkdir(parents=True)
    (old / "index.json").write_text(json.dumps({"clips": {"1": {"file": "b.wav"}}}), encoding="utf-8")
    (old / "b.wav").write_bytes(b"RIFF")
    assert not [f for f in rs.deploy_files(tmp_path) if f.startswith("data/events/voice/")]


def test_deploy_ships_the_ceremony_file_even_before_it_is_tracked(tmp_path, monkeypatch):
    """CEREMONY_FILE (the Emma Ceremony app's script and running order, 9 Nov) is data the server
    reads: shipped by name -- without it the ceremony app on the robot has nothing to press. Only
    the top level of data/ceremony/ (its voice/ goes by its index, above)."""
    folder = tmp_path / "data" / "ceremony"
    (folder / "voice").mkdir(parents=True)
    (folder / "floor1_ceremony.json").write_text("{}", encoding="utf-8")
    (folder / "voice" / "stray.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(rs, "tracked_files", lambda root=None: [])
    shipped = {f for f in rs.deploy_files(tmp_path) if f.startswith("data/ceremony/")}
    assert shipped == {"data/ceremony/floor1_ceremony.json"}
    # the real one, from this repo (tracked or not yet)
    assert "data/ceremony/floor1_ceremony.json" in set(rs.deploy_files())


def test_the_ceremony_font_ships_by_name_though_it_is_gitignored(tmp_path, monkeypatch):
    """The film's face, Jipatype Kantalad (owner 22 Nov, licensed for the event only), is gitignored:
    `git ls-files --others --exclude-standard` never lists it, and the robot's ceremony-show.css would
    point at nothing. deploy_files names exactly the four faces this PC has; a deploy that would leave
    one out stops before anything is pushed (a dry run says so and goes on previewing)."""
    folder = tmp_path / rs.CEREMONY_FONT_DIR
    folder.mkdir(parents=True)
    for name in rs.CEREMONY_FONT_FILES[:3] + ("Other.otf",):
        (folder / name).write_bytes(b"OTTO")
    monkeypatch.setattr(rs, "tracked_files", lambda root=None: [])
    shipped = {f for f in rs.deploy_files(tmp_path) if f.startswith(rs.CEREMONY_FONT_DIR + "/")}
    assert shipped == {"%s/%s" % (rs.CEREMONY_FONT_DIR, n) for n in rs.CEREMONY_FONT_FILES[:3]}
    assert rs.ceremony_font_missing(shipped) == ["Kantalad-Bold.otf"]
    assert "MISSING on this PC: Kantalad-Bold.otf" in rs.ceremony_font_summary(sorted(shipped), [])
    (folder / rs.CEREMONY_FONT_FILES[3]).write_bytes(b"OTTO")
    files = rs.deploy_files(tmp_path)
    assert rs.ceremony_font_missing(files) == []
    assert rs.ceremony_font_summary(files, files) == "ceremony font: 4 Kantalad face(s), 4 to push"
    # the stylesheet's @font-face rules load exactly these files
    css = (ROOT / "client" / "assets" / "ceremony-show.css").read_text(encoding="utf-8")
    assert sorted(set(re.findall(r"ceremony-show/fonts/(Kantalad-\w+\.otf)", css))) == sorted(rs.CEREMONY_FONT_FILES)
    # a deploy without them stops before it reads the robot; --without-ceremony-font / --dry-run go on
    monkeypatch.setattr(rs, "refresh_faq_cache", lambda: "")
    monkeypatch.setattr(rs, "offline_lines_warning", lambda: "")
    monkeypatch.setattr(rs, "deploy_files", lambda root=None: ["app/main.py"])

    def reached(robot):
        raise RuntimeError("reached the robot")
    monkeypatch.setattr(rs, "remote_manifest", reached)
    with pytest.raises(rs.Fail, match="MISSING on this PC"):
        rs.cmd_deploy(None)
    with pytest.raises(RuntimeError, match="reached the robot"):
        rs.cmd_deploy(None, without_font=True)
    with pytest.raises(RuntimeError, match="reached the robot"):
        rs.cmd_deploy(None, dry=True)
    assert "--without-ceremony-font" in inspect.getsource(rs.main)


def test_the_ceremony_font_is_never_in_the_repository_but_the_real_deploy_carries_it():
    if shutil.which("git") is None:
        pytest.skip("no git: the Kantalad ignore rule was NOT checked")
    names = ["%s/%s" % (rs.CEREMONY_FONT_DIR, n) for n in rs.CEREMONY_FONT_FILES]
    # (review 22 Nov night: the rule is the family's name, not four file names -- a web-font copy or a
    # renamed one must not slip into a commit either; Inter, which is not licensed that way, is not ignored)
    others = ["%s/%s" % (rs.CEREMONY_FONT_DIR, n) for n in
              ("Kantalad-Bold.woff2", "Kantalad-Bold (1).otf", "Kantalad-SemiBold copy.otf", "Kantalad.ttf")]
    r = subprocess.run(["git", "check-ignore", "--no-index"] + names + others, cwd=ROOT, capture_output=True,
                       text=True)
    assert sorted(r.stdout.splitlines()) == sorted(names + others)
    inter = subprocess.run(["git", "check-ignore", "--no-index", rs.CEREMONY_FONT_DIR + "/Inter.ttf"], cwd=ROOT,
                           capture_output=True, text=True)
    assert inter.returncode == 1 and inter.stdout == ""
    tracked = subprocess.run(["git", "ls-files", "--", rs.CEREMONY_FONT_DIR], cwd=ROOT, capture_output=True, text=True).stdout
    assert "Kantalad" not in tracked
    have = [n for n in names if (ROOT / n).is_file()]
    if not have:
        pytest.skip("this PC has no Kantalad faces: the real deploy list was NOT checked (deploy would stop)")
    assert set(have) <= set(rs.deploy_files())


def test_a_ceremony_face_that_is_not_a_font_is_never_shipped(tmp_path, monkeypatch):
    """Review 22 Nov night: the deploy checked only that the four names existed. An empty file (a copy
    that never finished), a web font renamed .otf or a saved error page would have shipped, and Chrome
    drops a face it cannot read without a word. The four are CFF OpenType ("OTTO"): anything else is
    named, never shipped, and the deploy stops (--without-ceremony-font then leaves the robot's as they are)."""
    folder = tmp_path / rs.CEREMONY_FONT_DIR
    folder.mkdir(parents=True)
    regular, medium, semibold, bold = rs.CEREMONY_FONT_FILES
    (folder / regular).write_bytes(b"OTTO" + bytes(60))
    (folder / medium).write_bytes(b"")
    (folder / semibold).write_bytes(b"wOF2" + bytes(60))
    (folder / bold).write_bytes(b"<!doctype html><title>404</title>")
    assert rs.ceremony_font_problems(tmp_path) == [
        "Kantalad-Medium.otf is empty",
        "Kantalad-SemiBold.otf is not a CFF OpenType font (it starts b'wOF2', not b'OTTO')",
        "Kantalad-Bold.otf is not a CFF OpenType font (it starts b'<!do', not b'OTTO')"]
    monkeypatch.setattr(rs, "tracked_files", lambda root=None: [])
    shipped = {f for f in rs.deploy_files(tmp_path) if f.startswith(rs.CEREMONY_FONT_DIR + "/")}
    assert shipped == {"%s/%s" % (rs.CEREMONY_FONT_DIR, regular)}
    # the deploy stops before it reads the robot, and says which file and why
    real_files = rs.deploy_files
    monkeypatch.setattr(rs, "ROOT", tmp_path)
    monkeypatch.setattr(rs, "deploy_files", lambda root=None: real_files(tmp_path))
    monkeypatch.setattr(rs, "refresh_faq_cache", lambda: "")
    monkeypatch.setattr(rs, "offline_lines_warning", lambda: "")

    def reached(robot):
        raise RuntimeError("reached the robot")
    monkeypatch.setattr(rs, "remote_manifest", reached)
    with pytest.raises(rs.Fail) as stopped:
        rs.cmd_deploy(None)
    said = str(stopped.value)
    assert "MISSING on this PC: Kantalad-Medium.otf, Kantalad-SemiBold.otf, Kantalad-Bold.otf" in said
    assert "Kantalad-Medium.otf is empty" in said and "it starts b'wOF2'" in said and "it starts b'<!do'" in said
    with pytest.raises(RuntimeError, match="reached the robot"):
        rs.cmd_deploy(None, without_font=True)


def test_a_deploy_never_deletes_the_robots_kantalad_faces(monkeypatch, capsys):
    """Review 22 Nov night: `deploy --without-ceremony-font` deleted the faces already on the robot --
    every file in the robot's MANIFEST that this PC lacks is deleted, and the flag exists for a PC that
    lacks them. With a robot whose MANIFEST names the four faces and a PC without them: the deploy goes
    on, deletes the code file this PC dropped, never the faces (--force or not), and the new MANIFEST
    still names them as what is on the robot."""
    main = "app/main.py"
    local = rs.manifest(ROOT, [main])
    faces = ["%s/%s" % (rs.CEREMONY_FONT_DIR, n) for n in rs.CEREMONY_FONT_FILES]
    previous = {main: local[main], "app/old_module.py": "o0", **{f: "k%d" % i for i, f in enumerate(faces)}}

    class FakeRobot:
        def __init__(self):
            self.ran, self.fed = [], None

        def hashes(self, base, files):
            out = {f: previous[f] for f in files if f in previous}
            if "MANIFEST.json.new" in files and self.fed is not None:
                out["MANIFEST.json.new"] = rs.sha256(self.fed)
            return out

        def root(self, script, check=True):
            self.ran.append(script)
            return ""

        def feed(self, script, data):
            self.ran.append(script)
            self.fed = data

    monkeypatch.setattr(rs, "refresh_faq_cache", lambda: "")
    monkeypatch.setattr(rs, "offline_lines_warning", lambda: "")
    monkeypatch.setattr(rs, "deploy_files", lambda root=None: [main])
    monkeypatch.setattr(rs, "remote_manifest", lambda robot: dict(previous))
    monkeypatch.setattr(rs, "offline_asr_pins", lambda root=None: {})
    monkeypatch.setattr(rs, "FETCH_ON_ROBOT", {})
    monkeypatch.setattr(rs, "_permissions", lambda robot: None)
    for force in (False, True):
        robot = FakeRobot()
        rs.cmd_deploy(robot, force=force, without_font=True)
        deletes = [c for c in robot.ran if c.startswith("rm -f ") and "MANIFEST" not in c]
        assert len(deletes) == 1 and "app/old_module.py" in deletes[0], deletes
        written = json.loads(robot.fed)["files"]
        assert {f: written.get(f) for f in faces} == {f: previous[f] for f in faces}
        assert "app/old_module.py" not in written and written[main] == local[main]
        out = capsys.readouterr().out
        assert "keeping the 4 Kantalad face(s) already on the robot" in out and "deployed" in out
    # a dry run (no flag: the real one would stop) shows the same: the faces are not on the delete list
    rs.cmd_deploy(FakeRobot(), dry=True)
    out = capsys.readouterr().out
    assert "  delete app/old_module.py" in out and not any(line.startswith("  delete ") and "Kantalad" in line for line in out.splitlines())
    # the flag's help and the MISSING message say what it does
    help_text = re.sub(r"\s+", " ", inspect.getsource(rs.main))
    assert "the faces already on the robot stay there" in help_text and "no deploy deletes them" in help_text
    assert "the faces already on the robot stay there" in rs.ceremony_font_summary([main], [])


def test_the_real_deploy_carries_every_module_the_server_imports_and_no_secret():
    files = set(rs.deploy_files())
    for f in files:
        assert not rs._SECRET_NAME.search(f), f
        assert not f.endswith((".pyc", ".bak", ".lock", ".part")), f
    # app/main.py: uvicorn gets it as the string "app.main:app", not an import
    assert '"app.main:app"' in (ROOT / "run_server.py").read_text(encoding="utf-8")
    todo, seen = ["run_server.py", "scripts/emma_server.py", "app/main.py"], set()
    while todo:
        rel = todo.pop()
        if rel in seen or not (ROOT / rel).is_file():
            continue
        seen.add(rel)
        for node in ast.walk(ast.parse((ROOT / rel).read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "app":
                names = [node.module] + ["%s.%s" % (node.module, a.name) for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names if a.name.split(".")[0] == "app"]
                names += [a.name for a in node.names if (ROOT / "scripts" / (a.name + ".py")).is_file()
                          and rel.startswith("scripts/")]
            for name in names:
                for cand in (name.replace(".", "/") + ".py", name.replace(".", "/") + "/__init__.py",
                             "scripts/%s.py" % name):
                    if (ROOT / cand).is_file():
                        todo.append(cand)
    assert "app/main.py" in seen and "scripts/robot_dns.py" in seen
    missing = sorted(seen - files)
    assert not missing, missing


def test_changed_pushes_what_differs_and_deletes_only_what_we_shipped():
    push, gone = rs.changed({"a": "1", "b": "2", "c": "3"}, {"a": "1", "b": "X", "old": "9"})
    assert push == ["b", "c"] and gone == ["old"]


def test_files_the_robots_server_changed_are_kept_not_overwritten():
    """Enrolments (people.json, gallery.npz) and data-console approvals are
    written by the server ON the robot; a plain hash diff pushed the PC's
    older copy over them."""
    previous = {"data/faces/people.json": "p0", "app/main.py": "m0", "data/x.json": "x0"}
    local = {"data/faces/people.json": "p0", "app/main.py": "m1", "data/x.json": "x1", "new.json": "n1",
             "data/registry/approval_audit.jsonl": "a1"}
    remote = {"data/faces/people.json": "pROBOT",           # enrolled on the robot: keep
              "app/main.py": "m0",                          # unchanged there, new here: push
              "data/x.json": "x1",                          # already what we have
              "data/registry/approval_audit.jsonl": "aROBOT"}   # robot wrote it before we ever shipped it
    assert rs.robot_edited(local, remote, previous) == ["data/faces/people.json",
                                                        "data/registry/approval_audit.jsonl"]
    assert rs.robot_edited(local, {}, previous) == []
    assert rs.robot_edited({"a": "1"}, {"a": "1"}, {}) == []


def test_code_outside_data_is_never_kept_as_a_robot_edit():
    """Review 12 Oct: the server (uid 9999) can write only under data/. A
    code file that is neither what we pushed nor what we have is a deploy that
    broke after the tar and before the MANIFEST (adb dropped mid-push), and
    keeping it pinned half-written code on the robot until --force."""
    previous = {"app/main.py": "m0", "client/voice-preview.html": "v0"}
    local = {"app/main.py": "m2", "client/voice-preview.html": "v2"}
    remote = {"app/main.py": "mHALF", "client/voice-preview.html": "v1"}
    assert rs.robot_edited(local, remote, previous) == []
    push, _ = rs.changed(local, remote)
    assert push == ["app/main.py", "client/voice-preview.html"]


def test_a_checkout_without_the_faces_never_deletes_the_robots_enrolments():
    """Review 12 Oct: the delete path had none of the push path's care. The
    gallery and people.json are gitignored; a PC that lacks them deleted the
    enrolments and consent records made at the robot. data/faces/ is never
    deleted without --force; other data/ files only when the robot has not
    changed them since we shipped them; code always."""
    gone = ["app/old.py", "data/faces/people.json", "data/personal-docs/a.md",
            "data/personal-docs/b.md", "data/personal-docs/gone-already.md"]
    previous = {"app/old.py": "o0", "data/faces/people.json": "p0", "data/personal-docs/a.md": "a0",
                "data/personal-docs/b.md": "b0", "data/personal-docs/gone-already.md": "g0"}
    remote = {"app/old.py": "o0", "data/faces/people.json": "p0",      # unchanged, but faces
              "data/personal-docs/a.md": "a0",                         # unchanged: delete
              "data/personal-docs/b.md": "bROBOT"}                     # robot changed it: keep
    assert rs.spared_deletes(gone, remote, previous) == ["data/faces/people.json",
                                                         "data/personal-docs/b.md"]
    src = (ROOT / "scripts" / "robot_server.py").read_text(encoding="utf-8")
    body = src[src.index("def cmd_deploy("):src.index("    size = sum(")]
    assert "spared = [] if force else spared_deletes(gone, robot.hashes(CODE, gone), previous)" in body
    assert "gone = [f for f in gone if f not in spared]" in body


def test_bundle_is_plain_tar_with_lf_scripts_modes_and_a_real_mtime(tmp_path: Path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "go.sh").write_bytes(b"#!/system/bin/sh\r\necho hi\r\n")
    (tmp_path / "app.py").write_bytes(b"print(1)\r\n")
    blob = rs.bundle(tmp_path, ["scripts/go.sh", "app.py"], mtime=1_760_000_000)
    assert blob[:2] != b"\x1f\x8b"                  # through su, toybox zcat refuses the TTY stdin
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as t:
        members = {m.name: m for m in t.getmembers()}
        assert set(members) == {"scripts/go.sh", "app.py"}
        assert t.extractfile("scripts/go.sh").read() == b"#!/system/bin/sh\necho hi\n"
        assert t.extractfile("app.py").read() == b"print(1)\r\n"          # only .sh is touched
        assert members["scripts/go.sh"].mode == 0o755 and members["app.py"].mode == 0o644
        assert {m.mtime for m in members.values()} == {1_760_000_000}
    # default: now -- an mtime of 0 on every file hid a redeploy from the
    # server's (mtime, size) caches (mydocs, the gallery follower, unit plans)
    with tarfile.open(fileobj=io.BytesIO(rs.bundle(tmp_path, ["app.py"])), mode="r:") as t:
        assert abs(t.getmember("app.py").mtime - time.time()) < 120


def test_the_start_script_reaches_the_robot_lf_only():
    """A Windows checkout (core.autocrlf=true on the owner's PC) hands us CRLF;
    Android's sh reads the "\\r" as part of every word."""
    blob = rs.bundle(ROOT, ["scripts/emma_server.sh"])
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as t:
        shipped = t.extractfile("scripts/emma_server.sh").read()
    assert b"\r" not in shipped and shipped.startswith(b"#!/system/bin/sh\n")


def test_forward_prints_the_address_not_adbs_port_echo(monkeypatch, capsys):
    class FakeRobot:
        def __init__(self, serial):
            pass

        def adb_out(self, *args):
            assert args == ("forward", "tcp:18001", "tcp:8001")
            return "18001\n"                    # what adb really prints

    monkeypatch.setattr(rs, "Robot", FakeRobot)
    assert rs.main(["forward"]) == 0
    assert capsys.readouterr().out.strip() == "https://127.0.0.1:18001/ -> robot 127.0.0.1:8001"


def test_secrets_dry_run_writes_nothing_and_names_keys_not_values(monkeypatch, capsys, tmp_path):
    """3 Nov 2026: `secrets --dry-run` wrote the robot's real .env -- only
    deploy read the flag. A preview reads the robot's .env and prints key
    NAMES (added/removed/changed), never a value, and writes nothing."""
    (tmp_path / ".env").write_text("GEMINI_API_KEY=pc-secret-1\nKEEP=1\nGONE=x\n", encoding="utf-8")
    (tmp_path / "certs").mkdir()
    (tmp_path / "certs" / "lan-key.pem").write_bytes(b"pem")
    over = tmp_path / "robot.env"
    over.write_text("NEW_ONE=en\nKEEP=2\n", encoding="utf-8")
    monkeypatch.setattr(rs, "ROOT", tmp_path)
    monkeypatch.setattr(rs, "OVERRIDES", over)
    monkeypatch.setattr(rs, "OPTIONAL_SECRET_FILES", {})
    monkeypatch.setattr(rs, "ENV_DROP", set())

    class FakeRobot:
        def __init__(self, serial):
            self.wrote = []

        def root(self, script, check=True):
            assert script.startswith("cat ") and script.split()[1].endswith("/.env")
            return "GEMINI_API_KEY=robot-secret-2\nKEEP=1\nGONE=x\nOLD=y\n"

        def hashes(self, base, files):
            return {"certs/lan-key.pem": rs.sha256(b"pem")}

        def write_secret(self, rel, data):
            raise AssertionError("a dry run wrote %s" % rel)

    monkeypatch.setattr(rs, "Robot", FakeRobot)
    assert rs.main(["secrets", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "added: NEW_ONE" in out and "removed: OLD" in out
    assert "changed: GEMINI_API_KEY, KEEP" in out
    assert "files that would change: -" in out and "nothing written" in out
    assert "secret-1" not in out and "secret-2" not in out and "=en" not in out


def test_dry_run_on_a_command_that_acts_does_nothing(monkeypatch, capsys):
    def no_robot(serial):
        raise AssertionError("reached the robot")

    monkeypatch.setattr(rs, "Robot", no_robot)
    for what in ("restart", "start", "stop", "install", "forward", "lock"):
        assert rs.main([what, "--dry-run"]) == 2
    assert "nothing done" in capsys.readouterr().err


def test_every_root_script_starts_from_a_sane_umask(monkeypatch):
    """su hands its child umask 0: a `mkdir -p` in a root script made a
    world-writable directory (code/certs before the first deploy)."""
    robot = object.__new__(rs.Robot)
    robot.adb, robot.serial = "adb", "x"
    seen = []
    monkeypatch.setattr(robot, "shell", lambda cmd, check=True: seen.append(cmd) or "")
    robot.root("mkdir -p /data/local/emma-server/code/certs")
    assert seen == ["/system/xbin/su 0 sh -c 'umask 022; mkdir -p /data/local/emma-server/code/certs'"]
    ran = []
    monkeypatch.setattr(rs.subprocess, "run", lambda argv, **kw: ran.append(argv))
    robot.feed("umask 077; cat > f.new", b"secret")
    assert ran[0][-1] == "/system/xbin/su 0 sh -c 'umask 022; umask 077; cat > f.new'"


# ---- emma_server.sh ------------------------------------------------------------------

def _script() -> str:
    """The script without its comments, so prose cannot satisfy (or trip) a check."""
    out = []
    for line in SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n"):
        if line.lstrip().startswith("#"):
            continue
        out.append(re.sub(r"\s+#\s.*$", "", line))
    return "\n".join(out)


def _case(text: str, name: str) -> str:
    start = text.index("\n  %s)" % name)
    return text[start:text.index(";;", start)]


def test_the_server_runs_as_nobody_with_inet_and_never_as_root():
    text = _script()
    start = _case(text, "start")
    assert start.count("/system/xbin/su ") == 1
    # root forks the server through su, then hands off to the server's own
    # `run` half -- it does not run a command STRING (no `sh -c` to parse the
    # DNS or anything else again), it runs this very script's `run` case
    assert "nohup /system/xbin/su 9999,9999,3003 /system/bin/sh $C/scripts/emma_server.sh run" in start
    assert re.search(r"\bsu 0\b", text) is None
    # the `run` half is the SERVER user's, and refuses to be root: everything
    # it does touches data/, which belongs to the server, so a symlink there
    # can only reach what that user could already reach
    run = _case(text, "run")
    assert 'if [ "$(id -u)" = 0 ]' in run
    # -s: no user site-packages. HOME is data/ (writable); without -s a
    # ~/.local site-packages there is code the server runs at the next boot
    assert "$R/py -s scripts/emma_server.py" in run


def test_emma_dns_is_sanitised_and_never_interpolated_into_a_command_string():
    start = _case(_script(), "start")
    assert "EMMA_DNS=$(echo \"$EMMA_DNS\" | tr -cd '0-9.,')" in start
    assert "export EMMA_DNS" in start
    launch = start[start.index("nohup "):]
    # DNS reaches the server through su's inherited environment, never a
    # command string a shell parses again (no `'$DNS'`, no `sh -c`)
    assert "$DNS" not in launch and "sh -c" not in launch and "EMMA_DNS" not in launch


def test_what_the_server_writes_is_not_world_writable():
    """Measured 9 Oct: code/data/logs drwxrwxrwx, the jsonl -rw-rw-rw-."""
    text = _script()
    assert "\numask 027\n" in text                          # the top-level default
    run = _case(text, "run")
    assert "umask 027" in run                                # su may reset it: set again in `run`
    assert "chmod -R g-w,o-rwx data" in run                 # and as the SERVER user, not root
    # root never recurses over the server's tree (chown/chmod -R follow
    # symlinks the server can plant) and never opens a path under data/
    start = _case(text, "start")
    assert "chmod -R" not in start and "chown -R" not in start
    assert "data/server.log" not in start and "$LOG" not in start


def test_a_pid_is_signalled_only_if_proc_says_it_is_our_server():
    text = _script()
    ours = text[text.index("ours() {"):text.index("\n}\n", text.index("ours() {"))]
    assert "/proc/$p/cmdline" in ours and "scripts/emma_server.py" in ours
    stop = _case(text, "stop")
    assert stop.index("p=$(ours) ||") < stop.index("kill -TERM")
    assert "kill" not in _case(text, "start") and "kill" not in _case(text, "status")


def test_an_untracked_server_is_found_never_a_shell_that_names_it():
    """11 Nov: the pid file held a dead starter's pid while the real server ran on -- each watchdog
    tick booted another. `untracked` finds the interpreter (its command line ENDS with the script);
    start tracks it instead of starting one, stop stops it, status says so."""
    text = _script()
    fn = text[text.index("untracked() {"):text.index("\n}\n", text.index("untracked() {"))]
    assert '*python*" scripts/emma_server.py "|*python*" scripts/emma_server.py")' in fn
    start = _case(text, "start")
    assert start.index('mkdir "$LOCK"') < start.index("p=$(untracked)") < start.index("nohup ")
    assert 'echo "$p" > "$PIDF"' in start[start.index("p=$(untracked)"):start.index("nohup ")]
    assert "p=$(ours) || p=$(untracked) ||" in _case(text, "stop")
    assert "p=$(untracked)" in _case(text, "status")


def test_one_server_process_for_its_whole_life_booting_or_serving():
    """The port answers only once a server has booted: one started during the first's boot passed
    the port check and ran its startup (review 11 Nov). A flock held for the process's life, taken
    before anything is imported."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import emma_server

    src = (ROOT / "scripts" / "emma_server.py").read_text(encoding="utf-8")
    main = src[src.index('if __name__ == "__main__":'):]
    assert main.index("hold_server_lock(") < main.index("from app.config import settings") \
        < main.index("already_serving(") < main.index("runpy.run_path")
    try:
        import fcntl  # noqa: F401
    except ImportError:
        pytest.skip("no fcntl here (Windows): the server lock itself was NOT exercised -- only its place")
    import tempfile

    lock = Path(tempfile.mkdtemp()) / ".server.lock"
    first = subprocess.Popen([sys.executable, "-c",
                              "import sys, time; sys.path.insert(0, %r); import emma_server; "
                              "print(emma_server.hold_server_lock(__import__('pathlib').Path(%r)), flush=True); "
                              "time.sleep(5)" % (str(ROOT / "scripts"), str(lock))],
                             stdout=subprocess.PIPE, encoding="utf-8")
    try:
        assert first.stdout.readline().strip() == "True"
        assert emma_server.hold_server_lock(lock) is False      # another process holds it
    finally:
        first.kill()
        first.wait()


def test_stop_leaves_no_untracked_server_behind():
    stop = _case(_script(), "stop")
    after = stop[stop.index('rm -f "$PIDF"'):]
    assert "q=$(untracked)" in after and 'kill -TERM "$q"' in after


def test_a_second_server_exits_before_it_boots(monkeypatch):
    """Its startup must not run while another server holds the port (11 Nov: each boot cleared the
    walk in progress and cancelled the board's action)."""
    import socket
    sys.path.insert(0, str(ROOT / "scripts"))
    import emma_server

    src = (ROOT / "scripts" / "emma_server.py").read_text(encoding="utf-8")
    main = src[src.index('if __name__ == "__main__":'):]
    assert main.index("already_serving(") < main.index("runpy.run_path")
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    try:
        assert emma_server.already_serving("127.0.0.1", port)
        assert emma_server.already_serving("0.0.0.0", port)          # a 0.0.0.0 bind is asked on loopback
    finally:
        srv.close()
    assert not emma_server.already_serving("127.0.0.1", port)


def test_one_starter_at_a_time_and_off_the_apps_process_group():
    start = _case(_script(), "start")
    nohup = start.index("nohup ")
    assert start.index('mkdir "$LOCK"') < nohup
    assert start.index("trap 'rmdir \"$LOCK\"") < nohup
    assert start.index("/sys/fs/cgroup /acct") < nohup


def _working_sh():
    sh = shutil.which("sh") or shutil.which("bash")
    if sh is not None and subprocess.run([sh, "-c", "exit 0"], capture_output=True).returncode != 0:
        sh = None
    return sh


_NET_HEAD = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"


def _adb_tcp(tmp: Path, sh: str, *, port="5555", adbd="running", usb="adb", tcp="", tcp6="") -> list[str]:
    """Run emma_server.sh's adb_tcp alone: getprop/setprop/stop/start stubbed (the calls land in
    a file), /proc/net and run/ in tmp. Returns the calls it made."""
    fn = _script()
    fn = fn[fn.index("adb_tcp() {"):fn.index("\ncase \"$1\" in")]
    (tmp / "net").mkdir(exist_ok=True)
    (tmp / "net" / "tcp").write_text(_NET_HEAD + tcp, encoding="utf-8")
    (tmp / "net" / "tcp6").write_text(_NET_HEAD + tcp6, encoding="utf-8")
    calls = tmp / "calls"
    harness = "\n".join([
        f'B="{tmp.as_posix()}"; NETP="$B/net"; ADBSTAMP="$B/run/adb.restart"; ADBLOG="$B/run/adb.log"',
        f'getprop() {{ case "$1" in persist.adb.tcp.port) echo "{port}";; init.svc.adbd) echo "{adbd}";; '
        f'persist.sys.usb.config) echo "{usb}";; *) echo "";; esac; }}',
        'setprop() { echo "setprop $*" >> "$B/calls"; }',
        'stop() { echo "stop $*" >> "$B/calls"; }',
        'start() { echo "start $*" >> "$B/calls"; }',
        fn,
        "adb_tcp",
        "",
    ])
    r = subprocess.run([sh, "-c", harness], capture_output=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout, r.stderr)   # nothing on the app's line
    return calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


def test_adb_over_wifi_comes_back_when_nothing_listens_on_its_port(tmp_path):
    """20 Nov: after a power cycle the robot answered ping but not adb :5555 for minutes (port set,
    USB debugging on) and only somebody at its screen could bring it back. The app's watchdog runs
    `start` as root every 30 s: adbd is restarted then -- and only then."""
    sh = _working_sh()
    if sh is None:
        pytest.skip("no working sh: emma_server.sh's adb_tcp was NOT checked")
    restart = ["setprop service.adb.tcp.port 5555", "stop adbd", "start adbd"]
    a = tmp_path / "a"
    a.mkdir()
    assert _adb_tcp(a, sh) == restart                                   # nothing on :5555
    assert "nothing on :5555" in (a / "run" / "adb.log").read_text(encoding="utf-8")
    assert _adb_tcp(a, sh) == restart                                   # a minute has not passed: no second one
    listen4 = "   0: 00000000:15B3 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 1\n"
    listen6 = ("   0: 00000000000000000000000000000000:15B3 00000000000000000000000000000000:0000 0A "
               "00000000:00000000 00:00000000 00000000     0        0 1\n")
    for case, kw in {"v4": {"tcp": listen4}, "v6": {"tcp6": listen6}, "no port": {"port": ""},
                     "port 0": {"port": "0"}, "usb debugging off": {"adbd": "stopped", "usb": "mtp"},
                     "usb config empty": {"adbd": "stopped", "usb": ""}}.items():
        d = tmp_path / case.replace(" ", "_")
        d.mkdir()
        assert _adb_tcp(d, sh, **kw) == [], case                       # listening / not ours to touch
    # adbd not running at all while USB debugging is still on: started again ("mtp,adb" too)
    for usb in ("adb", "mtp,adb"):
        d = tmp_path / ("dead_" + usb.replace(",", "_"))
        d.mkdir()
        assert _adb_tcp(d, sh, adbd="stopped", usb=usb) == restart, usb
    # a connection on :5555 that is not a LISTEN (01 = established) is no listener
    est = "   1: 0A000001:15B3 0A000002:C350 01 00000000:00000000 00:00000000 00000000     0        0 2\n"
    d = tmp_path / "est"
    d.mkdir()
    assert _adb_tcp(d, sh, tcp=est) == restart
    # another port the owner set is the one looked for (8555 = 0x216B)
    d = tmp_path / "other"
    d.mkdir()
    assert _adb_tcp(d, sh, port="8555", tcp=listen4) == ["setprop service.adb.tcp.port 8555", "stop adbd", "start adbd"]


def test_start_looks_after_adb_before_it_answers_running():
    start = _case(_script(), "start")
    assert start.index("adb_tcp") < start.index("if p=$(ours)")        # every tick, the server up or not
    fn = _script()
    fn = fn[fn.index("adb_tcp() {"):fn.index("\ncase \"$1\" in")]
    for line in fn.splitlines():                                       # the app reads start's stdout
        if "echo" in line and "$(" not in line.split("echo")[0]:
            assert ">" in line.split("echo", 1)[1], line
    assert "ADB_" not in fn.replace("ADBSTAMP", "").replace("ADBLOG", "")   # nothing taken from the environment


def test_the_script_parses():
    sh = shutil.which("sh") or shutil.which("bash")
    # Run from PowerShell on Windows, "bash" is System32's WSL relay, which
    # fails before reading the script when no Linux distro is installed (the
    # full suite, 27 Oct): that is this machine, not the script
    if sh is not None and subprocess.run([sh, "-c", "exit 0"], capture_output=True).returncode != 0:
        sh = None
    if sh is None:
        pytest.skip("no working sh: emma_server.sh was NOT syntax-checked")
    r = subprocess.run([sh, "-n", str(SCRIPT)], capture_output=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr


def _fake_layout(tmp: Path, sh: str) -> tuple[Path, Path]:
    """emma_server.sh with its paths pointed into tmp, a su that drops its uid
    argument, and a 'runtime' whose py sleeps under the server's cmdline."""
    b = tmp / "b"
    for d in ("runtime", "code/data", "code/scripts", "run"):
        (b / d).mkdir(parents=True, exist_ok=True)
    (b / "code" / ".env").write_text("", encoding="utf-8")
    su = tmp / "su"
    su.write_bytes(b'#!/bin/sh\nshift\nexec "$@"\n')
    py = b / "runtime" / "py"
    py.write_bytes(('#!/bin/sh\necho "py EMMA_DNS=$EMMA_DNS umask=$(umask)"\n'
                    'exec "%s" -c "import time; time.sleep(20)" scripts/emma_server.py\n'
                    % Path(sys.executable).as_posix()).encode())
    for f in (su, py):
        f.chmod(0o755)
    text = (_script()
            .replace("B=/data/local/emma-server", "B=%s" % b.as_posix())
            .replace("/system/xbin/su", su.as_posix())
            .replace("/system/bin/sh", "sh")                        # start re-execs the server user's half
            .replace("/sys/fs/cgroup /acct", "/nonexistent"))       # never move this test's shell
    run = tmp / "run.sh"
    run.write_bytes(text.encode())
    # start forks `su ... sh $C/scripts/emma_server.sh run`: that path must hold
    # this same rewritten script (same B, same fake su and py)
    (b / "code" / "scripts" / "emma_server.sh").write_bytes(text.encode())
    return b, run


def test_start_is_idempotent_locked_and_strips_the_dns_string(tmp_path: Path):
    sh = shutil.which("sh")
    if sh is None or not Path("/proc/self/cmdline").exists() and os.name != "nt":
        pytest.skip("no sh with /proc: emma_server.sh start/stop were NOT run")
    b, run = _fake_layout(tmp_path, sh)

    started: list[str] = []

    def go(action: str, **env) -> tuple[int, str]:
        r = subprocess.run([sh, run.as_posix(), action], capture_output=True, encoding="utf-8",
                           errors="replace", timeout=60, env={**os.environ, **env})
        out = r.stdout.strip()
        if out.startswith("started "):
            started.append(out.split()[1])
        return r.returncode, out

    try:
        code, out = go("start", EMMA_DNS="192.168.1.1;touch /tmp/pwned,8.8.8.8")
        if code == 1 and "failed to start" in out and not (b / "code" / "data" / "server.log").read_text():
            pytest.skip("this sh cannot run the fake server: start/stop were NOT exercised")
        assert code == 0 and out.startswith("started "), out
        pid = out.split()[1]
        log = (b / "code" / "data" / "server.log").read_text(encoding="utf-8")
        assert "py EMMA_DNS=192.168.1.1,8.8.8.8 umask=0027" in log
        assert go("start") == (0, "running " + pid)                     # idempotent: the watchdog's path
        assert go("status") == (0, "running " + pid)
        # 11 Nov: a starter that lost the race left ITS pid in the file -- the winner ran untracked
        # and every watchdog tick booted another server. Found and tracked again, nothing started:
        (b / "run" / "server.pid").write_text("999999\n")
        assert go("start") == (0, "running %s (was untracked)" % pid)
        assert (b / "run" / "server.pid").read_text().strip() == pid
        (b / "run" / "server.pid").write_text("999999\n")
        assert go("status") == (0, "running %s (untracked)" % pid)
        (b / "run" / "server.pid").write_text(pid + "\n")
        assert not (b / "run" / "start.lock").exists()                  # released on exit
        # another starter holds the lock: say so, start nothing
        (b / "run" / "start.lock").mkdir()
        (b / "run" / "server.pid").write_text("999999\n")
        assert go("start") == (0, "starting (another start is in progress)")
        assert (b / "run" / "server.pid").read_text().strip() == "999999"
        (b / "run" / "server.pid").write_text(pid + "\n")
        assert go("stop")[1] == "stopped " + pid
        assert go("status") == (3, "not running")
        # a lock left by a starter that died is taken over after a minute
        old = time.time() - 120
        os.utime(b / "run" / "start.lock", (old, old))
        code, out = go("start")
        assert code == 0 and out.startswith("started "), out
        assert not (b / "run" / "start.lock").exists()
    finally:
        # every fake server this test started, even one a failing run lost
        # track of (it would hold tmp_path open on Windows)
        if started:
            subprocess.run([sh, "-c", "kill -9 %s" % " ".join(started)], capture_output=True, timeout=60)


# ---- the pinned runtime -----------------------------------------------------------------

def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name.lower())


def test_every_runtime_artifact_is_pinned_https_and_sha256():
    lock = json.loads((CONF / "wheels.lock.json").read_text(encoding="utf-8"))
    items = [lock["python"]] + lock["debs"] + lock["wheels"]
    for item in items:
        assert item["url"].startswith("https://"), item["file"]
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), item["file"]
        assert item["url"].split("/")[-1].replace("%2B", "+") == item["file"], item["file"]
    assert "aarch64-unknown-linux-gnu" in lock["python"]["file"]
    # the same glibc as the route runtime (robot_python.DEBS)
    assert {d["url"]: d["sha256"] for d in lock["debs"]} == {rp.DEBIAN + p: h for p, h in rp.DEBS.items()}
    for w in lock["wheels"]:
        assert w["url"].startswith("https://files.pythonhosted.org/"), w["file"]
        assert "aarch64" in w["file"] or w["file"].endswith("-none-any.whl"), w["file"]
        # the GUI build needs libGL, which Android does not have
        assert not rs.SKIP_WHEELS.match(w["file"]), w["file"]
    assert any(w["file"].startswith("opencv_python_headless-") for w in lock["wheels"])


def test_the_lock_is_exactly_the_pinned_requirements():
    """A pin added to requirements.txt without `robot_server.py lock` is a
    module the robot does not have -- found at the server's first import."""
    pins = {}
    for line in (CONF / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            name, version = line.split("==")
            pins[_norm(name)] = version
    lock = json.loads((CONF / "wheels.lock.json").read_text(encoding="utf-8"))
    locked = {_norm(w["file"].split("-")[0]): w["file"].split("-")[1] for w in lock["wheels"]}
    assert locked == pins


# ---- semantic search on the robot: the ONNX engine (app/embed_onnx.py) --------------------

def test_the_robot_runs_local_embeddings_and_has_the_engine_to_do_it():
    """EMBED_PROVIDER=local on a robot without the engine's packages is a
    query that raises on every question (lexical, logged) -- the state this
    replaced. All three ends must move together."""
    from app import embed_onnx

    values = dict(rs.parse_env((CONF / "robot.env").read_text(encoding="utf-8")))
    assert values["EMBED_PROVIDER"] == "local"
    pins = {}
    for line in (CONF / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            name, version = line.split("==")
            pins[_norm(name)] = version
    assert "tokenizers" in pins and "onnxruntime" in pins and "numpy" in pins
    lock = json.loads((CONF / "wheels.lock.json").read_text(encoding="utf-8"))
    wheel = [w for w in lock["wheels"] if w["file"].startswith("tokenizers-%s-" % pins["tokenizers"])]
    assert len(wheel) == 1 and re.fullmatch(r"[0-9a-f]{64}", wheel[0]["sha256"])
    # a CPython-3.14-loadable aarch64 build: abi3, or cp314 itself
    assert "aarch64" in wheel[0]["file"] and re.search(r"-(cp3\d+-abi3|cp314-cp314)-", wheel[0]["file"])
    # install's smoke test imports what the engine needs, so a missing wheel
    # fails `install`, not the first guest question
    source = inspect.getsource(rs.cmd_install)
    smoke = re.search(r'"import ([^;"]+)', source.replace('"\n', "").replace('                 "', ""))
    imported = {m.strip() for m in smoke.group(1).split(",")}
    assert {"numpy", "onnxruntime", "tokenizers"} <= imported, imported
    # the model: downloaded by the robot itself, from the pinned commit
    for name in embed_onnx.FILES:
        rel = "%s/%s" % (embed_onnx.REL_DIR, name)
        assert rs.FETCH_ON_ROBOT.get(rel) == embed_onnx.url(name), rel
        assert embed_onnx.REVISION in rs.FETCH_ON_ROBOT[rel]


def test_the_robot_never_builds_an_index_and_deploy_ships_the_faq_cache():
    """15 Oct: the report FAQ's embeddings were not in EXTRA_GLOBS, so each FAQ
    change left the robot to embed 299 questions at app start -- and it
    hard-reset ~25 s in, six times. All three ends move together: the robot
    does not build (EMBED_BUILD=false), deploy ships the PC's cache, and
    deploy refreshes that cache on the PC before it diffs anything."""
    values = dict(rs.parse_env((CONF / "robot.env").read_text(encoding="utf-8")))
    assert values["EMBED_BUILD"] == "false"
    assert "SEARCH_SEMANTIC" not in values          # the wrong first guess, undone
    assert "data/projects/*/sales/faq_embeddings.npz" in rs.EXTRA_GLOBS
    assert rs._deployable("data/projects/embassy_world/sales/faq_embeddings.npz")
    body = inspect.getsource(rs.cmd_deploy)
    assert body.index("refresh_faq_cache()") < body.index("deploy_files()")


def test_models_the_robot_fetches_never_share_a_staging_name():
    """_MODEL_FETCH stages each download as STAGE/models/<basename> and copies
    it from there: two pins with one basename (two `model.onnx`s) would copy
    one file to both places, and the sha check would fail only after the
    download."""
    names = [os.path.basename(f) for f in rs.FETCH_ON_ROBOT]
    assert len(names) == len(set(names)), names


def test_the_embedding_model_is_gitignored_and_never_pushed_over_adb():
    """470 MB at adb's 0.3 MB/s is 26 minutes; git history is forever."""
    if shutil.which("git") is None:
        pytest.skip("no git: the data/models/ ignore rule was NOT checked")
    from app import embed_onnx

    rels = ["%s/%s" % (embed_onnx.REL_DIR, n) for n in embed_onnx.FILES]
    r = subprocess.run(["git", "check-ignore", "--no-index", *rels], cwd=ROOT, capture_output=True,
                       encoding="utf-8")
    assert sorted(r.stdout.split()) == sorted(rels)
    assert not [f for f in rs.deploy_files() if f.startswith("data/models/")]


def test_the_launcher_writes_no_bytecode_and_runs_through_glibc():
    """PYTHONDONTWRITEBYTECODE: code/ is root-owned and read-only to the
    server, and a .pyc beside a redeployed .py of the same size is the stale
    cache the bundle's real mtime exists to avoid."""
    import robot_server_setup as setup
    assert "export PYTHONDONTWRITEBYTECODE=1" in setup.LAUNCHER
    assert "ld-linux-aarch64.so.1" in setup.LAUNCHER and "export TZ=ICT-7" in setup.LAUNCHER


def test_the_robot_runtime_can_encode_any_wake_spelling():
    """Without sentencepiece only EMMA/IMMA/A MA had encodings on the robot
    and every other WAKE_SPELLINGS entry was dropped (review 10 Oct). Pinned
    as a CPython-3.14 aarch64 wheel, and imported by install's smoke test so
    a missing wheel fails `install`, not the first tuning round."""
    lock = json.loads((CONF / "wheels.lock.json").read_text(encoding="utf-8"))
    wheel = [w for w in lock["wheels"] if w["file"].startswith("sentencepiece-")]
    assert len(wheel) == 1, wheel
    assert re.search(r"-cp314-cp314-.*aarch64", wheel[0]["file"]), wheel[0]["file"]
    source = inspect.getsource(rs.cmd_install)
    smoke = re.search(r'"import ([^;"]+)', source.replace('"\n', "").replace('                 "', ""))
    assert "sentencepiece" in {m.strip() for m in smoke.group(1).split(",")}
