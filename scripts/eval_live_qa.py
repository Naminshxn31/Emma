#!/usr/bin/env python3
"""
Ask the real Emma every question we can think of, and record what she says.

    .venv/Scripts/python.exe scripts/eval_live_qa.py                    # scenarios + FAQ
    .venv/Scripts/python.exe scripts/eval_live_qa.py --suite scenarios
    .venv/Scripts/python.exe scripts/eval_live_qa.py --suite faq --limit 20
    .venv/Scripts/python.exe scripts/eval_live_qa.py --ids S04-th-1,S12-th-1
    .venv/Scripts/python.exe scripts/eval_live_qa.py --resume data/eval-runs/live-qa-....jsonl
    .venv/Scripts/python.exe scripts/eval_live_qa.py --suite robot --profile joke --concurrency 1
    .venv/Scripts/python.exe scripts/eval_live_qa.py --suite robot --model gemini-3.8-live-extended-thinking
    .venv/Scripts/python.exe scripts/eval_live_qa.py --suite robot --check   # offline: validate, no session

Why
---
`scripts/eval_search.py` measures slide search, and `app/emma_chat.py` is a
*local* model wearing Emma's prompt. Neither answers the owner's question —
"does the model we built answer, is it right, how much does it cover" —
because the thing that answers customers is the Gemini Live session, with
the condo prompt, the approved facts and the real tools. This drives exactly
that: `build_instructions(profile="condo")` + `GeminiProvider` + the real
tool registry, one fresh Live session per case, greeting first (as in the
gallery), then the guest's turns as text.

What it does NOT measure: the ear. Turns go in as text, so speech
recognition, VAD and the mic chain are out of scope — a case that passes
here can still fail when "Embassy" is heard as "empathy". The scenario file
has mishearing cases written as the *transcript* the model would receive.

Safety — nothing physical moves
-------------------------------
The condo tool set includes the IR blaster, the robot chassis and the arm.
Every one of them is stubbed before the first session opens: the robot
answers "mock" (exactly what the gallery gets when no robot is connected),
the IR hub answers "mock", the handshake never reaches the arm, and no
browser window can launch. Read-only tools run for real: approved facts,
the slide catalog, and the live sales inventory (read-only Supabase).
The turn log is switched off — test traffic is not guest traffic.

Output: one JSON line per case in data/eval-runs/ (gitignored), with every
turn's transcript, audio length, tool calls with arguments and results, and
heuristic flags. Grading against the reference answers is a separate step.

The robot suite (design docs/emma-body-nav-design-2026-10-08.md §5 P6)
---------------------------------------------------------------------
`--suite robot` runs data/eval/qa_live_robot.json on the robot face with the
body and walk switches on — in this process only, never in .env. The one
path to the robot, `robot_face_link.request`, is replaced by a script: each
case says what the "robot" answers (accepted / a refusal code / timeout), and
every command the tools would have sent is recorded with that answer. A stop
is always accepted, as on the robot. The arm's serial/adb path refuses
outright. Each guest turn is also recorded as the face's caption after an
activity mark — what local VAD + the recogniser would produce if they heard
it perfectly — so the heard guard sees what it would see in the room.

Every run also writes, per turn, the gap from a `turn_complete` to the tool
call that follows it (gemini-3.8 sends turn_complete *before* the call in
13/15 turns, 8 Oct), and `<run>.summary.json` with the p95 of those gaps as
the *proposed* ANNOUNCE_SETTLE_S, a reading on TURN_IDLE_TRACKS_TOOLS, and the
Live usage reports. Proposed, never applied: both stay at their off defaults
until the owner sets them.

The content suite (docs/robot-content-review-2026-10-09.md)
-----------------------------------------------------------
`--suite content` runs data/eval/qa_live_robot_content.json: what EmmaJoke
SAYS on the robot face, set up the way the server on the robot ran on 9 Oct
(smarthome + robot tools seen by joke, smart home in mock, routes on, voice
walks off, body off, no greeting — a camera-opened call has none). Same
scripted robot as the robot suite; nothing reaches a robot. Every sentence in
the file is written for the suite, by category — never a guest's words. Its
own flags: owner_address, filler_offer, too_long, wrong_lang,
denies_language, mode_switch_repeated, plus the per-turn regexes.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import contextvars
import datetime as dt
import json
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCENARIOS = ROOT / "data" / "eval" / "qa_live_scenarios.json"
ROBOT_SCENARIOS = ROOT / "data" / "eval" / "qa_live_robot.json"
CONTENT_SCENARIOS = ROOT / "data" / "eval" / "qa_live_robot_content.json"
FAQ = (ROOT / "data" / "personal-docs" / "embassy-world" / "project-report-2026-10-06"
       / "19_faq.md")
OUT_DIR = ROOT / "data" / "eval-runs"

logger = logging.getLogger("eval_live_qa")

#: The case currently running in this task. Tool dispatch reads it to file
#: each call under the right case — several sessions run at once, and the
#: provider's tool task inherits the context of the case that opened it.
_case_log: contextvars.ContextVar[list | None] = contextvars.ContextVar("case_log", default=None)
#: What the scripted robot answers in the case currently running, per kind.
_case_acks: contextvars.ContextVar[dict | None] = contextvars.ContextVar("case_acks", default=None)


# ---------------------------------------------------------------- cases

def parse_faq(path: Path) -> list[dict]:
    """The report's FAQ chapter as cases: **ถาม:**/**ตอบ:** and **Q:**/**A:**.

    Built at run time and never written to the repo: the report lives in
    data/personal-docs/ (gitignored, copied to the gallery machine by hand).
    """
    if not path.exists():
        return []
    cases: list[dict] = []
    section = ""
    question = None
    lang = "th"
    counter: dict[str, int] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            section = line[3:].strip()
            if section.lower().startswith("english"):
                lang = "en"
            continue
        m = re.match(r"^\*\*(ถาม|Q):\*\*\s*(.+?)\s*$", line)
        if m:
            question = m.group(2).strip()
            continue
        m = re.match(r"^\*\*(ตอบ|A):\*\*\s*(.+?)\s*$", line)
        if m and question:
            key = section.split()[0] if section else "x"
            if lang == "en":
                key = "EN"
            counter[key] = counter.get(key, 0) + 1
            cases.append({
                "id": f"F{key}-{counter[key]:02d}",
                "suite": "faq",
                "cat": section,
                "lang": lang,
                "turns": [question],
                "reference": m.group(2).strip(),
            })
            question = None
    return cases


def load_cases(suite: str, faq_path: Path) -> list[dict]:
    cases: list[dict] = []
    if suite in ("scenarios", "all"):
        data = json.loads(SCENARIOS.read_text(encoding="utf-8"))
        for case in data["cases"]:
            case.setdefault("suite", "scenarios")
            cases.append(case)
    if suite in ("faq", "all"):
        faq = parse_faq(faq_path)
        if not faq:
            print(f"!! no FAQ at {faq_path} — the FAQ suite is NOT being measured", file=sys.stderr)
        cases.extend(faq)
    # Not part of "all": the robot suite turns the body/walk switches on and
    # changes the profile's default, which would change what the sales-room
    # scenarios measure if they shared a run.
    if suite == "robot":
        data = json.loads(ROBOT_SCENARIOS.read_text(encoding="utf-8"))
        problems = validate_robot_cases(data)
        if problems:
            raise ValueError("qa_live_robot.json: " + "; ".join(problems))
        for case in data["cases"]:
            case.setdefault("suite", "robot")
            cases.append(case)
    # Not part of "all" either: it measures the robot face's EmmaJoke with the
    # robot server's switches, not the sales room.
    if suite == "content":
        data = json.loads(CONTENT_SCENARIOS.read_text(encoding="utf-8"))
        problems = validate_content_cases(data)
        if problems:
            raise ValueError("qa_live_robot_content.json: " + "; ".join(problems))
        for case in data["cases"]:
            case.setdefault("suite", "content")
            cases.append(case)
    return cases


#: The answers a scripted robot can give: accepted, timeout, or a refusal
#: code of that kind (the same tables the real link sanitises acks against,
#: so a case cannot script a reason no robot would ever send).
def _ack_choices(kind: str) -> set[str]:
    from app import robot_contracts as rc

    table = rc.BODY_REASONS if kind == "body" else rc.NAV_REASONS
    return {"accepted", "timeout"} | set(table)


#: Settings a case may override, and only these: the switches whose *off*
#: behaviour is itself under test (voice_disabled), never a safety flag of
#: the harness (the stubs, the face token).
_CASE_SETTINGS = {"robot_nav_voice", "robot_body_enabled", "robot_nav_routes_enabled",
                  "robot_nav_voice_unverified"}
_EXPECT_KEYS = {"tools_any", "tools_none", "args", "face_any", "face_none",
                "said_none", "said_any"}
_FACE_REF = re.compile(r"^(body|nav)(:[a-z][a-z0-9_]*(:[a-z][a-z0-9_]*)?)?$")


def validate_robot_cases(data: dict) -> list[str]:
    """Every problem with the robot scenario file, by case id. Empty = valid.

    Checked offline (tests and `--check`) because a malformed case would
    otherwise surface two hours into a paid live run, or worse, run and
    measure nothing: a `face_none` with a typo'd action never fires.
    """
    from app import robot_contracts as rc

    problems: list[str] = []
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list) or not cases:
        return ["no cases"]
    seen: set[str] = set()
    for case in cases:
        cid = case.get("id") if isinstance(case, dict) else None
        if not isinstance(cid, str) or not cid.startswith("R"):
            problems.append(f"{cid!r}: id must be a string starting with R")
            continue
        if cid in seen:
            problems.append(f"{cid}: duplicate id")
        seen.add(cid)
        if not case.get("cat") or not case.get("rubric"):
            problems.append(f"{cid}: needs cat and rubric")
        turns = case.get("turns")
        if not isinstance(turns, list) or not turns or not all(
                isinstance(t, str) and t.strip() for t in turns):
            problems.append(f"{cid}: turns must be non-empty strings")
        for kind, answer in (case.get("acks") or {}).items():
            if kind not in ("body", "nav") or answer not in _ack_choices(kind):
                problems.append(f"{cid}: ack {kind}={answer!r} is not a scripted answer")
        for key in case.get("settings") or {}:
            if key not in _CASE_SETTINGS:
                problems.append(f"{cid}: setting {key} may not be overridden by a case")
        expect = case.get("expect") or {}
        for key in expect:
            if key not in _EXPECT_KEYS:
                problems.append(f"{cid}: unknown expect key {key}")
        for ref in expect.get("face_any", []) + expect.get("face_none", []):
            m = _FACE_REF.match(ref) if isinstance(ref, str) else None
            if not m:
                problems.append(f"{cid}: face ref {ref!r} is not kind[:action[:place]]")
                continue
            parts = ref.split(":")
            if len(parts) > 1:
                actions = rc.BODY_ACTION_IDS if parts[0] == "body" else ("go", "stop")
                if parts[1] not in actions:
                    problems.append(f"{cid}: {ref!r} names no {parts[0]} action")
        for pattern in expect.get("said_none", []) + expect.get("said_any", []):
            try:
                re.compile(pattern)
            except (re.error, TypeError):
                problems.append(f"{cid}: bad regex {pattern!r}")
        for name, args in (expect.get("args") or {}).items():
            if not isinstance(args, dict):
                problems.append(f"{cid}: args for {name} must be an object")
            elif name == "move_body" and args.get("action") not in rc.BODY_ACTION_IDS:
                problems.append(f"{cid}: move_body action {args.get('action')!r} unknown")
        # Review 2 #13: tools_any/args see the *call*, and a call the heard
        # guard refuses is still a call. Only face_any sees whether a
        # command reached the robot, so a case that expects a motion must
        # say which one — or a refused gesture passes with flags=[].
        face_any = [r for r in expect.get("face_any", []) if isinstance(r, str)]
        motion = ((expect.get("args") or {}).get("move_body") or {}).get("action")
        if not isinstance(motion, str) or motion == "stop":
            motion = None
        wants_motion = motion is not None or bool(
            set(expect.get("tools_any", [])) & {"move_body", "go_to_place", "return_to_base"})
        if wants_motion and not face_any:
            problems.append(f"{cid}: expects a motion but has no face_any "
                            "(a heard-guard refusal would pass silently)")
        elif motion is not None and not any(_ref_matches(f"body:{motion}", r) for r in face_any):
            problems.append(f"{cid}: face_any does not cover body:{motion}")
    return problems


_CONTENT_EXPECT_KEYS = {"tools_none", "face_none", "said_none", "said_any", "mode_switch_max"}
_TURN_EXPECT_KEYS = {"max_chars", "lang", "said_none", "said_any", "silent_ok", "filler_ok"}
_LANGS = {"th", "en", "he", "zh"}


def validate_content_cases(data: dict) -> list[str]:
    """Every problem with the content scenario file, by case id. Empty = valid.

    Offline for the same reason as the robot file: a typo'd key or a regex
    that never compiles would otherwise run, cost a Live session, and flag
    nothing — a pass that measured nothing.
    """
    problems: list[str] = []
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list) or not cases:
        return ["no cases"]
    seen: set[str] = set()
    for case in cases:
        cid = case.get("id") if isinstance(case, dict) else None
        if not isinstance(cid, str) or not cid.startswith("C"):
            problems.append(f"{cid!r}: id must be a string starting with C")
            continue
        if cid in seen:
            problems.append(f"{cid}: duplicate id")
        seen.add(cid)
        if not case.get("cat") or not case.get("rubric"):
            problems.append(f"{cid}: needs cat and rubric")
        for key in case:
            if key not in {"id", "cat", "lang", "turns", "expect", "turn_expect", "rubric",
                           "greeting", "suite"}:
                problems.append(f"{cid}: unknown key {key}")
        turns = case.get("turns")
        if not isinstance(turns, list) or not turns or not all(
                isinstance(t, str) and t.strip() for t in turns):
            problems.append(f"{cid}: turns must be non-empty strings")
            turns = []
        expect = case.get("expect") or {}
        patterns = list(expect.get("said_none", [])) + list(expect.get("said_any", []))
        for key in expect:
            if key not in _CONTENT_EXPECT_KEYS:
                problems.append(f"{cid}: unknown expect key {key}")
        for ref in expect.get("face_none", []):
            if not isinstance(ref, str) or not _FACE_REF.match(ref):
                problems.append(f"{cid}: face ref {ref!r} is not kind[:action[:place]]")
        per_turn = case.get("turn_expect") or []
        if not isinstance(per_turn, list) or len(per_turn) > len(turns):
            problems.append(f"{cid}: turn_expect must be a list no longer than turns")
            per_turn = []
        for n, te in enumerate(per_turn):
            if not isinstance(te, dict):
                problems.append(f"{cid}: turn_expect[{n}] must be an object")
                continue
            for key in te:
                if key not in _TURN_EXPECT_KEYS:
                    problems.append(f"{cid}: turn_expect[{n}] unknown key {key}")
            if "lang" in te and te["lang"] not in _LANGS:
                problems.append(f"{cid}: turn_expect[{n}] lang {te['lang']!r} not in {sorted(_LANGS)}")
            if "max_chars" in te and not (isinstance(te["max_chars"], int) and te["max_chars"] > 0):
                problems.append(f"{cid}: turn_expect[{n}] max_chars must be a positive int")
            patterns += list(te.get("said_none", [])) + list(te.get("said_any", []))
        for pattern in patterns:
            try:
                re.compile(pattern)
            except (re.error, TypeError):
                problems.append(f"{cid}: bad regex {pattern!r}")
    return problems


#: Calling the person in front the owner, the boss or "ท่าน", or calling
#: itself someone's personal assistant. On 9 Oct the robot face (EmmaJoke)
#: did it in 17 of 397 replies to visitors and staff — its introduction was
#: the owner's-assistant one ("คนที่คุยกับคุณคือเจ้าของ").
_OWNER_ADDRESS = re.compile(
    r"เจ้าของ|บอส|ท่านประธาน|ผู้ช่วยส่วนตัว|รับใช้|personal assistant|assistente pessoal|"
    r"\bmy owner\b|\bboss\b", re.IGNORECASE)
#: A loose offer tacked on after the answer — the condo prompt bans it by
#: quotation and lands at 4% of replies; EmmaJoke had no such line and ended
#: 34% (robot, 9 Oct) to 45% (PC, 22 Sep-8 Oct) of replies with one. Wider
#: than _FILLER: "บอกได้เลยนะคะ" and "อยากคุยเรื่องอะไรอีก" are the shapes
#: the joke log is full of.
_OFFER = re.compile(
    r"(มีอะไร.{0,20}(ช่วย|ดูแล|รับใช้|เล่า|คุย)|บอก.{0,12}ได้(เลย|ตลอด|เสมอ)"
    r"|เรียก(ใช้)?ได้(ตลอด|เสมอ)|ถามได้(เลย|ตลอด)|อยาก(คุย|ชวน|เล่า|ให้|ฟัง).{0,40}(ไหม|มั้ย|ดีคะ)"
    r"|anything (else )?(i can|to help)|how can i help|can i help|what would you like"
    r"|i'?m here (if|whenever)|let me know"
    r"|איך אני יכולה לעזור|אם תצטרך|יש משהו|有什么.{0,8}(帮|需要)|Wie kann ich)",
    re.IGNORECASE)
_MODE_SWITCH = re.compile(r"หน้าอก|สลับ.{0,4}โหมด|แตะไฟ|chest|בחזה|switch (to )?(the )?(emma )?mode",
                          re.IGNORECASE)
_DENIES_LANGUAGE = re.compile(r"ไม่เข้าใจภาษา|לא מבינה|don'?t (understand|speak) (hebrew|that)",
                              re.IGNORECASE)
_SCRIPTS = {"th": re.compile(r"[฀-๿]"), "he": re.compile(r"[֐-׿]"),
            "zh": re.compile(r"[一-鿿]"), "en": re.compile(r"[A-Za-z]")}


def main_script(text: str) -> str | None:
    """The writing system most of `text` is in (th/he/zh/en), None for none."""
    counts = {k: len(p.findall(text or "")) for k, p in _SCRIPTS.items()}
    best = max(counts, key=counts.get)
    return best if counts[best] else None


def content_flags(case: dict, turns: list[dict]) -> list[str]:
    """Flags for the content suite. Conditions on the record, per turn."""
    flags: list[str] = []
    expect = case.get("expect") or {}
    per_turn = case.get("turn_expect") or []
    switched = 0
    for i, turn in enumerate(turns):
        te = per_turn[i] if i < len(per_turn) else {}
        said = (turn.get("said") or "").strip()
        if _OWNER_ADDRESS.search(said):
            flags.append(f"t{i}:owner_address")
        if said and not te.get("filler_ok") and _OFFER.search(said[-90:]):
            flags.append(f"t{i}:filler_offer")
        if te.get("max_chars") and len(said) > te["max_chars"]:
            flags.append(f"t{i}:too_long={len(said)}>{te['max_chars']}")
        if said and te.get("lang") and main_script(said) != te["lang"]:
            flags.append(f"t{i}:wrong_lang={main_script(said)}")
        if _DENIES_LANGUAGE.search(said):
            flags.append(f"t{i}:denies_language")
        if _MODE_SWITCH.search(said):
            switched += 1
        for pattern in te.get("said_none", []):
            if re.search(pattern, said, re.IGNORECASE):
                flags.append(f"t{i}:said_forbidden={pattern}")
        for pattern in te.get("said_any", []):
            if not re.search(pattern, said, re.IGNORECASE):
                flags.append(f"t{i}:said_missing={pattern}")
    if "mode_switch_max" in expect and switched > expect["mode_switch_max"]:
        flags.append(f"mode_switch_repeated={switched}>{expect['mode_switch_max']}")
    return flags


def _silent_ok_turns(case: dict) -> set[int]:
    return {i for i, te in enumerate(case.get("turn_expect") or [])
            if isinstance(te, dict) and te.get("silent_ok")}


def _robot_places():
    """The places table the robot suite's walk cases name (floor2.json unless
    ROBOT_NAV_PLACES_FILE says otherwise), or None."""
    from app import robot_places
    from app.config import settings

    path = Path((settings.robot_nav_places_file or "").strip() or "data/robot-places/floor2.json")
    if not path.is_absolute():
        path = ROOT / path
    try:
        return robot_places.load(path)
    except (OSError, ValueError):
        return None


def _nav_guard_passes(text: str, place, places, *, dock: bool) -> bool:
    """`robot_nav._heard_place` itself, on a throwaway session holding one
    fresh caption — the layer a guest meets, not a copy of its rule."""
    import time as _time

    from app import heard, robot_nav

    session = object()
    heard._CAPTIONS[id(session)] = (session, heard.Caption(text=text, at=_time.monotonic()))
    try:
        verdict = robot_nav._heard_place(session, place, places, dock=dock)
    finally:
        heard.forget(session)
    # (ok, why) since review 2 (#18 follow-up); a bare bool before. A tuple
    # is always truthy, so reading it as a bool would pass every caption.
    return bool(verdict[0] if isinstance(verdict, tuple) else verdict)


def _body_guard_passes(text: str, action: str) -> bool:
    """The body guard's word rule as `robot_body.heard_ok` applies it."""
    from app import robot_body, robot_contracts as rc

    return rc.heard_matches(action, robot_body._heard_words(text))


