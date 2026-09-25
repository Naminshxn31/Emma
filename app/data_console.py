"""The owner's approval desk: read the registry, edit curated JSON, sign it.

Why this module exists
----------------------
`app/knowledge_policy.evaluate_claim` denies by default, which is right, and
until today the only way to make it say yes was to hand-edit JSON. So Emma
answers every project question with "ไม่มีข้อมูล" and the reason lives in a
file nobody standing at the sales desk is going to open. This module is the
front end for that one decision, and nothing else: it shows the live verdict
for every registered source, lets the owner edit the two curated text files,
and stamps an approval that the *server* dates and signs.

Three rules it is built around, each paid for by a bug in this repo:

* Nobody but the owner may invent project data. The console never writes a
  price, a room size, a promotion or an opening hour of its own — those four
  fields stay `null` in `condo_facts.json` until a human types them here.
* `approved_by` names a human who is responsible. It is never pre-filled,
  never defaulted, never remembered between approvals, and an empty or
  whitespace name is refused *by the server*, not only by the page.
* A safety flag must not default to the unsafe side. This repo has shipped
  that twice already (`script_approved` marking all 59 narrations approved;
  `HOST=0.0.0.0` with `WS_TOKEN=""`). An approve button reachable with no
  token on a server that binds 0.0.0.0 would be the third and the worst:
  anyone on the LAN could put words in a robot's mouth over the owner's
  name. So `require_token()` refuses when `WS_TOKEN` is *unset* as well as
  when it is wrong — the missing gate is treated as a closed one.

  The same rule caught the approve-then-swap hole: editing an approved
  file withdraws its approval in the same write, because otherwise new
  text goes out to customers over the signature of whoever approved the
  old text.

Approval is authorization, never construction progress: `content_state`
stays whatever the owner picks, and `state_instruction()` keeps forcing the
"แนวคิดเบื้องต้น" caveat into Emma's mouth after approval exactly as before.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from app.knowledge_policy import CONTENT_STATES, POLICY_VERSION, evaluate_claim, state_instruction


#: Repository root. A module-level name (not a constant baked into every
#: call) so the tests can point the whole console at a temp copy of `data/`
#: — the suite must never be one bug away from stamping the owner's real
#: files, which are the ones he presses the button on.
DATA_ROOT = Path(__file__).resolve().parents[1]

REGISTRY_RELATIVE = "data/registry/source_registry.json"
#: One JSON line per write. Lives beside the registry because it is part of
#: the same record: who said this data may be spoken, and when.
AUDIT_RELATIVE = "data/registry/approval_audit.jsonl"

#: Sources this console may edit and sign, and the shape of each.
#:
#: The registry's own `authority` field is *not* the key here, and that is
#: deliberate: `project_sales_context` and `slide_catalog` are filed as
#: `reference`, yet both are hand-maintained files the sales team owns and
#: the owner must be able to sign, while several `curated` entries (the unit
#: table, the print catalogue) are not files this console knows the shape
#: of. So the list is explicit and lives next to the editors that understand
#: each file. A source that is not named here has no approve path at all,
#: and adding one is a reviewed code change rather than a checkbox.
CURATED: dict[str, dict[str, str]] = {
    "project_facts": {
        "editor": "facts",
        "what": "ข้อเท็จจริงของโครงการที่ Emma จะยกไปพูดกับลูกค้าได้",
    },
    "project_sales_context": {
        "editor": "rules",
        "what": "กติกาการนำเสนอที่ Emma ต้องทำตามทุกเทิร์น",
    },
    "slide_catalog": {
        "editor": "catalog",
        "what": "คำบรรยายสไลด์ทั้งชุด ที่ Emma จะอ่านเป็นคำพูดของโครงการ",
    },
    "project_qa_script": {
        "editor": "qa",
        "what": "รูปแบบคำตอบที่ Emma ใช้ได้ เมื่อลูกค้าเดินเข้ามาถามหน้าห้องขาย",
    },
}

#: The markdown copy of the Q&A script, regenerated from the JSON on every
#: successful write.
#:
#: Why a copy at all: the owner reads and reviews this thing as a document,
#: and the repo has had a document in it since yesterday. Two editable copies
#: of the same sentences drift, and the one that drifts is always the one
#: nobody opened — so only one of them is writable. The JSON is the source of
#: truth, the markdown is output, and it says so in its own first line.
#: Relative to `DATA_ROOT` like everything else here, which is what keeps the
#: test suite from rewriting the owner's real document.
QA_EXPORT_RELATIVE = "docs/emma-qa-walkin-script-2026-09-24.md"

#: Which keys carry the approval decision. Exactly the six `evaluate_claim`
#: requires — written out here so a change on either side is a visible
#: mismatch rather than a silently half-stamped file.
APPROVAL_FIELDS = ("approval_status", "approved_by", "approved_at",
                   "effective_at", "disclosure_scope", "content_state")

#: The five permission fields, back to the closed position. `content_state`
#: is deliberately *not* in here: it describes the content ("this is still a
#: concept"), not the permission, and an un-approval that quietly rewrote it
#: would be deciding something nobody asked it to decide. See `_unstamp`.
PERMISSION_DRAFT: dict[str, Any] = {
    "approval_status": "draft",
    "approved_by": "",
    "approved_at": None,
    "effective_at": None,
    "disclosure_scope": "internal",
}

#: Where an approval parks the `content_state` it found, so that undoing the
#: approval can put it back.
#:
#: Why this key exists: approving asks the operator to pick a content state,
#: and picking a weaker caveat ("existing" instead of "preliminary_concept")
#: is a real decision. Revoking is supposed to remove the permission and
#: nothing else — but without a snapshot it left the *weakened* caveat behind
#: and the page then offered it as the default for the next approval, so one
#: approval could quietly strip the "แนวคิดเบื้องต้น" caveat from every
#: approval after it. Only the top-level record carries the snapshot; the
#: slide records are overwritten wholesale by every approve, so a per-image
#: copy would be 135 extra lines of diff that decide nothing.
STATE_SNAPSHOT = "content_state_before_approval"

#: Thai for each policy content state, kept beside the policy list rather
#: than in the page: the operator is picking the one field that decides
#: whether Emma must say "นี่เป็นแนวคิดเบื้องต้น", and raw English enum
#: values on a Thai screen are a coin flip. A state the policy gains and this
#: map has not is shown as its raw value rather than silently hidden.
CONTENT_STATE_TH = {
    "existing": "มีอยู่จริงแล้ว ณ วันนี้",
    "completed": "สร้าง/ทำเสร็จแล้ว",
    "developing": "กำลังพัฒนา ยังไม่เสร็จ",
    "preliminary_concept": "แนวคิดเบื้องต้น อาจเปลี่ยนแปลงได้",
    "proposed": "เสนอไว้ ยังไม่ยืนยันว่าจะเกิด",
}


def content_state_options() -> list[dict]:
    """Each policy state with its Thai gloss and the exact sentence Emma will
    be forced to say if it is chosen — so the choice is made with the
    consequence visible, not two screens away in `state_instruction`."""
    return [{"value": state,
             "label_th": CONTENT_STATE_TH.get(state, state),
             "caveat_th": state_instruction(state)}
            for state in sorted(CONTENT_STATES)]

#: Plain Thai for every reason `evaluate_claim` can return, because "why is
#: Emma silent" is the only question this page exists to answer.
REASON_TH = {
    "missing_identity": "ไฟล์ไม่มี source_id หรือ project_id — แก้ที่ไฟล์ต้นทางก่อน",
    "wrong_project": "เป็นข้อมูลของโครงการอื่น ไม่ใช่โครงการนี้",
    "not_approved": "ยังไม่อนุมัติ (approval_status ไม่ใช่ approved)",
    "missing_metadata": "ข้อมูลอนุมัติยังไม่ครบหกช่อง",
    "not_customer_visible": "ขอบเขตยังเป็นภายใน (internal) ยังไม่เปิดให้ลูกค้า",
    "invalid_content_state": "สถานะเนื้อหาไม่อยู่ในรายการที่นโยบายรู้จัก",
    "invalid_timestamp": "เวลาอนุมัติผิดรูปแบบหรือไม่มีโซนเวลา",
    "not_effective": "เวลาอนุมัติหรือเวลาเริ่มใช้เป็นอนาคต จึงยังไม่มีผล",
    "expired": "หมดอายุแล้ว",
    "approved_customer_claim": "อนุมัติแล้ว Emma พูดกับลูกค้าได้",
}

#: Audit lines record what changed. Long prose is capped so one rule edit
#: cannot bury the rest of the trail; the full previous text is never lost,
#: it is in the `.bak` beside the file.
_AUDIT_VALUE_CHARS = 400


class ConsoleError(Exception):
    """A refusal with a Thai sentence that says what to do about it."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------------- the gate

