"""The debut on the event screen -- card 1 of the ceremony. Owner 23 Nov 2026: "ตอนจะเปิดตัวหุ่นเรา
อาจจะต้องมี soundeffect ให้มันเล้าใจ ดุดัน แล้วจอจะขึ้นเป็นไฟไหม" -> "ฟิวเหมือนคอนเสิร์ต" -> "เอาเป็นดับไฟก่อน
ปิดม่านจากนั้นก็เอาซาวน์นับถอยหลัง 10 9 8 ... เมื่อถึง 0 ก็ให้ทุกอย่างมืด แล้วก็มีซาวน์ แล้วหุ่นก็เปิดตัว" ->
"แนวนอน.mp4 เพิ่มในตอนเปิดตัว" -> after listening: "เอาเวลานับออก" (no countdown: no digits, no counting voice).

The room is already dark (the lighting team, before the press). Card 1 calls `start()`: the room screen
(/mirror, the ceremony's show mode) plays the music's build on the dark stage -- a concert's light
pulsing on its beats, rising -- goes BLACK at 0 (the music breaks to silence), and on the music's HIT
the light bursts, the logo (the opening clip) bursts in and the owner's glitter wave rises behind it;
when the wave has drained away the dark lifts off the living stage and the screen holds the logo's last
frame -- the frame the first line's slide (s02_l03) has always come in from. The robot walks out of
its box on the hit. (A mix with a counting voice would bring its numbers, drawn huge: the code is kept,
the owner's debut has none.)

DARK (reveal.json `"dark": true`, owner 25 Nov ~02:15: "ตอนเปิดทุกอย่างจะดับ โลโก้ไม่ต้องขึ้นก็ได้ ถ้าจะขึ้นก็คือ
สปอร์ตไลท์หลังไฟดับ ซึ่งยังไม่ได้ต่อสมาทโฮม"): the whole room is dark at the opening and nothing on the screen
lights it -- the music's build, its black and its hit are heard exactly as above and the robot walks out on
the same hit, but the screen stays plain black before the press, through it and after it (no light show,
no logo clip, no wave), until the first line's slide comes in from black. Everything else (the times, the
stops, impact_at) is the same run. `false` (or no key) brings the lit debut back; nothing else changes.

    ok, why = ceremony_reveal.ready()            # (False: why_th(why) for the staff)
    r = await ceremony_reveal.start()            # {"ok", "why", "run", "impact_in_s", "length_s", ...}
    ...wait until time.monotonic() >= ceremony_reveal.impact_at() (None = cancelled: no walk)...
    ceremony_reveal.abort("red_stop")            # on every stop path of card 1

Everything is data. data/ceremony/reveal.json names the sound and the video (ids of the ceremony's
asset index) and gives the times for a silent run. The sound is ONE mix (the music's build from a bar
line, its break, its hit: scripts/ceremony_reveal_sound.py mix) whose index entry holds the build's beats
(`beats`), the numbers (`numbers`, empty), when the screen goes black (`zero_s`) and the hit (`impact_s`),
measured on the file. Swapping either is that script; nothing here changes.

Stops. `abort()` and a second start take the screen's debut down at once, sound and all, and mark the
run cancelled (`impact_at()` None). The page and this module take it down on exactly the same things
(review 23 Nov: they used not to agree, and the robot walked out over the held logo): the red stop
(face_mirror.subtitle_cut -- a line's own end, even cut short, is not one), the call over, show mode over
(a `ready` of another app's call), a slide coming up -- face_mirror tells this module (on_stop). Never an
`interrupted` of the call (card 1 rests the arm during the build, the mic opens, and the hall is
playing the music) and never a screen's socket blinking: the page plays on through it (its
sound and wave are already loaded), and when it is back the snapshot stops it if the debut is over.

Screens. The debut goes to every connected screen in the ceremony's show mode, whatever it is: a screen
is never refused (re-review 23 Nov: a 1920 TV at 200% scaling read 960 px and was). A /mirror page says
what it is when it connects (sound aloud or not, touch-first or not, its size in device pixels; again
after it turns landscape): only warnings come of it -- every screen phone-like ("small_screen_only": a
hall screen plays aloud, or is landscape and not touch-first), none playing the sound aloud, two of them
(the mix doubled), and the owner not having listened to this mix (`listened`, pinned to its bytes).
Refused, nothing sent: no screen at all, or not the ceremony's call.

Timing. Every time `start()` gives is from the moment the message is sent, plus START_LAG_S (the message
on the LAN and the sound's start on a screen that loaded it when it opened). The press-to-"ten" lead is
in the sound itself (its first number at ~1 s), so `impact_in_s` is simply START_LAG_S + the hit's time in
the mix. START_LAG_S is a judgement: TO BE MEASURED AT REHEARSAL -- the turnlog row `debut_start` has the
send's wall clock (`wall_ms`); film the screen with a phone, read the hit's frame, the difference less
the mix's `impact_s` is the lag. The screen's own clock follows the sound within 0.5 s and the page's
clock otherwise (a sound late, stalled or refused), so the hit on the screen stays where this counted it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from app import ceremony_slides, face_mirror, turnlog

logger = logging.getLogger("condo_voice.ceremony_reveal")

#: The opening clip's own flare (c_logo_reveal: the ring lit white-blue at ~2.6 s of its 6.7, looked at
#: 23 Nov at 2 frames a second): the clip starts this long before the hit, so the logo bursts in lit.
CLIP_PEAK_S = 2.6
#: The clip's length when the index does not say it.
CLIP_SECONDS = 6.667
#: From the message leaving to the sound starting on the screen (LAN, the sound already loaded -- the
#: screen asks for it when it opens): added to every time `start()` gives, so the walk starts on the hit
#: the room hears. A judgement (Chrome's audio start ~50-100 ms + a LAN hop): to be measured at the
#: rehearsal (the module's docstring: turnlog `debut_start` against a phone's video).
START_LAG_S = 0.15
#: After the hit: the burst held this long before the light settles (no video), and the dark's lift.
HIT_HOLD_S = 1.4
LIFT_S = 1.8
#: A silent run's ring after the hit (no sound to measure).
SILENT_TAIL_S = 3.0

_clock = time.monotonic
_state: dict = {"seq": 0, "runs": {}, "current": None}


# ---- the debut's times ---------------------------------------------------------------------------

def _row(asset_id: str) -> dict:
    """The index's own row (numbers, zero_s, impact_s, duration: fields the parsed entry does not keep)."""
    folder = ceremony_slides.index_dir()
    try:
        rows = json.loads((folder / "index.json").read_text(encoding="utf-8")) if folder else []
    except (OSError, ValueError):
        return {}
    rows = rows.get("assets") if isinstance(rows, dict) else rows
    return next((r for r in rows if isinstance(r, dict) and r.get("id") == asset_id), {}) if isinstance(rows, list) else {}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def _beats(beats, zero: float) -> list | None:
    """The build's beats (the light pulses on them): in order, from 0, before 0 (the black); [] for none."""
    if beats is None:
        return []
    if not (isinstance(beats, list) and len(beats) <= 64 and all(_num(t) for t in beats)):
        return None
    out = [round(float(t), 3) for t in beats]
    if any(t < 0 or t >= zero for t in out) or any(b <= a for a, b in zip(out, out[1:])):
        return None
    return out


