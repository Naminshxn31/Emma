#!/usr/bin/env python3
"""Record Emma's ceremony lines in her LIVE voice, each checked word for word.

    python scripts/render_event_script.py --dry-run              # what would render + cost, nothing sent
    python scripts/render_event_script.py                        # approved cues that have no clip
    python scripts/render_event_script.py --drafts --takes 3     # + clean drafts, as a PREVIEW
    python scripts/render_event_script.py --ids s3_units --fresh # that cue again

How a take is made: one fresh Gemini Live session per take on GEMINI_MODEL with
GEMINI_VOICE -- the model and voice the robot speaks with, never a TTS model (two
"Despina"s already sounded like two women here: app/event_voice.py) -- a short
system instruction (say what you are given, exactly, in your usual voice), no
tools, and the cue's own read turn (`event_script.read_text`, the same turn the
cue player sends live when a cue has no clip) as one user text turn (realtime
text by default -- see SEND_MODES). The audio up to turn_complete is the take;
the session's own output transcription is scored against the line
(`event_voice.line_fidelity`: speech_match.fidelity with a number said in Thai
words counted as said). The best take at or over --min-fidelity that says no
number the line lacks (`number_check` extra) and leaves out none of its numbers
(`event_voice.unspoken_numbers` -- a dropped "155" costs ~2% fidelity, under any
bar) is trimmed of silence at both ends and written as a 24 kHz mono PCM16 WAV
with its index entry (beside-and-rename, after every cue: an interrupted run
keeps what it paid for). A take that scores 1.0 ends the cue's takes early --
nothing can beat it on the measure that picks. A take with no transcript is
unscored and never kept.

What the transcript can and cannot tell (measured 6 Nov): on 3.8 it reads like
the model's own TEXT -- "1,066", "Studio", "2 Bedroom" come back exactly as
written, so every take with a transcript scored 1.000 and fidelity catches a
word added or dropped in what she generated, NOT a word she mispronounced. The
local Thai recognizer on the same clips (app/offline/asr.py, a one-off check)
heard every line said whole, with loanwords as expected ("1 Bedroom" spoken
"วันเบดรูม"). Listening to each clip is still the owner's check.

Why a direct client and not GeminiProvider: the provider is a conversation. Its
receive path decides what passes (the noise and bare-"ค่ะ" gates, the
transcript budget, Thai re-spacing), a reply watchdog asks again on its own, its
config declares the robot's tools and VAD, and a stall can move the call to the
fallback model. A recording must be the model's own audio and words for exactly
one turn. What it shares with the provider is what decides how she sounds: the
model, the prebuilt voice, AUDIO only, output transcription, the v1beta rule for
native-audio models and the thinking field per model family (copied from
GeminiProvider._build_config; 3.8 Live refuses any thinking_config). The system
instruction is NOT the robot's: a stage read wants no sales rules -- if the
owner hears a different manner from her live replies, that is where to look.

Drafts: `--drafts` also renders cues nobody has signed, so the owner can hear
them before approving. A preview only: the cue player checks the approval at
playback (event_script.servable(), drafts never), so a draft's clip is listed by
status() and never played. The clip is keyed on the line's words, so approving
the same words keeps it; an edit = no clip until this runs again.

The bar (6 Nov 2026 preview, 22:50, gemini-3.8-live / Despina, Thai, 11 cues):
in the first pass (19 sessions) every take that had a transcript scored 1.000
and 9 had no transcript at all; s3_joke's re-run kept a 0.952 take, which was
"155" spelled in Thai words (1.000 under line_fidelity). So
DEFAULT_MIN_FIDELITY 0.92 (app/event_voice.py)
only leaves room for spacing and punctuation; FIDELITY_FLOOR 0.90 is the lowest
this script accepts and the robot plays.

Quota: the key is the sales room's. Sessions run one after another with a
pause; a quota/429 answer stops the run (what was accepted is kept). The key is
never printed, and is scrubbed from any error text before it is.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import event_script, event_voice, speech_match  # noqa: E402
from app.config import settings  # noqa: E402

#: Thai speaking rate the face measured (emma-story.js voiceRate 12.5 chars/s;
#: 11.5-13.6 on the robot, 14 Oct) -- English 15. For the estimate only.
CHARS_PER_S = {"th": 12.5, "en": 15.0}
#: Live audio out, paid tier: $0.018 a minute -- the gemini-3.1-flash-live-
#: preview price list (CLAUDE.md, "โมเดลกับ free tier"); the 3.8 Live price was
#: not checked here. The read turn's text in is a few hundred tokens a session
#: and left out. An ESTIMATE, printed as one.
AUDIO_OUT_USD_PER_S = 0.018 / 60
#: What the session is told. Not a sentence she could say back as a reply: it
#: describes the job, and the line comes quoted in the read turn.
SYSTEM = ("คุณคือเอ็มม่า หุ่นยนต์ผู้หญิงที่เป็นเจ้าบ้านของโครงการ กำลังพูดบนเวทีงานเปิดตัวโครงการ "
          "ข้อความที่ได้รับคือบทที่ต้องพูดออกเสียง ให้พูดตามตัวอักษรทุกคำ ด้วยน้ำเสียงปกติของคุณ "
          "อบอุ่น เป็นธรรมชาติ ไม่เร่ง ไม่ลากเสียง ไม่เติมคำ ไม่ตัดคำ ไม่พูดอย่างอื่นก่อนหรือหลังบท")
_QUOTA = ("429", "resource_exhausted", "quota", "rate limit")


class QuotaHit(Exception):
    """The key is out of quota or rate-limited: stop the run, keep what is done."""


def _safe(text: object) -> str:
    """Error text with the API key taken out (a websocket URL carries ?key=)."""
    out = str(text)
    if settings.gemini_api_key:
        out = out.replace(settings.gemini_api_key, "***")
    return re.sub(r"(key=)[^&\s'\"]+", r"\1***", out)


# ---- what to render ---------------------------------------------------------------

def selected(drafts: bool, ids: list[str] | None, lang: str) -> tuple[list[dict], list[tuple[str, str]]]:
    """(cues to consider, [(id, why skipped)]) in script order."""
    usable = event_script.servable(drafts=drafts)
    picked, skipped = [], []
    for row in event_script.status():
        cid = row["id"]
        if ids and cid not in ids:
            continue
        c = usable.get(cid)
        if c is None:
            why = ("problems: " + ", ".join(row["problems"])) if row["problems"] else (
                "draft (--drafts renders a preview)" if not row["approved"] else "not servable")
            skipped.append((cid, why))
        elif not c.get(lang):
            skipped.append((cid, "no %s line (%s)" % (lang, "live only" if c.get("live_th") else "empty")))
        else:
            picked.append(c)
    if ids:
        unknown = [i for i in ids if not any(r["id"] == i for r in event_script.status())]
        skipped.extend((i, "no such cue") for i in unknown)
    return picked, skipped


def _read_index(base: Path) -> dict | None:
    try:
        raw = json.loads((base / event_voice.INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) and isinstance(raw.get("clips"), dict) else None


def plan(cues: list[dict], lang: str, base: Path | None, *, fresh: bool = False) -> dict:
    model, voice = settings.gemini_model, settings.gemini_voice
    index = _read_index(base) if base is not None else None
    same = bool(index) and index.get("model") == model and index.get("voice") == voice
    todo, have = [], []
    for c in cues:
        key = event_voice.clip_key(model, voice, lang, event_script.norm(c[lang]))
        entry = index["clips"].get(key) if same else None
        if entry and not fresh and base is not None and (base / str(entry.get("file") or "")).is_file():
            have.append(c)
        else:
            todo.append(c)
    chars = sum(len(c[lang]) for c in todo)
    seconds = chars / CHARS_PER_S[lang]
    return {"todo": todo, "have": have, "chars": chars, "seconds": seconds,
            "index_model": (index or {}).get("model"), "index_voice": (index or {}).get("voice")}


# ---- one take -----------------------------------------------------------------------

def live_config(model: str, voice: str):
    """The Live session's config: GeminiProvider._build_config's voice-deciding
    parts and nothing of the conversation (no tools, no VAD, no resumption)."""
    from google.genai import types

    config: dict = {
        "response_modalities": [types.Modality.AUDIO],
        "system_instruction": SYSTEM,
        "speech_config": types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
        "output_audio_transcription": types.AudioTranscriptionConfig(),
    }
    name = model.removeprefix("models/")
    if name == "gemini-3.8-live":
        pass                                   # refuses any thinking_config (1007)
    elif name == "gemini-3.8-live-extended-thinking":
        level = (settings.gemini_thinking_level or "low").strip().lower()
        config["thinking_config"] = types.ThinkingConfig(thinking_level="low" if level == "minimal" else level)
    elif name.startswith("gemini-3"):
        config["thinking_config"] = types.ThinkingConfig(thinking_level=settings.gemini_thinking_level)
    else:
        config["thinking_config"] = types.ThinkingConfig(thinking_budget=settings.gemini_thinking_budget)
    return config


def _rate(mime: str | None) -> int | None:
    m = re.search(r"rate=(\d+)", mime or "")
    return int(m.group(1)) if m else None


#: How the read turn goes in. Measured 6 Nov on gemini-3.8-live, Thai cues: the
#: output transcription was ABSENT for 5 of 9 turns sent with send_client_content
#: (what the robot's provider.send_text uses on 3.8) and for 1 of 7 probes sent
#: as realtime text -- but then for 9 of 19 render sessions sent as realtime text
#: too, the same line one time without and the next with. Same model and voice,
#: the audio came either way. Not clearly better; realtime stays the default
#: because it measured no worse.
SEND_MODES = ("realtime", "client")


async def live_take(read_text: str, *, model: str, voice: str, timeout: float, send: str = "realtime") -> dict:
    """One fresh session, one turn: {"pcm", "transcript", "rate", "complete"}."""
    from google import genai
    from google.genai import types

    http_options = {"api_version": "v1beta"} if "native-audio" in model else None
    client = genai.Client(api_key=settings.gemini_api_key, http_options=http_options)
    got = {"pcm": bytearray(), "said": [], "rate": None, "complete": False}

    async def run() -> None:
        async with client.aio.live.connect(model=model, config=live_config(model, voice)) as session:
            if send == "realtime" or model.removeprefix("models/").startswith("gemini-3.1-flash-live"):
                await session.send_realtime_input(text=read_text)
            else:
                await session.send_client_content(
                    turns=types.Content(role="user", parts=[types.Part(text=read_text)]), turn_complete=True)
            async for message in session.receive():
                content = getattr(message, "server_content", None)
                if content is None:
                    continue
                said = getattr(getattr(content, "output_transcription", None), "text", None)
                if said:
                    got["said"].append(said)
                turn = getattr(content, "model_turn", None)
                for part in getattr(turn, "parts", None) or []:
                    blob = getattr(part, "inline_data", None)
                    if blob is not None and blob.data:
                        got["rate"] = got["rate"] or _rate(getattr(blob, "mime_type", None))
                        got["pcm"].extend(blob.data)
                if getattr(content, "turn_complete", False):
                    got["complete"] = True
                    return

    try:
        await asyncio.wait_for(run(), timeout)
    except asyncio.TimeoutError:
        pass                                   # complete stays False: scored as an unfinished take
    except Exception as exc:
        if any(t in str(exc).lower() for t in _QUOTA):
            raise QuotaHit(_safe(exc)) from None
        raise
    return {"pcm": bytes(got["pcm"]), "transcript": "".join(got["said"]),
            "rate": got["rate"], "complete": got["complete"]}


def score(take: dict, line: str, bar: float) -> dict:
    """The take, judged: trimmed pcm, fidelity, numbers, and whether it may be kept."""
    pcm = event_voice.trim_silence(take.get("pcm") or b"") if take.get("rate") == event_voice.RATE else b""
    transcript = " ".join((take.get("transcript") or "").split())
    fid = event_voice.line_fidelity(transcript, line)        # a number said in Thai words is said
    numbers = speech_match.number_check(transcript, line)
    why = None
    if not take.get("complete"):
        why = "no turn_complete"
    elif take.get("rate") != event_voice.RATE:
        why = "rate %s" % take.get("rate")
    elif not pcm:
        why = "no voice"
    elif not transcript:
        why = "no transcript"                 # 3.8 sometimes sends none: unscored, not a bad read
    elif numbers["extra"]:
        why = "extra number %s" % numbers["extra"]
    elif event_voice.unspoken_numbers(transcript, line):
        # a dropped number costs ~2% fidelity -- under any bar -- so it is its own check
        why = "number not said %s" % event_voice.unspoken_numbers(transcript, line)
    elif fid < bar:
        why = "fidelity %.3f < %.2f" % (fid, bar)
    return {"pcm": pcm, "transcript": transcript, "fidelity": fid, "numbers": numbers,
            "unspoken": event_voice.unspoken_numbers(transcript, line) if transcript else numbers["missing"],
            "seconds": event_voice.seconds_of(pcm), "ok": why is None, "why": why}


# ---- the run ------------------------------------------------------------------------

def _write_index(base: Path, index: dict) -> None:
    tmp = base / (event_voice.INDEX + ".part")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, base / event_voice.INDEX)


def _prune(base: Path, index: dict) -> None:
    """WAVs in the folder that no entry names: an older take of an edited line."""
    keep = {str(e.get("file")) for e in index["clips"].values() if isinstance(e, dict)}
    for p in base.glob("*.wav"):
        if p.name not in keep:
            try:
                p.unlink()
            except OSError:
                pass


async def render(todo: list[dict], lang: str, base: Path, *, takes: int, bar: float, take_fn,
                 pause: float = 3.0, fresh: bool = False, out=print) -> list[dict]:
    """Every cue in `todo`: up to `takes` sessions, the best acceptable take kept.
    `take_fn(read_text) -> {"pcm", "transcript", "rate", "complete"}` (awaitable)."""
    model, voice = settings.gemini_model, settings.gemini_voice
    index = _read_index(base)
    if index and (index.get("model") != model or index.get("voice") != voice):
        if not fresh:
            raise SystemExit("the clips in %s are model %r voice %r, not %s / %s -- one voice for everything: "
                             "start over with --fresh" % (base, index.get("model"), index.get("voice"), model, voice))
        index = None
    if not index:
        index = {"schema": event_voice.SCHEMA, "model": model, "voice": voice, "rate": event_voice.RATE, "clips": {}}
    base.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    sessions = 0
    for n, c in enumerate(todo, 1):
        line = event_script.norm(c[lang])
        read = event_script.read_text(c, lang)
        scored: list[dict] = []
        errors: list[str] = []
        for _ in range(max(1, takes)):
            if sessions:
                await asyncio.sleep(pause)
            sessions += 1
            try:
                take = await take_fn(read)
            except QuotaHit as exc:
                out("QUOTA/RATE LIMIT on %s after %d session(s) -- stopped; accepted clips are kept: %s"
                    % (c["id"], sessions, exc))
                rows.append({"id": c["id"], "takes": len(scored), "errors": errors + ["quota"], "accepted": False,
                             "best": max(scored, key=lambda s: s["fidelity"], default=None), "stopped": True})
                return rows
            except Exception as exc:                # one failed session is one failed take
                errors.append("%s: %s" % (type(exc).__name__, _safe(exc)[:160]))
                continue
            s = score(take, line, bar)
            scored.append(s)
            if s["ok"] and s["fidelity"] >= 1.0:
                break                               # nothing can beat it on the measure that picks
        good = [s for s in scored if s["ok"]]
        best = max(good or scored, key=lambda s: s["fidelity"], default=None)
        row = {"id": c["id"], "takes": len(scored), "errors": errors, "best": best, "accepted": bool(good),
               "chars": len(line)}
        if good:
            key = event_voice.clip_key(model, voice, lang, line)
            name = key + ".wav"
            event_voice.write_wav(base / name, best["pcm"])
            # the same cue's clip of its old words goes: one clip per cue and language
            for old in [k for k, e in index["clips"].items()
                        if isinstance(e, dict) and e.get("cue") == c["id"] and e.get("lang") == lang and k != key]:
                index["clips"].pop(old)
            index["clips"][key] = {
                "file": name, "sha256": hashlib.sha256((base / name).read_bytes()).hexdigest(), "lang": lang,
                "cue": c["id"], "fidelity": round(best["fidelity"], 4), "min_fidelity": bar,
                "transcript": best["transcript"], "numbers": best["numbers"],
                "seconds": round(best["seconds"], 2), "takes": len(scored),
                "rendered_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "approved_at_render": event_script.approved(c)}
            _write_index(base, index)
            _prune(base, index)
        rows.append(row)
        out("[%d/%d] %s: %d take(s), best %s -> %s" % (
            n, len(todo), c["id"], len(scored), "%.3f" % best["fidelity"] if best else "-",
            "kept" if good else "NOT kept (%s)" % (best["why"] if best else "; ".join(errors) or "no take")))
    event_voice.reset()
    return rows


def table(rows: list[dict], out=print) -> None:
    """One row per cue. chars/s is the only measure here that does not come from
    the model's own transcript: an added or repeated sentence makes the audio
    long for its line (Thai on the robot 11.5-13.6, 14 Oct)."""
    out("")
    out("%-16s %5s %8s %7s %7s %-22s %s" % ("cue", "takes", "fidelity", "seconds", "chars/s", "numbers", "accepted"))
    for r in rows:
        b = r["best"]
        nums = "-"
        if b:
            nums = "ok" if not b["numbers"]["missing"] and not b["numbers"]["extra"] else (
                "%s in Thai words" % b["numbers"]["missing"]
                if not b["numbers"]["extra"] and not b.get("unspoken") else
                "missing %s extra %s" % (b.get("unspoken") or b["numbers"]["missing"], b["numbers"]["extra"]))
        cps = "%.1f" % (r["chars"] / b["seconds"]) if b and b["seconds"] and r.get("chars") else "-"
        out("%-16s %5s %8s %7s %7s %-22s %s" % (
            r["id"], r["takes"], "%.3f" % b["fidelity"] if b else "-", "%.1f" % b["seconds"] if b else "-",
            cps, nums, "yes" if r["accepted"] else "NO" + (" (%s)" % b["why"] if b and b["why"] else "")))
        if b and not r["accepted"]:
            out("    heard: %s" % b["transcript"][:300])
        for e in r["errors"]:
            out("    error: %s" % e)


def main(argv: list[str] | None = None, take_fn=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="what would render and its cost; nothing sent")
    ap.add_argument("--drafts", action="store_true", help="also clean drafts, as a preview (never played)")
    ap.add_argument("--ids", nargs="+", metavar="CUE", help="only these cue ids")
    ap.add_argument("--lang", choices=list(event_script.LANGS), default="th")
    ap.add_argument("--takes", type=int, default=3, help="sessions per cue, best kept (default 3)")
    ap.add_argument("--min-fidelity", type=float, default=event_voice.DEFAULT_MIN_FIDELITY,
                    help="bar a take must reach (default %.2f; never under %.2f)"
                         % (event_voice.DEFAULT_MIN_FIDELITY, event_voice.FIDELITY_FLOOR))
    ap.add_argument("--fresh", action="store_true",
                    help="render the selected cues again even with a clip; replaces an index of another model/voice")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds per take (connect + reply)")
    ap.add_argument("--pause", type=float, default=3.0, help="seconds between sessions (shared key)")
    ap.add_argument("--send", choices=SEND_MODES, default="realtime",
                    help="how the read turn goes in (realtime: fewer turns without a transcript, measured)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if args.min_fidelity < event_voice.FIDELITY_FLOOR or args.min_fidelity > 1.0:
        ap.error("--min-fidelity must be between %.2f and 1.0 -- the robot never plays a take under %.2f"
                 % (event_voice.FIDELITY_FLOOR, event_voice.FIDELITY_FLOOR))
    if args.takes < 1:
        ap.error("--takes must be at least 1")
    base = event_voice.folder()
    # "--ids a,b" as approve_event_script.py takes them, or "--ids a b" (7 Nov: the
    # comma form came back "no such cue" and rendered nothing)
    ids = [i.strip() for arg in args.ids or [] for i in arg.split(",") if i.strip()] or None
    cues, skipped = selected(args.drafts, ids, args.lang)
    p = plan(cues, args.lang, base, fresh=args.fresh)
    model, voice = settings.gemini_model, settings.gemini_voice
    print("ceremony script: %s | clips in %s" % (event_script.path(), base))
    print("model %s, voice %s, lang %s | %d cue(s) selected, %d already recorded, %d to render"
          % (model, voice, args.lang, len(cues), len(p["have"]), len(p["todo"])))
    for c in p["todo"]:
        print("  render %-16s %s  %3d chars ~%4.1f s" % (
            c["id"], "approved" if event_script.approved(c) else "DRAFT (preview, never played)",
            len(c[args.lang]), len(c[args.lang]) / CHARS_PER_S[args.lang]))
    for c in p["have"]:
        print("  have   %-16s" % c["id"])
    for cid, why in skipped:
        print("  skip   %-16s %s" % (cid, why))
    sessions = len(p["todo"]) * args.takes
    print("~%.0f s of speech per take; up to %d session(s) -> up to ~%.0f s of audio out, ESTIMATED <= $%.3f "
          "(paid Live audio out $0.018/min, the 3.1 Flash Live list price; 3.8 not checked)"
          % (p["seconds"], sessions, p["seconds"] * args.takes, p["seconds"] * args.takes * AUDIO_OUT_USD_PER_S))
    if p["index_model"] and (p["index_model"] != model or p["index_voice"] != voice):
        print("NOTE: %s holds clips of model %r voice %r -- rendering needs --fresh (they are dropped)"
              % (base, p["index_model"], p["index_voice"]))
    if args.dry_run or not p["todo"]:
        return 0
    if base is None:
        raise SystemExit("CEREMONY_FILE is empty -- no ceremony, no folder for its clips")
    if take_fn is None:
        if not settings.gemini_api_key:
            raise SystemExit("GEMINI_API_KEY is not set -- nothing rendered")

        async def take_fn(read_text: str) -> dict:
            return await live_take(read_text, model=model, voice=voice, timeout=args.timeout, send=args.send)

    rows = asyncio.run(render(p["todo"], args.lang, base, takes=args.takes, bar=args.min_fidelity,
                              take_fn=take_fn, pause=args.pause, fresh=args.fresh))
    table(rows)
    kept = sum(1 for r in rows if r["accepted"])
    print("\nkept %d of %d into %s%s" % (kept, len(rows), base,
                                         " -- STOPPED on quota" if any(r.get("stopped") for r in rows) else ""))
    return 0 if kept == len(p["todo"]) else 1


if __name__ == "__main__":
    sys.exit(main())