def require_token(token: str | None) -> None:
    """Refuse unless the caller proves it is the operator.

    Same comparison as `/unit_price` and the sockets: encoded before
    `compare_digest`, which refuses non-ASCII and would otherwise raise on a
    token pasted out of a password manager.

    The one difference from `_authorized()` in main.py is the reason this
    module exists: there, a blank `WS_TOKEN` means "no gate configured, let
    it through", which is tolerable for a page that reads a battery level.
    Here it would mean a stranger on the LAN can sign the owner's name to a
    price. Blank is treated as closed, and the message says which knob.
    """
    import hmac

    from app.config import settings

    if not settings.ws_token:
        raise ConsoleError(
            "ยังไม่ได้ตั้ง WS_TOKEN ใน .env — หน้านี้แก้และอนุมัติข้อมูลที่ Emma "
            "จะพูดกับลูกค้า จึงต้องมีรหัสก่อนถึงจะเปิดใช้ได้ ตั้ง WS_TOKEN แล้วรันเซิร์ฟเวอร์ใหม่",
            status=503,
        )
    if not hmac.compare_digest((token or "").encode("utf-8"),
                               settings.ws_token.encode("utf-8")):
        raise ConsoleError("รหัสผ่านไม่ถูกต้อง เปิดหน้านี้จากลิงก์ที่มี token ของเครื่องนี้",
                           status=403)


def clean_name(value: object) -> str:
    """The approver's own name, or a refusal.

    Never defaulted and never carried over from the previous approval: the
    whole point of the field is that a person put their name on it.
    """
    name = str(value or "").strip()
    if not name:
        raise ConsoleError("ต้องพิมพ์ชื่อผู้รับผิดชอบก่อน — ช่องนี้คือคนที่รับผิดชอบข้อมูลชุดนี้")
    if len(name) > 80:
        raise ConsoleError("ชื่อผู้รับผิดชอบยาวเกินไป")
    return name


# ------------------------------------------------------------ file plumbing

def _root() -> Path:
    return Path(DATA_ROOT)


