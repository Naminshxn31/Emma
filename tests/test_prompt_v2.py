"""The condo prompt's v2 layout (PROMPT_LAYOUT=v2, experiment 10 Oct 2026).

v2 regroups v1's sentences; it must never lose one. Every line of v1 was
written after a measured failure, so the test that matters is "nothing of
v1 is missing from v2" -- and that the gallery's default is still v1."""
import re

import pytest

from app import prompts
from app.config import settings


def _v1(monkeypatch, **kw):
    monkeypatch.setattr(settings, "prompt_layout", "v1")
    return prompts.build_instructions("Embassy World", **kw)


def _v2(monkeypatch, **kw):
    monkeypatch.setattr(settings, "prompt_layout", "v2")
    return prompts.build_instructions("Embassy World", **kw)


def test_the_default_layout_is_v1():
    assert settings.prompt_layout == "v1"


SECTION_LABELS = {"กฎการสนทนา:", "ข้อห้าม:", "วิธีนำเสนอ:", "จอแสดงสไลด์:",
                  "ข้อมูลตัวหุ่น Emma ที่ตรวจสอบแล้ว:", "บนหน้าหุ่นคุณได้ยินทั้งห้อง:"}


def _sentences(prompt: str) -> list[str]:
    out = []
    for line in prompt.splitlines():
        line = line.strip()
        if not line or line in SECTION_LABELS or line.startswith("## "):
            continue
        line = re.sub(r"^(- |\d+\. )", "", line)
        out.append(line)
    return out


@pytest.mark.parametrize("robot_face", [False, True])
def test_v2_keeps_every_v1_sentence(monkeypatch, robot_face):
    v1 = _v1(monkeypatch, robot_face=robot_face)
    v2 = _v2(monkeypatch, robot_face=robot_face)
    lights = prompts._tool_visible("set_lights") or prompts._tool_visible("set_air_conditioner")
    missing = []
    for sentence in _sentences(v1):
        if sentence.startswith("ควบคุมไฟ/แอร์") and not lights:
            continue                       # the v1 defect v2 fixes (below)
        if sentence.startswith("บุคลิก: พนักงานต้อนรับมืออาชีพ"):
            # v1's one self-contradiction, resolved: the owner made her the
            # host, "เจ้าบ้าน ไม่ใช่พนักงานต้อนรับ" (11 Sep, CLAUDE.md) -- the
            # host block's identity line says so and stays
            assert "เป็นเจ้าบ้านของโครงการ" in v2
            continue
        # v2 may put a label in front of a bullet ("ราคา: ...") -- the
        # sentence itself must be there, whole
        body = sentence.split(": ", 1)[1] if sentence.startswith(("ราคา:", "ตัวตนและน้ำเสียง:")) else sentence
        if body not in v2:
            missing.append(sentence[:80])
    assert not missing, missing


def test_v2_puts_the_every_turn_rules_first_and_the_facts_last_in_a_tag(monkeypatch):
    v2 = _v2(monkeypatch, robot_face=True)
    core = v2.index("# กฎหลัก")
    # the tag's own line, not core rule 2 that names it
    assert core < v2.index("# ขอบเขต") < v2.index("# ตัวตนและวิธีนำเสนอ") < v2.rindex("<ข้อมูลโครงการ>")
    assert v2.index("# บนหน้าหุ่น") < v2.index("# ขอบเขต")       # room chatter is not "off-topic"
    assert v2.rstrip().endswith("</ข้อมูลโครงการ>")
    assert "## ข้อมูลโครงการ" not in v2                            # one label, the tag
    for n in range(1, 7):
        assert "\n%d. " % n in v2[core:v2.index("# บนหน้าหุ่น")]


def test_v2_names_light_and_ac_only_with_their_tools(monkeypatch):
    seen = {"set_lights": False, "set_air_conditioner": False}
    monkeypatch.setattr(prompts, "_tool_visible", lambda name: seen.get(name, False))
    assert "ควบคุมไฟ/แอร์" not in _v2(monkeypatch, robot_face=True)
    seen["set_lights"] = True
    assert "ควบคุมไฟ/แอร์ด้วยเครื่องมือทันที" in _v2(monkeypatch, robot_face=True)


def test_v2_only_changes_the_condo_profile(monkeypatch):
    for profile in ("joke", "emma", "translator"):
        assert _v1(monkeypatch, profile=profile) == _v2(monkeypatch, profile=profile)
