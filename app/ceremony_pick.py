"""Script-first slide picker for the ceremony's live mode (owner 24 Nov ~12:00: "อยากให้มันขึ้นตอบของบทก่อน ส่วนของโมชั่นอันเก่า
เอาไว้ตอนเขาถามที่เราไม่มีในบท").

Live first (CEREMONY_FOLLOW_CUES off): Emma answers live. The slide on the hall screen is chosen from the SCRIPT first:
every servable cue with a slide is a candidate, scored on content-word overlap between the evidence -- (a) the host's
words, (b) her own live answer (the output transcription of that turn: the strongest signal, she answers close to the
script) -- and the cue's own words (its host line th/en, its approved `en` line, its slide's texts). Distinctive words
count more (idf across cues). Decided twice: EARLY on his words once he has finished, REFINED on her answer (after
REFINE_WORDS content words and at her turn end). Never below a threshold or within a margin of the runner-up: then the
old topic slide (ceremony_motion) / the logo stays.

Pure: no I/O but the cached tables (invalidated by file mtime). Words only ever compared, never logged or shown.
scripts/eval_ceremony_pick.py and the tests call these same functions.
"""
from __future__ import annotations

import logging
import math
import re

logger = logging.getLogger("condo_voice.ceremony_pick")

#: Evidence from the host's words alone (early): score, and how far ahead of the runner-up.
EARLY_MIN = 0.50
EARLY_MARGIN = 0.12
#: Host + her answer (refine).
REFINE_MIN = 0.45
REFINE_MARGIN = 0.10
#: A pick this strong may move the pointer (mark the step shown); a weaker one only puts the slide up.
STRONG = 0.70
#: Her answer is refined after this many content words, and again at her turn end.
REFINE_WORDS = 8
#: The next unplayed cues weigh 1.0, the others (far / already played) this much.
FAR_WEIGHT = 0.85
#: A word no cue has weighs this (idf scale below): chatter the cues cannot explain lowers the score.
UNKNOWN_W = 1.0
#: Combined evidence: her answer counts this share.
ANSWER_SHARE = 0.6

#: Cue kinds that are never picked (chain jokes, manual steps) and cues whose slide must not seem to answer (price,
#: lights, curtains: same list as ceremony_motion's EXCLUDED).
_NEVER_KINDS = ("chain_follow", "manual")
_NEVER_REASON = ("quotation", "lights", "curtains", "the pool joke", "the superfan joke")

#: Words that say "ask / tell" and nothing about a topic.
_FILLER = frozenset("tell say talk know want like hear curious wonder question ask answer explain describe share give get go "
                    "come make see look think thing much many also first next today everyone everybody guest please thank "
                    "thanks welcome hello hi good great nice wow now listen sure course right alright okay let need would "
                    "tonight morning afternoon lady gentleman".split())

_STOCK: dict = {}


def _filler(token: str) -> bool:
    return token in _FILLER


def _tok(text) -> set:
    from app import ceremony_follow

    return {t for t in ceremony_follow.tokens(text) if not _filler(t)}


