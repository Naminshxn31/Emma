"""Offline run of the whole ceremony by the host's words (owner 24 Nov 2026, app/ceremony_follow.py).

No model, no robot, no network: the REAL running order and the REAL host-lines file, the SAME pure rule the server
uses (`ceremony_follow.pointer_of` + `decide_windows`). Staff are simulated only where staff are in the real thing:
a manual tick is ticked, a cue with no host line is pressed ("manual"), a walk is pressed. At every step where the
server follows the host, the host's lines are fed in -- verbatim, with the transcriber's habits (tone marks, a
dropped word, one letter off), with chatter before, and in two pieces across a pause -- mixed with off-script
questions that must NOT fire a cue. Per cue: fired / missed / wrong (a different cue fired). Run:

    python scripts/eval_ceremony_follow.py            # the table
    python scripts/eval_ceremony_follow.py --seed 3   # another draw of the noise

    python scripts/eval_ceremony_follow.py --lang en  # the English set (25 Nov: the host speaks English)
    python scripts/eval_ceremony_follow.py --lang en --semantic        # ...and the semantic net behind it
    python scripts/eval_ceremony_follow.py --lang en --sweep           # the net's thresholds, train / held-out

The English set is OUR paraphrase of OUR translation of his lines (`host_en`, tests/data/ceremony_follow_en.json) --
not Khun Miki's speech. It says whether the matcher and the net can recognise an English rendering of a line; it
cannot say how he will actually phrase it.

What it measures: the matcher and the pointer. What it does not: the transcriber's real errors on the GCK mic, the
timing (QUIET_S against the host's pauses), her live answer racing a cue.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: What the host may say that is not his script. None of it may play a cue, wherever the order stands.
OFF_SCRIPT = [
    "เอ็มม่า ห้องสตูดิโอราคาเริ่มต้นเท่าไหร่ครับ",
    "แล้วที่จอดรถของโครงการมีกี่คันครับ",
    "ขอบคุณทุกท่านที่มาร่วมงานในวันนี้ครับ ผมดีใจมากที่เห็นทุกคนอยู่ที่นี่",
    "ใครเป็นคนออกแบบโครงการนี้ครับ",
    "เอ็มม่า วันนี้อากาศเป็นยังไงบ้าง",
    "What time does the fitness room open?",
    "Emma, how many bedrooms does the biggest unit have?",
    "ขอเสียงปรบมือให้เอ็มม่าหน่อยครับ",
    "ผมขอเชิญคุณลูกค้าท่านนี้ขึ้นมาบนเวทีด้วยครับ",
    "ค่าส่วนกลางต่อเดือนประมาณเท่าไหร่ครับ",
    "เอ็มม่าช่วยแนะนำตัวให้แขกที่เพิ่งมาถึงฟังหน่อยครับ",
    "ลองถามอะไรเอ็มม่าก็ได้นะครับ ไม่ต้องเกรงใจ",
    "สระว่ายน้ำลึกเท่าไหร่ครับ",
    "โครงการเสร็จเมื่อไหร่ครับ",
    # near the script's own topics -- an ad-lib, not the line (owner 24 Nov: strict against off-script talk)
    "แล้วทำเลของโครงการอยู่ใกล้อะไรบ้างครับ",
    "ห้องพักของที่นี่มีแบบไหนบ้างครับ",
    "สระว่ายน้ำของเรายาวเท่าไหร่ครับ",
    "ผมอยากให้ทุกท่านเห็นภาพของโครงการนะครับ",
    "เอ็มม่าช่วยเปิดวิดีโอให้หน่อยครับ",
    "ขอเสียงปรบมือให้ทีมงานทุกคนครับ",
    "ใครมีคำถามเกี่ยวกับสิ่งอำนวยความสะดวกบ้างครับ",
    "ผมว่าห้องที่มองเห็นทะเลน่าสนใจมากเลยนะครับ",
    "ปิดไฟหน่อยครับ",
    "ผ้าม่านสวยไหมครับ",
]

_SWAPS = {"ิ": "ี", "ี": "ิ", "ุ": "ู", "ู": "ุ", "ะ": "า", "า": "ะ", "ล": "ร", "ร": "ล", "ก": "ค", "ค": "ก"}


def noisy(text: str, rng: random.Random) -> str:
    """The transcriber's habits on `text`: a word dropped, a letter off in a long word, tone marks gone."""
    from app import ceremony_follow as f

    words = text.split()
    out = []
    for w in words:
        roll = rng.random()
        if roll < 0.10 and len(words) > 6:
            continue                                     # a word the transcriber lost
        if roll < 0.28 and len(w) >= 5:
            at = rng.randrange(1, len(w) - 1)
            w = w[:at] + _SWAPS.get(w[at], w[at]) + w[at + 1:]
        out.append(w)
    return " ".join(out) or f._norm(text)