def _safe_path(location: str) -> Path:
    """A registry location is repo-relative POSIX, and must stay inside."""
    if (not isinstance(location, str) or not location or "\\" in location
            or Path(location).is_absolute()
            or (len(location) > 1 and location[0].isalpha() and location[1] == ":")):
        raise ConsoleError("ที่อยู่ไฟล์ในทะเบียนไม่ถูกต้อง")
    path = (_root() / location).resolve()
    if not path.is_relative_to(_root().resolve()):
        raise ConsoleError("ที่อยู่ไฟล์ออกนอกโฟลเดอร์โปรเจกต์")
    return path


def read_registry() -> dict:
    path = _root() / REGISTRY_RELATIVE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConsoleError(f"อ่านทะเบียนแหล่งข้อมูลไม่ได้: {exc}", status=500) from exc
    if not isinstance(payload.get("sources"), list):
        raise ConsoleError("ทะเบียนแหล่งข้อมูลผิดรูปแบบ", status=500)
    return payload


def _read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: Mapping) -> None:
    """Temp file then replace, keeping one `.bak` of what was there.

    Style (indent, CRLF, trailing newline) is copied from the file being
    replaced. Not cosmetic: rewriting a 135-slide catalogue with different
    line endings turns "one field changed" into a diff nobody can review,
    and an unreviewable diff is how a wrong approval hides.
    """
    previous = path.read_bytes() if path.exists() else b""
    newline = "\r\n" if b"\r\n" in previous else "\n"
    indent = 2
    if previous:
        for line in previous.decode("utf-8", "replace").splitlines()[1:]:
            stripped = line.lstrip(" ")
            if stripped and stripped != line:
                indent = len(line) - len(stripped)
                break
    text = json.dumps(payload, ensure_ascii=False, indent=indent)
    if not previous or previous.endswith(b"\n"):
        text += "\n"
    if previous:
        path.with_name(path.name + ".bak").write_bytes(previous)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline=newline) as handle:
        handle.write(text)
    os.replace(tmp, path)


def _persist(source_id: str, path: Path, payload: Mapping, location: str) -> str:
    """Write the file, then regenerate anything derived from it.

    One funnel for all three writers (edit/approve/revoke) rather than three
    copies of "and also update the markdown": the reason the repo copy can
    never drift is that there is no write path that skips this. Returns a
    Thai warning when the derived file could not be written — the JSON is the
    source of truth and the save stands, but a stale document sitting in the
    repo looking authoritative is exactly what this export exists to prevent,
    so the operator is told rather than left to find out.
    """
    _write_json(path, payload)
    if CURATED.get(source_id, {}).get("editor") != "qa":
        return ""
    target = _root() / QA_EXPORT_RELATIVE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_qa_markdown(payload, location),
                          encoding="utf-8", newline="\n")
    except OSError as exc:
        return f"บันทึกลงไฟล์หลักแล้ว แต่เขียนสำเนาเอกสาร {QA_EXPORT_RELATIVE} ไม่ได้: {exc}"
    return ""


# -------------------------------------------------- the Q&A script document

def _cell(text: object) -> str:
    """One markdown table cell. A pipe inside an answer would end the column
    and silently eat the rest of the sentence, which on a document about what
    a robot may say to a buyer is a lost rule, not a layout glitch."""
    return str(text or "").replace("|", "\\|").replace("\n", "<br>")


def render_qa_markdown(payload: Mapping, location: str) -> str:
    """The reviewable document, rebuilt from the record every time.

    Nothing is composed here: every sentence comes out of the JSON exactly as
    a person typed it into the console. The only text this function of its
    own adds is the header saying where the file came from.
    """
    approved = str(payload.get("approval_status") or "") == "approved"
    if approved:
        status = "อนุมัติแล้ว โดย %s เมื่อ %s" % (payload.get("approved_by") or "-",
                                                  payload.get("approved_at") or "-")
    else:
        status = "ร่าง ยังไม่อนุมัติ"

    out: list[str] = [
        "<!-- ไฟล์นี้ถูกสร้างอัตโนมัติจาก %s -->" % location,
        "<!-- ห้ามแก้ไฟล์นี้ด้วยมือ ทุกครั้งที่กดบันทึกในหน้า /data-console ไฟล์นี้จะถูกเขียนใหม่ทั้งไฟล์ -->",
        "",
        "# " + str(payload.get("title") or ""),
        "",
        "> เอกสารนี้ **สร้างอัตโนมัติ** จาก `%s` · **ห้ามแก้ไฟล์นี้ด้วยมือ**" % location,
        "> แก้ที่หน้า `/data-console` แล้วกดบันทึก ระบบจะเขียนไฟล์นี้ใหม่ทั้งไฟล์ ไฟล์ JSON คือต้นฉบับ",
        "",
        "สถานะ: **%s** · %s · %s" % (status, payload.get("drafted_on_th") or "",
                                     payload.get("audience_th") or ""),
        "",
    ]
    for paragraph in payload.get("intro") or []:
        out += [str(paragraph), ""]

    how = payload.get("how_to_read") if isinstance(payload.get("how_to_read"), dict) else {}
    if how:
        out += ["## " + str(how.get("title") or ""), "", str(how.get("lead") or ""), ""]
        out += ["- " + str(item) for item in how.get("items") or []]
        out += ["", str(how.get("tail") or ""), ""]

    rules = payload.get("rules") or []
    if rules:
        out += ["## " + str(payload.get("rules_title") or ""), ""]
        out += ["%d. %s" % (number, text) for number, text in enumerate(rules, 1)]
        out += [""]

    for number, entry in enumerate(payload.get("entries") or [], 1):
        if not isinstance(entry, dict):
            continue
        out += ["---", "", "## %d. %s" % (number, entry.get("title") or ""), ""]
        says = [str(phrase) for phrase in entry.get("guest_says") or [] if str(phrase).strip()]
        if says:
            out += ["**ลูกค้า:** " + " หรือ ".join(says), ""]
        out += ["| | คำตอบ |", "|---|---|",
                "| ตอนนี้ | %s |" % _cell(entry.get("answer_now")),
                "| หลังอนุมัติ | %s |" % _cell(entry.get("answer_after")), ""]
        for note in entry.get("notes") or []:
            if not isinstance(note, dict):
                continue
            kind = str(note.get("kind") or "").strip()
            text = str(note.get("text") or "").strip()
            if not text and not kind:
                continue
            out += [("**%s** %s" % (kind, text)).strip(), ""]

    decisions = payload.get("decisions") or []
    if decisions:
        out += ["---", "", "## " + str(payload.get("decisions_title") or ""), ""]
        out += ["%d. %s" % (number, text) for number, text in enumerate(decisions, 1)]
        out += [""]

    steps = payload.get("next_steps") or []
    if steps:
        out += ["## " + str(payload.get("next_steps_title") or ""), ""]
        for paragraph in steps:
            out += [str(paragraph), ""]

    return "\n".join(out).rstrip("\n") + "\n"


