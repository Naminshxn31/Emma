"""Getting offline mode onto the robot: the fetch script's archive handling
(pinned, verified before extraction, only the pinned files, no path out of
the model folder) and robot_server.py shipping the clips / pinning the
recognizers only while the robot runs OFFLINE_FALLBACK. No network: the
"download" is a file:// URL."""
from __future__ import annotations

import hashlib
import io
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_offline_models as fetch  # noqa: E402
import robot_server as rs  # noqa: E402


def _tar(path: Path, members: list[tuple[str, bytes | None, str]]) -> str:
    """members: (name, data, kind) kind = file | symlink | dir."""
    with tarfile.open(path, "w:bz2") as t:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                t.addfile(info)
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                t.addfile(info)
            else:
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_only_the_pinned_files_come_out_and_nothing_leaves_the_folder(tmp_path):
    archive = tmp_path / "in" / "m.tar.bz2"
    archive.parent.mkdir()
    _tar(archive, [("m", None, "dir"), ("m/model.onnx", b"weights", "file"), ("m/tokens.txt", b"tok", "file"),
                   ("m/test_wavs/0.wav", b"wav", "file"), ("m/../../escape.txt", b"evil", "file"),
                   ("/abs.txt", b"evil", "file"), ("other/model.onnx", b"evil", "file")])
    dest = tmp_path / "out" / "m"
    got = fetch.extract_pinned(archive, "m", {"model.onnx": _h(b"weights"), "tokens.txt": _h(b"tok")}, dest)
    assert sorted(got) == ["model.onnx", "tokens.txt"]
    assert sorted(p.name for p in dest.iterdir()) == ["model.onnx", "tokens.txt"]
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path / "out" / "escape.txt").exists()


def test_a_pinned_name_that_is_a_link_is_refused(tmp_path):
    archive = tmp_path / "m.tar.bz2"
    _tar(archive, [("m/model.onnx", None, "symlink")])
    with pytest.raises(fetch.Refused):
        fetch.extract_pinned(archive, "m", {"model.onnx": _h(b"x")}, tmp_path / "out")
    assert not (tmp_path / "out" / "model.onnx").exists()


def test_a_file_that_is_not_its_pin_is_refused_and_not_kept(tmp_path):
    archive = tmp_path / "m.tar.bz2"
    _tar(archive, [("m/model.onnx", b"other weights", "file")])
    with pytest.raises(fetch.Refused):
        fetch.extract_pinned(archive, "m", {"model.onnx": _h(b"weights")}, tmp_path / "out")
    assert list((tmp_path / "out").iterdir()) == []


def test_a_missing_pinned_file_is_refused(tmp_path):
    archive = tmp_path / "m.tar.bz2"
    _tar(archive, [("m/model.onnx", b"weights", "file")])
    with pytest.raises(fetch.Refused):
        fetch.extract_pinned(archive, "m", {"model.onnx": _h(b"weights"), "tokens.txt": _h(b"t")},
                             tmp_path / "out")


def test_the_archive_is_checked_before_anything_is_extracted(tmp_path):
    src = tmp_path / "src.tar.bz2"
    _tar(src, [("m/model.onnx", b"weights", "file")])
    dest = tmp_path / "stage" / "m.tar.bz2"
    with pytest.raises(fetch.Refused):
        fetch.download(src.as_uri(), dest, "0" * 64)
    assert not dest.exists() and not dest.with_name(dest.name + ".part").exists()
    good = _h(src.read_bytes())
    assert fetch.download(src.as_uri(), dest, good, src.stat().st_size) == dest


def test_the_whole_script_with_pins_file(tmp_path):
    src = tmp_path / "m.tar.bz2"
    sha = _tar(src, [("m/model.onnx", b"weights", "file")])
    pins = tmp_path / "pins.json"
    import json

    pins.write_text(json.dumps({"thai": {"dir": "m", "url": src.as_uri(), "sha256": sha,
                                         "size": src.stat().st_size,
                                         "files": {"model.onnx": _h(b"weights")}}}), encoding="utf-8")
    assert fetch.main(["--pins", str(pins), "--dest", str(tmp_path / "asr"),
                       "--stage", str(tmp_path / "stage")]) == 0
    assert (tmp_path / "asr" / "m" / "model.onnx").read_bytes() == b"weights"
    assert not (tmp_path / "stage" / "m.tar.bz2").exists()          # archive dropped after


