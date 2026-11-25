"""Her name first (CEREMONY_NAME_FIRST, owner 24 Nov 2026 ~17:40): the words of Khun Miki that CALL her, and what a
question of his means when its words are not the script's.

"ให้ทั้งหมดที่หุ่นจะตอบก็ต่อเมื่อมีคำว่า emma ไม่ว่าจะถามไปแล้วหรือยังไม่ถาม ... ในกรณีที่เขาพูดชื่อ emma แล้วยังพูดก็ยังไม่ตอบ
รอจนกว่าเขาจะพูดเสร็จ ... ให้หุ่นมองหาคีย์เวิร์ด ... บางทีคำไม่ตรงกับสคริป์ แต่ความหมายเหมือนกันก็ได้"

Pure text in, answer out: no state, no I/O, no logging of his words. app/ceremony_follow.py keeps the state.

* `called`: he speaks TO her ("Emma, where is ...", "... in particular, Emma?"), not about her ("Emma just told us ...",
  "how Emma can help us", "ask Emma to play it later").
* `is_request`: he asks for something, he does not tell the guests about it.
* `by_meaning`: the one cue whose `means` patterns (host.json) are in his words, or None (none / a tie).
"""
from __future__ import annotations

import re

#: Her name as the recogniser writes it. "Ma," / a caption that opens with a comma (", how can you help us?") is her
#: name with its head lost (rehearsal 24 Nov): only where nothing else could stand.
_NAME = r"(?:emmas?|emmah|emmar|emar|emme|emmi|emmer|emmett?|emmerd|ema|emmy|hemma|gemma|jemma|amma|imma|ima|em ma|em a)"
NAME = re.compile(r"\b" + _NAME + r"(?:['’]s(?=\s*(?:[,?!.]|$)))?(?![a-z])", re.IGNORECASE)
_HEAD_LOST = re.compile(r"^\s*(?:(?:hey|hi|hello|ok|okay|so|well|now|and)[\s,]+)?(?:(?:ma|mama|amy|mr|m a|emmanuel)\s*[,?]|,|\.\s*,)",
                        re.IGNORECASE)
_WORD = re.compile(r"[a-z']+")

#: The word before her name that makes it ABOUT her whatever comes after...
_ABOUT_STRONG = frozenset("ask asked asking let lets welcome meet with introduce named call called invite invited thank "
                          "and hear heard watch watched make made give gave gives robot at".split())
#: ...and the word that does unless he asked first and ends on her name ("where is it Emma?").
_ABOUT_BEFORE = frozenset("as how what when if that than to by about our is was the like for from of see seen where why "
                          "whether because while until after before have had".split())
#: ...a helper verb right before her name asks the GUESTS about her ("Can Emma open the curtains?", "Isn't Emma great?")
#: unless he stops on it ("My question is Emma, how many units?"). The chatter audit of 24 Nov: the curtains opened on it.
_HELPER_BEFORE = frozenset("can can't could will won't would does doesn't did didn't do don't isn't aren't wasn't is was has "
                           "hasn't should".split())
#: ...and telling what was or will be said to her ("I told Emma, turn off the lights. And she did.", "When I say Emma,
#: turn off ...", "My friend said hey Emma ..."). Not "I must say Emma, you know the details very well".
_REPORTED = re.compile(r"\b(?:(?:told|tells|tell|said|says|saying)(?:\s+(?:hey|hi|hello))?|"
                       r"(?:can|could|will|would|when i|when you|when we|if i|if you|if we|later i|just|only)\s+say\s*,?(?:\s+(?:hey|hi|hello))?)\s*$",
                       re.IGNORECASE)
#: ...and the word after it.
_ABOUT_AFTER = frozenset("said says say told tells just has had was knows know's remembers mentioned mentions explained "
                         "explains showed shows already answered answers does did introduced introduces also recommends "
                         "recommended suggested suggests 's speaks spoke talks talked helps wants gave gives".split())
