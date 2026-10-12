"""
Guest-side transcription: the caption must say what the guest actually said.

Reported as "ข้อความไม่ตรงกับเสียง ... ที่บอกว่าไม่ตรงคือตัว input" — the
panel showed the guest saying "ao rummy" (a romanisation) when they had
spoken Thai, and rendered Thai as "เอา ทุก คน เลย".

Two independent causes:

1. Every transcription field the code was sending is deprecated in
   google-genai 2.16, and no language hint was being sent at all by default,
   so Thai audio could be decoded by an English recogniser.
2. The recogniser word-spaces Thai, which isn't how Thai is written.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.providers.thai_spacing import ThaiSpacing, collapse

# ==================== language hints ====================


def _cfg():
    from app.providers.gemini import _transcription_config

    return _transcription_config().model_dump(exclude_none=True)


def test_uses_the_current_field_names_not_the_deprecated_ones():
    """`language_hints`, `language_auto` and `adaptation_phrases` are all
    marked Deprecated in google-genai 2.16. Sending only those risks the
    server ignoring them outright — which is consistent with the hints
    having had no visible effect."""
    cfg = _cfg()
    assert "language_codes" in cfg
    assert "custom_vocabulary" in cfg
    for dead in ("language_hints", "language_auto", "adaptation_phrases"):
        assert dead not in cfg, "%s is deprecated and must not be sent" % dead


def test_thai_is_hinted_first_by_default():
    """The gallery is in Thailand. With no hint at all the recogniser guessed
    English for Thai speech and romanised it."""
    cfg = _cfg()
    assert cfg["language_codes"][0].startswith("th")


def test_hints_do_not_lock_out_other_languages(monkeypatch):
    """These are hints, not a whitelist — a Chinese walk-in must still be
    captionable. Guard against someone 'simplifying' this to one code."""
    cfg = _cfg()
    assert len(cfg["language_codes"]) > 1, "one code reads as a whitelist"


def test_auto_still_disables_hinting(monkeypatch):
    monkeypatch.setattr(settings, "transcribe_languages", "auto")
    assert "language_codes" not in _cfg()


def test_custom_vocabulary_carries_the_project_name(monkeypatch):
    monkeypatch.setattr(settings, "project_name", "Embassy World")
    assert "Embassy World" in _cfg()["custom_vocabulary"]


# ---- facility vocabulary --------------------------------------------------
#
# The fixtures pin their own approval state. A test that leaned on the real
# deck being approved (it is, since 2026-10-06) would turn red the day a
# slide is revoked — with the code still right — and one that leaned on it
# being a draft is the inverted-safety bug CLAUDE.md records. The real deck
# gets its own test below that holds for *any* approval state.

_APPROVED = {
    "source_id": "slide_catalog", "approval_status": "approved",
    "approved_by": "test", "approved_at": "2026-01-01T00:00:00+07:00",
    "effective_at": "2026-01-01T00:00:00+07:00", "disclosure_scope": "customer",
    "content_state": "preliminary_concept",
}


def _slide(title_en, kind="facility", **overrides):
    return {**_APPROVED, "project_id": settings.project_id, "id": title_en,
            "type": kind, "title_en": title_en, "title_th": "", **overrides}


def _facts(facts, **overrides):
    return {**_APPROVED, "source_id": "project_facts",
            "project_id": settings.project_id, "facts": facts, **overrides}


@pytest.fixture
def vocabulary_sources(monkeypatch, tmp_path):
    """Point facility_names at a fixture deck and a fixture facts file."""
    import json

    import app.providers.gemini as gem
    from app.tools import slides as sl

    def use(deck, facts):
        path = tmp_path / "facts.json"
        path.write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(sl, "load_slides", lambda: deck)
        monkeypatch.setattr(gem, "_facts_path", lambda: path)

    return use


def _folded(names):
    return {n.casefold() for n in names}


def test_the_facility_names_are_hinted_to_the_recogniser(vocabulary_sources):
    """A guest asked for the **ice bath**. The recogniser, biased toward Thai
    because that's what most guests speak, produced "ไอ้บ้า" — an insult —
    and the robot apologised for having offended them.

    The names are in the deck already; nobody should have to retype them.
    The 2026-10-06 library writes them in mixed case ("Ice Bath
    (Underground)", "Sky Pool on the 3rd Floor"), which the old ALL-CAPS
    scrape could not see: it found 8 terms, three of them junk, and Ice Bath
    was not one of them. Verified before rewriting this test: the real
    approved deck *does* have the ice bath (intro-034), so demanding it is
    honest — the fixture below just pins the approval state.
    """
    from app.providers.gemini import facility_names

    vocabulary_sources(
        [_slide("Ice Bath (Underground)"),
         _slide("Sky Pool on the 3rd Floor"),
         _slide("Himalayan Salt Sauna / Salt Cave"),
         _slide("Journey to Mars space-themed pool tunnel"),
         _slide("BIOGENESIS curved-glass fitness overlooking the pool"),
         _slide("BUNGEE GYM & YOGA room")],
        _facts([{"label": "จุดเด่น",
                 "value": "ชั้นใต้ดิน: Aqua Cinema, Crystal Stone Sauna, Ice Bath · "
                          "ชั้น 3: Co-working Lounge, Common Sphere"}]),
    )
    names = _folded(facility_names())
    for expected in ("ice bath", "sky pool", "himalayan salt sauna", "journey to mars",
                     "biogenesis", "bungee gym & yoga", "aqua cinema",
                     "crystal stone sauna", "co-working lounge", "common sphere"):
        assert expected in names, expected
    assert "ice bath" in _folded(_cfg()["custom_vocabulary"])


def test_facility_names_exclude_slogan_and_layout_words(vocabulary_sources):
    """Slide titles are full of marketing copy and page furniture. "Section
    Divider", "Lifestyle Concept", "Living Room" are not facilities and
    dilute the hints — and the old filter, a hand-kept list of "not a name"
    words, *manufactured* junk: stripping THE and JUNIOR turned THE PARK and
    JUNIOR WORLD into "PARK" and "WORLD". Only facility pages name places,
    and a single English word inside Thai ("Lobby", "Studio") is as often a
    common noun as a name."""
    from app.providers.gemini import facility_names

    vocabulary_sources(
        [_slide("Section Divider — The Park", kind="concept"),
         _slide("Lifestyle Concept — Wealth as a Lifestyle", kind="concept"),
         _slide("The Units — Living Room toward Kitchen", kind="interior"),
         _slide("JUNIOR WORLD kids' space- and ocean-themed play room")],
        _facts([{"label": "โครงการ", "value": "ชั้น G: Lobby, Studio 24 ตร.ม. Low-rise (A–F)"}]),
    )
    names = facility_names()
    assert _folded(names) == {"junior world"}, names


def test_facility_names_never_carry_unapproved_copy(vocabulary_sources):
    """This list leaves the building inside the session config. A draft
    slide's name, a fact the file narrows to non-customer, or a whole facts
    file awaiting approval must not ride along — the same gate the prompt
    and the customer search apply."""
    from app.providers.gemini import facility_names

    vocabulary_sources(
        [_slide("Ice Bath (Underground)"),
         _slide("Secret Spa (Underground)", approval_status="draft")],
        _facts([{"label": "a", "value": "Sky Pool, Water Lagoon"},
                {"label": "b", "value": "Hidden Garden, Night Market",
                 "disclosure_scope": "internal"}]),
    )
    names = _folded(facility_names())
    assert {"ice bath", "sky pool", "water lagoon"} <= names
    assert not names & {"secret spa", "hidden garden", "night market"}, names

    vocabulary_sources([], _facts([{"label": "a", "value": "Sky Pool, Water Lagoon"}],
                                  approval_status="pending"))
    assert facility_names() == [], "an unapproved facts file contributes nothing"


def test_one_entry_per_facility_however_it_is_spelled(vocabulary_sources):
    """The deck and the facts spell some names differently. Two spellings
    of one place are two slots of a deliberately small list, and the deck's
    ALL-CAPS styling gives way to an ordinary spelling when one exists."""
    from app.providers.gemini import facility_names

    vocabulary_sources(
        [_slide("Hydro Therapy Capsule (Underground)"),
         _slide("SPINNING CLASS studio")],
        _facts([{"label": "a", "value": "Hydrotherapy Capsule, Spinning Class"}]),
    )
    names = facility_names()
    assert len(names) == 2, names
    assert "Spinning Class" in names


def test_the_real_deck_reaches_the_recogniser_in_any_approval_state():
    """Against the real files, whatever their approval state on the day:
    every approved facility page's name is hinted, nothing from an
    unapproved one is, the list stays small, and the whole vocabulary stays
    under ~100 phrases (the SDK documents no cap; see _FACILITY_MAX)."""
    from app.knowledge_policy import evaluate_claim
    from app.providers.gemini import (_FACILITY_MAX, _leading_name, adaptation_phrases,
                                      facility_names)
    from app.tools.slides import load_slides

    names = _folded(facility_names())
    for slide in load_slides():
        if slide.get("type") != "facility":
            continue
        name = _leading_name(slide.get("title_en", "")).casefold()
        if evaluate_claim(slide, settings.project_id).allowed:
            assert name in names, name
    assert len(names) <= _FACILITY_MAX
    for junk in ("park", "world", "yoga", "section divider", "concept"):
        assert junk not in names, junk
    assert len(adaptation_phrases()) <= 100


def test_facility_names_survive_unreadable_facts(vocabulary_sources, monkeypatch):
    """Vocabulary is a nicety: a broken facts file costs the fact names,
    not the deck's names and not the session."""
    import app.providers.gemini as gem
    from app.providers.gemini import facility_names

    vocabulary_sources([_slide("Ice Bath (Underground)")], _facts([]))
    monkeypatch.setattr(gem, "_facts_path", lambda: Path("no/such/facts.json"))
    assert _folded(facility_names()) == {"ice bath"}


def test_facility_names_survive_a_missing_deck(monkeypatch):
    """Vocabulary is a nicety; the session starting is not."""
    import app.providers.gemini as gem
    from app.tools import slides as sl

    monkeypatch.setattr(sl, "load_slides", lambda: (_ for _ in ()).throw(OSError("no deck")))
    assert gem.adaptation_phrases(), "must still return the built-in phrases"


def test_transcription_config_never_raises(monkeypatch):
    """A rejected config kills the whole session. Losing a caption is
    survivable; losing the conversation over one is not."""
    import app.providers.gemini as gem

    monkeypatch.setattr(gem, "adaptation_phrases", lambda: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        gem.adaptation_phrases()
    # The real call must still produce something usable.
    monkeypatch.setattr(gem, "adaptation_phrases", lambda: [])
    assert gem._transcription_config() is not None


# ==================== Thai word-spacing ====================


def test_collapsing_is_off_by_default():
    """It cannot distinguish word-spacing from the clause-boundary spaces
    Thai legitimately uses, so it silently rewrites correct transcripts.
    Opt-in only — the language hint is the real fix."""
    from app.config import Settings

    assert Settings().thai_spacing is False


def test_the_clause_space_this_would_eat_is_a_real_one():
    """Documents the exact cost, caught by the existing conversation test:
    a normal Thai reply loses the space after ค่ะ."""
    assert collapse("ขอโทษค่ะ ยังไม่มีข้อมูลราคา") == "ขอโทษค่ะยังไม่มีข้อมูลราคา"


def test_collapses_spaces_between_thai_words():
    assert collapse("เอา ทุก คน เลย") == "เอาทุกคนเลย"


def test_keeps_spaces_around_latin_words():
    """Removing these would run the project name into the Thai around it."""
    assert collapse("โครงการ Embassy World พัฒนา โดยบริษัท") == (
        "โครงการ Embassy World พัฒนาโดยบริษัท"
    )


def test_keeps_spaces_between_latin_words():
    text = "The swimming pool is on the fourteenth floor"
    assert collapse(text) == text


def test_keeps_the_space_between_thai_and_a_number():
    assert collapse("ราคา 3.5 ล้าน") == "ราคา 3.5 ล้าน"


def test_streaming_matches_whole_string_processing():
    """A space can be the last character of one delta with the deciding
    character in the next, so the split must not change the result."""
    text = "โครงการ Embassy World พัฒนา โดยบริษัท ในเครือ Empire Group ค่ะ"
    expected = collapse(text)
    for size in (1, 2, 3, 5, 7, 11):
        f = ThaiSpacing()
        out = "".join(f.feed(text[i:i + size]) for i in range(0, len(text), size))
        out += f.flush()
        assert out == expected, "chunk size %d gave %r" % (size, out)


def test_a_trailing_space_is_not_swallowed():
    """Held back waiting for the next character that never came."""
    f = ThaiSpacing()
    assert f.feed("สวัสดี ") + f.flush() == "สวัสดี "


def test_disabled_passes_text_through_untouched():
    f = ThaiSpacing(enabled=False)
    assert f.feed("เอา ทุก คน เลย") == "เอา ทุก คน เลย"


def test_reset_drops_held_state():
    f = ThaiSpacing()
    f.feed("สวัสดี ")
    f.reset()
    assert f.feed("ครับ") == "ครับ"
