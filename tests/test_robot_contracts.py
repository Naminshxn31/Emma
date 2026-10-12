"""The shared ids and codes (design §2.0), and the heard-word rule that lives
with its table.

These constants are copied into Java (P4) and HTML (P5) and checked there
against this module, so the Python side is pinned here first.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app import robot_contracts as rc

ROOT = Path(__file__).resolve().parent.parent


def test_body_vocabulary_is_the_designed_one():
    assert rc.BODY_ACTION_IDS == (
        "head_up", "head_down", "head_left", "head_right", "head_center", "nod",
        "shake_head", "raise_left_arm", "raise_right_arm", "greet_arm", "wave",
        "handshake", "hands_left", "hands_right", "rest", "stop")
    assert len(set(rc.BODY_ACTION_IDS)) == len(rc.BODY_ACTION_IDS)
    for action_id, label, kind in rc.BODY_ACTIONS:
        assert rc.valid_id(action_id)
        assert label.strip() and any("\u0e00" <= ch <= "\u0e7f" for ch in label)
        assert kind in ("head", "arm", "stop")
    assert rc.BODY_KINDS["stop"] == "stop"


def test_every_action_but_stop_is_guarded_and_stop_never_is():
    assert set(rc.BODY_HEARD) == set(rc.BODY_ACTION_IDS) - {"stop"}
    sided = {"head_left", "head_right", "raise_left_arm", "raise_right_arm",
             "hands_left", "hands_right"}
    for action, (verbs, sides) in rc.BODY_HEARD.items():
        assert verbs, action
        assert (sides is not None) == (action in sided), action


def test_bare_nouns_are_never_verbs():
    """ "ลดราคา", "ข้างหน้า" and "ท่าน" must not authorise motion: the nouns
    they share with real commands are not in any verb list."""
    for verbs, _sides in rc.BODY_HEARD.values():
        for bare in ("หน้า", "ท่า", "มือ", "ซ้าย", "ขวา", "ลด"):
            assert bare not in verbs
    assert not set(rc.BODY_HEARD_FILLERS) & {
        v for verbs, _ in rc.BODY_HEARD.values() for v in verbs}


def test_link_codes_exist_for_both_kinds_and_busy_is_the_arms_alone():
    """Review B10: `busy` meant both "the arm is busy" and "a request is
    pending". The link's code is now `request_pending`, in both tables."""
    for table in (rc.BODY_REASONS, rc.NAV_REASONS):
        for code in ("request_pending", "disconnected", "timeout", "bad_request",
                     "not_robot_face", "heard_missing"):
            assert table[code].strip()
    assert "busy" in rc.BODY_REASONS
    assert "busy" not in rc.NAV_REASONS


def test_nav_states_and_activity():
    assert set(rc.NAV_TERMINAL) <= set(rc.NAV_STATES)
    assert rc.NAV_STATES[0] == "idle"
    assert rc.NAV_ACTIVITY == ("idle", "active", "stale", "unknown")
    assert rc.INBOUND_TYPES == ("body_ack", "body_status", "body_caps",
                                "nav_ack", "nav_status", "nav_caps")
    assert rc.RUN_ID_RE == rc.COMMAND_ID_RE
    assert rc.MAX_INBOUND_BYTES == 8192


@pytest.mark.parametrize("value", ["nod", "big_room", "dock", "a1", "x" * 32])
def test_valid_ids(value):
    assert rc.valid_id(value)


@pytest.mark.parametrize("value", [
    "Nod", "a", "1room", "big-room", "big room", "nod\n", "ห้อง", "../x",
    "x" * 33, None, 7, "nod;rm"])
def test_invalid_ids(value):
    assert not rc.valid_id(value)


def test_command_ids():
    assert rc.valid_command_id("0123456789abcdef" * 2)
    assert rc.valid_run_id("f" * 32)
    for bad in ("F" * 32, "f" * 31, "f" * 33, "f" * 32 + "\n", "g" * 32, None):
        assert not rc.valid_command_id(bad)


def test_reason_text_never_speaks_a_raw_code():
    assert rc.body_reason_th("needs_mapping") == rc.BODY_REASONS["needs_mapping"]
    assert rc.body_reason_th("made_up") == rc.BODY_REASONS["bad_request"]
    assert rc.nav_reason_th(None) == rc.NAV_REASONS["bad_request"]


# ---- the heard-word rule (§2d step 4) ----


def _words(text: str) -> list[str]:
    from app.tools.retrieval import tokenize

    return tokenize(text)