def guard_problems(cases: list[dict]) -> list[str]:
    """Run the real heard guards over every robot case, offline.

    A case that expects a motion (face_any body:X / nav:go:P) must have a
    turn the guard authorises — otherwise the paid run measures a
    heard_missing refusal and calls it the model's answer (review 2 #13:
    "มองตรงๆ หน่อย" for head_center). And a face_none of a case without
    setting overrides must hold at the guard too: those sentences are the
    guard's own regression list (R-heard-*). Overridden cases (voice off)
    are refused by a switch, not by the guard, so they are skipped.
    """
    from app import robot_contracts as rc, robot_nav

    places = _robot_places()
    problems: list[str] = []
    for case in cases:
        cid = case.get("id")
        turns = case.get("turns") or []
        expect = case.get("expect") or {}

        def nav_ok(place_id: str | None) -> bool:
            if places is None:
                return False
            dock = robot_nav._dock(places)
            targets = [places.place(place_id)] if place_id else list(places.places)
            return any(_nav_guard_passes(t, p, places, dock=dock is not None and p.id == dock.id)
                       for p in targets if p is not None for t in turns)

        def body_ok(action: str | None) -> bool:
            actions = [action] if action else list(rc.BODY_HEARD)
            return any(_body_guard_passes(t, a) for a in actions for t in turns)

        for ref in expect.get("face_any", []):
            parts = ref.split(":")
            if parts[0] == "body" and len(parts) > 1 and parts[1] != "stop" and not body_ok(parts[1]):
                problems.append(f"{cid}: the heard guard refuses {ref} for every turn")
            if parts[:2] == ["nav", "go"] and not nav_ok(parts[2] if len(parts) > 2 else None):
                problems.append(f"{cid}: the heard guard refuses {ref} for every turn"
                                + ("" if places else " (no places file)"))
        if case.get("settings"):
            continue
        for ref in expect.get("face_none", []):
            parts = ref.split(":")
            if parts[0] == "body" and (len(parts) == 1 or parts[1] != "stop") and body_ok(
                    parts[1] if len(parts) > 1 else None):
                problems.append(f"{cid}: the heard guard would allow {ref}")
            if parts[0] == "nav" and parts[1:2] in ([], ["go"]) and nav_ok(
                    parts[2] if len(parts) > 2 else None):
                problems.append(f"{cid}: the heard guard would allow {ref}")
    return problems


