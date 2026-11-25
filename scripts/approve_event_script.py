#!/usr/bin/env python3
"""Record that a person has read the ceremony cues and stands behind them.

    python scripts/approve_event_script.py --status          # the cues, then the running order
    python scripts/approve_event_script.py --by "pete"                    # every clean cue
    python scripts/approve_event_script.py --by "pete" --ids s2_location,s3_units
    python scripts/approve_event_script.py --revoke s2_location

The cues (CEREMONY_FILE, data/ceremony/floor1_ceremony.json -- the Emma Ceremony app's
own file since 9 Nov) start as drafts, and a draft is never said (app/event_script.py,
deny by default). This is the sign-off, a separate step
a PERSON runs after reading each line and the number rows under it: it records who
and when and pins the digest of the cue (`approved_sha256` over the words, the live
brief, the number sources and the clip), so editing a cue afterwards makes it a
draft again until somebody runs this again. Nothing in the code calls it, and
nothing writes `approved_by` on anyone's behalf (the facts' rule, 6 Oct).

Refuses a cue that breaks a content rule (a number with no approved source,
"ไม่มีข้อมูล", ...): it would not be said anyway, and an approval should cover what
is actually said.

**The recording is approved by listening, separately.** Reading the words is one
check, hearing the clip another, so `--by NAME` signs the words only and
`--by NAME --listened` also pins each cue's current clip (`approved_voice_sha256`,
from `scripts/render_event_script.py`) -- run that after listening to the clips in
data/ceremony/voice/. A clip rendered later (`--fresh`, a new take) does not match the
pin, and the stage reads the line live instead of playing audio nobody heard
(review 6 Nov); listen and run `--listened` again to re-pin.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import event_script  # noqa: E402

AUDIT = ROOT / "data" / "registry" / "approval_audit.jsonl"
SOURCE_ID = "event_script"


def _now() -> str:
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


#: The language the file's cues are said in (`"speak"`, event_script.speak): the clip a
#: `--listened` pins and the "no English line" check follow it. Set by status_lines/approve.
_SPEAK = {"lang": "th"}


def _clip_sha(cue: dict) -> str | None:
    """sha256 of the recorded clip the stage would play for this cue's words, or None."""
    try:
        from app import event_voice
    except ImportError:
        return None
    fn = getattr(event_voice, "clip_sha", None)
    if fn is None:
        return None
    return fn(cue, _SPEAK["lang"]) if _SPEAK["lang"] != "th" else fn(cue)


def _voice_blocker(raws: list[dict]) -> str | None:
    """Why `--listened` cannot pin the recordings of these cues (None = it can). 24 Nov: on a Python
    without the app's packages (plain `python` here has no dotenv) `from app import event_voice` fails,
    `_clip_sha` answered None for every cue, and `--by NAME --listened` signed the WORDS, pinned no
    recording and said nothing -- the owner would believe the takes were approved, and on stage Gemini
    would read every line live instead of playing them. The run is refused, before anything is written."""
    try:
        from app import event_voice  # noqa: F401
        if getattr(event_voice, "clip_sha", None) is None:
            raise ImportError("app.event_voice has no clip_sha")
    except Exception as exc:                                 # noqa: BLE001 -- any import failure
        return ("cannot load app.event_voice (%s: %s)\n"
                "ไม่สามารถโหลด app.event_voice ได้ จึงไม่รู้ว่าเสียงอัดไฟล์ไหนคือเทคที่ฟังแล้ว\n"
                "Run it with the project's Python:  .venv/Scripts/python.exe scripts/approve_event_script.py ...\n"
                "รันด้วย Python ของโปรเจกต์: .venv/Scripts/python.exe scripts/approve_event_script.py ..."
                % (type(exc).__name__, exc))
    missing = [r.get("id") for r in raws if event_script._normalise(r).get("th")
               and not _clip_sha(event_script._normalise(r))]
    if missing:
        return ("no usable recording for: %s\n"
                "ไม่มีเสียงอัดที่ใช้ได้ (ไฟล์หาย หรือไม่ตรงกับคำในบท/ดัชนีเสียง) ของ: %s\n"
                "Render the clips first (scripts/render_event_script.py), listen, then run --listened again."
                % (", ".join(map(str, missing)), ", ".join(map(str, missing))))
    return None