def _sound_times(row: dict) -> tuple[list, list, float, float, float] | None:
    """(numbers, beats, zero, impact, length) of the mix's entry, or None when they do not hold together:
    the numbers (none: no countdown -- owner 23 Nov "เอาเวลานับออก") counting down to 1 a beat apart at
    most 3 s, in order, before 0; the beats in order before 0; 0 before the hit, within the file."""
    numbers, zero, impact, length = row.get("numbers"), row.get("zero_s"), row.get("impact_s"), row.get("duration")
    if not (isinstance(numbers, list) and len(numbers) <= 20 and _num(zero) and _num(impact) and _num(length)):
        return None
    beats = _beats(row.get("beats"), float(zero))
    if beats is None:
        return None
    out = []
    for i, pair in enumerate(numbers):
        if not (isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], int) and _num(pair[1])):
            return None
        if pair[0] != len(numbers) - i or pair[1] < 0 or (out and not 0.2 <= pair[1] - out[-1][1] <= 3.0):
            return None
        out.append([pair[0], round(float(pair[1]), 3)])
    if not ((not out or out[-1][1] < zero) and 0 < zero < impact < length <= 60):
        return None
    return out, beats, round(float(zero), 3), round(float(impact), 3), round(float(length), 3)


def _silent_times(cfg: dict) -> tuple[list, list, float, float, float]:
    """A run without its sound, on reveal.json's times: count_from numbers a beat apart after lead_s, or
    (0: no countdown) a build of build_s with the light pulsing every beat_s."""
    count = cfg.get("count_from")
    count = count if isinstance(count, int) and 0 <= count <= 20 else 0
    lead, beat, black, build = (float(cfg[k]) if _num(cfg.get(k)) and 0 <= cfg[k] <= 12 else d
                                for k, d in (("lead_s", 1.0), ("beat_s", 0.667), ("blackout_s", 1.5), ("build_s", 5.33)))
    beat = beat if beat >= 0.3 else 0.667
    numbers = [[count - k, round(lead + k * beat, 3)] for k in range(count)]
    zero = round(lead + count * beat, 3) if count else round(max(1.0, build), 3)
    beats = [] if count else [round(zero - k * beat, 3) for k in range(int(zero / beat), 0, -1)]
    return numbers, beats, zero, round(zero + black, 3), round(zero + black + SILENT_TAIL_S, 3)


