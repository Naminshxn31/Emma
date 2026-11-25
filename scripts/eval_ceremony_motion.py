#!/usr/bin/env python3
"""How well "ตอบสด"'s topic slides follow the host (app/ceremony_motion.py `decide`), offline.

    python scripts/eval_ceremony_motion.py            # both sets, a table
    python scripts/eval_ceremony_motion.py --misses   # and every case that did not come out as expected

The cases are the host's ad-lib lines with the slide that may show (or `none` = the logo loop), written
blind by separate agents from the slides' descriptions only (data/eval/ceremony_motion_cases_*.json):
A was used to tune the keywords, B was written after that tuning and never tuned on, C was written after
review round 1 (23 Nov) and scored before anything was changed for it. Counted per set, ON THE SCREEN:

* hit          the expected topic decided (`slide` of them put a slide up -- a topic without one,
               technology, shows the logo, see app/ceremony_motion.py TOPICS)
* wrong_topic  a slide up that is not the expected topic's -- the target is 0
* fallback     the logo where a topic was expected (no match: preferred to a weak one)
* right_none   the logo where none belongs
* partial      cases where a slide not the expected one would go up at SOME prefix of the words -- the
               worst case of a host pausing longer than QUIET_S after every single word (step(), the
               rule the server runs, over each prefix of one utterance)

No model, no network: the matcher is pure, and so is this.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SETS = {"A (tuned on)": ROOT / "data" / "eval" / "ceremony_motion_cases_a.json",
        "B (held out)": ROOT / "data" / "eval" / "ceremony_motion_cases_b.json",
        "C (held out)": ROOT / "data" / "eval" / "ceremony_motion_cases_c.json"}


def _cue(topic):
    from app import ceremony_motion

    return ceremony_motion.TOPICS[topic]["cue"] if topic else None


def _prefixes(text: str) -> list[str]:
    """Every prefix the transcription could have stopped at: word by word (Thai by the same dictionary)."""
    import re

    from app import event_script

    out, said = [], ""
    for piece in re.findall(r"\S+\s*", text):
        if re.search(r"[\u0e00-\u0e7f]", piece):
            for w in event_script._segment(piece):
                said += w
                out.append(said)
        else:
            said += piece
            out.append(said)
    return out or [text]


def score(cases: list[dict]) -> dict:
    from app import ceremony_motion

    out = {"n": len(cases), "hit": 0, "slide": 0, "wrong_topic": 0, "fallback": 0, "right_none": 0, "partial": 0,
           "misses": []}
    for c in cases:
        got, why = ceremony_motion.decide(c["text"])
        want = c["expect"]
        want_slide = _cue(want) if want != "none" else None
        if _cue(got) and _cue(got) != want_slide:
            key = "wrong_topic"
        elif want == "none":
            key = "right_none"
        elif got == want:
            key = "hit"
            out["slide"] += 1 if want_slide else 0
        else:
            key = "fallback"
        out[key] += 1
        up, wrong = None, False                          # the screen through every prefix (logo first)
        for said in _prefixes(c["text"]):
            change, topic, _ = ceremony_motion.step(up, 1.0, said, 1.0)
            if change:
                up = topic
            if _cue(up) and _cue(up) != want_slide:
                wrong = True
        out["partial"] += wrong
        if key not in ("hit", "right_none") or wrong:
            out["misses"].append({"id": c["id"], "kind": key + (" +partial" if wrong else ""), "expect": want,
                                  "got": got or "none", "why": why, "text": c["text"]})
    return out


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")    # (Thai cases on a Windows console)
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--misses", action="store_true", help="list every case that did not come out as expected")
    args = ap.parse_args(argv)
    print("%-14s %4s %5s %6s %12s %9s %11s %8s" % ("set", "n", "hit", "slide", "wrong_topic", "fallback", "right_none",
                                                  "partial"))
    worst = 0
    for name, path in SETS.items():
        cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
        s = score(cases)
        worst = max(worst, s["wrong_topic"])
        topics = sum(1 for c in cases if c["expect"] != "none")
        print("%-14s %4d %2d/%-2d %6d %12d %9d %8d/%-2d %8d" % (name, s["n"], s["hit"], topics, s["slide"],
                                                              s["wrong_topic"], s["fallback"], s["right_none"],
                                                              s["n"] - topics, s["partial"]))
        if args.misses:
            for m in s["misses"]:
                print("   %-11s %-4s expect %-10s got %-10s (%s) %s" % (m["kind"], m["id"], m["expect"], m["got"],
                                                                     m["why"], m["text"]))
    return 1 if worst else 0


if __name__ == "__main__":
    raise SystemExit(main())