@pytest.mark.parametrize("text,action", [
    ("ก้มหน้าหน่อย", "head_down"),
    ("เงยหน้าหน่อย", "head_up"),
    ("พยักหน้า", "nod"),
    ("ส่ายหน้า", "shake_head"),
    ("หันหน้าไปทางซ้าย", "head_left"),
    ("หันขวา", "head_right"),
    ("มองตรงหน่อย", "head_center"),
    ("ช่วยยกแขนซ้ายหน่อยค่ะ", "raise_left_arm"),
    ("ยกแขนขวา", "raise_right_arm"),
    ("ยกมือทักทาย", "greet_arm"),
    ("โบกมือ", "wave"),
    ("จับมือกันไหม", "handshake"),
    ("ขยับมือไปทางซ้าย", "hands_left"),
    ("เลื่อนมือไปทางขวา", "hands_right"),
    ("เอาแขนลง", "rest"),
    ("ท่าพัก", "rest"),
    ("look up please", "head_up"),
    ("shake hands", "handshake"),
    ("raise your left arm", "raise_left_arm"),
])
def test_real_phrasings_pass(text, action):
    assert rc.heard_matches(action, _words(text))


@pytest.mark.parametrize("text", [
    "ลดราคา", "ข้างหน้า", "ยกเลิก", "มือถือ", "ไปไหนมา", "ท่าน", "ลงทะเบียน", "ห้องพัก",
    "stopwatch", "",
])
def test_lookalikes_authorise_nothing(text):
    """Compounds pass only when they split *completely* into a term and
    fillers; ยกเลิก is ยก+เลิก, and เลิก is not a filler."""
    assert [a for a in rc.BODY_HEARD if rc.heard_matches(a, _words(text))] == []


def test_verb_and_side_must_both_match():
    assert not rc.heard_matches("raise_right_arm", _words("ยกแขนซ้าย"))
    assert not rc.heard_matches("raise_left_arm", _words("ก้มหน้าหน่อย"))
    assert not rc.heard_matches("raise_left_arm", _words("ยกแขนได้ไหม"))
    assert not rc.heard_matches("head_right", _words("หันหน้าไปทางซ้าย"))


def test_compound_rule_is_what_lets_glued_words_through():
    """The filler split is load-bearing, not decoration: newmm keeps
    ก้มหน้า as one word, so whole-token matching alone refuses the most
    ordinary command there is."""
    assert _words("ก้มหน้าหน่อย") == ["ก้มหน้า", "หน่อย"]
    original = rc.BODY_HEARD_FILLERS
    try:
        rc.BODY_HEARD_FILLERS = ()
        assert not rc.heard_matches("head_down", _words("ก้มหน้าหน่อย"))
    finally:
        rc.BODY_HEARD_FILLERS = original
    assert rc.heard_matches("head_down", _words("ก้มหน้าหน่อย"))


def test_stop_always_matches_and_unknown_never_does():
    assert rc.heard_matches("stop", [])
    assert not rc.heard_matches("fly", _words("บินไป"))


