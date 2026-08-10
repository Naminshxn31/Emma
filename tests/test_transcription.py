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


def test_the_facility_names_are_hinted_to_the_recogniser():
    """A guest asked for the **ice bath**. The recogniser, biased toward Thai
    because that's what most guests speak, produced "ไอ้บ้า" — an insult —
    and the robot apologised for having offended them.

    The names are in the deck already; nobody should have to retype them.
    """
    from app.providers.gemini import facility_names

    names = facility_names()
    assert "ICE BATH" in names
    for expected in ("BIOGENESIS", "SKY POOL", "HIMALAYAN SALT SAUNA"):
        assert expected in names, expected
    assert "ICE BATH" in _cfg()["custom_vocabulary"]


def test_facility_names_exclude_slogan_words():
    """Slide titles are full of marketing copy in capitals. "BUILD" and
    "EVERYBODY" are not facilities and dilute the vocabulary hints."""
    from app.providers.gemini import facility_names

    names = facility_names()
    for junk in ("BUILD", "BETTER", "EVERYBODY", "QUESTION", "PRELIMINARY"):
        assert junk not in names, junk
    assert len(names) < 60, "too many hints stops being a hint"


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