def _qa_lines(value: object, what: str) -> list[str]:
    """A numbered list of prose (the rules, the owner's open decisions).

    Blank is refused rather than dropped: a cleared box on this page means
    "I emptied this one", and silently removing the line would make a rule
    disappear from the document with nothing in the audit trail to ask about.
    """
    if not isinstance(value, list) or not value:
        raise ConsoleError(f"ไม่มี{what}ที่ส่งมา")
    out: list[str] = []
    for index, item in enumerate(value, 1):
        text = str(item or "").strip()
        if not text:
            raise ConsoleError(f"{what}ข้อ {index} ว่าง — ถ้าต้องการลบให้กดปุ่มลบข้อนั้น")
        out.append(text)
    return out


def _qa_next_id(used: set[str]) -> str:
    """A new situation gets the next free `qNN`. Ids are how an edit knows
    which situation moved and which one is new, so they are never reused."""
    number = 1
    while f"q{number:02d}" in used:
        number += 1
    return f"q{number:02d}"


def _qa_entries(value: object) -> list[dict]:
    """The situations as the page sends them: order is the list's order,
    a missing id is a new situation, an id that is not in the list any more
    was deleted.

    An answer left blank stays blank. This is the same rule as the four empty
    facts: "" means nobody has written this answer yet, and filling it with a
    helpful-looking placeholder would put a sentence Emma could be made to
    say into a document that people sign.
    """
    if not isinstance(value, list) or not value:
        raise ConsoleError("ไม่มีสถานการณ์ที่ส่งมา — ต้องเหลืออย่างน้อยหนึ่งข้อ")
    used: set[str] = set()
    out: list[dict] = []
    for position, row in enumerate(value, 1):
        if not isinstance(row, dict):
            raise ConsoleError("รูปแบบสถานการณ์ไม่ถูกต้อง")
        title = str(row.get("title") or "").strip()
        if not title:
            raise ConsoleError(f"สถานการณ์ข้อ {position} ยังไม่มีหัวข้อ")
        entry_id = str(row.get("id") or "").strip()
        if not entry_id or entry_id in used:
            entry_id = _qa_next_id(used)
        used.add(entry_id)
        says = [str(phrase).strip() for phrase in (row.get("guest_says") or [])
                if str(phrase or "").strip()]
        notes: list[dict] = []
        for note in row.get("notes") or []:
            if not isinstance(note, dict):
                continue
            text = str(note.get("text") or "").strip()
            kind = str(note.get("kind") or "").strip()
            # An empty row is the "+ เพิ่มหมายเหตุ" button nobody filled in.
            if not text and not kind:
                continue
            if not text:
                raise ConsoleError(f"หมายเหตุ '{kind}' ของข้อ {position} ยังไม่มีเนื้อหา")
            notes.append({"kind": kind, "text": text})
        out.append({
            "id": entry_id,
            "title": title,
            "guest_says": says,
            "answer_now": str(row.get("answer_now") or "").strip(),
            "answer_after": str(row.get("answer_after") or "").strip(),
            "notes": notes,
        })
    return out


def _qa_flat(entry: Mapping) -> dict:
    """One situation as comparable scalars, so the audit line can name the
    field that moved instead of dumping the whole record twice."""
    return {
        "title": entry.get("title") or "",
        "guest_says": " · ".join(entry.get("guest_says") or []),
        "answer_now": entry.get("answer_now") or "",
        "answer_after": entry.get("answer_after") or "",
        "notes": " · ".join("%s: %s" % (note.get("kind") or "", note.get("text") or "")
                            for note in entry.get("notes") or []
                            if isinstance(note, dict)),
    }


def _qa_list_changes(before: list, after: list, prefix: str) -> list[dict]:
    changes: list[dict] = []
    for index in range(max(len(before), len(after))):
        old = before[index] if index < len(before) else None
        new = after[index] if index < len(after) else None
        if old != new:
            changes.append({"field": "%s/%d" % (prefix, index + 1),
                            "from": _short(old), "to": _short(new)})
    return changes