def test_contracts_and_places_import_with_the_stdlib_only():
    """The robot runs these under a bare Python with no pythainlp, no numpy
    and no dotenv; one third-party import at module top and the first walk
    dies at `import`."""
    code = (
        "import sys, json\n"
        "before = set(sys.modules)\n"
        "import app.robot_contracts, app.robot_places\n"
        "new = sorted(m for m in set(sys.modules) - before\n"
        "             if m.split('.')[0] not in sys.stdlib_module_names)\n"
        "print(json.dumps(new))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", timeout=60, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == [
        "app", "app.robot_contracts", "app.robot_places"]


# ---- review #19/#22: ordinary sentences and negations authorise nothing ----

#: Every sentence the reviews probed with the real tokenizer, each of which
#: authorised a gesture under the first table: guest questions about price,
#: floors and lifts, directions, and Emma's own narration echoed back.
ORDINARY_SENTENCES = [
    "ราคาลงไหม", "ขยับราคาลงได้ไหม", "ขึ้นลงลิฟต์", "ทางลงอยู่ไหน", "Can I rest here",
    "ตรงไปเลย", "ผ่อนตรงไหม", "Where is the shopping center", "Is the lift on the left?",
    "Can we move to the right tower", "มองไปทางซ้ายจะเห็นสระ", "โบกรถ", "ทักทายลูกค้า",
    "ผมจะลงที่ชั้นไหนครับ", "ที่จอดรถลงไปชั้นใต้ดินใช่ไหม", "ขยับไปทางขวาหน่อยค่ะ",
    "ลงมือทำเลย", "turn right at the lobby", "look at the left tower",
]


@pytest.mark.parametrize("text", ORDINARY_SENTENCES)
def test_ordinary_sentences_authorise_no_gesture(text):
    assert [a for a in rc.BODY_HEARD if rc.heard_matches(a, _words(text))] == []


@pytest.mark.parametrize("text,action", [
    ("ไม่ต้องยกแขนซ้ายนะ", "raise_left_arm"),
    ("อย่ายกแขนซ้าย", "raise_left_arm"),
    ("ไม่ยกแขนขวา", "raise_right_arm"),
    ("don't raise your left arm", "raise_left_arm"),
    ("do not wave your hand", "wave"),
    ("ห้ามโบกมือ", "wave"),
])
def test_a_negated_command_authorises_nothing(text, action):
    """Review #22: the guard used to read "ไม่ต้องยกแขนซ้ายนะ" — "don't raise
    your left arm" — as permission to raise it."""
    assert not rc.heard_matches(action, _words(text))
    assert [a for a in rc.BODY_HEARD if rc.heard_matches(a, _words(text))] == []


def test_a_negation_belongs_to_its_own_verb_only():
    """ "Raise the left arm, no need to wave": the arm, not the wave."""
    text = "ยกแขนซ้ายแล้วไม่ต้องโบกมือ"
    assert [a for a in rc.BODY_HEARD if rc.heard_matches(a, _words(text))] == ["raise_left_arm"]


@pytest.mark.parametrize("text,action", [
    ("เอาแขนลง", "rest"), ("พักแขน", "rest"), ("ท่าพัก", "rest"), ("เก็บแขน", "rest"),
    ("ขยับมือไปทางขวา", "hands_right"), ("ยกมือขวา", "raise_right_arm"),
    ("หันซ้าย", "head_left"), ("หน้าตรง", "head_center"), ("หันหน้าตรง", "head_center"),
    ("wave your hand", "wave"), ("turn your head left", "head_left"),
    ("shake your head", "shake_head"),
])
def test_commands_with_their_body_noun_still_pass(text, action):
    assert rc.heard_matches(action, _words(text))


@pytest.mark.parametrize("text", [
    "ขยับไปทางซ้ายมือหน่อย",              # ซ้ายมือ is a side, not a hand
    "Is the lift on the left hand side?",
    "ห้องนี้หันหน้าไปทางขวาไหม",            # which way the unit faces
    "ห้องฝั่งขวามือหันหน้าทางไหน",
    "หันไปทางขวาจะเจอลิฟต์",               # Emma's own directions, echoed
    "มองไปทางซ้ายจะเห็นสระหน้าตึก",
    "look at the left face of the building",
])
def test_sentences_about_the_room_authorise_no_sided_gesture(text):
    """The integrator's check of #19 found these still passing after the
    noun rule, measured with the real tokenizer."""
    assert [a for a in rc.BODY_HEARD if rc.heard_matches(a, _words(text))] == []


@pytest.mark.parametrize("text,action", [
    ("ยกมือซ้าย", "raise_left_arm"), ("ยกมือขวาหน่อย", "raise_right_arm"),
    ("ขยับมือไปทางซ้ายมือ", "hands_left"), ("หันขวา", "head_right"),
    ("หันหน้าไปทางซ้ายหน่อย", "head_left"), ("raise your left hand", "raise_left_arm"),
])
def test_the_scene_and_hand_side_rules_keep_real_commands(text, action):
    assert rc.heard_matches(action, _words(text))


def test_shake_hands_is_a_handshake_and_not_a_head_shake():
    assert [a for a in rc.BODY_HEARD
            if rc.heard_matches(a, _words("shake hands"))] == ["handshake"]


def test_ambiguous_verbs_are_not_enough_alone():
    """The table itself: no bare ลง/ตรง/rest/center/straight is a verb, and
    each action that keeps an ambiguous verb has a noun rule."""
    for verbs, _sides in rc.BODY_HEARD.values():
        for bare in ("ตรง", "straight", "center", "centre", "rest", "พัก"):
            assert bare not in verbs
    for action in ("rest", "raise_left_arm", "raise_right_arm", "hands_left",
                   "hands_right", "greet_arm", "wave", "head_left", "head_right"):
        nouns, free = rc.BODY_HEARD_NOUNS[action]
        assert nouns and set(free) <= set(rc.BODY_HEARD[action][0]), action


# ---- review 2 #12: stop / reaching negation / narration / past / yes-no ----

@pytest.mark.parametrize("text,action", [
    # a stop is not a request for the gesture it stops
    ("หยุดพยักหน้า", "nod"), ("เลิกพยักหน้า", "nod"), ("พอแล้ว ไม่ต้องโบกมือ", "wave"),
    ("หยุดๆ พยักหน้า", "nod"),
    # a negation that reaches across the want/ask chain
    ("ไม่อยากให้ก้มหน้า", "head_down"), ("อย่าให้ก้มหน้า", "head_down"),
    ("ผมไม่ได้สั่งให้ยกแขนซ้าย", "raise_left_arm"), ("ไม่ต้องการให้ยกแขนขวา", "raise_right_arm"),
    ("I won't nod", "nod"), ("I don't want you to nod", "nod"),
    ("I didn't ask you to raise your left arm", "raise_left_arm"),
    # somebody else doing it is narration
    ("เขาพยักหน้าแล้ว", "nod"), ("ลูกค้าส่ายหน้า", "shake_head"),
    ("ผมยกมือขวาแล้ว", "raise_right_arm"), ("เขากำลังโบกมือ", "wave"),
    # the past, and yes/no questions about the gesture
    ("เมื่อกี้หุ่นพยักหน้า", "nod"), ("เมื่อกี๊พยักหน้าทำไม", "nod"),
    ("เคยยกแขนซ้ายไหม", "raise_left_arm"), ("หุ่นพยักหน้าเป็นไหม", "nod"),
    ("พยักหน้าหรือเปล่า", "nod"), ("Did you nod?", "nod"),
    ("มันยกแขนขวาได้ไหม", "raise_right_arm"),
])
def test_stop_negation_narration_and_questions_authorise_nothing(text, action):
    """Review 2 #12, measured with the real tokenizer: each of these
    authorised its gesture under the two-word negator window."""
    assert not rc.heard_matches(action, _words(text))
    assert [a for a in rc.BODY_HEARD if rc.heard_matches(a, _words(text))] == []


@pytest.mark.parametrize("text,action", [
    ("ผมอยากให้พยักหน้า", "nod"),            # ให้ before the verb: a request
    ("หนูยกแขนซ้ายหน่อย", "raise_left_arm"),   # หนู is said *to* the robot too
    ("หุ่นยกแขนซ้ายหน่อย", "raise_left_arm"),
    ("ถ้าไม่รบกวน ช่วยยกแขนซ้ายหน่อย", "raise_left_arm"),   # ไม่ is about รบกวน
    ("ยกแขนซ้ายได้ไหมครับ", "raise_left_arm"),   # ได้ไหม asks politely
    ("จับมือกันไหม", "handshake"),              # กันไหม: shall we
    ("ผมขอจับมือหน่อย", "handshake"),
    ("ผมจับมือได้ไหม", "handshake"),            # done *with* the speaker
    ("หยุดก่อน แล้วยกแขนซ้าย", "raise_left_arm"),
    ("Can you raise your right arm?", "raise_right_arm"),
    ("พยักหน้าหน่อยครับ", "nod"),
])
def test_requests_next_to_those_words_still_pass(text, action):
    assert rc.heard_matches(action, _words(text))


# ---- review 2 #13: natural phrasings the guard refused ----

@pytest.mark.parametrize("text,action", [
    ("มองตรงๆ หน่อย", "head_center"),   # R-body-head_center-th, refused live on 9 Oct
    ("มองตรงๆ", "head_center"), ("หน้าตรงๆ", "head_center"), ("หันหน้าตรงๆ", "head_center"),
    ("มองหน้าตรงๆ", "head_center"),
    ("ก้มลงหน่อย", "head_down"), ("ก้มลง", "head_down"),
    ("เอามือลง", "rest"), ("เอามือลงได้แล้ว", "rest"), ("put your arms down", "rest"),
])
def test_natural_phrasings_pass(text, action):
    assert rc.heard_matches(action, _words(text))


def test_lng_glues_to_head_down_only():
    """ลง is a piece for ก้ม alone: as a global filler it would bring back
    "ราคาลง" and "ลงมือ", which the design rejected."""
    assert "ลง" not in rc.BODY_HEARD_FILLERS
    assert set(rc.BODY_HEARD_GLUE) == {"head_down"}
    for text in ("ราคาลงไหม", "ลงมือทำเลย", "ลงทะเบียน"):
        assert [a for a in rc.BODY_HEARD if rc.heard_matches(a, _words(text))] == [], text


def test_every_thai_term_survives_the_tokenizer_unchanged():
    """retrieval.tokenize rewrites ำ as ํ+า; heard_matches compares a table
    term with the tokens as they are, so a term spelled with ำ (or anything
    the tokenizer normalises) would silently never match."""
    from app.tools.retrieval import tokenize

    terms = {t for vs, ss in rc.BODY_HEARD.values() for t in vs + (ss or ())}
    terms |= set(rc.BODY_HEARD_NEGATORS) | set(rc.BODY_HEARD_REACH) | set(
        rc.BODY_HEARD_NARRATORS) | set(rc.YES_NO_TAILS) | set(rc.PARTICLES) | set(rc._ASPECT)
    for term in terms:
        if any("\u0e00" <= ch <= "\u0e7f" for ch in term):
            assert "".join(tokenize(term)) == term, term