#: A helper verb after her name is about her unless he goes on to "you / we / I / it / there / this / that".
_HELPER = frozenset("will can could would is should may might must shall".split())
_TO_HER = frozenset("you we i it there this that us everyone what where how".split())
#: ("Emma is the pool deep?" asks; "Emma is the smart assistant" tells.)
_TO_HER_IF_ASKED = frozenset("the our your my".split())


#: What follows her name and a comma when he tells the guests of her.
_TOLD_OF_HER = re.compile(r"\s*,\s*(?:as (?:you|we|everyone) (?:can|all|will)|who (?:is|can|will|has|was)|our (?:new |smart |own )?(?:robot|assistant|host)|"
                          r"the (?:smart |new )?(?:robot|assistant))\b", re.IGNORECASE)
#: A sentence put to the hall after a word to her ("Thank you, Emma. How many of you came by car?").
_TO_THE_HALL = re.compile(r"\b(?:how many|who|which|any|some|all|most) (?:of )?(?:you|the guests|our guests)\b(?! (?:think|recommend|suggest))|"
                          r"\braise your hands?\b|\bladies and gentlemen\b|\b(?:everyone|everybody),? please\b|\bplease,? (?:everyone|everybody)\b|"
                          r"\b(?:sit down|take (?:your|a) seats?)\b", re.IGNORECASE)


def _plain_head(sentence: str) -> str:
    """The sentence with her name and a comma after it out of its head ("Emma, I want to tell ..." -> "I want to tell ...")."""
    return re.sub(r"^\s*(?:(?:hey|hi|hello|ok|okay|so|now)[\s,]+)?" + _NAME + r"\b[\s,]*", "", sentence or "", flags=re.IGNORECASE)


def _words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower().replace("’", "'"))


def called(text: str) -> bool:
    """He calls her in these words (at least one mention of her name that is not ABOUT her)."""
    t = " ".join((text or "").split())
    if not t:
        return False
    if _HEAD_LOST.match(t) and len(_words(t)) >= 3:
        return True
    for m in NAME.finditer(t):
        before, after = t[:m.start()], t[m.end():]
        prev = re.search(r"([a-z']+)\s*$", before.lower())
        prev = prev.group(1) if prev and not re.search(r"[.,!?;:]\s*$", before) else None
        stop_after = bool(re.match(r"\s*[.,!?;:]|\s*$", after))
        tail = _words(after)
        nxt = tail[0] if tail else None
        nxt2 = tail[1] if len(tail) > 1 else None
        if after.lower().startswith(("'s", "’s")):
            continue                                     # Emma's answer, Emma's here
        first = not re.search(r"[a-z']+[^a-z']+[a-z']+\s*$", re.split(r"[.!?]", before.lower())[-1])
        if _REPORTED.search(before):
            continue
        if prev in _HELPER_BEFORE and not (after.lstrip()[:1] == "," and not re.match(r"\s*,\s*(?:everyone|everybody|guys)\b", after)) \
                and not (prev in ("do", "did") and stop_after and _asks_before(before)):
            continue                                     # ("What should we do Emma?" asks her)
        if prev in _ABOUT_STRONG and not (prev == "and" and stop_after) and not (prev == "welcome" and first):
            continue
        if prev in _ABOUT_BEFORE and not ((stop_after or prev in ("that", "it", "this")) and _asks_before(before)):
            continue
        if stop_after:
            if _TOLD_OF_HER.match(after) and "?" not in after and not _PLEASE.search(after):
                continue                                 # "Emma, as you can see, is a very smart robot."
            return True
        if nxt in ("really", "always", "never", "still", "even") and nxt2 in _ABOUT_AFTER | {"knows", "remembers", "looks", "does", "is", "has"}:
            continue                                     # "Wow, Emma really knows the details."
        if nxt in _ABOUT_AFTER or nxt == "to" or (nxt == "here" and nxt2 in ("is", "can", "will", "has")):
            continue                                     # "I told Emma to ...", "we want Emma to ...", "Emma here is our robot"
        if nxt in _HELPER and nxt2 not in _TO_HER and not (nxt2 in _TO_HER_IF_ASKED and "?" in after):
            continue
        if nxt == "and" and nxt2 in ("i", "our", "the", "me", "we"):
            continue                                     # "Emma and I will ..."
        return True
    return False


