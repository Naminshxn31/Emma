"""Which language this conversation is in -- kept by the server, from what the
guest said, so one misheard line cannot move it.

A robot conversation of 19 ต.ค., an English talk: the guest's accent came out
as "Bei der Kondom ist inklusive Karte?" ("is the condo inclusive curtain?")
and Emma told the whole furniture story in German; a "yeah" heard as "ใช่"
turned her Thai; a garbled "de" got the prompt's own Thai example sentence;
a question about which room to buy got a Thai sentence quoted in the sales
context. Twice the guest had to say "speak English".
The prompt already said one stray utterance is not a switch -- a system-prompt
rule; what this model obeys is what rides on a tool result (CLAUDE.md), so the
language is computed here and handed over with every tool result
(`TALK_LANGUAGE_NOTE`, providers/gemini.py).

The rule, in order:
* the guest's first utterance that says enough sets the language (the
  greeting is ours, not theirs: 7 ต.ค. a Thai guest got English replies when
  the greeting counted);
* asking for a language ("answer in German", "พูดภาษาไทย", "พูดภาษาอังกฤษ
  ได้ไหม" -- Thai asks politely with ได้ไหม) sets it at once and holds until
  asked again; "Can you speak Hebrew?" in English is a question about her
  and counts for nothing; a negation next to the verb ("I don't speak Thai",
  "ผมพูดภาษาอังกฤษไม่ได้", "ไม่ต้องพูดภาษาอังกฤษ") or somebody else as the
  subject ("My wife can speak Thai") is no request at all -- the line counts
  in the language it is said in, like any other;
* a whole sentence in another SCRIPT moves it at once (Thai and English do
  not mishear into each other past a word or two; replaying 15 ต.ค., a Thai
  team and an English tester took turns, and "What is there for children?"
  is somebody asking in English);
* the same script (English -> German, where an accent does exactly that)
  moves only on two utterances in a row; after a request, any language
  needs three -- a misread request must not hold a whole talk;
* short ones ("ใช่", "OK", "de") say too little to count either way.

Replayed over the robot's logs 12-19 ต.ค. (252 replies): it disagrees with the
language Emma answered in 10 times -- the 19 ต.ค. slips above, a Chinese reply
to "快去外面玩了吧" in a Thai talk, a Thai reply to a knock heard as words.

Pure: no I/O, no settings. Script for th/zh/ja/ko/ru/he/ar/hi; inside Latin a
handful of function words tells English from German/French/Spanish/Italian.
"""
from __future__ import annotations

import re

#: What the language is called in the note (Thai, like every other
#: instruction the tools hand the model).
NAMES = {"th": "ไทย", "en": "อังกฤษ", "zh": "จีน", "ja": "ญี่ปุ่น", "ko": "เกาหลี", "ru": "รัสเซีย",
         "he": "ฮีบรู", "ar": "อาหรับ", "de": "เยอรมัน", "fr": "ฝรั่งเศส", "es": "สเปน", "it": "อิตาลี",
         "hi": "ฮินดี"}