def _voice_state(cue: dict) -> str:
    sha = _clip_sha(cue)
    if not sha:
        return "no recording"
    return "recording approved" if cue.get("approved_voice_sha256") == sha else "recording NOT listened"


def status_lines(data: dict) -> list[str]:
    _SPEAK["lang"] = event_script.speak(data)
    out = ["spoken in: %s%s" % (_SPEAK["lang"], " (th = the screen's subtitle)" if _SPEAK["lang"] == "en" else "")]
    for c in event_script.cues(data):
        state = "approved" if event_script.approved(c) else (
            "CHANGED since approval" if c.get("approved_by") else "draft")
        bad = event_script._problems_in(c, _SPEAK["lang"])
        kind = "line" if c.get("th") else ("live" if c.get("live_th") else "empty")
        voice = _voice_state(c) if c.get("th") else "-"
        # an English nobody says (a Thai-spoken file) never blocks the Thai line; it is still named
        english = event_script.en_problems(c) if _SPEAK["lang"] != "en" else []
        out.append("%-16s scene %-2s %-5s %-24s %-22s%s%s" % (
            c["id"], c.get("scene") or "-", kind, state, voice, ("  BREAKS: " + ",".join(bad)) if bad else "",
            ("  ENGLISH (not spoken): " + ",".join(english)) if english else ""))
    return out


def _event_beside(path: Path) -> dict | None:
    """The event file whose spots and films the ceremony's order names (EVENT_FILE, or the
    tracked floor-1 one when this machine's EVENT_FILE is blank), its ids as the server reads
    them. Not the file beside the script any more (9 Nov): the ceremony is an app of its own
    that SHARES the event's spots and clips -- `path` only stops a script being checked against
    itself. None = no such file."""
    from app import event_host
    from app.config import settings

    name = (settings.event_file or "").strip() or "data/events/floor1_event.json"
    target = Path(name) if Path(name).is_absolute() else ROOT / name
    if target == path or not target.is_file():
        return None
    try:
        return event_host._normalise(json.loads(target.read_text(encoding="utf-8")), {})
    except (OSError, ValueError):
        return None


def order_lines(data: dict, event: dict | None = None) -> list[str]:
    """The running order staff press on the phone (app/event_order.py), step by step, with
    what this file alone can say about each: a cue's approval and content problems, and
    whether every id exists. What the robot decides at the moment (a call on its face,
    walking switched on, a recorded spot, the arm) is on the phone, not here."""
    steps = event_script.order(data)
    if steps is None:
        return []
    cues = {c["id"]: c for c in event_script.cues(data)}
    out = ["", "running order:"]
    for problem in event_script.order_problems(data, event):
        out.append("  ORDER PROBLEM: %s" % problem)
    for sc in steps:
        out.append("  %s. %s" % (sc["n"], sc["title_th"] or ""))
        for st in sc["steps"]:
            what = (st["screen"] or st["spot"] or st["action"] or st["cue"] or st["clip"] or st["to"] or "")
            if st["do"] == "device":
                # a venue light / curtain (app/venue_devices.py): on the robot's order only where it drives it
                what = "%s %s" % (st.get("device") or "?", st["action"] or "?")
            state = ""
            if st["do"] == "cue" and what in cues:
                c = cues[what]
                bad = event_script._problems_in(c, _SPEAK["lang"])
                if bad:
                    state = "BREAKS: " + ",".join(bad)
                elif not c.get("th"):
                    state = "live brief" if c.get("live_th") else "no line yet"
                else:
                    state = "approved" if event_script.approved(c) else "draft"
            show = ("  + story " + st["show"]) if st["show"] else ""
            label = ("  -- " + st["label_th"]) if st["label_th"] else ""
            out.append("     %-2s %-6s %-20s %-16s%s%s" % (st["i"], st["do"] or "?", what, state, show, label))
    return out