def only_her_name(text: str) -> bool:
    """"Emma?", "Emma, please." -- her name and nothing to go on."""
    return called(text) and len([w for w in _plain(text).split() if w not in ("please", "hey", "hello", "so", "ok", "okay")]) == 0


def _asks_before(before: str) -> bool:
    """"... what is that, Emma?" / "... where is it Emma?": a question came before her name."""
    return bool(re.search(r"\b(?:what|where|how|which|who|when|can you|could you|please)\b", before.lower()))


_REQUEST_START = re.compile(
    r"(?:what|where|how|which|who|when|why|can you|could you|would you|will you|can we|could we|may i|may we|shall we|"
    r"do you|do we|does|is there|is it|are there|are we|have you|has it|you tell|tell|show|please|play|open|turn|switch|"
    r"print|spin|pin|stay|suppose|i would like|i'd like|i would love|i'd love|i want|i prefer|we would like|we'd like|"
    r"we want|make|watch|give|explain|introduce|describe|let's|lets|let us|help|start|run|put|bring|prepare|draw|close|"
    r"pass|send|issue|walk us|run us|say|share|pull|light|imagine|pretend|say i|any|anything|regarding|about|compare)\b", re.IGNORECASE)
_FILLER_START = re.compile(
    r"^(?:(?:ok|okay|so|and|well|wow|now|no|yes|yeah|alright|all right|right|great|good|very good|perfect|excellent|"
    r"thanks|thank you|thank you very much|then|but|ma|hello|hi|hey|dear|"
    r"in that case|first|next|finally|lastly|also|just|maybe|um|uh|er|again|once more|sorry|excuse me|pardon)\b[\s,]*)+", re.IGNORECASE)


_TO_GUESTS = re.compile(r"^please (?:be|welcome|enjoy|give|join|take|have|look|wait|say (?:hello|hi|thank)|come|don't|do not|excuse)\b",
                        re.IGNORECASE)
#: "please" asks -- of her ("lights on please"), not of the guests ("please welcome ...", "please be careful").
_PLEASE = re.compile(r"\bplease\b(?! (?:be|welcome|enjoy|give|join|take|have|look|wait|say (?:hello|hi|thank)|come|don't|do not|excuse))", re.IGNORECASE)
_CURIOUS = re.compile(r"(?:want|wants|like|love|curious|eager|happy)(?:s|d)? to (?:know|hear|learn|understand) "
                      r"(?:what|how|where|which|why|more|about)", re.IGNORECASE)


#: What he is about to TELL the guests ("I want to tell everyone ...", "We would like to thank ..."), and what he asks of the
#: guests after a filler ("So please be patient with us.") -- no request to her (long-talk test 24 Nov night).
_TOLD = re.compile(r"^(?:(?:now|and|so|well|okay|ok|first|today)[\s,]+)*(?:i|we) (?:want|would like|wish|'d like|'d love|would love) "
                   r"to (?:tell|thank|say|show you|invite|welcome|introduce|share|explain|talk|announce|present)\b", re.IGNORECASE)
_TO_GUESTS_AFTER = re.compile(r"^(?:(?:so|and|now|ok|okay|well|then)[\s,]+)+please (?:be|take|enjoy|welcome|join|have|wait)\b",
                              re.IGNORECASE)


#: A clause that opens like a question and is a statement: "Where we are located is ...", "What makes it special is ...".
_WH_STATEMENT = re.compile(r"^(?:what|where|how|why|when)\s+(?:we|i|you|they|it|our \w+|the \w+|this \w+)(?:\s+[a-z']+){1,4}?"
                           r"\s+(?:is|are|was|were)\b", re.IGNORECASE)