_SCRIPTS = (("th", re.compile(r"[฀-๿]")), ("ja", re.compile(r"[぀-ヿ]")),
            ("zh", re.compile(r"[一-鿿]")), ("ko", re.compile(r"[가-힯]")),
            ("ru", re.compile(r"[Ѐ-ӿ]")), ("he", re.compile(r"[֐-׿]")),
            ("ar", re.compile(r"[؀-ۿ]")), ("hi", re.compile(r"[ऀ-ॿ]")))
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")
_WORDS = {
    "en": {"the", "is", "are", "you", "what", "how", "do", "does", "can", "i", "we", "my", "it", "this",
           "there", "have", "a", "an", "of", "to", "in", "and", "for", "where", "much", "many", "room"},
    "de": {"der", "die", "das", "und", "ist", "sind", "ich", "wir", "sie", "nicht", "mit", "wie", "was",
           "wo", "ein", "eine", "einem", "haben", "gibt", "es", "auch", "zimmer", "wohnung", "bitte"},
    "fr": {"le", "la", "les", "est", "sont", "je", "nous", "vous", "avec", "comment", "que", "où", "une",
           "des", "il", "y", "pas", "chambre"},
    "es": {"el", "la", "los", "las", "es", "son", "yo", "usted", "con", "cómo", "qué", "dónde", "una",
           "hay", "cuánto", "habitación"},
    "it": {"il", "la", "è", "sono", "io", "noi", "con", "come", "che", "dove", "una", "quanto",
           "camera", "non"},
}
#: Enough letters to count: a "ใช่"/"OK"/"de" says nothing about the language.
MIN_LETTERS = 8
#: Utterances in a row in another language that move it: SWITCH_RUN as a
#: rule, LOCKED_RUN after the guest asked for a language.
SWITCH_RUN = 2
LOCKED_RUN = 3
#: ...except a whole sentence in another SCRIPT, which moves it at once:
#: Thai and English do not mishear into each other past a word or two
#: ("ใช่"/yeah, "โอเค"/okay), while an accent turns English into German
#: letter for letter. Replaying the robot log (15 ต.ค., a Thai team and an
#: English tester taking turns): "What is there for children?" in a Thai talk
#: is somebody asking in English, and two-in-a-row answered it in Thai.
#: 12 letters: "ตึ้ง ตึง ตึง" (a knock heard as words, 10) stays out.
SCRIPT_SWITCH_LETTERS = 12
_LATIN_LANGS = {"en", "de", "fr", "es", "it"}

_VERB_EN = (r"\b((speak|talk|answer|reply|respond)( to (me|us))?( in)?|switch( back)? to"
            r"|change( back)? to|continue in)\s+")
_VERB_TH = r"(พูด|ตอบ|คุย)\s*(เป็น|ด้วย)?\s*"
#: A language asked for, by code: a verb of speaking aimed at her before the
#: name. Not a bare "in English" or "เป็นภาษาอังกฤษ" -- replaying the robot
#: log, "ชื่อเป็นภาษาอังกฤษอะไร" (what is it called in English) read as a
#: request, locked English, and a Thai team's next 15 questions would have
#: been told "answer in English".
_ASK = [
    ("en", re.compile(_VERB_EN + r"english\b|" + _VERB_TH + r"ภาษาอังกฤษ|(说|讲|用)英(语|文)", re.I)),
    ("th", re.compile(_VERB_EN + r"thai\b|" + _VERB_TH + r"ภาษาไทย|(说|讲|用)泰语", re.I)),
    ("zh", re.compile(_VERB_EN + r"(chinese|mandarin)\b|" + _VERB_TH + r"ภาษาจีน|(说|讲|用)中文", re.I)),
    ("de", re.compile(_VERB_EN + r"german\b|" + _VERB_TH + r"ภาษาเยอรมัน|auf deutsch", re.I)),
    ("ja", re.compile(_VERB_EN + r"japanese\b|" + _VERB_TH + r"ภาษาญี่ปุ่น|日本語で", re.I)),
    ("ko", re.compile(_VERB_EN + r"korean\b|" + _VERB_TH + r"ภาษาเกาหลี|한국어로", re.I)),
    ("ru", re.compile(_VERB_EN + r"russian\b|" + _VERB_TH + r"ภาษารัสเซีย|по-русски", re.I)),
    ("fr", re.compile(_VERB_EN + r"french\b|" + _VERB_TH + r"ภาษาฝรั่งเศส|en français", re.I)),
    ("es", re.compile(_VERB_EN + r"spanish\b|" + _VERB_TH + r"ภาษาสเปน|en español", re.I)),
]
#: English "can you speak X?" is a question about her: it moves nothing
#: (the eval's S21-speak-zh: say yes, switch only when told).
_QUESTION_EN = re.compile(r"\b(can|could|do|does)\s+(you|she|emma)\s+(also\s+)?(speak|understand)\b", re.I)
_PLEASE = re.compile(r"\b(please|from now on)\b", re.I)