# ---------------------------------------------------------------- safety

def make_safe() -> None:
    """Stub every tool that touches the room. Called once, before any session."""
    from app.config import settings

    settings.turn_log = False          # test traffic is not guest traffic
    settings.robot_enabled = False     # robot_link.send -> "mock"
    # Measure the configured model or nothing. The first full run hit the
    # free-tier limit and one session quietly continued on the 2.5 fallback —
    # a score for a model nobody configured, paid for out of the quota the
    # gallery falls back on when its own model is unavailable.
    settings.gemini_model_fallback = ""

    from app import robot_chassis

    robot_chassis.configured = lambda: False
    robot_chassis.connected = lambda: False

    from app.tools import broadlink_ir

    broadlink_ir.send = lambda *a, **k: "mock"
    broadlink_ir.available = lambda: False

    from app.tools import canva_display, webstage

    async def refuse(*_a, **_k):
        raise RuntimeError("eval_live_qa never opens a browser window")

    canva_display._launch_browser = refuse
    webstage._launch_browser = refuse

    def record_stage(url: str) -> None:
        log = _case_log.get()
        if log is not None:
            log.append({"screen_web": url})

    webstage.request = record_stage

    from app import robot_arm

    async def no_arm(*_a, **_k) -> bool:
        return True   # the same "requested" the gallery gets; nothing moves

    robot_arm.greet = no_arm

    # Below greet, the arm's own writers: the serial line and adb. This
    # machine has every ROBOT_ARM_* flag on and an adb serial set, and the
    # robot suite turns the body interlock on in-process — a gesture tool
    # that ever fell back to the serial path (design §4, "two writers") must
    # meet a refusal here, not the servo board.
    async def no_port(*_a, **_k):
        raise RuntimeError("eval_live_qa never touches the arm port")

    for name in ("_write", "_run"):
        if hasattr(robot_arm, name):
            setattr(robot_arm, name, no_port)

    # The only path from a tool to the robot face (design §2a). Replaced for
    # every suite: with no face connected the real one answers not_robot_face,
    # and in the robot suite the scripted answers below are the robot.
    from app import robot_face_link

    global _REAL_REQUEST
    if robot_face_link.request is not scripted_request:
        _REAL_REQUEST = robot_face_link.request
    robot_face_link.request = scripted_request

    # Record every call with its ARGUMENTS — the events stream only carries
    # names, and "find_units(max_price=5,000,000)" vs "find_units()" is the
    # difference between right and wrong for half the inventory cases.
    from app import tools

    real = tools.dispatch_all

    async def recording_dispatch(calls):
        results = await real(calls)
        log = _case_log.get()
        if log is not None:
            by_id = {cid: (name, args) for cid, name, args in calls}
            for cid, name, result in results:
                _n, args = by_id.get(cid, (name, {}))
                log.append({"name": name, "args": args, "result": result})
        return results

    tools.dispatch_all = recording_dispatch


_REAL_REQUEST = None


async def scripted_request(session, kind: str, payload: dict, timeout: float) -> dict:
    """The robot face, as the case scripts it — and a record of every command.

    Outside the robot suite (no script set) the real link answers, which with
    no claimed face is `not_robot_face` without sending anything. Inside it,
    the request is validated by the same rules as the real link first (so a
    malformed command is `bad_request` here as there), then answered:
    `stop` always accepted (on the robot a stop is never refused, §2b);
    otherwise the case's answer for that kind, default accepted.
    """
    import secrets

    from app import robot_contracts as rc

    acks = _case_acks.get()
    if acks is None:
        return await _REAL_REQUEST(session, kind, payload, timeout)
    action = payload.get("action") if isinstance(payload, dict) else None
    entry = {"face_request": kind, "action": action,
             "place": payload.get("place") if isinstance(payload, dict) else None}
    valid = (kind == "body" and action in rc.BODY_ACTION_IDS) or (
        kind == "nav" and (action == "stop" or (action == "go" and rc.valid_id(payload.get("place")))))
    command_id = secrets.token_hex(16)
    answer = "accepted" if action == "stop" else acks.get(kind, "accepted")
    if not valid:
        answer = "bad_request"
    if answer == "timeout":
        await asyncio.sleep(timeout)
        ack = {"accepted": False, "reason": "timeout", "command_id": command_id}
    else:
        ack = {"accepted": answer == "accepted",
               "reason": None if answer == "accepted" else answer,
               "command_id": command_id, "action": action}
        if kind == "body":
            ack["status"] = ("needs_mapping" if answer == "needs_mapping"
                             else "verified" if answer == "accepted" else None)
            ack["est_ms"] = 3000 if answer == "accepted" else None
        else:
            ack["run_id"] = secrets.token_hex(16) if answer == "accepted" and action == "go" else None
    entry["ack"] = answer
    log = _case_log.get()
    if log is not None:
        log.append(entry)
    return ack


