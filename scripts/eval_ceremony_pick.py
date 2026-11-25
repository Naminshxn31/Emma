"""Offline eval of the script-first slide picker (app/ceremony_pick.py, owner 24 Nov ~12:00).

No model, no robot: the REAL host lines, cues and slides; the SAME `ceremony_pick.pick`. Per cue with a slide:
  full    -- his scripted line (host_en)                      + her answer = the cue's approved `en` line
  partial -- a short topic question (PARTIAL, hand-written)    + her answer = the first ~60% of the `en` line
  loose   -- a paraphrase (tests/data/ceremony_follow_en.json) + her answer = the `en` line with every 4th word gone
Off-script: tests/data/ceremony_follow_en.json off_en / off_th (+ the extra ones here) with GENERIC live answers.
Reported: correct / wrong / none (fell back to the topic slide or the logo) for the early pick (his words) and the
refined one (his words + her answer); off-script lines that showed a script slide; how often the topic fallback ran.
The paraphrases and answers are OUR text, not Khun Miki's or Gemini's: this says the picker works on the shape of the
data, not how they will really phrase it.

    python scripts/eval_ceremony_pick.py [-v]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PARTIAL = {
    "s02_l03": "Emma, what can you do for us?",
    "s02_l09": "What makes this project different?",
    "s03_l03": "Where is the project located?",
    "s04_l03": "Tell us about the units.",
    "s04_l06": "What about the facilities?",
    "s05_l03": "Let's try a real customer situation.",
    "s05_l06": "A room with a sea view please.",
    "s06_l03": "Tell us about the facilities.",
    "s07_l02": "Tell us about the technology.",
    "s07_l07": "What does this page show?",
    "s09_l02": "Tell us what makes this project special.",
}
EXTRA_OFF = ["How tall is the building?", "Which floor is the pool on?", "Is there parking for guests?",
             "Who designed the logo?", "Can you sing something for us?"]
GENERIC_ANSWERS = [
    "Of course, happy to help. I do not have that detail right now, please ask our sales team.",
    "That is a great question. Thank you for asking, everyone is welcome here today.",
    "Sure. I am glad to be here with all of you and I hope everybody is enjoying the event.",
    "I can only speak about the project, but our team will gladly assist you after the ceremony.",
]


def _sample(near_n: int):
    from app import ceremony_follow, ceremony_pick

    tab = ceremony_pick.table()
    ids = list(tab["cues"])
    lines = ceremony_follow.lines()
    return ids, lines


def run(verbose: bool = False) -> dict:
    from app import ceremony_pick, ceremony_motion, event_script
    from app.config import settings

    settings.stories_enabled = True                      # (the robot has it on; this PC may not)
    ceremony_pick.reset()
    tab = ceremony_pick.table()
    ids = list(tab["cues"])
    lines = __import__("app.ceremony_follow", fromlist=["x"]).lines()
    data = json.loads((ROOT / "tests/data/ceremony_follow_en.json").read_text(encoding="utf-8"))
    res = {k: {"ok": 0, "wrong": 0, "none": 0} for k in ("full", "partial", "loose")}
    res_a = {k: {"ok": 0, "wrong": 0, "none": 0} for k in ("full", "partial", "loose")}
    bad = []
    for cue in ids:
        en = event_script.cue(cue)["en"]
        words = en.split()
        near = {cue}                                     # (the cue is among the next unplayed: the usual case)
        others = [c for c in ids if c != cue][:2]
        near |= set(others)
        asks = {"full": (lines.get(cue) or {}).get("host_en"),
                "partial": PARTIAL.get(cue),
                "loose": (data["hand"].get(cue) or [None, None])[0]}
        answers = {"full": en, "partial": " ".join(words[:max(8, int(len(words) * 0.6))]),
                   "loose": " ".join(w for k, w in enumerate(words) if k % 4 != 3)}
        for kind, ask in asks.items():
            if not ask:
                continue
            e = ceremony_pick.pick(ask, None, near)
            r = ceremony_pick.pick(ask, answers[kind], near)
            for tab_, p in ((res, e), (res_a, r)):
                if p["verdict"] != "pick":
                    tab_[kind]["none"] += 1
                elif p["cue"] == cue:
                    tab_[kind]["ok"] += 1
                else:
                    tab_[kind]["wrong"] += 1
                    bad.append((kind, p["stage"], cue, p["cue"], p["score"]))
            if verbose:
                print(cue, kind, "early", e["cue"], e["score"], e["verdict"], "| refine", r["cue"], r["score"], r["verdict"])
    off = list(data["off_en"]) + list(data["off_th"]) + EXTRA_OFF + list(PARTIAL.values())[:0]
    shown_e = shown_r = topic = total = 0
    for k, q in enumerate(off):
        total += 1
        near = set(ids[:3])
        e = ceremony_pick.pick(q, None, near)
        r = ceremony_pick.pick(q, GENERIC_ANSWERS[k % len(GENERIC_ANSWERS)], near)
        shown_e += e["verdict"] == "pick"
        shown_r += r["verdict"] == "pick"
        if e["verdict"] == "pick" or r["verdict"] == "pick":
            bad.append(("off", q[:40], e["cue"], r["cue"], max(e["score"], r["score"])))
        elif ceremony_motion.decide(q)[0] is not None:
            topic += 1
    return {"early": res, "refined": res_a, "off_total": total, "off_early_shown": shown_e, "off_refine_shown": shown_r,
            "off_topic_fallback": topic, "bad": bad, "cues": len(ids)}


def main() -> int:
    out = run("-v" in sys.argv)
    print("cues with a slide:", out["cues"])
    for stage in ("early", "refined"):
        print(stage)
        for kind, c in out[stage].items():
            n = sum(c.values()) or 1
            print("  %-8s correct %5.1f%%  wrong %5.1f%%  none(fallback) %5.1f%%  (n=%d)" %
                  (kind, 100 * c["ok"] / n, 100 * c["wrong"] / n, 100 * c["none"] / n, n))
    print("off-script lines: %d; script slide shown early %d, after her answer %d; topic-slide fallback used %d" %
          (out["off_total"], out["off_early_shown"], out["off_refine_shown"], out["off_topic_fallback"]))
    for b in out["bad"]:
        print("  BAD", b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
