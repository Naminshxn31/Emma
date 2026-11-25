#!/usr/bin/env python3
"""The ceremony's lines as the show team needs them: what Emma SAYS and its Thai subtitle.

    python scripts/export_ceremony_subtitles.py            # write the CSV and the paired script (doc)
    python scripts/export_ceremony_subtitles.py --check    # exit 1 when either is out of step

Owner, 17 Nov 2026: Emma's voice in the ceremony is English, the Thai goes on the screen as
a subtitle, and the screen is a motion presentation made outside this repo ("เดี๋ยวจะทำเป็น
โมชั่นพรีเซ้นมาให้แทน แล้วก็เพิ่มซับด้านล่างไป"). So these two files are the hand-over:

* `<ceremony file>.subtitles.csv` -- one row per line in the order staff press them: the
  English as spoken, the Thai as shown, and how long she takes to say it;
* `docs/emma-ceremony-script-en-th.md` -- the same, scene by scene with the steps between
  the lines (walks, gestures, the show team's own cues), for a person to read.

`seconds` is the length of the cue's own recorded clip when there is one (the rendered
English in data/ceremony/voice, silence trimmed) -- `timing` says "clip". Without a clip it
is an estimate from the line's length (`timing` "estimate", "est." in the doc): good for
laying the deck out, not for cutting to. A cue no step of the order presses is listed last
with an empty `order` (the backup quotation line: staff press it by hand when needed).

Generated, never edited by hand: a test holds both files to the ceremony file
(tests/test_ceremony_english.py), so a changed line with a stale subtitle is a red test.
The timing is left out of that comparison -- it comes from rendered audio, which is not in
the repo -- so the files are in step on every machine and carry the durations of the
machine that last exported them.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import event_script  # noqa: E402

FIELDS = ("order", "scene", "step", "cue", "host_th", "en", "th", "seconds", "timing")
DOC = ROOT / "docs" / "emma-ceremony-script-en-th.md"
#: Spoken characters a second, for a line with no clip (scripts/render_event_script.py's own
#: planning figures; the English renders of 17 Nov ran 14-17).
CHARS_PER_S = {"th": 12.5, "en": 15.0}
#: A manual step whose label names one of these is the show team's, not staff beside Emma.
THEIRS = ("LED", "วิดีโอ", "ไฟ", "ผ้าม่าน", "ลูกโลก", "กล่อง")
WHO = {"walk": "Emma walks", "body": "Emma gesture", "handoff": "Emma → Emma Event app", "screen": "Emma's screen",
       "clip": "Film"}
NO_TIME = "—"


def _clip_seconds(cue: dict, lang: str, base: Path | None) -> float | None:
    """Length of this line's recorded clip, or None (no clip of these exact words)."""
    try:
        from app import event_voice

        index = event_voice.load_index(base)
        if not index:
            return None
        key = event_voice.clip_key(index["model"], index["voice"], lang, event_script.norm(cue.get(lang) or ""))
        entry = (index.get("clips") or {}).get(key)
        if not isinstance(entry, dict):
            return None
        if isinstance(entry.get("seconds"), (int, float)):
            return float(entry["seconds"])
        folder = base if base is not None else event_voice.folder()
        with wave.open(str(Path(folder) / entry["file"]), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:                                    # noqa: BLE001 -- no clip, then an estimate
        return None


def rows(doc: dict, *, voice_base: Path | None = None, clips: bool = True) -> list[dict]:
    lang = event_script.speak(doc)
    cues = {c["id"]: c for c in event_script.cues(doc)}
    out, used, n = [], set(), 0

    def row(cue: dict, scene, step: str, pressed: bool) -> dict:
        nonlocal n
        said = cue.get(lang) or ""
        secs = _clip_seconds(cue, lang, voice_base) if clips and said else None
        timing = "clip" if secs is not None else ("estimate" if said else "")
        if secs is None and said:
            secs = len(said) / CHARS_PER_S[lang]
        if pressed:
            n += 1
        return {"order": n if pressed else "", "scene": scene if scene is not None else "", "step": step,
                "cue": cue["id"], "host_th": cue.get("host_th") or "", "en": cue.get("en") or "",
                "th": cue.get("th") or "", "seconds": ("%.1f" % secs) if secs is not None else "", "timing": timing}

    for scene in event_script.order(doc) or []:
        for step in scene["steps"]:
            if step["do"] == "cue" and step["cue"] in cues:
                used.add(step["cue"])
                if not (cues[step["cue"]].get("th") or cues[step["cue"]].get("en")):
                    continue                     # a cue with no words yet (the demo room's): nothing to subtitle
                # `step` = the scene's title: the staff page shows no step numbers, the cue id is the handle
                out.append(row(cues[step["cue"]], scene["n"], scene.get("title_th") or "", True))
    for cid, cue in cues.items():
        if cid not in used and (cue.get("th") or cue.get("en")):
            out.append(row(cue, cue.get("scene"), "", False))
    return out


def render(doc: dict, **kw) -> str:
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows(doc, **kw))
    return buf.getvalue()