def is_request(text: str) -> bool:
    """He is asking for something, not telling the guests about it."""
    last = re.split(r"(?<=[.!?])\s+", " ".join((text or "").split()))[-1]
    if "?" not in (text or "") and (_TOLD.search(_plain_head(last)) or _TO_GUESTS_AFTER.match(last.strip())):
        return False
    if _TO_THE_HALL.search(last) and not NAME.search(last):
        return False                                     # (the hall is asked, not she)
    if "?" in (text or "") or _CURIOUS.search(text or "") or _PLEASE.search(text or ""):
        return True
    for clause in re.split(r"[.!?,;]+|\b" + _NAME + r"\b", text or "", flags=re.IGNORECASE):
        if _TO_GUESTS.match(clause.strip()) or _TOLD.search(clause.strip()) or _TO_GUESTS_AFTER.match(clause.strip()):
            continue
        if _WH_STATEMENT.match(_FILLER_START.sub("", clause.strip())):
            continue                                     # ("Where we are located is one of the best places ...")
        clause = clause.strip()
        if clause and (_REQUEST_START.match(clause) or _REQUEST_START.match(_FILLER_START.sub("", clause))):
            return True
    return False


_ATTENTION = re.compile(r"^(?:(?:please|now|ok|okay|so|just) )*(?:listen|wait|hold on|one (?:moment|second|minute)|a moment|get ready|"
                        r"be ready|pay attention|look at me|come on)\b")
_TELL_ME = re.compile(r"^(?:please )?(?:tell|explain|describe|talk|show)\b")
_HOW = re.compile(r"^(?:what|where|how|which|who|when|why)\b")
_THEM = re.compile(r"\b(?:it|them|that|this|those|these|one)\b")
_QUESTION = re.compile(r"^(?:what|where|how|which|who|when|why|can|could|would|will|do|does|did|is|are|have|has|may|shall)\b")


def only_attention(text: str) -> bool:
    """"Emma, please listen." -- he is about to say something: nothing to answer, she goes on listening."""
    plain = _plain(last_words(text, 1))
    return len(plain.split()) <= 5 and bool(_ATTENTION.match(plain))


_UNFINISHED = frozenset("know the a an of to and or about for with is are was be what how where which who when our my your its "
                        "if that like please can could would will you we i us tell show in on at from see hear "
                        "many much".split())
#: A command that is only its verb so far ("Emma, turn off", "Emma, please open"): the thing comes in his next words. With
#: anything else said ("lights off", "turn it off", "start the video") it is whole (review 24 Nov night: the verb alone at
#: the end of every finished command made "Emma, please turn the lights off" wait for ever without a full stop).
_VERB_END = frozenset("off open close turn play print start switch draw".split())
_VERB_ONLY = _VERB_END | frozenset("please can could would will you now just hey ok okay so".split())


#: All he said so far is the opening of a question ("Emma, do we have", "Emma, can you tell me"): more is coming.
_OPENER_ONLY = re.compile(r"^(?:(?:so|and|okay|ok|um|uh|well|hey|now|please)\s+)*(?:do (?:we|you) have|is there|are there|"
                          r"(?:can|could|would) you (?:tell|show|give) (?:me|us)|(?:tell|show|give) (?:me|us)|what (?:is|are)|"
                          r"where (?:is|are)|how (?:many|much|big|far|long)|do you know|i (?:want|would like|'d like) to know)$")
#: Words a question never ends on (no "?" and no full stop): a line is not pressed on them, the next words come first
#: ("So Emma," / "what facilities are" / "on the third floor?" pressed the facilities line before the floor came).
_DANGLING = frozenset("are is was were the a an of to and or about for with our my your their many much".split())


def dangling(text: str) -> bool:
    """His words stop where no question can end: wait for the rest before pressing anything (long-talk test 24 Nov night)."""
    words = _plain(text).split()
    if not words or "?" in (text or "") or (text or "").rstrip().endswith((".", "!")):
        return False
    return words[-1] in _DANGLING or bool(_OPENER_ONLY.match(" ".join(words)))