def dark() -> bool | str:
    """reveal.json's `dark`: True = the debut heard on a black screen (the module's docstring, DARK); "build" = black
    before the press and through the build and its black, the lit debut from the HIT on (owner 25 Nov ~04:25, after
    rehearsal 6: "ฉากแรกตอนปิดไฟจอก็ควรดำ ถ้าจะขึ้นก็ต้องตรงสปอตไลท์นะ" -- the spotlight comes on with the hit, and with it
    the light, the logo clip to its end and the wave); anything else = the lit debut."""
    value = ceremony_slides.reveal_config().get("dark")
    return True if value is True else "build" if value == "build" else False


def _video(impact: float) -> tuple[dict | None, str | None]:
    """The owner's video from the hit (muted), or (None, why not)."""
    vid = ceremony_slides.reveal_video_id()
    why = ceremony_slides.act_asset_problem(vid, video=True)
    if why is not None:
        return None, why
    entry = ceremony_slides.asset_index()[vid]
    length = _row(vid).get("duration")
    if not (_num(length) and 0.5 <= length <= 60):
        return None, "video_length_unknown"
    cfg = ceremony_slides.reveal_config()
    after = float(cfg["video_at_s"]) if _num(cfg.get("video_at_s")) and 0 <= cfg["video_at_s"] <= 10 else 0.0
    return {"src": "/ceremony-asset/%s" % entry["sha256"], "at_s": round(impact + after, 3),
            "length_s": round(float(length), 3)}, None


def timeline() -> dict:
    """The debut as the screen is told it, in seconds from its start: `numbers` ([number, when it comes
    up]), `zero_s` (black), `impact_s` (the hit), `clip_at_s` (the logo clip starts, hidden till the hit),
    `video` ({src, at_s, length_s} or absent), `settle_s` (the dark lifts), `length_s` (over: the held
    logo), `src` (the sound, absent when it cannot be used) -- and, for the caller, `sound_problem` /
    `video_problem` (None when fine)."""
    cfg = ceremony_slides.reveal_config()
    why = ceremony_slides.reveal_sound_problem()
    times = None
    if why is None:
        times = _sound_times(_row(ceremony_slides.reveal_sound_id()))
        why = None if times else "reveal_sound_times"
    src = None
    if times is not None:
        src = "/ceremony-asset/%s" % ceremony_slides.asset_index()[ceremony_slides.reveal_sound_id()]["sha256"]
    else:
        times = _silent_times(cfg)
    numbers, beats, zero, impact, sound_len = times
    mode = dark()
    black = mode is True
    # (dark: no wave to play, so none to be missing either -- a missing video does not hold a black debut up)
    video, video_why = (None, None) if black else _video(impact)
    clip = _row(ceremony_slides.IDLE_CLIP).get("duration")
    clip = float(clip) if _num(clip) and clip > 0 else CLIP_SECONDS
    clip_at = round(max(0.0, impact - CLIP_PEAK_S), 3)
    settle = round(impact if black else video["at_s"] + video["length_s"] if video else impact + HIT_HOLD_S, 3)
    out = {"numbers": numbers, "beats": beats, "zero_s": zero, "impact_s": impact, "clip_at_s": clip_at, "settle_s": settle,
           # (over when the sound, the clip and the lift are: it ends on the held logo -- dark: when the sound is)
           "length_s": round(sound_len if black else max(sound_len, clip_at + clip, settle + LIFT_S), 3),
           "sound_problem": why, "video_problem": video_why}
    if mode:
        out["dark"] = mode
    if video:
        out["video"] = video
    if src:
        out["src"] = src
    return out