def _qa_entry_changes(before: list, after: list) -> list[dict]:
    """Added, deleted, reordered, or one field of one situation edited.

    A deletion records the title it removed, not just the id: the trail has
    to be readable by the person who has to ask "who took that answer out",
    and `entries/-q07` on its own answers nothing.
    """
    old_by_id = {str(item.get("id")): item for item in before if isinstance(item, dict)}
    new_by_id = {str(item.get("id")): item for item in after if isinstance(item, dict)}
    changes: list[dict] = []
    for entry_id, item in old_by_id.items():
        if entry_id not in new_by_id:
            changes.append({"field": "entries/-" + entry_id,
                            "from": _short(item.get("title")), "to": None})
    for entry_id, item in new_by_id.items():
        if entry_id not in old_by_id:
            changes.append({"field": "entries/+" + entry_id,
                            "from": None, "to": _short(item.get("title"))})
            continue
        was, now = _qa_flat(old_by_id[entry_id]), _qa_flat(item)
        for field in was:
            if was[field] != now[field]:
                changes.append({"field": "entries/%s/%s" % (entry_id, field),
                                "from": _short(was[field]), "to": _short(now[field])})
    old_order = [key for key in old_by_id if key in new_by_id]
    new_order = [key for key in new_by_id if key in old_by_id]
    if old_order != new_order:
        changes.append({"field": "entries/order",
                        "from": _short(" ".join(old_order)), "to": _short(" ".join(new_order))})
    return changes


# ------------------------------------------------------------- the verdicts

def _verdict(payload: Mapping | None, project_id: str) -> dict:
    decision = evaluate_claim(payload or {}, project_id)
    trace = decision.trace()
    trace["reason_th"] = REASON_TH.get(decision.reason, decision.reason)
    trace["caveat_th"] = state_instruction(decision.content_state) if decision.allowed else ""
    return trace


def _slide_counts(payload: Mapping | None) -> dict:
    images = (payload or {}).get("images")
    if not isinstance(images, list):
        return {"total": 0, "types": {}}
    types: dict[str, int] = {}
    for item in images:
        if isinstance(item, dict):
            key = str(item.get("type") or "ไม่ระบุ")
            types[key] = types.get(key, 0) + 1
    return {"total": len(images), "types": dict(sorted(types.items(), key=lambda kv: -kv[1]))}


def overview(project_id: str) -> dict:
    """Every registered source with the verdict the runtime would get.

    The verdict is computed by calling the real `evaluate_claim`, not by
    re-deriving its rules here — the measurement has to be at the layer the
    customer meets, which is the trap this repo has fallen into three times.
    Sources whose authority is not curated get the same honest verdict plus
    the registry's own one-line description of why human approval is not
    the mechanism that governs them.
    """
    registry = read_registry()
    authority_levels = registry.get("authority_levels") or {}
    rows: list[dict] = []
    for source in registry["sources"]:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("id") or "")
        location = source.get("location")
        payload: dict | None = None
        exists = None
        if source.get("kind") == "json_file" and isinstance(location, str) and location:
            try:
                path = _safe_path(location)
            except ConsoleError:
                path = None
            if path is not None:
                exists = path.is_file()
                payload = _read_json(path) if exists else None
        scoped = str(source.get("project_id") or project_id)
        row = {
            "id": source_id,
            "label": source.get("label") or source_id,
            "authority": source.get("authority"),
            "authority_th": authority_levels.get(str(source.get("authority")), ""),
            "domain": source.get("domain"),
            "kind": source.get("kind"),
            "availability": source.get("availability"),
            "location": location,
            "project_id": source.get("project_id"),
            "required_for": source.get("required_for") or [],
            "exists": exists,
            "verdict": _verdict(payload, scoped),
            "editable": source_id in CURATED and source.get("project_id") == project_id,
            "editor": CURATED.get(source_id, {}).get("editor", ""),
            "what_th": CURATED.get(source_id, {}).get("what", ""),
        }
        if not row["editable"]:
            row["readonly_th"] = (
                "แหล่งนี้ไม่ได้อยู่ในรายการที่หน้านี้แก้/อนุมัติได้ "
                + (authority_levels.get(str(source.get("authority")), "") or "")
            ).strip()
        if source_id == "slide_catalog":
            row["counts"] = _slide_counts(payload)
        rows.append(row)
    return {
        "ok": True,
        "project_id": project_id,
        "policy": POLICY_VERSION,
        "content_states": content_state_options(),
        "sources": rows,
        "updated_on": registry.get("updated_on"),
    }


def _curated(source_id: str, project_id: str) -> tuple[dict, Path, dict]:
    """Resolve a source this console is allowed to touch, or refuse."""
    if source_id not in CURATED:
        raise ConsoleError(
            f"แหล่งข้อมูล '{source_id}' ไม่ได้อยู่ในรายการที่หน้านี้แก้หรืออนุมัติได้ "
            "— แก้ที่ไฟล์ต้นทางและให้ผู้ดูแลระบบเพิ่มเข้ารายการก่อน")
    registry = read_registry()
    source = next((item for item in registry["sources"]
                   if isinstance(item, dict) and item.get("id") == source_id), None)
    if source is None:
        raise ConsoleError(f"ไม่พบ '{source_id}' ในทะเบียนแหล่งข้อมูล", status=404)
    if source.get("project_id") != project_id:
        raise ConsoleError(
            f"'{source_id}' เป็นของโครงการ {source.get('project_id') or 'ไม่ระบุ'} "
            f"ไม่ใช่ {project_id} — เปิดเซิร์ฟเวอร์ด้วยโครงการที่ถูกต้องก่อน")
    path = _safe_path(str(source.get("location") or ""))
    payload = _read_json(path)
    if payload is None:
        raise ConsoleError(f"อ่านไฟล์ของ '{source_id}' ไม่ได้: {source.get('location')}", status=500)
    if payload.get("project_id") != project_id or payload.get("source_id") != source_id:
        raise ConsoleError("ไฟล์นี้มี source_id/project_id ไม่ตรงกับทะเบียน — ยังไม่แก้ให้", status=500)
    return source, path, payload