def unfinished(text: str) -> bool:
    """He stopped in the middle ("I would like to know", "can you tell us about the"): more is coming."""
    words = _plain(text).split()
    if not words or "?" in (text or "") or (text or "").rstrip().endswith((".", "!")):
        return False
    return (words[-1] in _UNFINISHED or (words[-1] in _VERB_END and all(w in _VERB_ONLY for w in words))
            or bool(_OPENER_ONLY.match(" ".join(words))))


def is_question(text: str) -> bool:
    return "?" in (text or "") or bool(_QUESTION.match(_FILLER_START.sub("", _plain(text))))


def asks_how(text: str) -> bool:
    return bool(_HOW.match(_FILLER_START.sub("", _plain(text))))


def points_back(text: str) -> bool:
    """"Emma, please turn them off." -- what "them" is was said just before her name."""
    plain = _plain(text)
    return len(plain.split()) <= 6 and bool(_THEM.search(plain))


def short(text: str, words: int = 6) -> bool:
    """So few words that they are the question, however the recogniser left them ("Emma and all project different.")."""
    return len(_plain(text).split()) <= words


# ---- what he means ------------------------------------------------------------------------

def _plain(text: str) -> str:
    """Lower case, her name and punctuation out, one space between words (what the `means` patterns read)."""
    t = NAME.sub(" ", (text or "").lower().replace("’", "'"))
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", t).split())


def last_words(text: str, min_words: int = 3) -> str:
    """His last sentence, with the one before it while it is too short to mean anything ("..., Emma?", "Please.")."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", " ".join((text or "").split())) if p.strip()]
    out = ""
    for p in reversed(parts):
        out = (p + " " + out).strip()
        if len(_plain(out).split()) >= min_words:
            break
    return out


def asked_part(text: str) -> str:
    """From the last sentence that opens by calling her, to the end ("We saw the rooms. Emma, how can you help us?" ->
    "Emma, how can you help us?"); the whole when none does."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", " ".join((text or "").split())) if p.strip()]
    for k in range(len(parts) - 1, 0, -1):
        if NAME.search(" ".join(parts[k].split()[:3])) and called(parts[k]):
            return " ".join(parts[k:])
    return " ".join(parts)


def by_meaning(text: str, rules: dict) -> dict | None:
    """`rules` = {cue: {"means": [pattern, ...], "not": [pattern, ...], "next"?: [...], "alike"?}}. The one cue of which a pattern is in his last
    sentence -- or, when the last sentence names none, in the whole of a short utterance. A tie = the longest stretch of
    his words matched wins; as long = None."""
    whole = " ".join((text or "").split())
    asked = _plain(asked_part(whole))
    for piece in dict.fromkeys((last_words(whole), whole if len(_plain(whole).split()) <= 40 else "")):
        plain = _plain(piece)
        if not plain:
            continue
        hits = {}
        for cue, row in rules.items():
            if any(re.search(p, plain) for p in row.get("not") or ()):
                continue
            if any(re.search(p, asked) for p in row.get("not_all") or ()):
                continue                                 # ("I am a customer with a family. How would you help?")
            spans = [m.end() - m.start() for p in row.get("means") or () for m in [re.search(p, plain)] if m]
            if row.get("next") and not is_question(piece) and not _TELL_ME.match(plain):
                # (only while this line is the very next: a bare answer to her question, praise before a joke)
                spans += [m.end() - m.start() for p in row["next"] for m in [re.search(p, plain)] if m]
            if spans and (row.get("tell") or short(piece) or is_request(piece)):
                # (a question / a command of his is ASKED: "I think everyone wants to see the technology" is told)
                hits[cue] = max(spans)
        if hits:
            ranked = sorted(hits.items(), key=lambda kv: -kv[1])
            if len(ranked) > 1 and ranked[1][1] == ranked[0][1]:
                return None
            n = len(plain.split())
            return {"verdict": "match", "cue": ranked[0][0], "by": "means", "cover": 1.0,
                    "second": 1.0 if len(ranked) > 1 else 0.0, "matched": max(1, n), "tokens": n, "heard": n}
    return None
