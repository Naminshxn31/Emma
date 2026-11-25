"""The ceremony's extra lines (owner 24 Nov 2026 ~19:20: "ก็ให้มันทำเป็นบททั้งหมดเลย ... เหมือนเพิ่มสคริปให้เยอะขึ้นกรณีคุณมิกิถามเรื่องอื่น").

    python scripts/build_ceremony_extra.py            # data/ceremony/floor1_ceremony.extra.json, every line a draft
    python scripts/build_ceremony_extra.py --check    # what is wrong with the lines, nothing written

One line per approved motion story the script does not answer (data/ceremony/offscript_stories.json): its English lines as
the words she says, its Thai lines as the hall's subtitle, every number sourced from the story itself. A line already
signed for the same words keeps its signature; changed words are a draft again (scripts/approve_event_script.py --file ...).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TARGET = ROOT / "data" / "ceremony" / "floor1_ceremony.extra.json"
#: Phrases that only look like a figure (event_script `words_ok`: part of what is signed).
WORDS_OK = {
    "developer_projects": ["as the next one"],
    "fitness_where": ["half of them curved", "ครึ่งหนึ่งเป็นแบบสายพานโค้ง"],
    "journey_future_where": ["ผนังด้านหนึ่ง"],
    "lobby_where": ["the very first step"],
    "nearby_7eleven": ["7-Eleven", "in one stop"],
    "nearby_attractions": ["ตลาดน้ำสี่ภาค"],
    "pools_all": ["The main one"],
    "salt_sauna_where": ["the first room"],
    "security": ["one of the four pillars", "หนึ่งในสี่เสาหลัก"],
    "travel_airports": ["an hour and a half", "ชั่วโมงครึ่ง"],
    "travel_bangkok": ["an hour and a half", "1 ชั่วโมงครึ่ง"],
    "unit_one_bedroom": ["roomier one-bedroom", "ห้องนอนเดียว"],
}
#: The Thai line where the subtitle must say what the English says (nothing else is changed).
THAI = {
    "fitness_where": [("ใครเข้ายิมแล้วไม่รู้จะเล่นท่าไหน หรือควรออกกำลังกายแบบไหน", "ใครเข้ายิมแล้วยังเลือกไม่ถูกว่าจะเล่นท่าไหน")],
    "nearby_attractions": [("ตลาดน้ำสี่ภาคก็ประมาณ 6 นาทีเหมือนกัน", "ตลาดน้ำสี่ภาคก็ใกล้เคียงกัน")],
}
_FIGURE = re.compile(r"(?:ประมาณ |ราว |อีก )?[0-9][0-9,.]*(?: ถึง [0-9][0-9,.]*)? ?(?:นาที|ตารางเมตร|เมตร|ชั่วโมง|เครื่อง|สระ|ชั้น)")
KEEP = ("approved_by", "approved_at", "approved_sha256", "approved_voice_sha256")


def build() -> dict:
    from app import ceremony_stories, event_script, stories
    from app.config import settings

    settings.stories_enabled = True
    old = {}
    if TARGET.exists():
        old = {c["id"]: c for c in json.loads(TARGET.read_text(encoding="utf-8"))["cues"]}
    have = stories.available()
    cues = []
    for topic in sorted(ceremony_stories.listed()):
        story = have[topic]
        th = " ".join(" ".join(line["th"].split()) for line in story["lines"])
        en = " ".join(" ".join(line["en"].split()) for line in story["lines"])
        numbers = []
        for old_th, new_th in THAI.get(topic, ()):
            assert th.count(old_th) == 1, (topic, old_th)
            th = th.replace(old_th, new_th)
        for line in story["lines"]:
            text = " ".join(line["th"].split())
            if text not in th:
                # (a line whose Thai was changed: its figures one by one, as the story gives them)
                numbers += [{"say": m.group(0).strip(), "source": "story:" + topic, "as": m.group(0).strip()}
                            for m in _FIGURE.finditer(text) if m.group(0).strip() in th]
                continue
            if event_script._NUMBER.search(text) or event_script.thai_numerals(text):
                numbers.append({"say": text, "source": "story:" + topic, "as": text})
        cue = {"id": "x_" + topic, "scene": None, "scene_th": "คุณมิกิถามเรื่องนอกบท", "show": topic,
               "host_th": None, "th": th, "en": en, "numbers": numbers,
               **({"words_ok": WORDS_OK[topic]} if topic in WORDS_OK else {}),
               "note_th": "บรรทัดของเรื่องเล่าที่อนุมัติแล้ว (stories.json: %s) — ภาพบนจอคือเรื่องเดียวกัน" % topic,
               "approved_by": None, "approved_at": None, "approved_sha256": None, "approved_voice_sha256": None}
        was = old.get(cue["id"])
        if was is not None and was.get("approved_sha256") == event_script.digest(event_script._normalise(cue)):
            cue.update({k: was.get(k) for k in KEEP})
        cues.append(cue)
    return {"format": event_script.FORMAT, "speak": "en",
            "about": "บทเพิ่มของพิธี: ตอบเรื่องที่บทหลักไม่มี (เจ้าของ 24 พ.ย.) — สร้างจากเรื่องเล่าที่อนุมัติแล้วด้วย scripts/build_ceremony_extra.py "
                     "ไม่อยู่ในลำดับพิธี ทุกบทเป็นร่างจนกว่าคนจะเซ็น (approve_event_script.py --file data/ceremony/floor1_ceremony.extra.json)",
            "cues": cues}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    from app import event_script

    doc = build()
    bad = 0
    for c in event_script.cues(doc):
        probs = event_script._problems_in(c, "en")
        if probs:
            bad += 1
            print(c["id"], probs)
    print("%d lines, %d with problems, %d words of English" % (len(doc["cues"]), bad,
                                                              sum(len(c["en"].split()) for c in doc["cues"])))
    if not args.check:
        TARGET.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print("wrote", TARGET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