# ---- robot_server.py ---------------------------------------------------------------

def _root(tmp_path, pc_env: str, robot_env: str) -> Path:
    (tmp_path / "data" / "robot-server").mkdir(parents=True)
    (tmp_path / ".env").write_text(pc_env, encoding="utf-8")
    (tmp_path / "data" / "robot-server" / "robot.env").write_text(robot_env, encoding="utf-8")
    voice = tmp_path / "data" / "offline" / "voice"
    voice.mkdir(parents=True)
    (voice / "index.json").write_text("{}", encoding="utf-8")
    (voice / "abc.wav").write_bytes(b"RIFF")
    (voice / "abc.part").write_bytes(b"half")
    return tmp_path


def test_off_ships_nothing_and_pins_nothing(tmp_path):
    root = _root(tmp_path, "GEMINI_API_KEY=x\n", "STORIES_ENABLED=true\n")
    assert not rs.offline_on(root)
    assert rs.offline_voice_files(root) == set() and rs.offline_asr_pins(root) == {}


def test_only_robot_env_turns_it_on_for_the_robot(tmp_path):
    """Review 26 Oct (reproduced): OFFLINE_FALLBACK=true in this PC's .env (a
    desk trial) reached the robot through the `secrets` merge -- recognizers
    fetched, drafts beside them -- while the lock below, reading robot.env,
    stayed green. The robot's switch is robot.env's alone now."""
    root = _root(tmp_path, "OFFLINE_FALLBACK=true\n", "STORIES_ENABLED=true\n")
    assert not rs.offline_on(root)
    assert rs.offline_voice_files(root) == set() and rs.offline_asr_pins(root) == {}
    assert "OFFLINE_FALLBACK" not in rs.compose_env("OFFLINE_FALLBACK=true\nGEMINI_API_KEY=x\n", "")
    (root / "data" / "robot-server" / "robot.env").write_text("OFFLINE_FALLBACK=true\n", encoding="utf-8")
    assert rs.offline_on(root)
    assert rs.offline_voice_files(root) == {"data/offline/voice/index.json", "data/offline/voice/abc.wav"}
    pins = rs.offline_asr_pins(root)
    assert all(k.startswith("data/offline/asr/") for k in pins) and len(pins) == 8
    # this PC saying false does not turn the robot's off either
    (root / ".env").write_text("OFFLINE_FALLBACK=false\n", encoding="utf-8")
    assert rs.offline_on(root)


def test_deploy_warns_when_the_robot_runs_it_beside_drafts(tmp_path):
    import json
    import shutil

    root = _root(tmp_path, "", "OFFLINE_FALLBACK=true\n")
    target = root / "data" / "projects" / "embassy_world" / "offline" / "lines.json"
    target.parent.mkdir(parents=True)
    shutil.copy(ROOT / "data" / "projects" / "embassy_world" / "offline" / "lines.json", target)
    assert "0 of" in rs.offline_lines_warning(root)
    from app.offline import lines

    data = json.loads(target.read_text(encoding="utf-8"))
    for line in data["lines"]:
        line.update(approved_by="test", approved_at="2026-10-26T10:00:00+07:00",
                    approved_sha256=lines.digest(line))
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    lines.reset()
    assert rs.offline_lines_warning(root) is None
    (root / "data" / "robot-server" / "robot.env").write_text("", encoding="utf-8")
    assert rs.offline_lines_warning(tmp_path) is None


def test_the_recognizers_never_go_over_adb():
    assert not [f for f in rs.deploy_files() if f.startswith("data/offline/asr/")]


def test_the_robot_env_switches_offline_on_only_with_every_line_approved():
    """The stories' lock, for this switch: robot.env may turn offline mode on
    only beside a lines file a person signed in full -- an offline Emma with
    draft lines answers nothing but stories, and nobody would know why."""
    from dotenv import dotenv_values

    from app.offline import lines

    value = (dotenv_values(ROOT / "data" / "robot-server" / "robot.env").get("OFFLINE_FALLBACK") or "")
    if value.strip().lower() in ("1", "true", "yes", "on"):
        lines.reset()
        assert set(lines.servable()) >= set(lines.IDS)