def info() -> dict:
    """What the screen is sent (and asks for when it opens, to load the sound and the video before the
    press): the timeline without the caller's notes."""
    return {k: v for k, v in timeline().items() if not k.endswith("_problem")}


# ---- card 1's entry points (the contract: ready / start / impact_at / abort / cancelled / running) -------

#: The reasons `ready()` / `start()` give, in Thai for the staff's screen (why_th). The first group stops
#: a start (or says it runs without a part); WARNINGS are said and the debut still runs.
WHY_TH = {
    "no_screen": "ยังไม่มีจอห้องต่ออยู่ (เปิดหน้าจอห้อง /mirror บนจอใหญ่ก่อน)",
    "small_screen_only": "เตือน: จอที่ต่ออยู่ดูเหมือนมือถือ/จอสัมผัสแนวตั้งทั้งหมด -- ยังไม่เห็นจอใหญ่ของห้อง (เปิดตัวยังเล่นบนทุกจอ)",
    "not_ceremony": "จอห้องยังไม่อยู่ในโหมดพิธี (สายของแอป Emma Ceremony ยังไม่ขึ้น)",
    "sound_unusable": "ไฟล์เสียงเปิดตัวใช้ไม่ได้ (ไม่มี หรือไม่ตรงกับที่ปักไว้) -- จะเล่นเปิดตัวแบบไม่มีเสียง",
    "video_unusable": "วิดีโอเปิดตัวใช้ไม่ได้ (ไม่มี หรือไม่ตรงกับที่ปักไว้) -- จะขึ้นโลโก้โดยไม่มีวิดีโอ",
    "no_sound_screen": "เตือน: ไม่มีจอไหนเปิดเสียง (/mirror ไม่มี sound=1) -- เปิดตัวจะเงียบ",
    "two_sound_screens": "เตือน: มีจอเปิดเสียงมากกว่าหนึ่งจอ -- เสียงเปิดตัวจะซ้อนกัน",
    "not_listened": "เตือน: เจ้าของยังไม่ได้ฟังไฟล์เสียงเปิดตัวชุดนี้ (ceremony_reveal_sound.py listened)",
    "aborted": "หยุดการเปิดตัวแล้ว",
}
#: Said with ok True: the debut runs (on every connected screen), the staff should know. The refusals
#: (ok False, nothing sent) are only "no_screen" and "not_ceremony"; ready() also answers False for
#: "sound_unusable" / "video_unusable" (start() still runs: silent / without the wave).
WARNINGS = ("small_screen_only", "no_sound_screen", "two_sound_screens", "not_listened")


def why_th(code: str | None) -> str:
    if not code:
        return ""
    return WHY_TH.get(code, WHY_TH.get(code.split(":", 1)[0], "เปิดตัวไม่ได้ (%s)" % code))


def _hall(kind: dict) -> bool:
    """A screen the hall watches, by what its page said: it plays the sound aloud (the robot's room-screen
    URL always says so, ROOM_SCREEN_SOUND), or it is landscape and not touch-first. Never by its size:
    re-review 23 Nov -- a 1920 screen at 200% scaling, a browser zoom, a window not yet maximised all
    read small, and the hall's TV stayed dark while the robot came out."""
    return bool(kind.get("sound")) or (kind.get("w", 0) > kind.get("h", 0) and not kind.get("coarse"))