#: The robot suite's switches, set in this process only (never .env). Two
#: keys on the server as in the design, plus the arm interlock the body gate
#: checks (§2d step 2) — safe here because the face link is scripted and the
#: arm's writers refuse (make_safe). Voice walks `on` and unverified legs
#: allowed so the model's side of a walk is measured at all; the case
#: R-nav-voice-off puts the default back to measure voice_disabled.
ROBOT_SUITE_SETTINGS = {
    "tool_groups": "smarthome,robot,body",
    "robot_body_enabled": True,
    "robot_arm_enabled": True,
    "robot_arm_motion_enabled": True,
    "robot_arm_groups_enabled": True,
    "robot_body_require_heard": True,
    "robot_nav_routes_enabled": True,
    "robot_nav_require_heard": True,
    "robot_nav_voice": "on",
    "robot_nav_voice_unverified": True,
    "robot_face_token": "",
}


def make_robot_suite() -> list[str]:
    """Switch the robot suite on in this process; return what was set (for
    the run header) and warn loudly about anything that makes it measure less."""
    from app.config import settings

    for key, value in ROBOT_SUITE_SETTINGS.items():
        setattr(settings, key, value)
    notes = ["%s=%s" % kv for kv in ROBOT_SUITE_SETTINGS.items()]
    places = Path(settings.robot_nav_places_file or "")
    if not places.is_absolute():
        places = ROOT / places
    if not settings.robot_nav_places_file or not places.exists():
        print(f"!! no places file at {places} — every walk case measures unknown_place, "
              "NOT a walk", file=sys.stderr)
    import importlib.util

    for module in ("app.tools.body", "app.robot_nav"):
        if importlib.util.find_spec(module) is None:
            print(f"!! {module} is not in this tree — its cases measure the model "
                  "without the tool", file=sys.stderr)
    return notes


#: The content suite's switches: the server on the robot as it ran on 9 Oct
#: (boot line: "body=False nav_routes=True nav_voice=off", tools seen by
#: joke = set_lights, set_air_conditioner, get_room_status, go_to_place,
#: stop_moving, return_to_base, get_robot_status; smart home in mock). Set in
#: this process only, never .env.
CONTENT_SUITE_SETTINGS = {
    "tool_groups": "smarthome,robot",
    "robot_body_enabled": False,
    "robot_nav_routes_enabled": True,
    "robot_nav_require_heard": True,
    "robot_nav_voice": "off",
    "robot_face_token": "",
}


def make_content_suite() -> list[str]:
    """Switch the content suite's robot-server settings on in this process."""
    from app.config import settings

    for key, value in CONTENT_SUITE_SETTINGS.items():
        setattr(settings, key, value)
    return ["%s=%s" % kv for kv in CONTENT_SUITE_SETTINGS.items()]


_SMARTHOME_BOOT: dict | None = None


def reset_state() -> None:
    import copy

    from app.tools import calc, robot_link, slides, smarthome, units

    global _SMARTHOME_BOOT
    calc.reset()
    slides.reset_state()
    robot_link.reset_state()
    units.reset()
    # Every case starts from a freshly booted server's room state. Left to
    # carry over, "เปิดแอร์" in one case found the AC "on" from the case
    # before and the tool skipped the send — the same skip a booted server
    # makes (its default is on), but it should not depend on case order.
    if _SMARTHOME_BOOT is None:
        _SMARTHOME_BOOT = copy.deepcopy(smarthome.STATE)
    smarthome.STATE.clear()
    smarthome.STATE.update(copy.deepcopy(_SMARTHOME_BOOT))


# ---------------------------------------------------------------- flags

