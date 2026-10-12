"""A second air conditioner (owner, 2026-10-09: "เพิ่มแอร์ ... samsung",
"เพิ่มเป็นตัวที่สอง") — and proof that a machine with only the original
codes did not notice.

IR is stubbed throughout, as in test_tools.py: the point is which code goes
to which unit, what the model is told, and that nothing is guessed. The hub
itself is unreachable from the suite (conftest blanks the device and codes
files); tests that want codes point the settings at their own tmp files.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from app.config import settings
from app.tools import registry

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = Path(__file__).parent / "data" / "body_nav_p0_snapshot.json"
TRACKED_DEVICES = ROOT / "data" / "ir_devices.json"
SMARTHOME_TOOLS = ("set_lights", "set_air_conditioner", "get_room_status")


def run(coro):
    return asyncio.run(coro)


def _codes(prefix: str, *suffixes: str) -> dict:
    from app.tools.smarthome import unit_code_suffixes

    return {f"{prefix}{s}": "2600aabb" for s in (suffixes or unit_code_suffixes())}


@pytest.fixture
def room(tmp_path, monkeypatch):
    """The tracked devices file (main + samsung) and a codes file this test
    controls. `room.codes(...)` rewrites the codes; `room.sent` records
    every stubbed send."""
    import app.tools as tools_pkg
    from app.tools import broadlink_ir
    from app.tools import smarthome as sh

    tools_pkg.load_tools()
    codes_file = tmp_path / "ir_codes.json"
    monkeypatch.setattr(settings, "ir_devices_file", str(TRACKED_DEVICES))
    monkeypatch.setattr(settings, "ir_codes_file", str(codes_file))
    sent: list[tuple[str, int]] = []

    def fake_send(code, repeat=1, delay=0.6):
        sent.append((code, repeat))
        return fake_send.result

    fake_send.result = "ok"
    monkeypatch.setattr(broadlink_ir, "send", fake_send)
    monkeypatch.setattr(broadlink_ir, "available", lambda: True)
    sh.STATE["lights"] = True
    sh.STATE["ac"] = {"on": True, "temp": 25, "fan": "auto", "mode": "cool"}
    sh.STATE["ac_units"] = {}

    def write_codes(data: dict) -> None:
        codes_file.write_text(json.dumps(data), encoding="utf-8")

    write_codes(_codes("ac_"))
    return type("Room", (), {"sent": sent, "send": fake_send, "codes": staticmethod(write_codes),
                             "mod": sh, "file": codes_file})


def _two_units(room) -> None:
    room.codes({**_codes("ac_"), **_codes("samsung_ac_")})


def _snapshot_entries(key: str = "condo/face=False") -> dict:
    tools = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["tools"][key]
    return {t["name"]: t for t in tools if t["name"] in SMARTHOME_TOOLS}


def _declared() -> dict:
    return {d["name"]: d for d in registry.as_openai_tools() if d["name"] in SMARTHOME_TOOLS}


# ---- the default machine: byte-identical ----


@pytest.mark.parametrize("machine", ["suite_default", "listed_not_learned", "half_learned"])
def test_one_unit_machine_declares_exactly_the_snapshot(room, monkeypatch, machine):
    """The sales room after a pull: data/ir_devices.json names the Samsung
    unit (it is tracked), but no Samsung codes were learned there. Both
    provider views must hand the model what they handed it before units
    existed — compared with the pre-change snapshot, through the same path
    the providers use, not through the static attributes."""
    from google.genai import types

    from app.tools import smarthome as sh

    if machine == "suite_default":
        monkeypatch.setattr(settings, "ir_devices_file", "")
    elif machine == "half_learned":
        room.codes({**_codes("ac_"), **_codes("samsung_ac_", "on", "temp_24")})  # no "off"

    entry = registry.get("set_air_conditioner")
    description, parameters = registry.declaration(entry)
    assert description is sh._AC_DESCRIPTION and parameters is sh._AC_PARAMETERS
    assert entry.description is description and entry.parameters is parameters

    expected = _snapshot_entries()
    declared = _declared()
    assert set(declared) == set(SMARTHOME_TOOLS)
    for name, snap in expected.items():
        assert declared[name]["description"] == snap["description"], name
        assert declared[name]["parameters"] == snap["parameters"], name
    gemini = {d.name: d for d in registry.as_gemini_tool().function_declarations}
    snap = expected["set_air_conditioner"]
    assert gemini["set_air_conditioner"] == types.FunctionDeclaration(
        name="set_air_conditioner", description=snap["description"],
        parameters=snap["parameters"])


def test_every_profile_in_the_snapshot_is_unchanged_on_a_one_unit_machine(room):
    from app.tools import registry as reg

    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["tools"]
    for key in snapshot:
        profile, face = key.split("/face=")
        p_token = reg.session_profile.set(profile)
        f_token = reg.robot_face_session.set(object() if face == "True" else None)
        try:
            declared = _declared()
        finally:
            reg.robot_face_session.reset(f_token)
            reg.session_profile.reset(p_token)
        for name, snap in _snapshot_entries(key).items():
            assert (declared[name]["description"], declared[name]["parameters"]) == (
                snap["description"], snap["parameters"]), (key, name)


def test_one_unit_results_carry_no_unit_fields(room):
    """Not just the declaration: what the original AC answers is unchanged."""
    out = run(registry.dispatch("set_air_conditioner", {"on": True}))
    assert set(out) == {"ok", "ac", "hardware", "instruction"}
    assert room.sent == [("ac_on", 1)]
    status = run(registry.dispatch("get_room_status", {}))
    assert set(status) == {"lights", "ac", "ok", "source"}


def test_a_learned_second_unit_adds_the_unit_argument(room):
    from app.tools import smarthome as sh

    _two_units(room)
    description, parameters = registry.declaration(registry.get("set_air_conditioner"))
    unit = parameters["properties"]["unit"]
    assert unit["enum"] == ["main", "samsung"]
    assert "แอร์ Samsung ห้องนี้" in unit["description"] and "แอร์ตัวเดิม" in unit["description"]
    assert "unit" not in parameters.get("required", [])
    assert description.startswith(sh._AC_DESCRIPTION) and "ห้ามเลือกเอง" in description
    # The registered static declaration was copied, never edited in place.
    assert "unit" not in sh._AC_PARAMETERS["properties"]
    assert _declared()["set_air_conditioner"]["parameters"]["properties"]["unit"]["enum"] == [
        "main", "samsung"]


# ---- never a guess ----


def test_two_units_and_none_named_sends_nothing_and_asks(room):
    from app.tools import smarthome as sh

    _two_units(room)
    before_main = dict(sh.STATE["ac"])
    out = run(registry.dispatch("set_air_conditioner", {"on": False}))
    assert out["ok"] is False and out["error"] == "unit required"
    assert room.sent == [], "nothing may be sent while the unit is a guess"
    assert sh.STATE["ac"] == before_main and sh.STATE["ac_units"] == {}
    assert "hardware" not in out, "no send was attempted, so no hardware outcome"
    # The sentence a guess produces, quoted, and the question to ask instead.
    assert "'ปรับแอร์ให้เรียบร้อยแล้ว'" in out["instruction"]
    assert "'I've turned on the air conditioner for you'" in out["instruction"]
    assert "'แอร์ตัวไหนคะ แอร์ตัวเดิม หรือ แอร์ Samsung ห้องนี้'" in out["instruction"]
    assert [u["unit"] for u in out["units"]] == ["main", "samsung"]


def test_an_unknown_unit_is_refused_not_mapped(room):
    _two_units(room)
    out = run(registry.dispatch("set_air_conditioner", {"on": True, "unit": "daikin"}))
    assert out["ok"] is False and out["error"] == "unknown unit"
    assert room.sent == []


def test_a_unit_that_is_listed_but_not_learned_is_refused(room):
    """One-unit machine (Samsung listed, never learned): a hallucinated
    unit="samsung" must not quietly drive the original AC."""
    out = run(registry.dispatch("set_air_conditioner", {"on": True, "unit": "samsung"}))
    assert out["ok"] is False and room.sent == []
    assert "โดยไม่ใส่ unit" in out["instruction"]


# ---- each unit its own codes and state ----


def test_each_unit_has_its_own_codes_and_state(room):
    from app.tools import smarthome as sh

    _two_units(room)
    out = run(registry.dispatch("set_air_conditioner", {"on": True, "unit": "samsung"}))
    assert room.sent == [("samsung_ac_on", 1)]
    assert out["hardware"] == "ok" and out["unit"] == "samsung"
    assert out["name_th"] == "แอร์ Samsung ห้องนี้"
    assert sh.STATE["ac"] == {"on": True, "temp": 25, "fan": "auto", "mode": "cool"}

    room.sent.clear()
    run(registry.dispatch("set_air_conditioner", {"temp": 22, "unit": "main"}))
    assert room.sent == [("ac_temp_22", 1)]
    assert sh.STATE["ac"]["temp"] == 22
    assert sh.STATE["ac_units"]["samsung"]["temp"] == 25, "the other unit did not move"

    status = run(registry.dispatch("get_room_status", {}))
    assert "ac" not in status, "a lone 'ac' would read as THE air conditioner"
    by_id = {u["unit"]: u for u in status["air_conditioners"]}
    assert by_id["main"]["temp"] == 22 and by_id["samsung"]["temp"] == 25
    assert by_id["samsung"]["name_en"] == "the Samsung air conditioner"


def test_power_goes_out_every_time_for_the_second_unit_too(room):
    _two_units(room)
    for _ in range(2):
        run(registry.dispatch("set_air_conditioner", {"on": True, "unit": "samsung"}))
    assert room.sent == [("samsung_ac_on", 1), ("samsung_ac_on", 1)]


def test_mock_and_failed_are_never_done_for_the_second_unit(room):
    from app.tools.smarthome import IR_MOCK_NOTE

    _two_units(room)
    room.send.result = "mock"
    out = run(registry.dispatch("set_air_conditioner", {"on": False, "unit": "samsung"}))
    assert out["hardware"] == "mock" and out["instruction"] == IR_MOCK_NOTE
    room.send.result = "failed"
    out = run(registry.dispatch("set_air_conditioner", {"on": False, "unit": "samsung"}))
    assert out["ok"] is False and "ห้ามยืนยันว่าทำสำเร็จ" in out["instruction"]


def test_a_code_not_learned_is_named_not_blamed_on_the_hub(room):
    """learn_ir.py lets the owner skip a code; the tool must then say so,
    not "the hub did not answer"."""
    from app.tools.smarthome import _MISSING_CODE_NOTE

    codes = {**_codes("ac_"), **_codes("samsung_ac_")}
    del codes["samsung_ac_temp_30"]
    room.codes(codes)
    out = run(registry.dispatch("set_air_conditioner", {"temp": 30, "unit": "samsung"}))
    assert room.sent == []
    assert out["ok"] is False and out["hardware"] == "failed"
    assert out["missing_codes"] == ["temp_30"] and out["instruction"] == _MISSING_CODE_NOTE


# ---- a remote that sends its whole state (Samsung) ----


def test_whole_state_unit_reports_what_the_frame_actually_set(room):
    from app.tools import smarthome as sh

    _two_units(room)
    run(registry.dispatch("set_air_conditioner", {"temp": 22, "unit": "samsung"}))
    out = run(registry.dispatch("set_air_conditioner", {"fan": "3", "unit": "samsung"}))
    assert room.sent[-1] == ("samsung_ac_fan_3", 1)
    # The fan frame was learned at 25 degrees: that is what the unit is at now.
    assert sh.STATE["ac_units"]["samsung"]["temp"] == 25 and out["ac"]["temp"] == 25
    assert "temp is now 25" in out["note"]


def test_whole_state_unit_applies_one_setting_per_call(room):
    from app.tools import smarthome as sh

    _two_units(room)
    out = run(registry.dispatch("set_air_conditioner",
                                {"temp": 22, "fan": "3", "unit": "samsung"}))
    assert room.sent == [("samsung_ac_temp_22", 1)], "a second frame would undo the first"
    assert out["not_applied"] == ["fan"]
    assert "'ปรับความแรงลมให้แล้ว'" in out["instruction"]
    assert sh.STATE["ac_units"]["samsung"] == {"on": True, "temp": 22, "fan": "auto", "mode": "cool"}


def test_whole_state_unit_power_on_then_temperature(room):
    from app.tools import smarthome as sh

    _two_units(room)
    run(registry.dispatch("set_air_conditioner", {"on": False, "unit": "samsung"}))
    room.sent.clear()
    out = run(registry.dispatch("set_air_conditioner", {"temp": 22, "unit": "samsung"}))
    assert room.sent == [] and out["ok"] is False
    assert sh.STATE["ac_units"]["samsung"]["temp"] == 25, \
        "the next power-on frame brings 25, so 22 must not be remembered"
    out = run(registry.dispatch("set_air_conditioner", {"on": True, "temp": 22, "unit": "samsung"}))
    assert room.sent == [("samsung_ac_on", 1), ("samsung_ac_temp_22", 1)]
    assert out["ac"]["temp"] == 22 and "not_applied" not in out


# ---- the devices file ----


def test_tracked_devices_file_names_the_samsung_unit():
    from app.tools import smarthome as sh

    settings.ir_devices_file = str(TRACKED_DEVICES)
    units = sh.configured_units()
    assert [u.id for u in units] == ["main", "samsung"]
    assert units[0].prefix == "ac_" and units[0].original
    samsung = units[1]
    assert samsung.prefix == "samsung_ac_" and samsung.whole_state
    assert samsung.learned_with == sh.LEARN_BASELINE


def test_devices_file_problems_fall_back_to_the_original(tmp_path, monkeypatch):
    from app.tools import smarthome as sh

    path = tmp_path / "devices.json"
    monkeypatch.setattr(settings, "ir_devices_file", str(path))
    assert [u.id for u in sh.configured_units()] == ["main"]          # missing
    path.write_text("{not json", encoding="utf-8")
    assert [u.id for u in sh.configured_units()] == ["main"]          # broken
    path.write_text(json.dumps({"air_conditioners": [
        {"id": "office", "name_th": "แอร์ห้องทำงาน", "prefix": "ac_"},   # renames the original
        {"id": "inside", "prefix": "ac_2_"},                              # inside ac_* space
        {"id": "lamp", "prefix": "light_x_"},                             # the lamp's space
        {"id": "Bad Id", "prefix": "x_"},
        {"id": "lg", "prefix": "lg_ac_"},
        {"id": "lg", "prefix": "lg2_ac_"},                                # duplicate id
    ]}), encoding="utf-8")
    units = sh.configured_units()
    assert [(u.id, u.prefix) for u in units] == [("office", "ac_"), ("lg", "lg_ac_")]
    assert units[0].name_th == "แอร์ห้องทำงาน" and units[0].original


# ---- scripts/learn_ir.py ----


@pytest.fixture
def learn_ir(monkeypatch):
    import sys

    spec = importlib.util.spec_from_file_location("learn_ir_under_test",
                                                  ROOT / "scripts" / "learn_ir.py")
    module = importlib.util.module_from_spec(spec)
    # Registered while it runs: @dataclass resolves string annotations
    # through sys.modules[cls.__module__].
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def samsung():
    from app.tools import smarthome as sh

    settings.ir_devices_file = str(TRACKED_DEVICES)
    return sh.configured_units()[1]


def test_plan_learns_exactly_the_codes_the_tool_sends(learn_ir, samsung):
    steps = learn_ir.plan(samsung)
    suffixes = [s.suffix for s in steps]
    assert suffixes[:2] == ["off", "on"]
    assert suffixes[2:7] == ["mode_cool", "mode_dry", "mode_fan", "mode_heat", "mode_auto"]
    assert suffixes[7:22] == [f"temp_{t}" for t in range(16, 31)]
    assert suffixes[22:] == ["fan_auto", "fan_1", "fan_2", "fan_3"]
    assert all(s.key == "samsung_ac_" + s.suffix and s.prep and s.press for s in steps)
    by = {s.suffix: s for s in steps}
    # One step before, then ONE press: the hub keeps only the first frame.
    assert "17 องศา" in by["temp_16"].prep and "▼" in by["temp_16"].press
    assert "16 องศา" in by["temp_17"].prep and "▲" in by["temp_17"].press
    assert "เย็น" in by["temp_24"].prep and "อัตโนมัติ" in by["temp_24"].prep


def test_merge_refuses_the_original_ac_and_the_lamp(learn_ir):
    codes = {"ac_on": "aa", "light_on": "bb"}
    for key, prefix in (("ac_on", "samsung_ac_"), ("light_on", "samsung_ac_"),
                        ("ac_on", "ac_"), ("ac_temp_25", "ac_"), ("lg_on", "samsung_ac_"),
                        # a prefix that is a stem of the protected ones
                        ("ac_on", "ac"), ("ac_off", "a"), ("light_off", "l")):
        with pytest.raises(ValueError):
            learn_ir.merge_code(codes, key, "2600", prefix)
    with pytest.raises(ValueError):
        learn_ir.merge_code(codes, "samsung_ac_on", "not hex", "samsung_ac_")
    merged = learn_ir.merge_code(codes, "samsung_ac_on", "2600AB", "samsung_ac_")
    assert merged == {"ac_on": "aa", "light_on": "bb", "samsung_ac_on": "2600ab"}
    assert codes == {"ac_on": "aa", "light_on": "bb"}, "input left untouched"


def test_save_keeps_the_original_codes_and_the_file_layout(learn_ir, tmp_path):
    """In the real file's layout (CRLF, indent 2, no final newline) with its
    real ac_*/light_* codes — only those, so the test reads the same after
    the owner has learned Samsung codes into it. The session's diff must be
    the added keys only, and the .bak the file as it was."""
    real = json.loads((ROOT / "data" / "ir_codes.json").read_text(encoding="utf-8"))
    kept = {k: v for k, v in real.items() if k.startswith(("ac_", "light_"))}
    assert "ac_on" in kept and "light_on" in kept
    original = json.dumps(kept, indent=2).replace("\n", "\r\n").encode("utf-8")
    path = tmp_path / "ir_codes.json"
    path.write_bytes(original)
    learn_ir.save_code(path, "samsung_ac_on", "26000a0b", "samsung_ac_", make_backup=True)
    learn_ir.save_code(path, "samsung_ac_off", "26000c0d", "samsung_ac_", make_backup=False)
    new = path.read_bytes()
    before = json.loads(original)
    after = json.loads(new)
    assert {k: after[k] for k in before} == before
    assert list(after)[len(before):] == ["samsung_ac_on", "samsung_ac_off"]
    assert new.startswith(original[:-len(b"\r\n}")] + b",\r\n")
    assert learn_ir.backup_path(path).read_bytes() == original
    assert not path.with_name(path.name + ".tmp").exists()


def test_an_interrupted_save_leaves_the_file_whole(learn_ir, tmp_path, monkeypatch):
    path = tmp_path / "ir_codes.json"
    path.write_text('{\n  "ac_on": "aa"\n}', encoding="utf-8")
    before = path.read_bytes()

    def crash(src, dst):
        raise OSError("power cut")

    monkeypatch.setattr(learn_ir.os, "replace", crash)
    with pytest.raises(OSError):
        learn_ir.save_code(path, "samsung_ac_on", "2600", "samsung_ac_", make_backup=False)
    assert path.read_bytes() == before
    assert not path.with_name(path.name + ".tmp").exists()


def test_save_refuses_even_if_the_merge_were_wrong(learn_ir, tmp_path, monkeypatch):
    """The second line: should merge_code ever let an ac_* edit through,
    save_code still refuses to write it."""
    path = tmp_path / "ir_codes.json"
    path.write_text('{"ac_on": "aa"}', encoding="utf-8")
    monkeypatch.setattr(learn_ir, "merge_code",
                        lambda codes, key, hex_, prefix: {**codes, "ac_on": "ff", key: hex_})
    with pytest.raises(RuntimeError):
        learn_ir.save_code(path, "samsung_ac_on", "2600", "samsung_ac_", make_backup=False)
    assert json.loads(path.read_text(encoding="utf-8")) == {"ac_on": "aa"}


def test_select_steps_resumes_and_names(learn_ir, samsung):
    steps = learn_ir.plan(samsung)
    codes = {"samsung_ac_off": "aa", "samsung_ac_on": "bb"}
    todo = learn_ir.select_steps(steps, codes)
    assert [s.suffix for s in todo][:1] == ["mode_cool"] and len(todo) == len(steps) - 2
    only = learn_ir.select_steps(steps, codes, only="temp_24, samsung_ac_fan_3,on")
    assert [s.suffix for s in only] == ["on", "temp_24", "fan_3"]
    assert len(learn_ir.select_steps(steps, codes, redo=True)) == len(steps)
    with pytest.raises(ValueError):
        learn_ir.select_steps(steps, codes, only="temp_99")


class FakeHub:
    def __init__(self, frames_after: int = 2, frame: bytes = b"\x26\x00" + b"\x11" * 40):
        self.learning = 0
        self.polls = 0
        self.frames_after = frames_after
        self.frame = frame
        self.sent: list[bytes] = []

    def enter_learning(self):
        self.learning += 1
        self.polls = 0

    def check_data(self):
        self.polls += 1
        if self.polls <= self.frames_after:
            raise RuntimeError("ReadError: nothing captured yet")
        return self.frame

    def send_data(self, data):
        self.sent.append(data)


def test_learn_step_with_a_fake_hub(learn_ir, samsung):
    step = {s.suffix: s for s in learn_ir.plan(samsung)}["temp_24"]
    hub = FakeHub()
    answers = iter(["", "4", "1", "1"])   # Enter, test-send, "it beeped", use
    saved: list[tuple[str, str]] = []
    said: list[str] = []

    def no_wait_capture(device, *, wait_s):
        return learn_ir.capture(device, wait_s=wait_s, sleep=lambda s: None)

    result = learn_ir.learn_step(hub, step, lambda k, h: saved.append((k, h)),
                                 ask=lambda prompt: next(answers), say=said.append,
                                 capture_fn=no_wait_capture)
    assert result == "saved"
    assert saved == [("samsung_ac_temp_24", hub.frame.hex())]
    assert hub.sent == [hub.frame] and hub.learning == 1
    assert any("ยาว 42 ไบต์" in line for line in said)


def test_capture_gives_up_after_the_wait(learn_ir):
    hub = FakeHub(frames_after=10**9)
    now = [0.0]
    out = learn_ir.capture(hub, wait_s=20, clock=lambda: now[0],
                           sleep=lambda s: now.__setitem__(0, now[0] + s))
    assert out is None and 19 <= hub.polls <= 21


def test_connect_never_prints_the_mac(learn_ir, tmp_path, monkeypatch):
    from app.tools import broadlink_ir

    mac = "a1b2c3d4e5f6"
    device_file = tmp_path / "broadlink_device.json"
    device_file.write_text(json.dumps({"host": "192.0.2.4", "mac": mac, "devtype": 21003}),
                           encoding="utf-8")
    monkeypatch.setattr(settings, "broadlink_device_file", str(device_file))
    monkeypatch.setattr(broadlink_ir, "package_installed", lambda: True)
    monkeypatch.setattr(broadlink_ir, "_get_device", lambda: FakeHub())
    said: list[str] = []
    assert learn_ir.connect(said.append) is not None
    text = "\n".join(said)
    assert "192.0.2.4" in text and mac not in text.lower()


def test_the_script_will_not_teach_the_original_unit(learn_ir):
    settings.ir_devices_file = str(TRACKED_DEVICES)
    said: list[str] = []
    assert learn_ir._find_unit("main", said.append) is None
    assert "ไม่เขียนทับ" in said[0]


def test_list_reports_what_is_learned(learn_ir, tmp_path, monkeypatch, capsys):
    codes = tmp_path / "ir_codes.json"
    codes.write_text(json.dumps({"ac_on": "aa", "samsung_ac_on": "2600", "samsung_ac_off": "2601"}),
                     encoding="utf-8")
    monkeypatch.setattr(settings, "ir_devices_file", str(TRACKED_DEVICES))
    monkeypatch.setattr(settings, "ir_codes_file", str(codes))
    assert learn_ir.main(["--unit", "samsung", "--list"]) == 0
    out = capsys.readouterr().out
    assert "สอนแล้ว 2/26" in out and "Emma เห็นแอร์ตัวนี้แล้ว" in out


def test_the_ac_acts_on_a_request_not_on_a_remark_about_the_weather():
    """Review 10 Oct: "ใช้เมื่อลูกค้าบอกว่าร้อน" turned small talk into a tool
    call — "วันนี้อากาศร้อนจังเลยนะ" fired set_air_conditioner in both live
    content runs, and the sales room drives a real AC with this declaration.
    The trigger sentence is quoted as a non-request, and "hot in here" is a
    question first."""
    from app.tools import smarthome as sh

    d = sh._AC_DESCRIPTION
    assert "ใช้เมื่อลูกค้าบอกว่าร้อน" not in d
    assert "ใช้เฉพาะเมื่อลูกค้าขอ" in d
    assert "'วันนี้อากาศร้อนจังเลย' ไม่ใช่คำขอ ห้ามเรียกเครื่องมือนี้" in d
    assert "ให้ถามก่อนว่าอยากให้ปรับแอร์ไหม" in d
