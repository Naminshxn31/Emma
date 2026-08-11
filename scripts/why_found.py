#!/usr/bin/env python3
"""Print exactly why `search_condo_info` accepted or rejected a question.

Written after `eval_search.py` reported every nonsense question rejected while
two tests asserting the same thing still failed. The eval measures
`search_slides` over the 144-slide deck; the tool searches whatever
`load_slides()` returns at the time, which in the test fixture also includes
the page documents imported from the sales script. Same rule, different
corpus — so the calibration was real and simply did not cover what the tests
ask about.

    python scripts/why_found.py "zzzz ไม่มีอยู่จริง qqqq"
    python scripts/why_found.py            # the three the tests assert on
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULTS = ["zzzz ไม่มีอยู่จริง qqqq", "zzzz qqqq ไม่มีจริง", "ราคาหุ้นวันนี้"]


def main() -> int:
    from app.tools import slide_search
    from app.tools.knowledge import _is_commercial
    from app.tools.slides import load_slides, search_slides

    questions = sys.argv[1:] or DEFAULTS
    slides = load_slides()
    print("corpus: %d documents | MIN_SIMILARITY=%.3f  MIN_COVERAGE=%.3f\n"
          % (len(slides), slide_search.MIN_SIMILARITY, slide_search.MIN_COVERAGE))

    for question in questions:
        print("%r" % question)
        if _is_commercial(question):
            print("   -> refused as a commercial question before any search\n")
            continue
        hits = search_slides(question)
        if not hits:
            print("   -> nothing ranked at all\n")
            continue
        for hit in hits[:3]:
            why = []
            if hit.similarity >= slide_search.MIN_SIMILARITY:
                why.append("similarity %.3f >= %.3f" % (hit.similarity, slide_search.MIN_SIMILARITY))
            if hit.coverage >= slide_search.MIN_COVERAGE:
                why.append("coverage %.2f >= %.2f" % (hit.coverage, slide_search.MIN_COVERAGE))
            print("   %-5s sim %.3f  cover %.2f  %-34s %s"
                  % ("FOUND" if hit.found else "-", hit.similarity, hit.coverage,
                     (hit.slide.get("title_th") or hit.slide.get("id") or "")[:34],
                     " and ".join(why) or "below both"))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