_FILLER = re.compile(
    r"(มีอะไรให้.{0,12}ช่วย|สนใจ.{0,30}ไหม|อยากให้.{0,30}ไหม|อยาก.{0,20}(ดู|ทราบ|รู้).{0,32}ไหม"
    r"|anything else|would you like|do you want|can i help)",
    re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
#: The caveat the owner banned on 2026-10-10 ("อย่าบอกว่าเป็นเพียงแนวคิดอาจจะมี
#: การเปลี่ยนแปลง ห้ามพูดแบบนี้เป็นอันขาด"). Until that day the prompt ordered
#: it on every facility, so a run before then is full of it by design. Same
#: pattern the app cuts from project text (app/tools/retrieval.py), and the
#: same exception: a sentence about price ("ราคาอาจเปลี่ยนตามห้องที่เหลือ") is
#: a fact, not the caveat.
def _concept_caveat(said: str) -> bool:
    from app.tools.retrieval import _PRICE_WORD, _THAI, CONCEPT_CAVEAT

    for sentence in re.split(r"(?<=[.!?])\s+", said or ""):
        # Thai clauses are space-delimited; an English sentence is one piece.
        parts = sentence.split() if _THAI.search(sentence) else [sentence]
        if any(CONCEPT_CAVEAT.search(p) and not _PRICE_WORD.search(p) for p in parts):
            return True
    return False


#: Owner 2026-10-12: "อย่าตอบว่าไม่รู้ ไม่มีในระบบ ให้ตอบแบบอื่นดูเป็นธรรมชาติ
#: ... อย่าให้เหมือนว่าหุ่นไม่มีข้อมูลนี้" — a gap goes to the sales team in
#: so many words, never "I don't have that". ("ไม่ทราบว่า...คะ" is a polite
#: question, so only the flat forms count.)
#: "ไม่มีระบุ/ไม่ได้ระบุ": robot log 15 Oct, "ข้อมูลความลึกของสระว่ายน้ำไม่มี
#: ระบุในระบบค่ะ" (the report has 1.30 m) slipped past every word above.
_SAYS_NO_INFO = re.compile(
    r"(ไม่มีข้อมูล|ไม่มีในระบบ|ไม่มีระบุ|ไม่ได้ระบุ|ไม่ทราบค่ะ|ไม่ทราบข้อมูล|ดิฉันไม่ทราบ|ฉันไม่ทราบ"
    r"|I don'?t have (that |this |any |the |specific )?(information|info|details|data)"
    # robot 20 Oct, English: the gap said as a gap in our records
    r"|I don'?t have the exact|not (specified|mentioned|listed) in (my|our|the) (current )?"
    r"(details|information|data|records|documents)"
    r"|not (in|available in) (my|our) system|I don'?t know)",
    re.IGNORECASE)


#: The question the owner asked for on a broad facility question ("อยาก
#: ฟังส่วนไหนคะ", "Which area would you like to hear about?") is not filler —
#: but only after a broad question: "มีสระไหม ฟิตเนสไหม" answered and then
#: "อยากทราบรายละเอียดส่วนไหนเพิ่มเติมไหมคะ" is the filler it always was.
_BROAD_ASK = re.compile(r"(มีอะไร|อะไรบ้าง|สิ่งอำนวยความสะดวก|facilit|what.{0,20}(have|there))", re.IGNORECASE)
_SECTION_CHOICE = re.compile(r"(ส่วนไหน|ชั้นไหน|โซนไหน|which\s+(floor|area|part|section|zone))", re.IGNORECASE)
#: Owner 12 Oct: after a meaningless line or an off-topic refusal she asks ONE
#: discovery question about the project -- wanted, not filler. Generic
#: invitations ("มีอะไรให้ช่วยไหมคะ") still match _FILLER and are still flagged.
#: Owner 15 Oct: the discovery question is "what may I recommend in the
#: project", never "to live in or to invest" -- that one is flagged now.
_DISCOVERY = re.compile(r"(ห้องแบบไหน|แนะนำส่วนไหน|ชั้นไหน|which (room|unit) type|"
                        r"(like|want) me to (recommend|introduce|show))", re.IGNORECASE)
#: The question the owner removed (15 Oct): a reply that asks it is a fault.
LIVE_OR_INVEST = re.compile(r"(อยู่เองหรือ|ไว้ลงทุน|อยู่เอง.{0,12}ลงทุน|live in .{0,20}invest|for yourself or)",
                            re.IGNORECASE)


def _numbers(text: str) -> set[str]:
    out = set()
    for tok in _NUMBER.findall(text or ""):
        tok = tok.replace(",", "").rstrip(".")
        if tok:
            out.add(tok)
            if "." in tok:
                out.add(tok.rstrip("0").rstrip("."))
    return out


#: A whole reply that is only a politeness particle (owner 12 Oct on the
#: robot: "เยี่ยมมาก" -> "ค่ะ", a remark -> "ค่ะ"). Short answers are wanted;
#: one word is not an answer.
_BARE_ACKS = {"ค่ะ", "คะ", "ครับ", "ค่า", "จ้า", "ok", "okay", "yes", "sure"}


def _bare_ack(said: str) -> bool:
    core = re.sub(r"[\s.!?,。！？…~]+", "", said or "").lower()
    return core in _BARE_ACKS


def flags_for(case: dict, turns: list[dict], allowed_text: str) -> list[str]:
    flags = []
    allowed = _numbers(allowed_text)
    # A content case may say a turn is allowed to get no answer at all (staff
    # talking to each other): silence there is the right answer, not a flag.
    quiet_ok = _silent_ok_turns(case) if case.get("suite") == "content" else set()
    for i, turn in enumerate(turns):
        said = turn.get("said", "")
        if not turn.get("complete") and not (i in quiet_ok and not said.strip()):
            flags.append(f"t{i}:incomplete_turn")
        if not said.strip() and i not in quiet_ok:
            flags.append(f"t{i}:silent")
        tail = said.strip()[-90:]
        # The content suite has its own, wider offer flag that knows which
        # turns may end on a question (a greeting) — one flag per offence.
        if (_FILLER.search(tail) and case.get("suite") != "content"
                and not (_SECTION_CHOICE.search(tail) and _BROAD_ASK.search(turn.get("user") or ""))
                and not _DISCOVERY.search(tail)):
            flags.append(f"t{i}:filler_question_at_end")
        if turn.get("audio_s", 0) > 25:
            flags.append(f"t{i}:long_{turn['audio_s']:.0f}s")
        if re.search(r"[*#`|]", said):
            flags.append(f"t{i}:markdown_symbols")
        if _concept_caveat(said):
            flags.append(f"t{i}:concept_caveat")
        if _SAYS_NO_INFO.search(said):
            flags.append(f"t{i}:says_no_info")
        if LIVE_OR_INVEST.search(said):
            flags.append(f"t{i}:asks_live_or_invest")
        if _bare_ack(said):
            flags.append(f"t{i}:bare_ack")
        if re.search(r"\d+\s*(นาที|minutes?\b|mins?\b)", said, re.IGNORECASE):
            flags.append(f"t{i}:mentions_minutes")
        tool_text = json.dumps([t.get("result") for t in turn.get("tools", [])], ensure_ascii=False)
        user_text = turn.get("user", "")
        loose = _numbers(said) - allowed - _numbers(tool_text) - _numbers(user_text)
        # Single digits are counting words ("2 ห้องนอน", "3 ชั้น") far more
        # often than claims; the judge still reads every reply in full.
        loose = {n for n in loose if len(n.replace(".", "")) > 1}
        if loose:
            flags.append(f"t{i}:numbers_not_in_sources={','.join(sorted(loose))}")
    expect = case.get("expect") or {}
    called = [t["name"] for turn in turns for t in turn.get("tools", []) if "name" in t]
    if expect.get("tools_any") and not set(called) & set(expect["tools_any"]):
        flags.append("expected_tool_not_called=" + "|".join(expect["tools_any"]))
    for name in expect.get("tools_none", []):
        if name in called:
            flags.append("forbidden_tool_called=" + name)
    if case.get("suite") in ("robot", "content") or any(k in expect for k in _EXPECT_KEYS - {
            "tools_any", "tools_none"}):
        flags += robot_flags(case, turns)
    if case.get("suite") == "content":
        flags += content_flags(case, turns)
    elif case.get("turn_expect"):
        flags += turn_expect_flags(case, turns)
    return flags


#: German words a reply in German cannot avoid, and an English one never has
#: (robot log 19 Oct: an English guest's accent heard as "Bei der Kondom ist
#: inklusive Karte?" got "Jedes Zimmer wird mit einem Möbelpaket geliefert").
#: main_script() reads letters, and German is Latin like English.
GERMAN = (r"\b(und|ist|wird|mit|uns|nur|etwa|vom|sehr|ich|wir|Sie|Ihnen|gerne|nicht|eine|einem|einen"
          r"|Zimmer|Möbel\w*|Wohnung\w*|Strand\w*)\b|[äöüß]")


def turn_expect_flags(case: dict, turns: list[dict]) -> list[str]:
    """A scenario's per-turn language and wording checks (the content suite
    has its own, wider set): `wrong_lang` by the script most of the reply is
    in, `said_forbidden` / `said_missing` by the turn's regexes."""
    flags: list[str] = []
    for i, turn in enumerate(turns):
        te = (case.get("turn_expect") or [])[i] if i < len(case.get("turn_expect") or []) else {}
        said = (turn.get("said") or "").strip()
        if said and te.get("lang") and main_script(said) != te["lang"]:
            flags.append(f"t{i}:wrong_lang={main_script(said)}")
        for pattern in te.get("said_none", []):
            if re.search(pattern, said, re.IGNORECASE):
                flags.append(f"t{i}:said_forbidden={pattern[:24]}")
        if te.get("said_any") and not any(re.search(pt, said, re.IGNORECASE) for pt in te["said_any"]):
            flags.append(f"t{i}:said_missing")
    return flags


#: A body/walk outcome stated as fact. The tool results all say "requested",
#: never "done" (§2d): the robot has not confirmed anything when the model
#: speaks, so each of these is a claim nothing backs. Quoted from the
#: instructions' own "ห้ามพูดว่า" lines plus their English forms.
_CLAIMED_DONE = re.compile(
    # Thai has no spaces, so no wildcard between verb and body word: "ยกเลิก
    # แล้ว" must not read as an arm raised (เลิก is not a body word, and one
    # body/side word is required). "ส่งคำสั่งหยุดแล้ว" / "ส่งคำสั่งเรียบร้อย
    # แล้ว" are what the instructions ask for, so a หยุด/เรียบร้อย right after
    # คำสั่ง is not a claim.
    # Review #35: the first pattern missed the shape CLAUDE.md records as the
    # real failure ("ปิดสไลด์เรียบร้อยแล้วค่ะ"), ขึ้นแล้ว, หัน/มอง/เอา gestures
    # and arrival with the place between ถึง and แล้ว ("มาถึงห้องประชุมใหญ่
    # แล้วค่ะ") — and flagged "I'm here to help" and "can be done". Arrival
    # stops at whitespace and skips "เมื่อ/พอ/ไม่ถึง" (a future or a negation).
    # Review 2 #28: the 9 Oct live run answered a stop mid-gesture with a
    # bare "เรียบร้อยค่ะ" (R-stop-while-gesturing, flags=[]) — the very
    # sentence the stop instructions now quote as forbidden — and none of
    # "หยุดเดินแล้วค่ะ", "จับมือแล้วค่ะ", "Done!", "Stopped.", "I stopped",
    # "Here we are" flagged either. Bare เรียบร้อย counts when it ends the
    # clause (a particle, punctuation, space or the end); "ยังไม่เรียบร้อย"
    # is honest. "be done" ("can be done once staff confirm") stays out
    # because a bare done/stopped counts only at the start of a clause.
    r"((?<!คำสั่ง)(เสร็จ|เรียบร้อย)แล้ว"
    r"|(ยก|ก้ม|เงย|พยัก|ส่าย|โบก|ขยับ|หัน|มอง|เอา|จับ|ยื่น)(แขน|หน้า|มือ|ซ้าย|ขวา|ตรง)(ซ้าย|ขวา)?"
    r"(ขึ้น|ลง|ทักทาย|ไปทาง(ซ้าย|ขวา))?(ให้)?(แล้ว|เรียบร้อย)"
    r"|แขนหยุดแล้ว|(?<!คำสั่ง)(?<!จะ)หยุด(เดิน|ทำท่า|แขน)?(ให้)?แล้ว"
    r"|(?<!คำสั่ง)(?<!ไม่)เรียบร้อย(ค่ะ|คะ|ค่า|ครับ|นะ|จ้ะ|$|[\s.!])"
    r"|ตามที่ขอ(ให้)?(แล้ว|เรียบร้อย)"
    r"|(?<!เมื่อ)(?<!พอ)(?<!ไม่)ถึง[^\s]{0,20}แล้ว"
    r"|\b(i'?ve|i have|i just|i|it has|it'?s|has|have|had) "
    r"(nodded|raised|waved|lowered|stopped|moved|arrived|turned|shook)\b"
    r"|\b(robot|arm|arms) (has |have )?(stopped|arrived|moved)\b"
    r"|\b(we'?re|we are|i'?m|i am) (here|there)\b(?!\s+(to|for|if|with)\b)"
    r"|\bhere we are\b"
    r"|\bhave arrived\b|\b(i'?m|i am|it'?s|it is|all) done\b"
    r"|(^|[.!?,;:]\s*)(done|stopped)\b)",
    re.IGNORECASE)
#: "ตามที่สั่ง…แล้ว" / "ตามคำสั่ง…เรียบร้อย" is the outcome *as the guest
#: ordered it* — a claim — but its สั่ง used to fall inside _NOT_A_CLAIM's
#: honest-wording span and vanish (review 2 #28: "หุ่นหยุดตามที่สั่งแล้วค่ะ",
#: "ทำตามคำสั่งเรียบร้อยแล้วค่ะ"). Rewritten to ตามที่ขอ first, which neither
#: the span nor the คำสั่ง look-behinds exempt.
_AS_ORDERED = re.compile(r"ตาม(?:ที่)?(?:คุณ|ลูกค้า)?(?:คำ)?สั่ง")
#: Spans removed before _CLAIMED_DONE looks (the integrator's check of #35
#: found the wider pattern flagging honest sentences): "ส่งคำสั่งยกแขนแล้ว",
#: "ส่งคำสั่งให้หุ่นเรียบร้อยแล้ว", "สั่งหยุดแล้ว" are the wording the
#: instructions ask for; "ถ้าไปถึงแล้วจะแจ้ง" is a condition; "ถึงแม้" is
#: "even though"; "...ถึงชั้นห้าแล้วแต่แบบ" is a range ("แล้วแต่" = depending
#: on); "หันขวาแล้วจะเห็นลิฟต์" is directions ("แล้วจะ" = then you will).
#: Lazy to the first แล้ว/เรียบร้อย, so "สั่งแล้ว ยกแขนแล้ว" still flags.
_NOT_A_CLAIM = re.compile(
    r"สั่ง[^\s]{0,30}?(?:แล้ว|เรียบร้อย)"
    r"|(?:ถ้า|เมื่อ|พอ|ไม่)(?:ไป|มา)?ถึง"
    r"|ถึงแม้|แล้วแต่|แล้วจะ")


def claims_done(said: str) -> bool:
    """Does the model say an unconfirmed motion/arrival already happened?"""
    text = _AS_ORDERED.sub("ตามที่ขอ", said or "")
    return bool(_CLAIMED_DONE.search(_NOT_A_CLAIM.sub(" ", text)))


#: Walking stated as under way. Only a false claim when no `go` was accepted
#: (refused, timed out, voice_disabled, never called) — then the guest is
#: watching a robot that is not moving while it says it is.
_CLAIMED_GOING = re.compile(
    r"(กำลัง(พา|เดิน)?ไป|เดี๋ยว(เอ็มม่า|ฉัน|หนู)?พาไป|ตามมา(เลย|ได้)|ออกเดินแล้ว"
    r"|\b(on my way|heading (there|over)|follow me|let'?s go)\b)",
    re.IGNORECASE)


def _face_refs(turns: list[dict]) -> list[tuple[str, str]]:
    """(ref, scripted answer) for every command a tool sent to the face."""
    out = []
    for turn in turns:
        for t in turn.get("tools", []):
            if "face_request" in t:
                ref = "%s:%s" % (t["face_request"], t.get("action"))
                if t.get("place"):
                    ref += ":" + t["place"]
                out.append((ref, t.get("ack")))
    return out


def _ref_matches(ref: str, pattern: str) -> bool:
    return ref == pattern or ref.startswith(pattern + ":")


def robot_flags(case: dict, turns: list[dict]) -> list[str]:
    """Flags for the robot suite: what reached the robot, and what was said
    about it. Each is a condition on the record, not on the model's style."""
    flags = []
    expect = case.get("expect") or {}
    sent = _face_refs(turns)
    for pattern in expect.get("face_any", []):
        if not any(_ref_matches(ref, pattern) for ref, _ in sent):
            flags.append("expected_face_command_missing=" + pattern)
    for pattern in expect.get("face_none", []):
        hit = [ref for ref, _ in sent if _ref_matches(ref, pattern)]
        if hit:
            flags.append("forbidden_face_command=" + hit[0])
    for name, want in (expect.get("args") or {}).items():
        calls = [t.get("args") or {} for turn in turns for t in turn.get("tools", [])
                 if t.get("name") == name]
        if calls and not any(all(c.get(k) == v for k, v in want.items()) for c in calls):
            flags.append("wrong_args=%s:%s" % (name, json.dumps(calls[0], ensure_ascii=False)))
    walking = any(_ref_matches(ref, "nav:go") and ack == "accepted" for ref, ack in sent)
    for i, turn in enumerate(turns):
        said = turn.get("said", "")
        # Review 2 #13: on 9 Oct the guard refused "มองตรงๆ หน่อย" (reason
        # heard_missing), Emma asked "ต้องการให้ทำท่าอะไร", and the case
        # recorded flags=[] — a gesture nobody got, counted as a pass. A
        # refusal by the heard guard is always worth a look: in a command
        # case the guard refused the guest, in a non-command case the model
        # tried to move and only the guard stopped it.
        for t in turn.get("tools", []):
            result = t.get("result")
            if isinstance(result, dict) and result.get("reason") == "heard_missing":
                flags.append(f"t{i}:heard_guard_refused={t.get('name')}")
        if claims_done(said):
            flags.append(f"t{i}:claimed_done")
        if not walking and _CLAIMED_GOING.search(said):
            flags.append(f"t{i}:claimed_going")
        for pattern in expect.get("said_none", []):
            if re.search(pattern, said, re.IGNORECASE):
                flags.append(f"t{i}:said_forbidden={pattern}")
    everything = " ".join(t.get("said", "") for t in turns)
    for pattern in expect.get("said_any", []):
        if not re.search(pattern, everything, re.IGNORECASE):
            flags.append("said_missing=" + pattern)
    return flags


# ---------------------------------------------------------------- timing

def tool_call_gaps(timeline: list) -> list[dict]:
    """Seconds from a `turn_complete` to the tool call that follows it.

    `timeline` is a turn's [(t, kind), ...]. For each tool call that opens a
    batch: order "A" with `gap_s` when a turn_complete came after the turn
    began (or after the previous tool event) and before the call — the
    gemini-3.8 ordering, where session.py's turn_idle opened and an
    announcement could land in the gap; order "B" with gap None when the
    call came first. The p95 of the A gaps is what ANNOUNCE_SETTLE_S has to
    cover for an announcement never to land inside one.
    """
    out = []
    last_tc = None
    prev = None
    for t, kind in timeline:
        if kind == "turn_complete":
            last_tc = t
        elif kind == "tool_call":
            if prev != "tool_call":     # later calls of one batch are not new gaps
                out.append({"order": "A" if last_tc is not None else "B",
                            "gap_s": round(t - last_tc, 2) if last_tc is not None else None})
            last_tc = None
        elif kind in ("tool_result", "tool_dropped"):
            last_tc = None
        prev = kind
    return out


def _p(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; None for no values (never a made-up 0)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * q // 100))   # ceil
    return ordered[int(rank) - 1]


def summarize(records: list[dict], *, profile: str, model: str | None) -> dict:
    """The run's timing and usage, and what it proposes — never applies.

    TURN_IDLE_TRACKS_TOOLS is recommended for a surface only when the run
    measured that surface (the robot suite = the robot face; the scenarios
    suite with --profile condo = the sales room) and saw the early
    turn_complete at all. Anything else says "not measured" rather than
    borrowing a number from the other surface.
    """
    gaps = []
    orders = {"A": 0, "B": 0}
    usage: dict[str, int] = {}
    usage_cases = 0
    for rec in records:
        for turn in rec.get("turns", []):
            for g in turn.get("gaps", []):
                orders[g["order"]] += 1
                if g["gap_s"] is not None:
                    gaps.append(g["gap_s"])
        peak = rec.get("usage") or {}
        if peak:
            usage_cases += 1
        for key, value in peak.items():
            usage[key] = usage.get(key, 0) + value
    p95 = _p(gaps, 95)
    calls = orders["A"] + orders["B"]
    suites = {rec.get("suite") for rec in records}
    measured = ("robot face" if "robot" in suites else
                "sales room" if profile == "condo" and suites & {"scenarios", "faq"} else None)
    if not calls:
        verdict = "no tool calls measured — no recommendation"
    elif orders["A"]:
        verdict = ("recommend TURN_IDLE_TRACKS_TOOLS=true with ANNOUNCE_SETTLE_S=%.1f "
                   "(turn_complete came before the tool call in %d/%d calls)"
                   % (-(-p95 * 10 // 1) / 10, orders["A"], calls))
    else:
        verdict = "not needed: no turn_complete before a tool call in %d calls" % calls
    return {
        "cases": len(records), "profile": profile, "model": model,
        "tool_calls": calls, "turn_complete_before_call": orders["A"],
        "gap_p50_s": _p(gaps, 50), "gap_p95_s": p95, "gap_max_s": max(gaps) if gaps else None,
        "proposed_announce_settle_s": None if p95 is None else -(-p95 * 10 // 1) / 10,
        "turn_idle_tracks_tools": {
            "robot face": verdict if measured == "robot face" else
            "not measured by this run — use --suite robot",
            "sales room": verdict if measured == "sales room" else
            "not measured by this run — use --suite scenarios --profile condo",
        },
        # Live reports carry no response identity (app/metrics.py: never sum
        # them as a bill). Per case the LARGEST report is kept, and these are
        # the sums of those peaks — a usage reading, not a price.
        "usage_peak_sum": usage, "usage_cases": usage_cases,
    }


# ---------------------------------------------------------------- one case

class _EvalFace:
    """The robot face's session in the robot suite. No `ws`: nothing can be
    sent to it — the scripted `robot_face_link.request` is the only reader."""
    ws = None


class _EvalListener:
    """Whose captions the heard store keeps in a case that is not the robot
    suite (it claims no face; only the captions are needed)."""


async def run_case(case: dict, *, greeting: bool, robot_face: bool,
                   turn_timeout: float, screen: bool = True,
                   profile: str = "condo") -> dict:
    from app import heard, robot_face_link
    from app.config import settings
    from app.prompts import build_instructions, greeting_for
    from app.providers.gemini import GeminiProvider
    from app.tools import registry

    # The content suite runs on the robot face too: same claimed face, same
    # heard guard captions, same scripted robot (acks default accepted).
    robot = case.get("suite") in ("robot", "content")
    registry.session_profile.set(profile)
    face = None
    if robot:
        # One object for every per-session store the tools read: the face
        # claim, the captions of the heard guard, the scripted acks.
        face = _EvalFace()
        robot_face_link.claim(face)
        heard.bind_session(face)
        _case_acks.set(dict(case.get("acks") or {}))
        registry.robot_face_session.set(face)
    else:
        _case_acks.set(None)
        registry.robot_face_session.set(object() if robot_face else None)
    # Every case gets the guest's words the way the room does: a session the
    # recogniser's captions belong to (search_condo_info reads them beside the
    # model's query -- robot 20 Oct, "unit details" for a ceiling question).
    listener = face if face is not None else _EvalListener()
    if face is None:
        heard.bind_session(listener)
    log: list = []
    _case_log.set(log)

    # show_plan only succeeds once THIS session's screen confirms it painted
    # the verified bytes (plan_display.confirm_plan). The gallery always has
    # that screen; the harness has none, and the first full run scored four
    # plan requests against "display unavailable" — Emma's honesty about a
    # failure, not the answer a guest gets. The stand-in acknowledges exactly
    # what a healthy screen would, echoing the command's own hash and asset.
    from app import plan_display, turnlog

    sid = "eval-" + case["id"]
    turnlog.session_id.set(sid)
    if screen:
        async def fake_screen(cmd: dict) -> None:
            if cmd.get("type") != "display_command":
                return
            log.append({"screen_plan": cmd.get("asset_id")})
            asyncio.get_running_loop().call_soon(plan_display.receive_ack, sid, {
                "command_id": cmd["command_id"], "status": "rendered",
                "project_id": cmd.get("project_id"), "asset_id": cmd.get("asset_id"),
                "sha256": cmd.get("expected_sha256")})

        plan_display.register(sid, fake_screen)

    instructions = build_instructions(
        settings.project_name, languages=settings.reply_languages,
        robot_name=settings.robot_name, profile=profile, robot_face=robot or robot_face)
    provider = GeminiProvider(
        settings.gemini_voice, instructions,
        greeting=greeting_for(profile) if greeting else None,
        use_tools=True, vad_floor=settings.call_vad_floor(profile))

    turns: list[dict] = []
    state = {"pending": 0, "last": time.monotonic(), "cur": None}
    usage_peak: dict[str, int] = {}

    def new_turn(user: str | None) -> dict:
        turn = {"user": user, "said": "", "audio_bytes": 0, "tools": [],
                "turn_completes": 0, "errors": [], "timeline": []}
        state["cur"] = turn
        state["last"] = time.monotonic()
        state["started"] = time.monotonic()
        # turn_complete seen since the last tool event. A turn_complete that
        # arrives BEFORE a tool call is not the end of the reply — the first
        # full run closed two turns as "silent" that way, 2.5 s after a tool
        # result while the model was still composing its answer to it.
        state["tc_since_tool"] = 0
        log.clear()
        return turn

    async def reader():
        async for ev in provider.events():
            cur = state["cur"]
            state["last"] = time.monotonic()
            if cur is None:
                continue
            if ev.kind != "audio" or not cur["timeline"] or cur["timeline"][-1][1] != "audio":
                cur["timeline"].append(
                    (round(time.monotonic() - state["started"], 2), ev.kind))
            if ev.kind == "assistant_transcript":
                cur["said"] += ev.text or ""
            elif ev.kind == "audio":
                cur["audio_bytes"] += len(ev.audio or b"")
            elif ev.kind == "tool_call":
                state["pending"] += 1
                state["tc_since_tool"] = 0
            elif ev.kind == "tool_result":
                state["pending"] = max(0, state["pending"] - 1)
                state["tc_since_tool"] = 0
            elif ev.kind == "turn_complete":
                cur["turn_completes"] += 1
                state["tc_since_tool"] += 1
            elif ev.kind == "error":
                cur["errors"].append(ev.text)
            elif ev.kind == "usage":
                # Largest report per counter: Live reports are observations
                # with no response identity, never summed (app/metrics.py).
                for key, value in (ev.data or {}).items():
                    if type(value) is int:
                        usage_peak[key] = max(usage_peak.get(key, 0), value)

    async def wait_turn(turn: dict, quiet: float = 2.5) -> bool:
        deadline = time.monotonic() + turn_timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.2)
            if turn["errors"]:
                return False
            if (state["tc_since_tool"] > 0 and state["pending"] == 0
                    and time.monotonic() - state["last"] > quiet):
                return True
        return False

    def close_turn(turn: dict, complete: bool) -> dict:
        turn["complete"] = complete
        turn["tools"] = [dict(x) for x in log]
        turn["audio_s"] = round(turn.pop("audio_bytes") / 2 / provider.output_sample_rate, 1)
        turn["said"] = turn["said"].strip()
        turn["gaps"] = tool_call_gaps(turn["timeline"])
        return turn

    started = time.monotonic()
    record: dict = {"id": case["id"], "suite": case.get("suite"), "cat": case.get("cat"),
                    "lang": case.get("lang")}
    reset_state()
    greet_turn = new_turn(None) if greeting else None
    try:
        async with provider:
            record["model"] = provider.model
            task = asyncio.create_task(reader())
            try:
                if greet_turn is not None:
                    ok = await wait_turn(greet_turn)
                    record["greeting"] = close_turn(greet_turn, ok)
                for n, text in enumerate(case["turns"]):
                    turn = new_turn(text)
                    # What local VAD + the recogniser hand the heard guard
                    # in the room: an activity_start, then this caption.
                    heard.mark_activity(listener)
                    heard.record(text, listener, utterance_id="eval-%d" % n)
                    # what the recogniser would have handed the provider's
                    # language tracker in the room (TALK_LANGUAGE_NOTE)
                    getattr(provider, "note_guest", lambda _t: None)(text)
                    await provider.send_text(text)
                    ok = await wait_turn(turn)
                    turns.append(close_turn(turn, ok))
                    if not ok and turn["errors"]:
                        break
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    except Exception as exc:   # one broken session must not end the run
        record["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        plan_display.unregister(sid)
        if face is not None:
            robot_face_link.session_closed(face)
        heard.forget(listener)
    record["screen"] = screen
    record["profile"] = profile
    record["usage"] = usage_peak
    # An upstream error arrives as an event, not an exception — a quota cut
    # mid-turn would otherwise be filed as a silent answer and graded.
    upstream = [e for t in turns + ([record["greeting"]] if "greeting" in record else [])
                for e in t.get("errors", [])]
    if upstream and not record.get("error"):
        record["error"] = "upstream: " + upstream[0][:480]
    record["turns"] = turns
    record["elapsed_s"] = round(time.monotonic() - started, 1)
    from app.prompts import load_facts

    record["flags"] = flags_for(case, turns, load_facts() + json.dumps(case, ensure_ascii=False))
    return record


# ---------------------------------------------------------------- main

def split_serial(todo: list[dict]) -> tuple[list[dict], list[dict]]:
    """(parallel, serial). A case that overrides a setting changes it for the
    whole process — every session running beside it would be measured under
    it — so it runs alone, after the rest. A story case is one of those."""
    parallel = [c for c in todo if not c.get("serial") and not c.get("settings")
                and not c.get("stories")]
    serial = [c for c in todo if c.get("serial") or c.get("settings") or c.get("stories")]
    return parallel, serial


#: A story case (`"stories": true`, data/eval/qa_live_scenarios.json, cat 27)
#: runs the robot face's motion stories (app/stories.py) the way the robot's
#: server would with them switched on: STORIES_ENABLED, `stories` in the
#: tool groups beside the robot.env groups that answer project questions,
#: and ANSWER_IMAGES on (robot.env) -- the picture a story must not fight.
#: The robot/body groups stay out: they are stubbed anyway, and not what a
#: story case measures. In this process only, never .env. TALK_LANGUAGE_NOTE
#: is robot.env's too (19 Oct): every tool result says the talk's language.
STORY_CASE_SETTINGS = {"stories_enabled": True, "answer_images": True,
                       "tool_groups": "knowledge,mydocs,units,calc,stories",
                       "talk_language_note": True,
                       # the robot has no web window (S31-map-story); and a PC
                       # .env with WEB_STAGE=true would open a real browser here
                       "web_stage": False}


@contextlib.contextmanager
def stories_approved_in_memory():
    """The story catalog is a DRAFT until a person approves it (deny by
    default; scripts/approve_stories.py --by NAME). The eval has to measure
    the stories before anyone may approve them, so for a story case -- and
    only in this process, for as long as the case runs -- the approval check
    answers "approved" for this project's catalog. Nothing is written: the
    catalog on disk keeps approved_by empty, and the patch is undone after
    the case whatever happens."""
    from app import stories

    real = stories.approval_problem

    def approved_for_this_eval(catalog):
        return "no_catalog" if not catalog else None

    stories.approval_problem = approved_for_this_eval
    stories.reset()
    try:
        yield
    finally:
        stories.approval_problem = real
        stories.reset()


@contextlib.contextmanager
def case_settings(case: dict):
    """Apply a case's setting overrides and put the old values back, whatever
    happens — R-nav-voice-off must not leave voice walks off for the cases
    after it (they would all measure voice_disabled)."""
    from app.config import settings

    overrides = dict(STORY_CASE_SETTINGS) if case.get("stories") else {}
    overrides.update(case.get("settings") or {})
    saved = {key: getattr(settings, key) for key in overrides}
    with contextlib.ExitStack() as stack:
        try:
            for key, value in overrides.items():
                setattr(settings, key, value)
            if case.get("stories"):
                stack.enter_context(stories_approved_in_memory())
            yield
        finally:
            for key, value in saved.items():
                setattr(settings, key, value)


def resolve_profile(suite: str, profile: str | None) -> str:
    """The robot suite measures the robot face, which on the owner's machine
    is the public EmmaJoke profile (design §6c D3); everything else measures
    the sales room. An explicit --profile wins."""
    return profile or ("joke" if suite in ("robot", "content") else "condo")


def check_offline(args) -> int:
    """`--check`: load and validate the selected cases, open nothing.

    The scenario files are only ever read by a two-hour paid run; this is
    the way to know beforehand that the run will measure what it says.
    """
    try:
        cases = load_cases(args.suite, Path(args.faq))
    except (OSError, ValueError) as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 1
    if args.suite == "robot":
        problems = guard_problems(cases)
        for problem in problems:
            print(f"!! {problem}", file=sys.stderr)
        if problems:
            return 1
    print(f"{len(cases)} case(s) in suite {args.suite} load and validate; "
          f"profile={resolve_profile(args.suite, args.profile)}")
    return 0


async def main_async(args) -> int:
    from app.config import settings

    if args.check:
        return check_offline(args)
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 2
    profile = resolve_profile(args.suite, args.profile)
    # enabled_tool_groups() reads the process profile (emma's default groups),
    # so the profile under test is set here too, not only per session.
    settings.assistant_profile = profile
    if args.model:
        # The 3.8 / 3.8-extended-thinking question (owner decision #11) is
        # answered by measuring both, never by switching the room's .env.
        settings.gemini_model = args.model
    make_safe()
    robot_notes = (make_robot_suite() if args.suite == "robot" else
                   make_content_suite() if args.suite == "content" else [])
    if args.suite in ("robot", "content") and args.concurrency > 3:
        # Free tier allows ~3 Live sessions in all, the room may hold one.
        print("!! --concurrency capped at 3 for the robot suite", file=sys.stderr)
        args.concurrency = 3
    from app import tools

    tools.load_tools()
    # The server warms the search encoder at boot (main._warm_search). Without
    # the same here, the first case's lookup cold-starts MiniLM past the 15 s
    # tool ceiling and the pilot run measured a timeout that no guest gets.
    from app.tools.knowledge import search_condo_info

    t0 = time.monotonic()
    await asyncio.to_thread(search_condo_info, "warm up")
    print(f"search warmed in {time.monotonic() - t0:.1f}s", flush=True)

    cases = load_cases(args.suite, Path(args.faq))
    if args.suite == "robot":
        # Loud, not fatal: a case the guard refuses still measures what the
        # model says about the refusal — but not what its id claims.
        for problem in guard_problems(cases):
            print(f"!! {problem}", file=sys.stderr)
    if args.ids:
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        cases = [c for c in cases if c["id"] in wanted]
    if args.cat:
        cases = [c for c in cases if args.cat.lower() in (c.get("cat") or "").lower()]
    if args.limit:
        cases = cases[: args.limit]
    if getattr(args, "stories", False):
        # --stories: every selected case as a story case -- what the robot face
        # answers once the stories are on, for the cases that are NOT stories
        # (a price, a chosen room type, a room number must not become one)
        cases = [dict(c, stories=True) for c in cases]

    out = Path(args.resume) if args.resume else OUT_DIR / (
        "live-qa-%s.jsonl" % dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not rec.get("error"):
                done.add(rec["id"])
    todo = [c for c in cases if c["id"] not in done]
    # Cases that drive module-level STATE (calc merges across calls) run one
    # at a time after the rest: two ROI sheets in parallel would merge.
    # A case that overrides a setting changes it for the whole process, so
    # it runs alone, after the rest, and puts the setting back.
    parallel, serial = split_serial(todo)
    print(f"{len(cases)} cases, {len(done)} already done, {len(todo)} to run "
          f"({len(serial)} serial) -> {out}  model={settings.gemini_model} "
          f"profile={profile}", flush=True)
    if robot_notes:
        print("robot suite (this process only): " + " ".join(robot_notes), flush=True)

    lock = asyncio.Lock()
    counter = {"n": 0}
    # The gallery runs on the same key. A quota error pauses EVERY worker
    # (not just the one that hit it), and a run that keeps hitting it stops
    # instead of hammering the limit the robot needs tomorrow morning.
    gate = {"until": 0.0, "strikes": 0, "abort": False}

    def quota_hit(text: str) -> bool:
        low = (text or "").lower()
        return "quota" in low or "resource_exhausted" in low or "429" in low

    async def one(case: dict, sem: asyncio.Semaphore):
        async with sem:
            rec = None
            for attempt in range(4):
                while time.monotonic() < gate["until"]:
                    await asyncio.sleep(1)
                if gate["abort"]:
                    return
                with case_settings(case):
                    # A camera-opened call on the robot has no greeting
                    # (9 Oct: 1 of 97 sessions greeted), so a content case
                    # greets only when it says so.
                    greets = (bool(case.get("greeting")) if case.get("suite") == "content"
                              else not args.no_greeting)
                    rec = await run_case(case, greeting=greets,
                                         robot_face=not args.desktop,
                                         turn_timeout=args.turn_timeout,
                                         screen=not args.no_screen, profile=profile)
                if not rec.get("error"):
                    gate["strikes"] = 0
                    break
                if quota_hit(rec["error"]):
                    gate["strikes"] += 1
                    gate["until"] = time.monotonic() + args.cooldown
                    print(f"!! quota limit ({gate['strikes']} in a row) — every worker pauses "
                          f"{args.cooldown:.0f}s", flush=True)
                    if gate["strikes"] >= args.max_strikes:
                        gate["abort"] = True
                        print("!! quota keeps failing — stopping the run; resume later with "
                              f"--resume {out}", flush=True)
                        return
                else:
                    await asyncio.sleep(5 * (attempt + 1))
            async with lock:
                with out.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                counter["n"] += 1
                said = " / ".join(t["said"][:70] for t in rec["turns"])
                print(f"[{counter['n']}/{len(todo)}] {rec['id']} {rec['elapsed_s']}s "
                      f"{'ERR ' + rec['error'][:80] if rec.get('error') else ''} "
                      f"{' '.join(rec['flags'])[:120]} | {said}", flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    await asyncio.gather(*(one(c, sem) for c in parallel))
    one_at_a_time = asyncio.Semaphore(1)
    for case in serial:
        await one(case, one_at_a_time)
    print(f"done -> {out}")
    write_summary(out, profile=profile, model=settings.gemini_model)
    return 0


def write_summary(out: Path, *, profile: str, model: str | None) -> dict:
    """Summarise every record in the run file (a resumed run included) next
    to it, and print it. Only records without an error count: a session that
    died mid-turn has no timing worth proposing a setting from."""
    records = []
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not rec.get("error"):
                records.append(rec)
    summary = summarize(records, profile=profile, model=model)
    path = out.with_name(out.stem + ".summary.json")
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("summary -> %s\n%s" % (path, json.dumps(summary, ensure_ascii=False, indent=1)),
          flush=True)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--suite", choices=["scenarios", "faq", "all", "robot", "content"],
                    default="all",
                    help="robot = body/walk commands on the robot face; content = what "
                         "EmmaJoke says on the robot face (neither is part of all)")
    ap.add_argument("--profile", choices=["condo", "joke", "emma"],
                    help="profile under test (default: joke for --suite robot, else condo)")
    ap.add_argument("--model", help="Live model for this run only (e.g. "
                    "gemini-3.8-live-extended-thinking); .env is not touched")
    ap.add_argument("--check", action="store_true",
                    help="load and validate the cases offline and exit; no session, no key")
    ap.add_argument("--faq", default=str(FAQ), help="path to the report's FAQ chapter")
    ap.add_argument("--ids", help="comma-separated case ids")
    ap.add_argument("--cat", help="substring of the category")
    ap.add_argument("--stories", action="store_true",
                    help="run every selected case with the robot face's stories on (approved in memory "
                         "for this process only; nothing is written)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--concurrency", type=int, default=2,
                    help="parallel Live sessions (free tier allows ~3 in total, and the "
                         "gallery or another test may hold one; 4 hit the limit in 8 cases)")
    ap.add_argument("--cooldown", type=float, default=150.0,
                    help="seconds every worker pauses after a quota error")
    ap.add_argument("--max-strikes", type=int, default=6,
                    help="stop the run after this many quota errors in a row; use 1 "
                         "while the gallery is open — the robot shares this key")
    ap.add_argument("--turn-timeout", type=float, default=75.0)
    ap.add_argument("--no-greeting", action="store_true",
                    help="skip the opening greeting turn (the gallery always sends it)")
    ap.add_argument("--no-screen", action="store_true",
                    help="no stand-in kiosk screen: show_plan then fails as 'display unavailable'")
    ap.add_argument("--desktop", action="store_true",
                    help="desktop-tab prompt instead of the robot-face prompt")
    ap.add_argument("--resume", help="append to this JSONL, skipping cases already done")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