def detail(source_id: str, project_id: str) -> dict:
    """Everything the page needs to show before anybody signs anything."""
    source, path, payload = _curated(source_id, project_id)
    editor = CURATED[source_id]["editor"]
    result: dict[str, Any] = {
        "ok": True,
        "id": source_id,
        "label": source.get("label") or source_id,
        "editor": editor,
        "what_th": CURATED[source_id]["what"],
        "location": source.get("location"),
        "verdict": _verdict(payload, project_id),
        "approval": {field: payload.get(field) for field in APPROVAL_FIELDS},
        "content_states": content_state_options(),
    }
    if editor == "facts":
        facts = payload.get("facts") if isinstance(payload.get("facts"), list) else []
        result["facts"] = [
            {"label": str(item.get("label") or ""), "value": item.get("value")}
            for item in facts if isinstance(item, dict)
        ]
        result["disclaimer"] = payload.get("disclaimer")
    elif editor == "rules":
        # Every `*_rule` key is prose Emma is made to follow. Listed by key
        # so adding a rule to the file shows up here without a code change.
        result["rules"] = [
            {"key": key, "text": value}
            for key, value in payload.items()
            if key.endswith("_rule") and isinstance(value, str)
        ]
        result["review_status"] = payload.get("review_status")
    elif editor == "qa":
        # The whole script, in the file's own order. The page is meant to be
        # read straight through by a salesperson before anybody signs it, so
        # there is no paging and no summary: a signature on a summary is a
        # blind one, same reason the slide catalogue is sent in full.
        result["doc"] = {
            "title": payload.get("title") or "",
            "drafted_on_th": payload.get("drafted_on_th") or "",
            "audience_th": payload.get("audience_th") or "",
            "intro": payload.get("intro") or [],
            "how_to_read": payload.get("how_to_read") or {},
            "rules_title": payload.get("rules_title") or "",
            "decisions_title": payload.get("decisions_title") or "",
            "next_steps_title": payload.get("next_steps_title") or "",
            "next_steps": payload.get("next_steps") or [],
            "export": QA_EXPORT_RELATIVE,
        }
        result["rules"] = [str(rule) for rule in payload.get("rules") or []]
        result["entries"] = [
            {"id": str(item.get("id") or ""),
             "title": item.get("title") or "",
             "guest_says": [str(phrase) for phrase in item.get("guest_says") or []],
             "answer_now": item.get("answer_now") or "",
             "answer_after": item.get("answer_after") or "",
             "notes": [{"kind": note.get("kind") or "", "text": note.get("text") or ""}
                       for note in item.get("notes") or [] if isinstance(note, dict)]}
            for item in payload.get("entries") or [] if isinstance(item, dict)
        ]
        result["decisions"] = [str(item) for item in payload.get("decisions") or []]
    else:
        images = payload.get("images") if isinstance(payload.get("images"), list) else []
        result["counts"] = _slide_counts(payload)
        # The whole list, not a sample: the owner is signing 135 captions as
        # words Emma may speak, and a signature on a summary is a blind one.
        result["images"] = [
            {"id": item.get("id"), "type": item.get("type"),
             "title_th": item.get("title_th") or item.get("title_en") or "",
             "summary_th": item.get("summary_th") or "",
             "verdict": evaluate_claim(item, project_id).reason}
            for item in images if isinstance(item, dict)
        ]
    return result


# ------------------------------------------------------------- audit trail

def audit_path() -> Path:
    return _root() / AUDIT_RELATIVE


def _record(source_id: str, action: str, by: str, changes: list[dict], note: str = "") -> dict:
    """Append one line. This is what gives an approval an owner structurally
    rather than by memory — the name is in the file, not in somebody's head.

    Nothing here is a secret and nothing here is a customer's words: the
    fields are a timestamp, a source id, an action, the name the operator
    typed, and which metadata/content fields moved.
    """
    entry = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_id": source_id,
        "action": action,
        "by": by,
        "policy": POLICY_VERSION,
        "changes": changes,
    }
    if note:
        entry["note"] = note
    path = audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_audit(limit: int = 50, source_id: str = "") -> list[dict]:
    """The last `limit` lines, oldest first — of one source when asked.

    The filtering is here rather than in the page because the page has to be
    able to say "this source has no history" and mean it. Reading the last
    thirty lines of the whole trail and filtering them in the browser made a
    twice-approved source report "ยังไม่มีประวัติ" as soon as thirty writes
    to *other* sources had happened since, which on a page whose only job is
    accountability is worse than showing nothing at all.
    """
    path = audit_path()
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if source_id and entry.get("source_id") != source_id:
            continue
        out.append(entry)
    return out[-limit:]


def _short(value: object) -> object:
    if isinstance(value, str) and len(value) > _AUDIT_VALUE_CHARS:
        return value[:_AUDIT_VALUE_CHARS] + "…"
    return value