def _strings(node, out: list) -> None:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            _strings(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _strings(v, out)


def _signature() -> tuple:
    from app import ceremony_follow, event_script

    paths = [ceremony_follow.host_path(), event_script.path()]
    sig = []
    for p in paths:
        try:
            st = p.stat() if p is not None else None
            sig.append((str(p), st.st_mtime_ns, st.st_size) if st else None)
        except OSError:
            sig.append(None)
    try:
        from app import ceremony_slides

        doc = ceremony_slides._doc()
        sig.append(id(doc))                              # (the slides doc is cached on its own files' stat)
    except Exception:                                    # noqa: BLE001
        sig.append(None)
    return tuple(sig)


def reset() -> None:
    _STOCK["sig"] = None                                 # (never clear(): another thread may be reading the table)


def table() -> dict:
    """cue id -> {"bags": [token sets], "all": set}, plus idf. Built once per file change (a second of CPU: the Thai
    dictionary): call `warm()` off the loop. {} when there is no file."""
    sig = _signature()
    if _STOCK.get("sig") == sig and "cues" in _STOCK:
        return _STOCK
    from app import ceremony_follow, ceremony_slides, event_script

    rows = ceremony_follow.lines()
    try:
        from app import ceremony_motion

        bad = {c for c, why in ceremony_motion.EXCLUDED.items() if why.startswith(_NEVER_REASON)}
    except Exception:                                    # noqa: BLE001
        bad = set()
    cues: dict = {}
    for cue_id in sorted(event_script.servable()):
        row = rows.get(cue_id) or {}
        if row.get("kind") in _NEVER_KINDS or cue_id in bad:
            continue
        slide = ceremony_slides.for_cue(cue_id, lang="en", drafts=_drafts())
        if slide is None:
            continue
        cue = event_script.cue(cue_id) or {}
        raw = ceremony_slides._doc()["slides"].get(cue_id) if ceremony_slides._doc() else None
        strings: list = []
        if isinstance(raw, dict):
            _strings(raw.get("texts"), strings)
            _strings(raw.get("figures"), strings)
        bags = [b for b in (_tok(" ".join(x for x in (row.get("host_th"), row.get("host_en")) if x)),
                            _tok(cue.get("en")), _tok(" ".join(strings))) if b]
        if not bags:
            continue
        cues[cue_id] = {"bags": bags, "all": set().union(*bags)}
    df: dict = {}
    for c in cues.values():
        for t in c["all"]:
            df[t] = df.get(t, 0) + 1
    n = max(1, len(cues))
    idf = {t: math.log((n + 1) / (d + 0.5)) + 0.3 for t, d in df.items()}
    fresh = {"sig": sig, "cues": cues, "idf": idf}
    _STOCK.update(fresh)                                 # (replaced in place, never emptied: a reader never sees a KeyError)
    return fresh


def _drafts() -> bool:
    try:
        from app import ceremony_motion

        return bool(ceremony_motion.DRAFTS)
    except Exception:                                    # noqa: BLE001
        return False


def warm() -> None:
    try:
        table()
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony pick: building the table")


def _mass(tokens, idf) -> float:
    return sum(idf.get(t, UNKNOWN_W) for t in tokens)


def _one(evidence: set, cue: dict, idf: dict) -> float:
    """sqrt(P*R): P = the share of the evidence the cue explains, R = how much of its best bag the evidence covers."""
    if not evidence:
        return 0.0
    hit = evidence & cue["all"]
    if not hit:
        return 0.0
    p = _mass(hit, idf) / _mass(evidence, idf)
    r = max(_mass(evidence & b, idf) / (_mass(b, idf) or 1.0) for b in cue["bags"])
    return math.sqrt(p * r)


def rank(host_text: str | None, answer_text: str | None = None, near=()) -> list[tuple[str, float]]:
    """[(cue, score)] best first. Host only, or host + her answer (`ANSWER_SHARE` of the combined score is hers).
    `near` = the next unplayed cues (full weight); the rest weigh FAR_WEIGHT."""
    tab = table()
    cues, idf = tab.get("cues") or {}, tab.get("idf") or {}
    if not cues:
        return []
    eh = _tok(host_text)
    ea = _tok(answer_text) if answer_text else set()
    out = []
    for cue_id, cue in cues.items():
        sh = _one(eh, cue, idf)
        sa = _one(ea, cue, idf) if ea else None
        score = sh if sa is None else (1 - ANSWER_SHARE) * sh + ANSWER_SHARE * sa
        if cue_id not in near:
            score *= FAR_WEIGHT
        out.append((cue_id, score))
    out.sort(key=lambda kv: -kv[1])
    return out


def _price_talk(host_text) -> bool:
    try:
        from app import ceremony_motion

        why = ceremony_motion.decide(host_text or "")[1]
        return why in ("never", "about_her")
    except Exception:                                    # noqa: BLE001
        return False


def pick(host_text: str | None, answer_text: str | None = None, near=()) -> dict:
    """{"verdict": "pick"|"none", "cue", "score", "second", "strong", "stage"}. stage "early" = the host's words
    alone, "refine" = with her answer. A price / lights / banter-about-her question never picks."""
    stage = "refine" if answer_text else "early"
    out = {"verdict": "none", "cue": None, "score": 0.0, "second": 0.0, "strong": False, "stage": stage}
    if _price_talk(host_text):
        out["why"] = "never"
        return out
    ranked = rank(host_text, answer_text, near)
    if not ranked:
        out["why"] = "no_table"
        return out
    best, score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    out.update(cue=best, score=round(score, 3), second=round(second, 3))
    need, margin = (REFINE_MIN, REFINE_MARGIN) if answer_text else (EARLY_MIN, EARLY_MARGIN)
    if score < need:
        out["why"] = "weak"
    elif score - second < margin:
        out["why"] = "tie"
    else:
        out.update(verdict="pick", strong=score >= STRONG and best in near)
    return out
