#!/usr/bin/env python3
"""
Measure slide search against a list of questions, and calibrate the
similarity thresholds against *your* deck.

    python scripts/eval_search.py                  # the built-in question set
    python scripts/eval_search.py --file mine.txt  # your own
    python scripts/eval_search.py --lexical        # no embeddings, for comparison

Why this exists
---------------
`SEARCH_MIN_SIMILARITY` and `SEARCH_SHOW_SIMILARITY` are cosine thresholds.
Cosine is absolute, so a fixed number is meaningful — but where the gap falls
between "related" and "unrelated" depends on the deck, and the shipped
defaults were picked without ever seeing yours. This runs a set of questions
that *should* match and a set that *shouldn't*, prints the similarity each
one got, and tells you where the boundary actually is.

Question file format — one per line, `expected` is optional:

    ฟิตเนสอยู่ชั้นไหน           | ฟิตเนส
    มีที่จอดรถไหม               | BASEMENT
    !ราคาเริ่มต้นเท่าไหร่
    !ใครชนะเลือกตั้ง

A leading `!` means "this must find nothing". Text after `|` is a substring
the winning slide's title should contain.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Questions a sales gallery actually gets. The negatives matter as much as
# the positives: a search that never says "I don't know" is not working.
# The gym is branded BIOGENESIS in this deck, and that matters more than it
# looks. The only slides with "GYM" in the title are "Bungee Gym & Yoga" — a
# different facility entirely — so an expectation of "GYM" was not merely
# strict, it was asking for the wrong answer. It reported four failures for
# four correct results, which is the way an eval stops being read.
DEFAULT_QUESTIONS = [
    ("มีฟิตเนสไหม", "BIOGENESIS"),
    ("ฟิตเนสอยู่ชั้นไหน", "BIOGENESIS"),
    ("สระว่ายน้ำอยู่ชั้นไหน", "POOL"),
    ("ขอดูสระว่ายน้ำหน่อย", "POOL"),
    ("มีซาวน่าไหม", None),   # sauna photo or the basement plan it sits on
    ("ที่จอดรถอยู่ไหน", "ใต้ดิน"),
    ("ขอดูห้องนอน", "ห้องนอน"),
    ("ห้องน้ำเป็นยังไง", "ห้องน้ำ"),
    ("ทำเลอยู่ตรงไหน", "ทำเล"),
    ("ใครเป็นเจ้าของโครงการ", "ผู้บริหาร"),
    # Cross-language: the point of the semantic half. None of these share a
    # character with the Thai titles they should reach.
    ("do you have a gym", "BIOGENESIS"),
    ("where is the swimming pool", "POOL"),
    ("is there a sauna", "SAUNA"),
    ("show me the parking", "ใต้ดิน"),
    ("游泳池在哪里", "POOL"),
    ("健身房", "BIOGENESIS"),
    ("有桑拿房吗", "SAUNA"),
    ("бассейн", "POOL"),
    ("где тренажерный зал", "BIOGENESIS"),
    ("プールはどこですか", "POOL"),
    ("수영장 어디예요", "POOL"),
    ("wo ist der Pool", "POOL"),
    ("où est la piscine", "POOL"),
    # Must find nothing.
    ("!ราคาเริ่มต้นเท่าไหร่", None),
    ("!โปรโมชั่นตอนนี้", None),
    ("!ใครชนะเลือกตั้ง", None),
    ("!แนะนำมือถือรุ่นไหนดี", None),
    ("!zzzz ไม่มีอยู่จริง qqqq", None),
    # The exact strings tests/test_knowledge.py asserts on. They belong in
    # the measured set, or the threshold gets calibrated against one list of
    # questions and asserted against another — which is how the suite still
    # had two red tests after a calibration that "rejected all 9".
    ("!zzzz qqqq ไม่มีจริง", None),
    ("!ราคาหุ้นวันนี้", None),
    # Pricing, in the languages a Thai gallery actually gets. These must be
    # refused in every one of them, not just the two someone tested.
    ("!价格是多少", None),
    ("!цена квартиры", None),
    ("!いくらですか", None),
    ("!얼마예요", None),
]


def load_questions(path: Path | None):
    #: The full set lives in a file rather than in this script, because it is
    #: content — the sales team can add the questions they actually get asked
    #: without touching Python, and the list grew past the point where it
    #: would bury the code that measures it.
    question_file = ROOT / "data" / "eval_questions.txt"
    if path is None and question_file.exists():
        path = question_file
    if path is None:
        return DEFAULT_QUESTIONS
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        question, _, expected = line.partition("|")
        out.append((question.strip(), expected.strip() or None))
    return out


def _separation(title: str, real: list[float], junk: list[float],
                fmt: str = "%.3f", setting: str | None = None) -> None:
    """Print where two populations sit, and whether a line exists between them.

    Reported for both the raw cosine and the standout score, because the
    cosine was measured on this deck and *cannot* separate them: the worst
    real match scores 0.644 and the best nonsense 0.649. That is the embedding
    model's floor showing, not a property of the questions, so the honest next
    step is to measure a quantity that doesn't depend on where the floor is —
    not to pick a threshold that splits an overlap.
    """
    if not real or not junk:
        return
    print("\n%s" % title)
    print(("  should match:    min " + fmt + "  mean " + fmt)
          % (min(real), sum(real) / len(real)))
    print(("  should not:      max " + fmt + "  mean " + fmt)
          % (max(junk), sum(junk) / len(junk)))
    if min(real) > max(junk):
        midpoint = (min(real) + max(junk)) / 2
        print(("  Clean separation — a threshold at " + fmt + " splits them.") % midpoint)
        if setting:
            print("    %s=%.2f" % (setting, midpoint))
    else:
        print(("  Overlap (" + fmt + " vs " + fmt + ") — no threshold separates these.")
              % (max(junk), min(real)))


def _best_pair(pairs: list[tuple[float, float, bool]]) -> None:
    """Search both thresholds together, because the code uses both together.

    `Hit.found` is `similarity >= MIN_SIMILARITY or coverage >= MIN_COVERAGE`.
    Judging either number on its own therefore answers a question the product
    never asks — and gets a misleading answer: measured on this deck, cosine
    alone overlaps (0.644 vs 0.649) and standout alone overlaps worse. Both
    said "no threshold exists". Both were reporting on half a rule.

    The pair does separate, and the reason is worth keeping: the real
    questions with a *low* cosine are the Thai and English ones, which the
    lexical side already matched perfectly. Only the cross-language questions
    depend on the cosine at all, and those score higher than the nonsense
    does. Neither signal is sufficient; the OR of them is.

    Prefers the widest margin rather than the most matches — a threshold that
    only just works is one that stops working when the deck changes.
    """
    real = [(sim, cov) for sim, cov, miss in pairs if not miss]
    junk = [(sim, cov) for sim, cov, miss in pairs if miss]
    if not real or not junk:
        return

    def accepted(sim, cov, min_sim, min_cov):
        return sim >= min_sim or cov >= min_cov

    def _thresholds(values: list[float]) -> list[float]:
        """Observed values **and the gaps between them**.

        Trying only the observed numbers means the suggestion always lands
        exactly on some real question's score, so the reported margin is zero
        by construction and the setting has no headroom at all. It also can't
        propose anything between two observations — which is how a run that
        needed "somewhere above 0.25" ended up recommending 0.46, tightening
        a threshold much further than the data asked for.
        """
        seen = sorted({round(v, 4) for v in values})
        midpoints = [round((a + b) / 2, 4) for a, b in zip(seen, seen[1:])]
        return sorted(set(seen + midpoints + [1.01]))

    candidates = []
    sims = _thresholds([s for s, _ in real + junk])
    covs = _thresholds([c for _, c in real + junk])
    for min_sim in sims:
        for min_cov in covs:
            if any(accepted(s, c, min_sim, min_cov) for s, c in junk):
                continue                      # lets nonsense through
            keeps = sum(accepted(s, c, min_sim, min_cov) for s, c in real)
            # How much room there is before a slightly different question
            # falls the wrong side.
            margin = min(
                [s - min_sim for s, c in real if s >= min_sim] +
                [c - min_cov for s, c in real if c >= min_cov] +
                [min_sim - s for s, c in junk] + [min_cov - c for s, c in junk]
            )
            # Negated so that ties prefer the *least* restrictive pair. Sorting
            # them positively picked the tightest setting that happened to work,
            # which is the one most likely to start rejecting real questions the
            # first time somebody adds a slide.
            # Whether both halves of the rule still do anything. A threshold
            # above every observed score switches its signal off entirely —
            # and that can score *better* on margin, because a disabled arm
            # has no near misses to be narrow about. Left unchecked the
            # search recommended exactly that: turn similarity off and let
            # coverage carry everything, which quietly deletes cross-language
            # search, the one thing embeddings are here for.
            live = min_sim <= max(s for s, _ in real) and min_cov <= max(c for _, c in real)
            candidates.append((live, keeps, round(margin, 4), -min_sim, -min_cov))

    print("\nBoth thresholds together (this is the rule the code actually uses)")
    if not candidates:
        print("  No pair rejects every bad question. The deck needs keywords,")
        print("  not a number — see the misses above.")
        return
    live, keeps, margin, neg_sim, neg_cov = max(candidates)
    min_sim, min_cov = -neg_sim, -neg_cov
    print("  Rejects all %d bad questions and keeps %d of %d good ones."
          % (len(junk), keeps, len(real)))
    if not live:
        print("  ! only by switching one of the two signals off — the deck needs")
        print("    keywords rather than a threshold.")
    print("  Narrowest margin: %.3f" % margin)
    print("\n  Suggested settings:")
    print("    SEARCH_MIN_SIMILARITY=%.3f" % min_sim)
    print("    (MIN_COVERAGE in app/tools/slide_search.py = %.3f)" % min_cov)
    for sim, cov in real:
        if not accepted(sim, cov, min_sim, min_cov):
            print("  ! would now miss a good question (sim %.3f, cover %.2f)" % (sim, cov))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, help="question list")
    parser.add_argument("--lexical", action="store_true", help="disable embeddings")
    args = parser.parse_args()

    from app.config import settings

    if args.lexical:
        settings.search_semantic = False

    from app.tools import slide_search
    from app.tools.knowledge import _is_commercial
    from app.tools.slides import load_slides, search_slides

    slides = load_slides()
    if not slides:
        print("No slides indexed. Run scripts/import_slides.py first.")
        return 1

    # Re-runs must be free and complete. See `retrieval.use_query_cache`: the
    # first full run of 127 questions tripped the free tier's 100-embeddings-
    # per-minute limit and finished lexically, which silently dropped most of
    # the questions from the calibration it was printing.
    from app.tools.retrieval import save_query_cache, use_query_cache

    use_query_cache(ROOT / "data" / "eval_query_cache.npz")

    index = slide_search.get_index(slides)
    mode = "hybrid (BM25 + embeddings)" if index.semantic_enabled else "lexical only (BM25)"
    print("%d slides | %s\n" % (len(slides), mode))
    if not index.semantic_enabled and not args.lexical:
        print("  NOTE: embeddings unavailable — cross-language questions will fail.")
        print("  Check GEMINI_API_KEY and network, or pass --lexical to silence this.\n")

    positives, negatives, wrong = [], [], []
    pos_out, neg_out = [], []       # the same queries, judged by standout
    pairs = []                      # (similarity, coverage, should_miss)
    print("%-30s %6s %6s %6s %-5s %s"
          % ("question", "cover", "sim", "stand", "found", "top slide"))
    print("-" * 104)

    for question, expected in load_questions(args.file):
        should_miss = question.startswith("!")
        text = question.lstrip("!")
        # The same first gate the tool has. `search_condo_info` refuses
        # commercial questions *before* searching, so measuring
        # `search_slides` alone reports failures the guest can never see —
        # "ค่าส่วนกลางเท่าไหร่" was counted as a wrong answer here while the
        # robot has always replied "no data, ask the sales team".
        #
        # This is the third time in this project that a measurement was taken
        # one layer away from the thing being judged. It reads as rigour and
        # it produces numbers about something else.
        if _is_commercial(text):
            print("%-30s %6s %6s %6s %-5s %s"
                  % (text[:30], "-", "-", "-", "no", "(commercial — refused before search)"))
            if not should_miss:
                wrong.append((text, "refused as commercial", "-"))
            continue

        hits = search_slides(text)
        if not hits:
            print("%-30s %6s %6s %6s %-5s %s"
                  % (text[:30], "-", "-", "-", "no", "(nothing)"))
            (negatives if should_miss else positives).append(0.0)
            (neg_out if should_miss else pos_out).append(0.0)
            pairs.append((-1.0, 0.0, should_miss))
            continue

        # `hits[0]`, and `knowledge.search_condo_info` now agrees: the top
        # hit decides. It used to scan the whole list, so the eval could
        # report a question rejected while the tool answered it from an
        # eighth-placed slide — RRF ranks by compromise, not by similarity.
        top = hits[0]
        pairs.append((top.similarity, top.coverage, should_miss))
        title = top.slide.get("title_th") or top.slide.get("title_en") or ""
        ok = " "
        if should_miss:
            negatives.append(top.similarity)
            neg_out.append(top.standout)
            if top.found:
                ok, _ = "!", wrong.append((text, "should have found nothing", title))
        else:
            positives.append(top.similarity)
            pos_out.append(top.standout)
            if not top.found:
                ok, _ = "!", wrong.append((text, "found nothing", title))
            elif expected and expected.lower() not in title.lower():
                ok, _ = "!", wrong.append((text, "expected %r" % expected, title))

        print("%s%-29s %6.2f %6.2f %6.2f %-5s %s" % (
            ok, text[:29], top.coverage, top.similarity, top.standout,
            "yes" if top.found else "no", title[:34],
        ))

    save_query_cache()
    print("\n%d correct, %d wrong" % (
        len(positives) + len(negatives) - len(wrong), len(wrong)))
    for text, why, got in wrong:
        print("  %-32s %-28s got: %s" % (text[:32], why, got[:30]))

    # The calibration payoff: where the two populations actually separate.
    real = [s for s in positives if s >= 0]
    junk = [s for s in negatives if s >= 0]
    if real and junk:
        print("\nCosine similarity on this deck")
        print("  should match:    min %.3f  mean %.3f" % (min(real), sum(real) / len(real)))
        print("  should not:      max %.3f  mean %.3f" % (max(junk), sum(junk) / len(junk)))
        if min(real) > max(junk):
            midpoint = (min(real) + max(junk)) / 2
            print("\n  Clean separation. Suggested settings:")
            print("    SEARCH_MIN_SIMILARITY=%.2f" % midpoint)
            print("    SEARCH_SHOW_SIMILARITY=%.2f" % ((midpoint + min(real)) / 2))
        else:
            print("\n  The two overlap (%.3f vs %.3f), so no threshold separates them"
                  % (max(junk), min(real)))
            print("  cleanly. Favour the higher value: a missing picture costs less")
            print("  than a wrong one. Adding keywords to the slides that are being")
            print("  missed will widen the gap.")
        _separation("Standout above the deck's own mean (sigmas)",
                    [s for s in pos_out if s >= 0], [s for s in neg_out if s >= 0],
                    fmt="%.2f", setting=None)
        _best_pair(pairs)
    elif not real:
        print("\nNo similarities recorded — running without embeddings.")

    # Whatever was paid for this run is kept, even if the run was cut short.
    save_query_cache()
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