def _field_changes(before: Mapping, after: Mapping, fields) -> list[dict]:
    return [{"field": field, "from": _short(before.get(field)), "to": _short(after.get(field))}
            for field in fields if before.get(field) != after.get(field)]


def _unstamp(record: Mapping) -> dict:
    """The same record with the permission removed and nothing else decided.

    Restores the `content_state` the approval found, if this console is the
    one that changed it, and drops the snapshot key again so an un-approved
    file carries no leftovers from an approval that no longer stands.
    """
    out = {**record, **PERMISSION_DRAFT}
    if STATE_SNAPSHOT in out:
        restored = out.pop(STATE_SNAPSHOT)
        if restored:
            out["content_state"] = restored
        else:
            out.pop("content_state", None)
    return out


# ----------------------------------------------------------------- writing

def edit(source_id: str, project_id: str, body: Mapping) -> dict:
    """Replace the editable content of a curated file, keeping everything else.

    "Everything else" includes the approval metadata, the map link, the
    comments the sales team left in the file, and the slide records: this
    console edits the part it understands and copies the rest through
    untouched. It never invents a value — an empty box stays `null`, which
    is how the four blank facts (price, room types, promotions, opening
    hours) are supposed to stay until the owner types into them.
    """
    by = clean_name(body.get("by"))
    source, path, payload = _curated(source_id, project_id)
    editor = CURATED[source_id]["editor"]
    updated = dict(payload)
    changes: list[dict] = []

    if editor == "facts":
        rows = body.get("facts")
        if not isinstance(rows, list) or not rows:
            raise ConsoleError("ไม่มีรายการข้อเท็จจริงที่ส่งมา")
        facts: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ConsoleError("รูปแบบข้อเท็จจริงไม่ถูกต้อง")
            label = str(row.get("label") or "").strip()
            if not label:
                raise ConsoleError("ทุกบรรทัดต้องมีชื่อหัวข้อ")
            if label in seen:
                raise ConsoleError(f"หัวข้อซ้ำ: {label}")
            seen.add(label)
            raw = row.get("value")
            value = raw.strip() if isinstance(raw, str) else raw
            # Blank means "ยังไม่มีข้อมูล", and it must keep meaning that:
            # an empty string would read as an approved answer of nothing.
            facts.append({"label": label, "value": value if value else None})
        before = {str(item.get("label")): item.get("value")
                  for item in payload.get("facts", []) if isinstance(item, dict)}
        after = {item["label"]: item["value"] for item in facts}
        for label in list(before) + [key for key in after if key not in before]:
            if before.get(label) != after.get(label):
                changes.append({"field": f"facts/{label}",
                                "from": _short(before.get(label)),
                                "to": _short(after.get(label))})
        updated["facts"] = facts
    elif editor == "rules":
        rows = body.get("rules")
        if not isinstance(rows, list) or not rows:
            raise ConsoleError("ไม่มีกติกาที่ส่งมา")
        for row in rows:
            if not isinstance(row, dict):
                raise ConsoleError("รูปแบบกติกาไม่ถูกต้อง")
            key = str(row.get("key") or "").strip()
            if not key.endswith("_rule") or "/" in key or " " in key:
                raise ConsoleError(f"ชื่อกติกาต้องลงท้ายด้วย _rule: {key or '(ว่าง)'}")
            text = str(row.get("text") or "").strip()
            if not text:
                raise ConsoleError(f"กติกา {key} ว่าง — ถ้าต้องการลบให้ลบที่ไฟล์")
            if updated.get(key) != text:
                changes.append({"field": key, "from": _short(updated.get(key)),
                                "to": _short(text)})
            updated[key] = text
    elif editor == "qa":
        # A key the caller did not send is left as the file has it, rather
        # than read as an empty list: a page that fails to render one panel
        # must not be able to delete that panel's contents by saving.
        if body.get("entries") is not None:
            entries = _qa_entries(body.get("entries"))
            changes += _qa_entry_changes(payload.get("entries") or [], entries)
            updated["entries"] = entries
        if body.get("rules") is not None:
            rules = _qa_lines(body.get("rules"), "กฎที่คุมทุกคำตอบ")
            changes += _qa_list_changes(payload.get("rules") or [], rules, "rules")
            updated["rules"] = rules
        if body.get("decisions") is not None:
            decisions = _qa_lines(body.get("decisions"), "ข้อที่เจ้าของต้องตัดสิน")
            changes += _qa_list_changes(payload.get("decisions") or [], decisions,
                                        "decisions")
            updated["decisions"] = decisions
        if not any(key in body for key in ("entries", "rules", "decisions")):
            raise ConsoleError("ไม่มีเนื้อหาสคริปต์ที่ส่งมา")
    else:
        # The catalogue is 135 machine-written captions; this console shows
        # them so the signature is informed, but editing them belongs in the
        # tooling that regenerates them, not in a text box at a sales desk.
        raise ConsoleError("คลังสไลด์แก้ข้อความจากหน้านี้ไม่ได้ ทำได้แค่ดูและอนุมัติ")

    if not changes:
        # Not a refusal: pressing save twice is a thing a tired person does,
        # and answering it in the red box the failures use reads as "your
        # edit was lost". Nothing is written and no audit line is appended,
        # because nothing happened.
        return {"ok": True, "changed": 0, "unchanged": True,
                "message_th": "ไม่มีอะไรเปลี่ยน ข้อความตรงกับไฟล์อยู่แล้ว",
                "verdict": _verdict(payload, project_id)}

    # The approve-then-swap hole, and the reason this block is here: editing
    # an approved file used to leave the six approval fields untouched, so
    # the new text went out to customers signed by whoever approved the OLD
    # text, at the old timestamp. That is `script_approved` again — the
    # safety flag ending up on the unsafe side because nobody decided it
    # should. Changing the content withdraws the permission, in the same
    # atomic write, and the approve button has to be pressed again by a
    # person who has read what it now says.
    demoted = evaluate_claim(payload, project_id).allowed
    action = "edit"
    if demoted:
        updated = _unstamp(updated)
        changes += _field_changes(payload, updated, APPROVAL_FIELDS)
        action = "edit_demoted"

    warning = _persist(source_id, path, updated, str(source.get("location") or ""))
    entry = _record(source_id, action, by, changes)
    message = ("แก้เนื้อหาแล้ว การอนุมัติเดิมถูกถอนอัตโนมัติ "
               "ต้องอนุมัติใหม่ Emma จึงจะพูดข้อความใหม่นี้ได้"
               if demoted else "บันทึกเนื้อหาแล้ว ยังไม่ได้อนุมัติ")
    return {"ok": True, "changed": len(changes), "audit": entry,
            "demoted": demoted,
            "message_th": message + ("\n" + warning if warning else ""),
            "verdict": _verdict(updated, project_id)}