def words(csv_text: str) -> list[tuple]:
    """The CSV without its timing: what must match the ceremony file on every machine (the
    clips the seconds come from are rendered audio, not in the repo)."""
    keep = [f for f in FIELDS if f not in ("seconds", "timing")]
    return [tuple(r.get(f) or "" for f in keep) for r in csv.DictReader(io.StringIO(csv_text))]


def target(script: Path) -> Path:
    return script.with_name(script.stem + ".subtitles.csv")


# ---- the paired script, for people -------------------------------------------------------------

def _cell(text) -> str:
    return str(text or "").replace("|", "/").replace("\n", " ")


def _secs(r: dict) -> str:
    if not r.get("seconds"):
        return NO_TIME
    return r["seconds"] + (" est." if r.get("timing") == "estimate" else "")


def render_doc(doc: dict, **kw) -> str:
    """docs/emma-ceremony-script-en-th.md. `clips=False` = estimates only (the comparison a
    test and `--check` make, after `doc_words` has taken the timing out of both sides)."""
    table = rows(doc, **kw)
    by_cue = {r["cue"]: r for r in table}
    cues = {c["id"]: c for c in event_script.cues(doc)}
    pressed = [r for r in table if r["order"] != ""]
    extra = [r for r in table if r["order"] == ""]
    total = sum(float(r["seconds"]) for r in pressed if r["seconds"])
    est = any(r["timing"] == "estimate" for r in table)
    spoken = "English" if event_script.speak(doc) == "en" else "Thai"
    out = [
        "# Emma Ceremony — paired script: English (spoken) + Thai (subtitle)", "",
        "> **For the motion-presentation team.** Emma's **voice is %s**. The **Thai column is the subtitle** for the "
        "bottom of your screen — Emma's own page draws no subtitles. The durations are of **preview takes** and will be "
        "measured again once the owner has approved the lines; leave each subtitle up until she stops rather than "
        "cutting on the clock. The demo-room line in scene 5 has **no words yet** (it waits for the room number from "
        "sales)." % spoken, "",
        "Generated from `data/ceremony/floor1_ceremony.json` by `python scripts/export_ceremony_subtitles.py`, which "
        "also writes the same rows to `data/ceremony/floor1_ceremony.subtitles.csv` (UTF-8 with BOM, opens in Excel). "
        "Never edit either by hand: change the line in the ceremony file and run the script again.", "",
        "## How to read it", "",
        "- Rows are in the order staff press them on the ceremony page. The page shows no step numbers: a row is named "
        "by its **scene** and the **step's label as the page shows it**, and a line also by its **cue id**.",
        "- **Emma says (EN)** is read word for word. **Subtitle (TH)** is the Thai line, unchanged.",
        "- **Seconds** = length of Emma's rendered voice for that line, silence trimmed at both ends%s. Her reading on "
        "the day can differ by about 15%%." % (" (\"est.\" = no rendered take: estimated from the line's length)" if est else ""),
        "- TIMING-TOTAL",
        "- **SHOW TEAM** rows are not Emma's: the LED, the videos, the lights, the curtain, the globe, the box. Staff "
        "tick them on the page once they have really happened; Emma's \"…เรียบร้อยแล้ว\" line always comes after its tick.",
        "- Numbers are the approved ones: the beach is **about 600 metres in a straight line**, the indoor facilities "
        "**around 2,500 square metres (the sales team's estimate)**. The LED must show the same.",
        "- A line is said on stage only once the owner has approved it (`scripts/approve_event_script.py`); a draft "
        "is never said.", ""]
    out[out.index("- TIMING-TOTAL")] = (
        "- Total speech: **%.0f s over the %d lines in the running order**%s." % (
            total, len(pressed), " (+ the backup line, pressed by hand only)" if extra else ""))
    for sc in event_script.order(doc) or []:
        out += ["## Scene %s — %s" % (sc["n"], sc.get("title_th") or ""), "",
                "| On the page | Who | Cue | Host's lead-in (TH) | Emma says (EN) | Subtitle (TH) | Seconds |",
                "|---|---|---|---|---|---|---|"]
        for st in sc["steps"]:
            label = st.get("label_th") or ""
            if st["do"] == "device":
                # the robot's own light / curtain command (app/venue_devices.py): on the staff page only
                # where the robot drives that device -- the SHOW TEAM's tick right after it is the row
                # the show team acts on, with or without it (20 Nov)
                continue
            if st["do"] == "follow":
                # card 2 (24 Nov, app/ceremony_follow.py): the robot's own live mode, a switch on the staff page --
                # nothing the show team acts on, so not a row of their hand-over
                continue
            if st["do"] == "cue":
                c = cues.get(st["cue"]) or {}
                r = by_cue.get(st["cue"])
                shown = label or "Emma: %s" % st["cue"]
                if not r or r["order"] == "":
                    out.append("| %s | Emma | `%s` | %s | *(no line yet)* | %s | %s |" % (
                        _cell(shown), st["cue"], _cell(c.get("host_th")), NO_TIME, NO_TIME))
                else:
                    out.append("| %s | Emma | `%s` | %s | %s | %s | %s |" % (
                        _cell(shown), st["cue"], _cell(c.get("host_th")), _cell(r["en"]), _cell(r["th"]), _secs(r)))
            else:
                who = WHO.get(st["do"]) or ("**SHOW TEAM**" if any(k in label for k in THEIRS) else "Staff")
                what = label or st.get("spot") or st.get("action") or st.get("to") or st.get("screen") or st.get("clip") or ""
                out.append("| %s | %s | %s | %s | %s | %s | %s |" % (_cell(what), who, NO_TIME, NO_TIME, NO_TIME, NO_TIME, NO_TIME))
        out.append("")
    if extra:
        out += ["## Pressed by hand only (not in the running order)", "",
                "| Cue | Host's lead-in (TH) | Emma says (EN) | Subtitle (TH) | Seconds |", "|---|---|---|---|---|"]
        out += ["| `%s` | %s | %s | %s | %s |" % (r["cue"], _cell(r["host_th"]), _cell(r["en"]), _cell(r["th"]), _secs(r))
                for r in extra]
        out.append("")
    return "\n".join(out)


