"""The semantic safety net of the ceremony's follow mode (25 Nov 2026: Khun Miki speaks English on the day).

The word matcher (app/ceremony_follow.py) matches the host's transcribed words against his scripted lines, Thai and
the English translation (`host_en`). His own English will not be those words. This is the second look, for what the
word matcher found nothing in: the local multilingual MiniLM (app/embed_onnx.py -- the same model search uses, one
model for Thai and English, CPU only) compares what he said with the same next-cue candidates ONLY.

It is a net, not a second matcher. A MISS is acceptable (staff press by hand); a WRONG cue is not. So it fires only
when ALL of these hold (the thresholds are measured: scripts/eval_ceremony_follow.py --semantic, docs/emma-ceremony.md):

* the best candidate is similar enough on its own (`SIM`);
* it leads the second candidate clearly (`MARGIN`);
* it beats the closest OFF-SCRIPT line (`ANCHORS`: the things an MC says to guests -- thanks, applause, jokes, a
  question to Emma) clearly (`BASE`): talk that is closer to "let's give a hand" than to his line is not his line.

No model on the machine (or the encoder fails) = it never fires: the word matcher alone, as before. Nothing is logged
but numbers. Nothing here has run on the robot.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("condo_voice.ceremony_follow")

#: Measured on the paraphrase set (tests/data/ceremony_follow_en.json): see `calibrate()` / the eval's --semantic.
SIM = 0.62
MARGIN = 0.10
BASE = 0.08

#: What an MC says to guests that is NOT one of his scripted lines (the baseline). Calibration anchors only: the
#: eval's off-script test lines are a different set (tests/data/ceremony_follow_en.json), or the numbers would be a
#: lookup of the training data.
ANCHORS = [
    "Ladies and gentlemen, thank you for being here tonight.",
    "Please give a warm round of applause.",
    "Let's give a big hand to our wonderful team.",
    "Welcome everyone, good evening.",
    "Thank you very much, that is wonderful.",
    "I have a question for Emma.",
    "Emma, can you tell us a joke?",
    "What do you think about that?",
    "Let me tell you a short story.",
    "Is everyone enjoying the show?",
    "We will continue in just a moment, please stay with us.",
    "Can everyone hear me clearly?",
    "How are you feeling today, Emma?",
    "Let's move on to the next part of the program.",
    "Please look at the screen over here.",
    "Absolutely, of course.",
    "Sorry about that, one moment please.",
    "Please take your seats.",
    "Is that okay with everyone?",
    "That was really impressive, don't you think?",
    "ขอบคุณทุกท่านที่มาร่วมงานครับ",
    "ขอเสียงปรบมือให้หน่อยครับ",
    "สวัสดีครับทุกท่าน ยินดีต้อนรับ",
    "เอาล่ะครับ เรามาต่อกันเลย",
    "เก่งมากเลยครับ น่าประทับใจมาก",
    "เอ็มม่าช่วยเล่าเรื่องตลกให้ฟังหน่อยครับ",
    "มีใครมีคำถามไหมครับ",
]

_cache: dict = {}


def reset() -> None:
    _cache.clear()


def _encode(texts):
    """L2-normalised vectors (n, d), or None when no local model is on this machine. Blocking (CPU): never call on
    the event loop. Never raises."""
    try:
        from app.tools import retrieval

        return retrieval._embed_local(list(texts))
    except Exception:                                    # noqa: BLE001 -- no model / broken = no net, the matcher alone
        return None


def available() -> bool:
    return _encode(["ok"]) is not None


def _prepare(cue_lines: dict, encode) -> dict | None:
    """cue id -> the vectors of its lines (Thai + English), and the anchors'. Cached on the lines themselves."""
    key = (tuple(sorted((c, tuple(v)) for c, v in cue_lines.items())), id(encode))
    hit = _cache.get("prep")
    if hit is not None and hit[0] == key:
        return hit[1]
    flat = [(c, t) for c, texts in sorted(cue_lines.items()) for t in texts]
    vecs = encode([t for _c, t in flat] + ANCHORS)
    if vecs is None:
        return None
    out: dict = {"cues": {}, "anchors": vecs[len(flat):]}
    for k, (c, _t) in enumerate(flat):
        out["cues"].setdefault(c, []).append(vecs[k])
    _cache["prep"] = (key, out)
    return out


def decide(text: str, cue_lines: dict, *, encode=None, sim=None, margin=None, base=None) -> dict:
    """`text`: what the host said; `cue_lines`: candidate cue id -> its lines (host_th / host_en that exist). Pure
    given `encode`. -> {"fire": bool, "cue", "sim", "second", "base", "why"}. Blocking."""
    sim = SIM if sim is None else sim
    margin = MARGIN if margin is None else margin
    base = BASE if base is None else base
    out = {"fire": False, "cue": None, "sim": 0.0, "second": 0.0, "base": 0.0, "why": "off"}
    text = " ".join((text or "").split())
    if not text or not cue_lines:
        out["why"] = "empty"
        return out
    encode = encode or _encode
    prep = _prepare(cue_lines, encode)
    if prep is None:
        out["why"] = "no_model"
        return out
    vec = encode([text])
    if vec is None:
        out["why"] = "no_model"
        return out
    v = vec[0]
    scores = sorted(((max(float(v @ w) for w in ws), c) for c, ws in prep["cues"].items()), reverse=True)
    best, cue = scores[0]
    second = scores[1][0] if len(scores) > 1 else -1.0
    anchor = max(float(v @ a) for a in prep["anchors"]) if len(prep["anchors"]) else -1.0
    out.update(cue=cue, sim=round(best, 3), second=round(second, 3), base=round(anchor, 3))
    if best < sim:
        out["why"] = "weak"
    elif best - second < margin:
        out["why"] = "tie"
    elif best - anchor < base:
        out["why"] = "off_script"
    else:
        out.update(fire=True, why="match")
    return out


def decide_windows(utterances: list, cue_lines: dict, *, windows: int = 2, **kw) -> dict:
    """The last 1..`windows` utterances tried as one text each (newest window first, as the word matcher does): the
    first that fires wins, else the result with the highest similarity. Blocking."""
    best = None
    for k in range(1, min(windows, len(utterances)) + 1):
        res = decide(" ".join(utterances[-k:]), cue_lines, **kw)
        res["window"] = k
        if res["fire"]:
            return res
        if best is None or res["sim"] > best["sim"]:
            best = res
    return best or decide("", cue_lines, **kw)