def approve(source_id: str, project_id: str, body: Mapping) -> dict:
    """Stamp the six fields `evaluate_claim` requires, on the server's clock.

    The page never sends a timestamp and never sends a name it remembered:
    the two things that decide who is responsible and from when are produced
    here. `effective_at` equals `approved_at` rather than being a field the
    page can choose, because a future date would silently leave the source
    denied and look approved on screen.
    """
    by = clean_name(body.get("by"))
    state = str(body.get("content_state") or "").strip()
    if state not in CONTENT_STATES:
        raise ConsoleError(
            "ต้องเลือกสถานะเนื้อหาจากรายการของนโยบายเท่านั้น: " + ", ".join(sorted(CONTENT_STATES)))
    if body.get("confirm") is not True:
        raise ConsoleError("ต้องติ๊กยืนยันว่าอ่านและรับผิดชอบข้อมูลชุดนี้ก่อน")
    source, path, payload = _curated(source_id, project_id)

    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    metadata = {
        "approval_status": "approved",
        "approved_by": by,
        "approved_at": stamp,
        "effective_at": stamp,
        "disclosure_scope": "customer",
        "content_state": state,
    }
    updated = {**payload, **metadata}
    if STATE_SNAPSHOT not in updated:
        # Park what was there so `revoke` can undo the whole approval and
        # not just five sixths of it. Only set on the first approval, so a
        # re-approval cannot overwrite the original with its predecessor's
        # pick.
        updated[STATE_SNAPSHOT] = payload.get("content_state")
    changes = _field_changes(payload, updated, APPROVAL_FIELDS)
    note = ""
    if CURATED[source_id]["editor"] == "catalog":
        # The runtime asks the policy about each slide (presentation_policy
        # evaluates the image record, not the file), so a top-level stamp
        # alone would leave all 135 captions denied while the page showed
        # green. Both levels, and the count goes in the audit line.
        images = payload.get("images") if isinstance(payload.get("images"), list) else []
        updated["images"] = [{**item, **metadata} if isinstance(item, dict) else item
                             for item in images]
        note = f"stamped {len(images)} slide records"
    # The document says "ร่าง ยังไม่อนุมัติ" in its own second line, so the
    # approval has to reach the copy too or the repo starts claiming the
    # opposite of the file the runtime reads.
    _persist(source_id, path, updated, str(source.get("location") or ""))
    verdict = _verdict(updated, project_id)
    entry = _record(source_id, "approve", by, changes, note)
    return {"ok": True, "verdict": verdict, "audit": entry,
            "caveat_th": state_instruction(state)}


def revoke(source_id: str, project_id: str, body: Mapping) -> dict:
    """Back to draft/internal in one click.

    A safety switch that cannot be turned off does not get turned on. This
    removes the *permission*, never the content: facts, rules and slide
    records are copied through exactly as they are, and the `.bak` plus the
    audit line keep the trail of what was approved and by whom.

    It also puts `content_state` back to what the approval found (see
    `STATE_SNAPSHOT`), because that field is content and not permission —
    leaving a weakened caveat behind is the one way an undo could make the
    next approval more dangerous than the one it undid.
    """
    by = clean_name(body.get("by"))
    if body.get("confirm") is not True:
        raise ConsoleError("ต้องติ๊กยืนยันว่าจะถอนการอนุมัติ")
    source, path, payload = _curated(source_id, project_id)
    updated = _unstamp(payload)
    if CURATED[source_id]["editor"] == "catalog":
        images = payload.get("images") if isinstance(payload.get("images"), list) else []
        # Slide records get the permission cleared only. Their content_state
        # is rewritten wholesale by every approve and is read by nothing
        # while the record is a draft, so restoring it per image would be
        # 135 lines of diff that change no decision.
        updated["images"] = [{**item, **PERMISSION_DRAFT} if isinstance(item, dict) else item
                             for item in images]
    changes = _field_changes(payload, updated, APPROVAL_FIELDS)
    _persist(source_id, path, updated, str(source.get("location") or ""))
    entry = _record(source_id, "revoke", by, changes)
    return {"ok": True, "verdict": _verdict(updated, project_id), "audit": entry}