def chatter(rng: random.Random) -> str:
    return rng.choice(["ครับ ขอบคุณครับ", "เอาล่ะครับ", "okay", "ใช่ครับ ถูกต้อง", "เดี๋ยวนะครับ"])


def split_in_two(text: str) -> list[str]:
    """The host paused inside his line: two utterances."""
    words = text.split()
    if len(words) < 6:
        return [text]
    k = len(words) // 2
    return [" ".join(words[:k]), " ".join(words[k:])]



#: Neutral talk of an MC: no word of any host line that is distinctive to it (owner 24 Nov: his line may be longer).
BANTER = ["ขอบคุณครับ ทุกท่านครับ ขอบคุณที่ตั้งใจฟังกันนะครับ", "เดี๋ยวเรามาดูกันต่อ ถือว่าน่าสนใจมากเลยนะครับ",
          "ก่อนอื่นต้องขอบคุณแขกทุกท่านที่มาร่วมงานในวันนี้ครับ", "ใช่ไหมครับ ทุกคนฟังอยู่ใช่ไหมครับ ผมเองก็ตื่นเต้นเหมือนกัน"]


def _pieces(line: str, cue: str):
    """The raw words of the host line (his own spelling), each with its weight (None = not a content word)."""
    import unicodedata

    from app import event_script

    weight = {}
    for tok, w in f_mod()._compiled().get(cue, []):
        weight.setdefault(tok, w)
    f = f_mod()
    out = []
    for piece in event_script._segment(f._NAME_RE.sub(" ", unicodedata.normalize("NFKC", line))):
        t = f._norm(piece)
        out.append((piece, weight.get(t) if t in weight else None))
    return out


def f_mod():
    from app import ceremony_follow

    return ceremony_follow


def shortened(line: str, cue: str, frac: float) -> str:
    """Only the key words of the line: the `frac` of its content words with the highest weight, in his order."""
    import math

    pieces = _pieces(line, cue)
    content = [(k, w) for k, (p, w) in enumerate(pieces) if w is not None]
    keep = {k for k, _w in sorted(content, key=lambda kw: (-kw[1], kw[0]))[:max(1, math.ceil(frac * len(content)))]}
    return " ".join(pieces[k][0] for k in range(len(pieces)) if k in keep)


def lengthened(line: str, cue: str, rng: random.Random, frac: float = 0.5) -> list[str]:
    """His line with banter before and after it (about `frac` of its length more), in one utterance."""
    n = len(f_mod().tokens(line))
    before, after, words = [], [], 0
    while words < frac * n:
        text = rng.choice(BANTER)
        (before if len(before) <= len(after) else after).append(text)
        words += len(f_mod().tokens(text))
    return [" ".join(before + [line] + after)]


def lengthened_en(line: str, cue: str, rng: random.Random, frac: float = 0.5) -> list[str]:
    n = len(f_mod().tokens(line))
    before, after, words = [], [], 0
    while words < frac * n:
        text = rng.choice(BANTER_EN)
        (before if len(before) <= len(after) else after).append(text)
        words += len(f_mod().tokens(text))
    return [" ".join(before + [line] + after)]