def _screens() -> tuple[int, int | None, bool]:
    """(screens, of them playing the sound aloud -- None when none said --, only small ones said)."""
    kinds = face_mirror.screen_kinds()
    said = [k for k in kinds if k is not None]
    unknown = len(kinds) - len(said)
    # (a page from before 23 Nov says nothing: a screen, its sound unknown -- no warning on what is not known)
    aloud = sum(1 for k in said if k.get("sound")) if said and not unknown else None
    small_only = bool(said) and not unknown and not any(_hall(k) for k in said)
    return len(kinds), aloud, small_only


def warnings() -> list[str]:
    """Everything the staff should know before the press, though the debut runs: only phone-like screens
    connected, no screen playing the sound aloud, two of them (the mix doubled), the owner not having
    listened to this mix."""
    out = []
    count, aloud, small_only = _screens()
    if not count:
        return out
    if small_only:
        out.append("small_screen_only")
    if aloud == 0:
        out.append("no_sound_screen")
    elif aloud is not None and aloud > 1:
        out.append("two_sound_screens")
    if ceremony_slides.reveal_sound_problem() is None and not _listened():
        out.append("not_listened")
    return out


def _listened() -> bool:
    """The owner listened to THIS mix: `listened` in its index entry, pinned to its bytes
    (scripts/ceremony_reveal_sound.py listened --by ...; a new mix clears it)."""
    sound = ceremony_slides.reveal_sound_id()
    note = _row(sound).get("listened")
    entry = ceremony_slides.asset_index().get(sound)
    return isinstance(note, dict) and bool(note.get("by")) and entry is not None and note.get("sha256") == entry["sha256"]


def ready() -> tuple[bool, str | None]:
    """Can a debut run now as made: a screen connected in the ceremony's show mode, the pinned sound and
    video present and the bytes their pins say. (False, code) otherwise; (True, code) with the first
    warning (WARNINGS: it runs, the staff should know -- warnings() has them all); why_th(code) in Thai."""
    count, _, _ = _screens()
    if not count:
        return False, "no_screen"
    if not face_mirror.show_mode():
        return False, "not_ceremony"
    spec = timeline()
    if spec["sound_problem"]:
        return False, "sound_unusable"
    if spec["video_problem"]:
        return False, "video_unusable"
    warn = warnings()
    return True, (warn[0] if warn else None)


async def start() -> dict:
    """Start the debut on the room screen(s); returns at once:
        {"ok", "why", "run", "impact_in_s", "length_s", "zero_in_s", "numbers_in_s", "warnings"}
    (numbers_in_s: [] -- no countdown; warnings: all of them, `why` the first that matters)
    -- the times in seconds from now (the screen's start lag included: START_LAG_S). Not ok = nothing was
    sent ("no_screen", "not_ceremony"); ok with a `why` = it runs without that part ("sound_unusable": the
    build silent on reveal.json's times; "video_unusable": the logo without the wave). A start while
    one runs cancels that one and starts over."""
    count, _, _ = _screens()
    if not count:
        logger.info("debut: no room screen connected -- nothing to show")
        return {"ok": False, "why": "no_screen", "run": "", "impact_in_s": 0.0, "length_s": 0.0, "warnings": []}
    if not face_mirror.show_mode():
        logger.info("debut: the room screen is not in the ceremony's show mode -- not sent")
        return {"ok": False, "why": "not_ceremony", "run": "", "impact_in_s": 0.0, "length_s": 0.0, "warnings": []}
    spec = timeline()
    now = _clock()
    _down_current("replaced", now)
    _state["seq"] += 1
    seq = _state["seq"]
    run = "debut-%d" % seq
    face_mirror.reveal({**{k: v for k, v in spec.items() if not k.endswith("_problem")}, "seq": seq})
    lag = START_LAG_S
    _state["runs"][run] = {"run": run, "sent": now, "impact_at": now + lag + spec["impact_s"],
                           "ends_at": now + lag + spec["length_s"], "zero_at": now + lag + spec["zero_s"],
                           "down": None, "why": None}
    _state["current"] = run
    for old in list(_state["runs"])[:-8]:
        _state["runs"].pop(old, None)
    warn = warnings()
    why = ("sound_unusable" if spec["sound_problem"] else "video_unusable" if spec["video_problem"]
           else warn[0] if warn else None)
    out = {"ok": True, "why": why, "run": run, "impact_in_s": round(lag + spec["impact_s"], 3),
           "length_s": round(lag + spec["length_s"], 3), "zero_in_s": round(lag + spec["zero_s"], 3),
           "numbers_in_s": [[n, round(lag + t, 3)] for n, t in spec["numbers"]], "warnings": warn}
    # (the rehearsal measures START_LAG_S by this row's wall clock against a phone's video of the screen)
    turnlog.record("debut_start", run=run, wall_ms=int(time.time() * 1000), impact_in_s=out["impact_in_s"],
                   zero_in_s=out["zero_in_s"], lag_s=lag, why=why, screens=face_mirror.screens())
    logger.info("debut %s: sent to %d screen(s); black in %.2f s, the hit in %.2f s, over in %.2f s%s%s", run,
                face_mirror.screens(), out["zero_in_s"], out["impact_in_s"], out["length_s"],
                "" if not spec["sound_problem"] else " (silent: %s)" % spec["sound_problem"],
                "" if not spec["video_problem"] else " (no video: %s)" % spec["video_problem"])
    return out