def approve(path: Path, by: str, ids: set[str] | None = None, *, audit: Path | None = AUDIT,
            voice: bool = False) -> list[str]:
    if not by.strip():
        raise SystemExit("--by needs the approver's name")
    data = json.loads(path.read_text(encoding="utf-8"))
    _SPEAK["lang"] = event_script.speak(data)
    raws = [r for r in data.get("cues") or [] if isinstance(r, dict) and (ids is None or r.get("id") in ids)]
    if ids is not None and {r.get("id") for r in raws} != ids:
        raise SystemExit("no such cue id: %s" % ", ".join(sorted(ids - {r.get("id") for r in raws})))
    # the digest is over the NORMALISED cue (a number row without "factor" means 1):
    # signing the raw dict would never match what the runtime checks
    broken = {r.get("id"): event_script._problems_in(event_script._normalise(r), _SPEAK["lang"]) for r in raws}
    broken = {k: v for k, v in broken.items() if v}
    if broken and ids is not None:
        raise SystemExit("not approved -- fix these first:\n  " + "\n  ".join(
            "%s: %s" % (k, ", ".join(v)) for k, v in broken.items()))
    # "every clean cue": a cue still waiting for its words (s6_tech_page) must not
    # hold the rest back -- it is skipped and named, never signed
    for k, v in broken.items():
        print("skipped %s: %s" % (k, ", ".join(v)))
    raws = [r for r in raws if r.get("id") not in broken]
    if voice:
        blocker = _voice_blocker(raws)
        if blocker:
            raise SystemExit("--listened refused, nothing was written / ไม่ได้เขียนอะไรลงไฟล์:\n" + blocker)
    at = _now()
    done = []
    for raw in raws:
        cue = event_script._normalise(raw)
        if not event_script.approved(cue):
            raw.update({"approved_by": by.strip(), "approved_at": at, "approved_sha256": event_script.digest(cue)})
            done.append(raw["id"])
        sha = _clip_sha(cue) if voice and cue.get("th") else None
        if sha and raw.get("approved_voice_sha256") != sha:
            raw["approved_voice_sha256"] = sha
            done.append(raw["id"] + ":voice")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if audit is not None and done:
        with audit.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": at, "source_id": SOURCE_ID, "action": "approve",
                                "by": by.strip(), "cues": done}, ensure_ascii=False) + "\n")
    return done


def revoke(path: Path, ids: set[str]) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    done = []
    for raw in data.get("cues") or []:
        if isinstance(raw, dict) and raw.get("id") in ids and (raw.get("approved_by")
                                                                or raw.get("approved_voice_sha256")):
            raw.update({"approved_by": None, "approved_at": None, "approved_sha256": None,
                        "approved_voice_sha256": None})
            done.append(raw["id"])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--file", type=Path, default=None)
    ap.add_argument("--by", default="")
    ap.add_argument("--ids", default="", help="comma-separated cue ids (default: all)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--revoke", default="", help="comma-separated cue ids back to draft")
    ap.add_argument("--listened", action="store_true",
                    help="also pin each cue's recorded clip as listened to (run after listening)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    path = args.file or event_script.path()
    if path is None or not path.is_file():
        print("no ceremony script at %s (CEREMONY_FILE)" % path)
        return 1
    if args.revoke:
        done = revoke(path, {i.strip() for i in args.revoke.split(",") if i.strip()})
        print("back to draft: %s" % (", ".join(done) or "(nothing was approved)"))
        return 0
    if args.status or not args.by:
        data = json.loads(path.read_text(encoding="utf-8"))
        print("\n".join(status_lines(data) + order_lines(data, _event_beside(path))))
        return 0
    ids = {i.strip() for i in args.ids.split(",") if i.strip()} or None
    done = approve(path, args.by, ids, voice=args.listened, audit=AUDIT)    # AUDIT looked up now: tests redirect it
    print("approved by %s: %s" % (args.by.strip(), ", ".join(done) or "(already approved)"))
    if args.listened:
        print("%d cue(s): words signed, %d recording(s) pinned" % (
            sum(1 for d in done if not d.endswith(":voice")), sum(1 for d in done if d.endswith(":voice"))))
    if any(d.endswith(":voice") for d in done):
        print("(:voice = the recorded clip of that line is pinned as listened to -- "
              "a later render is read live until approved again)")
    print("next: python scripts/render_event_script.py --dry-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