def reordered(line: str, cue: str) -> str:
    """His phrases in another order: the second half first."""
    words = line.split()
    if len(words) < 4:
        return " ".join(reversed(words))
    k = len(words) // 2
    return " ".join(words[k:] + words[:k])


VARIANTS = ["verbatim", "noisy", "chatter", "two_pieces", "off_script_first", "p40", "p70", "p150", "reorder", "p70_noisy"]
VARIANTS_EN = VARIANTS + ["hand_s", "hand_n", "hand_l", "asr"]

BANTER_EN = ["Thank you everyone, thank you for listening so carefully.", "Okay, so let's keep going, this is exciting.",
             "First of all I want to thank all our guests for joining us tonight.", "Right, you are all with me, aren't you? I am a little nervous too."]
CHATTER_EN = ["Okay, thank you.", "Alright then.", "Yes, exactly, that is right.", "One moment please."]
#: What the transcriber does to English names and words (the fixed ones are the owner's own examples).
ASR_EN = {"Embassy World": "Embassy Word", "Emma": "Ema", "Jomtien": "Jomtian", "lobby": "lobbie", "quotation": "quotations",
          "technology": "technolgy", "swimming": "swiming", "curtains": "curtain", "facilities": "facility",
          "located": "locating", "project": "projects", "units": "unit"}


def noisy_en(text: str, rng: random.Random) -> str:
    """An English transcript's habits: a word lost, a letter dropped / swapped / doubled in a long word."""
    out = []
    words = text.split()
    for w in words:
        roll = rng.random()
        if roll < 0.10 and len(words) > 6:
            continue
        if roll < 0.28 and len(w) >= 5:
            at = rng.randrange(1, len(w) - 2)
            kind = rng.randrange(3)
            w = (w[:at] + w[at + 1:] if kind == 0 else w[:at] + w[at + 1] + w[at] + w[at + 2:] if kind == 1
                 else w[:at] + w[at] + w[at:])
        out.append(w)
    return " ".join(out) or text


def asr_en(text: str, rng: random.Random) -> str:
    """The fixed ASR confusions of ASR_EN on the line, plus one word dropped."""
    for a, b in ASR_EN.items():
        text = text.replace(a, b)
    words = text.split()
    if len(words) > 8:
        del words[rng.randrange(len(words))]
    return " ".join(words)


def en_data() -> dict:
    return json.loads((ROOT / "tests" / "data" / "ceremony_follow_en.json").read_text(encoding="utf-8"))


def _sem_lines(cands: list) -> dict:
    """The semantic net's candidates: cue id -> the lines it knows (Thai, English)."""
    return {c["cue"]: [t for t in (c["host_th"], c.get("host_en")) if t] for c in cands}