#: ...and a verb of speaking next to a language name is not always aimed at
#: her. Pre-commit review 26 ต.ค., run on this class: "Sorry, I don't speak
#: Thai" locked Thai, so the next English questions were handed "answer in
#: Thai" (LOCKED_RUN=3 to undo it) -- the exact slip this module exists to
#: stop, caused by the guest objecting to it. "ผมพูดภาษาอังกฤษไม่ได้นะครับ"
#: (I can't speak English), "ไม่ต้องพูดภาษาอังกฤษนะ" (no need for English) and
#: "My wife can speak Thai" locked the same way. A match is no request when
#: a negation sits next to the verb (English before it; Thai before the verb
#: or after the language name) or when somebody else is its subject. "Why"
#: before the negation is still a request ("why don't you speak English",
#: "ทำไมไม่พูดภาษาไทย"). Erring this way is cheap: a line that is no request
#: still counts in the language it is said in (`detect`), while a lock read
#: wrong holds against three utterances. Replaying the robot logs 9-20 ต.ค.
#: (833 guest utterances, 6 requests) before and after: no decision moved --
#: the logs had none of these lines yet, and every real request still holds.
_CLAUSE = re.compile(r"[.,!?;:，。！？]")
_NEG_EN = re.compile(r"\b(\w+n['’]t|dont|cant|wont|cannot|not|never)"
                     r"(\s+(really|even|very|so|quite|actually|well|much|want to|wanna|need to|have to|like to"
                     r"|(want|like|need|prefer)\s+(you|her|emma)\s+to))?\s+$",
                     re.I)
_WHY_EN = re.compile(r"\bwhy(\s+you)?\s+$", re.I)
_PEOPLE_EN = (r"(wife|husband|son|daughter|kids?|child|children|friends?|family|mother|father|mom|mum|dad"
              r"|parents?|partner|boyfriend|girlfriend|brother|sister|colleagues?|boss|baby)")
_THIRD_EN = re.compile(r"\b(he|she|they|(my|his|her|our|their|your)\s+" + _PEOPLE_EN + r")\s+"
                       r"((can|could|will|would|does|do|did|also|only|just|really|even|to|wants? to|likes? to"
                       r"|\w+n['’]t|dont|cant|cannot|not|never)\s+){0,3}$", re.I)
_NEG_TH_BEFORE = re.compile(r"(ไม่\s*ต้อง|ไม่\s*ค่อย|ไม่\s*ได้|ไม่\s*เคย|ไม่|อย่า|ห้าม)\s*$")
_NEG_TH_AFTER = re.compile(r"\s*((ก็|ยัง)?\s*ไม่\s*(ได้|เป็น)|(ก็|ยัง|ได้)?\s*ไม่\s*(เก่ง|ค่อย|คล่อง))")
#: ...but "ให้เขาพูดภาษาอังกฤษ" (have her speak English) is the guest asking
#: for it, through somebody: เขา right after ให้ is the one to be told.
_THIRD_TH = re.compile(r"((?<!ให้)(?<!ให้ )เขา|ภรรยา|ภริยา|สามี|แฟน|ลูก(?!ค้า)|เพื่อน|พ่อ|แม่|ญาติ)(ชาย|สาว|ๆ)?\s*(ของ)?\s*"
                       r"(ผม|ฉัน|ดิฉัน|เรา|หนู)?\s*(ก็|จะ|เอง|ยัง)?\s*$")
_THAI_START = re.compile(r"\s*[฀-๿]")
#: "Can she speak Chinese?" asks about her (`_QUESTION_EN`), not about a third
#: person: it stays a question, which moves nothing.
_ASKS_HER_EN = re.compile(r"\b(can|could|do|does)\s+(you|she|emma)\s+(also\s+)?$", re.I)


def _not_a_request(text: str, match: re.Match) -> str | None:
    """Why this verb-and-language `match` is not the guest asking her for
    that language ("negation" / "third person"), or None when it is."""
    head = _CLAUSE.split(text[:match.start()])[-1]
    if _THAI_START.match(match.group()):
        neg = _NEG_TH_BEFORE.search(head)
        if neg and "ทำไม" not in head[max(0, neg.start() - 20):neg.start()]:
            return "negation"
        if _NEG_TH_AFTER.match(text, match.end()):
            return "negation"
        if _THIRD_TH.search(head):
            return "third person"
    elif _LATIN.match(match.group()):
        neg = _NEG_EN.search(head)
        if neg and not _WHY_EN.search(head[:neg.start()]):
            return "negation"
        if _THIRD_EN.search(head) and not _ASKS_HER_EN.search(head):
            return "third person"
    return None