_SECONDS_CELL = re.compile(r"\| \d+\.\d(?: est\.)? \|$", re.M)
_TOTAL_LINE = re.compile(r"^- Total speech: \*\*\d+ s over", re.M)
_EST_NOTE = re.compile(r" \(\"est\.\" = no rendered take: estimated from the line's length\)")


def doc_words(text: str) -> str:
    """The doc without its timing (the seconds cells, the total, the "est." note): what must
    match the ceremony file on every machine."""
    text = _SECONDS_CELL.sub("| %s |" % NO_TIME, text)
    text = _TOTAL_LINE.sub("- Total speech: **N s over", text)
    return _EST_NOTE.sub("", text)


def in_step(script: Path, doc: dict, doc_file: Path) -> dict[str, bool]:
    out = target(script)
    return {str(out): out.is_file() and words(out.read_text(encoding="utf-8-sig")) == words(render(doc, clips=False)),
            str(doc_file): doc_file.is_file()
            and doc_words(doc_file.read_text(encoding="utf-8")).strip() == doc_words(render_doc(doc, clips=False)).strip()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--file", type=Path, default=None, help="the ceremony file (default: CEREMONY_FILE)")
    ap.add_argument("--doc", type=Path, default=None, help="the paired script to write (default: %s)" % DOC.name)
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 when the CSV or the doc is out of step")
    args = ap.parse_args(argv)
    script = args.file or event_script.path() or ROOT / "data" / "ceremony" / "floor1_ceremony.json"
    doc_file = args.doc or (DOC if args.file is None else script.with_name(script.stem + ".script-en-th.md"))
    doc = json.loads(script.read_text(encoding="utf-8"))
    if args.check:
        state = in_step(script, doc, doc_file)
        for name, same in state.items():
            print("%s is %s" % (name, "in step" if same else "OUT OF STEP -- run scripts/export_ceremony_subtitles.py"))
        return 0 if all(state.values()) else 1
    voice = script.parent / "voice"
    text = render(doc, voice_base=voice)
    out = target(script)
    out.write_text("﻿" + text, encoding="utf-8", newline="")     # BOM: Excel opens the Thai right
    doc_file.write_text(render_doc(doc, voice_base=voice) + "\n", encoding="utf-8", newline="\n")
    print("wrote %s (%d lines, spoken in %s)\nwrote %s" % (out, text.count("\n") - 1, event_script.speak(doc), doc_file))
    return 0


if __name__ == "__main__":
    sys.exit(main())