def run(seed: int = 1, *, verbose: bool = False, lang: str = "th", semantic: bool = False, encode=None,
        thresholds: dict | None = None, collect: list | None = None) -> dict:
    """lang "th": the original Thai run (unchanged: same draws). "en": the English line of each cue, its hand-written
    paraphrases, the ASR errors, and English + Thai off-script talk. semantic: the net behind the word matcher, only
    where the matcher found nothing / weak (as the server does). `collect`: raw net numbers of every case."""
    from app import ceremony_follow as f
    from app import ceremony_follow_sem as sem
    from app import event_order, event_script
    from app.config import settings

    settings.ceremony_file = str(ROOT / "data" / "ceremony" / "floor1_ceremony.json")
    event_script.reset()
    f.reset()
    scenes = event_script.order()
    host = f.lines()
    # the real file has its card 2 in it; the pure pointer works over the visible order
    rng = random.Random(seed)
    marks: dict = {}
    last = None
    rows: list[dict] = []
    wrong_off = []                                       # an off-script line that played a cue
    wrong_off_sem = []                                   # ...or that the semantic net would have played
    en = lang == "en"
    data = en_data() if en else {}
    off_all = OFF_SCRIPT + (data.get("off_en", []) + data.get("off_th", []) if en else [])
    th = thresholds or {}
    key = event_order._key
    # steps taken by staff before follow mode starts (card 1): the pointer starts after card 2
    follow_at = next(((sc["n"], st["i"]) for sc in scenes for st in sc["steps"] if st["do"] == "follow"), None)
    for sc in scenes:
        for st in sc["steps"]:
            if (sc["n"], st["i"]) <= follow_at:
                marks[key(sc["n"], st["i"])] = {"state": "done"}
                last = (sc["n"], st["i"])
    guard = 0
    while guard < 400:
        guard += 1
        ptr = f.pointer_of(scenes, marks, last, host)
        if ptr["kind"] == "end":
            break
        if ptr["kind"] == "host":
            # every off-script line first: none may play anything
            for text in off_all:
                res = f.decide_windows([text], [c["cue"] for c in ptr["cands"]])
                if res["verdict"] == "match":
                    wrong_off.append({"text": text, "cue": res["cue"], "at": ptr["at"]})
                if semantic and res["verdict"] in ("nothing", "weak"):
                    sres = sem.decide_windows([text], _sem_lines(ptr["cands"]), encode=encode, **th)
                    if collect is not None:
                        collect.append({"kind": "off", "text": text, "cue": ptr["cands"][0]["cue"], **sres})
                    if sres["fire"]:
                        wrong_off_sem.append({"text": text, "cue": sres["cue"], "at": ptr["at"], "sim": sres["sim"]})
            want = ptr["cands"][0]
            line = want["host_en"] if en and want.get("host_en") else want["host_th"]
            lkey = want["cue"] + (f.EN if en and want.get("host_en") else "")
            if not en:
                variants = {"verbatim": [line], "noisy": [noisy(line, rng)],
                            "chatter": [chatter(rng), line], "two_pieces": split_in_two(line),
                            "off_script_first": [rng.choice(OFF_SCRIPT), line],
                            # "คำที่มิกิพูดอาจจะไม่ตรงกับสคริป อาจจะสั้นกว่าหรือยาวกว่าก็ได้" (owner 24 Nov)
                            "p40": [shortened(line, lkey, 0.4)], "p70": [shortened(line, lkey, 0.7)],
                            "p150": lengthened(line, lkey, rng), "reorder": [reordered(line, lkey)],
                            "p70_noisy": [noisy(shortened(line, lkey, 0.7), rng)]}
            else:
                hand = data["hand"][want["cue"]]
                variants = {"verbatim": [line], "noisy": [noisy_en(line, rng)],
                            "chatter": [rng.choice(CHATTER_EN), line], "two_pieces": split_in_two(line),
                            "off_script_first": [rng.choice(data["off_en"]), line],
                            "p40": [shortened(line, lkey, 0.4)], "p70": [shortened(line, lkey, 0.7)],
                            "p150": lengthened_en(line, lkey, rng), "reorder": [reordered(line, lkey)],
                            "p70_noisy": [noisy_en(shortened(line, lkey, 0.7), rng)],
                            "hand_s": [hand[0]], "hand_n": [hand[1]], "hand_l": [hand[2]],
                            "asr": [asr_en(line, rng)]}
            cell = {}
            for name, utts in variants.items():
                res = f.decide_windows(utts, [c["cue"] for c in ptr["cands"]])
                if res["verdict"] == "no_tail":                  # the server waits TAIL_GRACE_S more, then does not ask for the ending
                    res = f.decide_windows(utts, [c["cue"] for c in ptr["cands"]], final=True)
                cell[name] = ("fired" if res["verdict"] == "match" and res["cue"] == want["cue"]
                              else "wrong" if res["verdict"] == "match" else "missed")
                if verbose and cell[name] != "fired":
                    print(want["cue"], name, res, utts)
                if semantic:
                    # the net runs only where the words found nothing / weak (what the server does)
                    if res["verdict"] in ("nothing", "weak"):
                        sres = sem.decide_windows(utts, _sem_lines(ptr["cands"]), encode=encode, **th)
                        if collect is not None:
                            collect.append({"kind": "pos", "variant": name, "want": want["cue"], **sres})
                        cell["s:" + name] = ("sem" if sres["fire"] and sres["cue"] == want["cue"]
                                             else "wrong" if sres["fire"] else "missed")
                    else:
                        cell["s:" + name] = cell[name]
            rows.append({"cue": want["cue"], "kind": "host", **cell})
            marks[key(want["n"], want["i"])] = {"state": "done"}
            last = (want["n"], want["i"])
            continue
        at = ptr["at"]
        if ptr["kind"] in ("manual", "continues", "chain"):
            cue = ptr.get("cue") or "%s.%s" % at
            kind = "chain" if (host.get(cue) or {}).get("kind") == "chain_follow" else ptr["kind"]
            rows.append({"cue": cue, "kind": kind})
        marks[key(*at)] = {"state": "done"}              # staff tick / press / the server's own continuation
        last = at
    out = {"rows": rows, "wrong_off": wrong_off, "wrong_off_sem": wrong_off_sem, "seed": seed, "lang": lang,
           "off_lines": len(off_all)}
    cells = [v for r in rows if r["kind"] == "host" for k, v in r.items() if k not in ("cue", "kind") and not k.startswith("s:")]
    out["fired"], out["missed"], out["wrong"] = (cells.count("fired"), cells.count("missed"), cells.count("wrong"))
    if semantic:
        scells = [v for r in rows if r["kind"] == "host" for k, v in r.items() if k.startswith("s:")]
        out["sem_rescued"], out["sem_missed"], out["sem_wrong"] = (scells.count("sem"), scells.count("missed"),
                                                                  scells.count("wrong"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--lang", choices=("th", "en"), default="th")
    ap.add_argument("--semantic", action="store_true", help="the semantic net behind the matcher (needs the local model)")
    ap.add_argument("--sweep", action="store_true", help="the net's thresholds: a grid, train / held-out halves")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.sweep:
        return sweep(args.seed)
    res = run(args.seed, verbose=args.verbose, lang=args.lang, semantic=args.semantic)
    names = VARIANTS_EN if args.lang == "en" else VARIANTS
    print("%-9s %-9s %s" % ("cue", "kind", " ".join("%-9s" % n[:9] for n in names)))
    for r in res["rows"]:
        print("%-9s %-9s %s" % (r["cue"], r["kind"], " ".join("%-9s" % r.get(n, "") for n in names)))
    print("\nhost lines fed (%s): fired %d  missed %d  wrong %d   (seed %d)" % (args.lang, res["fired"], res["missed"],
                                                                              res["wrong"], res["seed"]))
    print("off-script lines (%d per position) that played a cue: %d" % (res["off_lines"], len(res["wrong_off"])))
    for w in res["wrong_off"]:
        print("  WRONG:", w)
    if args.semantic:
        print("semantic net on what the words missed: rescued %d  still missed %d  wrong %d; off-script fires %d" % (
            res["sem_rescued"], res["sem_missed"], res["sem_wrong"], len(res["wrong_off_sem"])))
        for w in res["wrong_off_sem"]:
            print("  NET WRONG:", w)
        for r in res["rows"]:
            if r["kind"] == "host":
                saved = [n for n in names if r.get("s:" + n) == "sem"]
                if saved:
                    print("  %-9s net rescued: %s" % (r["cue"], ", ".join(saved)))
    return 1 if res["wrong"] or res["wrong_off"] or (args.semantic and (res["sem_wrong"] or res["wrong_off_sem"])) else 0


def sweep(seed: int) -> int:
    """Collect the net's raw numbers for every case once, then grid the thresholds. Train = half of the off-script
    lines + the "normal/long/70/150" variants; held-out = the rest. A setting is safe when it fires nothing wrong and
    no off-script line on BOTH halves; the best is the one that rescues the most on the held-out half."""
    import itertools

    from app import ceremony_follow_sem as sem

    raw: list = []
    run(seed, lang="en", semantic=True, thresholds={"sim": -2.0, "margin": -2.0, "base": -2.0}, collect=raw)
    off_idx = {t: k for k, t in enumerate(en_data()["off_en"] + en_data()["off_th"])}
    train_var = {"hand_n", "hand_l", "p70", "p150"}

    def split(c):
        if c["kind"] == "off":
            return "train" if off_idx.get(c["text"], 0) % 2 == 0 and c["text"] in off_idx else "test"
        return "train" if c["variant"] in train_var else "test"

    def stats(sim_t, mar_t, base_t, which):
        ok = wrong = off = pos = 0
        for c in raw:
            if which != "all" and split(c) != which:
                continue
            fire = c["sim"] >= sim_t and c["sim"] - c["second"] >= mar_t and c["sim"] - c["base"] >= base_t
            if c["kind"] == "pos":
                pos += 1
                if fire:
                    ok += 1 if c["cue"] == c["want"] else 0
                    wrong += 0 if c["cue"] == c["want"] else 1
            elif fire:
                off += 1
        return ok, wrong, off, pos

    print("cases: %d positives, %d off-script (net saw them: the words found nothing/weak)" % (
        sum(c["kind"] == "pos" for c in raw), sum(c["kind"] == "off" for c in raw)))
    rows = []
    for sim_t, mar_t, base_t in itertools.product([0.5, 0.55, 0.6, 0.65, 0.7, 0.75], [0.0, 0.05, 0.1, 0.15, 0.2],
                                                 [-0.1, 0.0, 0.05, 0.1, 0.15, 0.2]):
        a, b = stats(sim_t, mar_t, base_t, "train"), stats(sim_t, mar_t, base_t, "test")
        rows.append((sim_t, mar_t, base_t, a, b))
    safe = [r for r in rows if r[3][1] == 0 and r[3][2] == 0 and r[4][1] == 0 and r[4][2] == 0]
    print("settings with zero wrong + zero off-script fires on both halves: %d of %d" % (len(safe), len(rows)))
    for r in sorted(safe, key=lambda r: -(r[3][0] + r[4][0]))[:12]:
        print("  sim>=%.2f margin>=%.2f base>=%.2f  train rescued %d/%d  held-out rescued %d/%d" % (
            r[0], r[1], r[2], r[3][0], r[3][3], r[4][0], r[4][3]))
    if not safe:
        best = sorted(rows, key=lambda r: (r[3][1] + r[3][2] + r[4][1] + r[4][2], -(r[3][0] + r[4][0])))[:5]
        for r in best:
            print("  closest: sim>=%.2f margin>=%.2f base>=%.2f  train %s  held-out %s" % r)
    cur = (sem.SIM, sem.MARGIN, sem.BASE)
    print("current constants sim>=%.2f margin>=%.2f base>=%.2f: all cases (ok, wrong, off, pos) = %s" % (cur + (stats(*cur, "all"),)))
    # the closest off-script line to each cue, for the report
    near = sorted((c for c in raw if c["kind"] == "off"), key=lambda c: -(c["sim"] - c["base"]))[:8]
    print("off-script lines closest to firing (sim, second, base):")
    for c in near:
        print("  %.3f %.3f %.3f  %s -> %s" % (c["sim"], c["second"], c["base"], c["text"][:70], c["cue"]))
    neg = sorted((c for c in raw if c["kind"] == "pos"), key=lambda c: c["sim"])[:8]
    print("weakest positives (sim, second, base):")
    for c in neg:
        print("  %.3f %.3f %.3f  %s %s" % (c["sim"], c["second"], c["base"], c["variant"], c["want"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