def letters(text: str) -> int:
    t = text or ""
    return sum(len(p.findall(t)) for _code, p in _SCRIPTS) + len(_LATIN.findall(t))


def detect(text: str) -> str | None:
    """th/zh/ja/ko/ru/he/ar/hi by script, en/de/fr/es/it by function words
    inside Latin, None when `text` says too little to tell. Thai wins on a
    fifth of the letters: a Thai sentence carries English names in it."""
    t = text or ""
    counts = {code: len(p.findall(t)) for code, p in _SCRIPTS}
    latin = len(_LATIN.findall(t))
    total = sum(counts.values()) + latin
    if total < MIN_LETTERS:
        return None
    if counts["th"] >= 0.2 * total:
        return "th"
    best = max(counts, key=counts.get)
    if counts[best] > latin:
        # kana marks Japanese even among kanji; kanji alone read as Chinese
        return "ja" if counts["ja"] else best
    words = re.findall(r"[a-zà-ɏ]+", t.lower())
    scores = {code: sum(w in vocab for w in words) for code, vocab in _WORDS.items()}
    lang = max(scores, key=scores.get)
    return lang if scores[lang] > scores["en"] else "en"


def asked_for(text: str) -> tuple[str | None, bool]:
    """(the language the guest names, True when it is only a question about
    her) -- (None, False) when no language is named as a choice."""
    t = text or ""
    for code, pattern in _ASK:
        # every mention, not only the first: "I don't speak English, please
        # speak Thai" refuses one and asks for the other
        for match in pattern.finditer(t):
            if _not_a_request(t, match):
                continue
            return code, bool(_QUESTION_EN.search(t) and not _PLEASE.search(t))
    return None, False


class TalkLanguage:
    """Fed the guest's words and Emma's as they are transcribed; says which
    language the conversation is in. Guest chunks before Emma speaks again
    are one utterance (the recogniser sends a sentence in pieces)."""

    def __init__(self) -> None:
        self._utterances: list[str] = []
        self._open = False             # the last thing heard was the guest's

    def guest(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if self._open and self._utterances:
            self._utterances[-1] += " " + text
        else:
            self._utterances.append(text)
            self._open = True

    def emma(self, text: str) -> None:
        if (text or "").strip():
            self._open = False

    def language(self) -> str | None:
        lang, locked, streak, run = None, False, None, 0
        for u in self._utterances:
            asked, question = asked_for(u)
            if asked and not question:
                lang, locked, streak, run = asked, True, None, 0
                continue
            if asked:
                continue                  # "can you speak Chinese?" says nothing either way
            got = detect(u)
            if got is None:
                continue
            if lang is None:
                lang = got
            elif got == lang:
                streak, run = None, 0
            else:
                run = run + 1 if streak == got else 1
                streak = got
                other_script = (got in _LATIN_LANGS) != (lang in _LATIN_LANGS) or (
                    got not in _LATIN_LANGS and lang not in _LATIN_LANGS)
                # a whole sentence in another script moves it now; otherwise two
                # in a row; after a request, three -- a request holds against a
                # slip, but a request misread must not hold against a whole
                # conversation in the other language
                if locked:
                    move = run >= LOCKED_RUN
                else:
                    move = run >= SWITCH_RUN or (other_script and letters(u) >= SCRIPT_SWITCH_LETTERS)
                if move:
                    lang, locked, streak, run = got, False, None, 0
        return lang

    def note(self) -> str | None:
        """The line a tool result carries, or None before the guest has said
        anything that tells."""
        lang = self.language()
        if lang is None:
            return None
        name = NAMES.get(lang, lang)
        return ("ภาษาของบทสนทนานี้คือภาษา%s: ตอบเป็นภาษา%sทั้งคำตอบ แม้ข้อความในผลนี้เป็นภาษาอื่น; "
                "คำสั้นหรือประโยคเดียวที่ฟังเหมือนภาษาอื่น (สำเนียง เสียงหลุด) ไม่ใช่การเปลี่ยนภาษา "
                "เปลี่ยนเมื่อลูกค้าขอ หรือพูดภาษาใหม่สองประโยคติดเท่านั้น" % (name, name))