def _current() -> dict | None:
    run = _state["current"]
    return _state["runs"].get(run) if run else None


def impact_at() -> float | None:
    """time.monotonic() of the current run's hit (the walk out starts on it); None with no run, or one
    that was cancelled."""
    r = _current()
    return None if r is None or r["down"] is not None else r["impact_at"]


def abort(why: str = "aborted") -> None:
    """End the current run on every screen at once (sound cut; the screen back to the held logo frame)
    and mark it cancelled. Idempotent; the screens are told to stop even with no run (a stop is never
    lost)."""
    _down_current(why or "aborted", _clock())
    face_mirror.reveal_stop()


def cancelled() -> bool:
    """The current run was taken down (abort, a new start, or what the screens were told to stop on: the red
    stop, the call over, show mode over, a slide -- face_mirror's hooks)."""
    r = _current()
    return r is not None and r["down"] is not None


def running() -> bool:
    """A run is on the screen now: started, not taken down, not over."""
    r = _current()
    return r is not None and r["down"] is None and _clock() < r["ends_at"]


def where() -> dict | None:
    """The run now: {"run", "phase" (build | dark | impact), "impact_in", "ends_in"}, or None."""
    r = _current()
    now = _clock()
    if r is None or r["down"] is not None or now >= r["ends_at"]:
        return None
    phase = "build" if now < r["zero_at"] else "dark" if now < r["impact_at"] else "impact"
    return {"run": r["run"], "phase": phase, "impact_in": round(r["impact_at"] - now, 2), "ends_in": round(r["ends_at"] - now, 2)}


def cancelled_before_impact(run: str) -> bool:
    """Run `run` was taken down before its hit (or is not known): the robot must not walk out for it."""
    r = _state["runs"].get(run)
    return r is None or (r["down"] is not None and r["down"] < r["impact_at"])


async def until_impact(run: str) -> bool:
    """Wait for run `run`'s hit: True on it, False as soon as it is cancelled (or not known)."""
    while True:
        if cancelled_before_impact(run):
            return False
        left = _state["runs"][run]["impact_at"] - _clock()
        if left <= 0:
            return True
        await asyncio.sleep(min(left, 0.05))


def _down_current(why: str, at: float) -> str | None:
    r = _current()
    if r is not None and r["down"] is None and at < r["ends_at"]:
        r.update(down=at, why=why)
        turnlog.record("debut_down", run=r["run"], why=why, before_hit=at < r["impact_at"])
        logger.info("debut %s: taken down (%s)%s", r["run"], why, " before its hit -- no walk" if at < r["impact_at"] else "")
    return r["run"] if r else None


def _on_screen_stop(why: str) -> None:
    """face_mirror: the screens were told what the page takes the debut down on (the red stop, the call
    over, show mode over, a slide coming up) -- so `impact_at()` never stands for a hit the room will not
    see. Never an `interrupted` (the counting voice in the hall may be heard as one) nor a screen's socket
    blinking (the page plays on through it)."""
    _down_current(why, _clock())


face_mirror.on_stop(_on_screen_stop)


def reset() -> None:
    """Tests."""
    _state.update(seq=0, runs={}, current=None)
