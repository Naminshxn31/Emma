"""
The page where a person records their voice: /voice-lock (owner 19 Nov 2026: "ทำเป็นที่ให้พูดเพื่อ
บันทึกเสียงหน่อย เพราะจะให้ไมค์มันจับแค่เสียงคนนั้น ทำระบบแล้วเช็คด้วย").

The whole PC test without a terminal: pick the mic, record the voice (a print built here with the
pinned CAM++ pipeline of app/one_voice.py -- the same code as scripts/voice_lock_try.py and the
door, not a copy), listen live (each utterance: this voice or not, its score and length), let
somebody else talk into the same mic, and get the verdict (`report`) -- then forget.

The socket is /ws/voice-lock behind the same guard as every other socket (`_reject_unauthorized`:
WS_TOKEN, or a tokenless page on the same loopback origin). The page has no controls of anything
else. Audio arrives as PCM16 16 kHz frames from the page's worklet; nothing here sends audio or a
vector anywhere: the page gets lengths, levels, scores and decisions. Recordings and prints live
in data/voice-lock/ (gitignored). The model work runs in a thread, never on the event loop.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from datetime import datetime

import numpy as np
# At module level, not inside register(): with postponed annotations FastAPI resolves the
# endpoints' "WebSocket" / "FileResponse" against this module's globals (a local import
# left the socket unresolvable -- every connection closed at once).
from fastapi import WebSocket
from fastapi.responses import FileResponse

from app import one_voice as ov

logger = logging.getLogger("condo_voice.one_voice_page")

PAGE = ov.ROOT / "client" / "voice-lock.html"
#: What the page records for a print (the reading text is ~35 s at a normal pace).
ENROLL_S = 40.0
#: The most a socket may hand over for one enrolment, whatever the page says.
ENROLL_MAX_S = 90.0
#: Below this peak a refused enrolment is "too quiet / wrong mic", not "read more". The GCK worn reads
#: 0.43-0.80 speaking (20 Nov); the wrong input (LCS_USB_AUDIO) read ~3e-5 dither.
TOO_QUIET_PEAK = 0.02
#: The labels the page's test buttons save under (the eval folders), and "unsorted": the mixed
#: recording (everyone, sorted by ear afterwards -- app/one_voice.unsorted_dir, outside the eval folders).
LISTEN_LABELS = ("target", "other", "nearby")
UNSORTED = "unsorted"
#: A frame bigger than this is not the page's 20 ms worklet output: ignored.
MAX_FRAME_BYTES = 64000
#: Audio may not arrive faster than time passes: more than FAST_RATIO x the wall clock (+ slack) is a
#: page sending the same mic twice, not a recording. 20 Nov: every mic switch left the old worklet
#: posting -- 151-251 frames/s for 50/s of time, interleaved, and the owner's reading came back as
#: "0 s of speech, peak 1.0" (read more, closer) when the page was at fault. Say what it is.
FAST_RATIO = 1.25
FAST_SLACK_S = 1.0
# Checked from the first frame, before the frame reaches the segmenter: the clock starts when the
# command arrives and the page only sends after our answer, so a real-time page is always behind it
# (review 20 Nov: a 3 s grace here let a doubled stream be judged and saved under a label first).


def too_fast(audio_s: float, wall_s: float) -> bool:
    """More audio than time: the page is sending a stream twice (or replaying)."""
    return audio_s > wall_s * FAST_RATIO + FAST_SLACK_S

#: Test seams: the model and the VAD (tests swap fakes in).
_embedder = ov.shared_embedder
_segmenter = ov.Segmenter


def register(app, reject, no_cache: dict) -> None:
    """The page and its socket on the server (app/main.py)."""

    @app.get("/voice-lock")
    async def serve_voice_lock() -> FileResponse:
        """No token on the page itself: it shows nothing until its socket is let in."""
        return FileResponse(PAGE, headers=no_cache)

    @app.websocket("/ws/voice-lock")
    async def voice_lock_socket(websocket: WebSocket, token: str | None = None) -> None:
        if await reject(websocket, token):
            return
        await handle(websocket)


def _json_default(x):
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, np.bool_):
        return bool(x)
    raise TypeError(type(x).__name__)


async def _send(ws, obj: dict) -> None:
    await ws.send_text(json.dumps(obj, ensure_ascii=False, default=_json_default))


def _vad_ready() -> bool:
    return (ov.ROOT / "data" / "wake" / "silero_vad.onnx").is_file()


def state() -> dict:
    from app.config import settings

    ok, why = ov.model_ready()
    # Whether a listed profile's call could carry the door at all (review 20 Nov): with
    # VAD_MODE=gemini (the default) it cannot, and the page says so instead of looking armed.
    attach, why_not = ov.gate_attachable()
    return {"type": "state", "prints": ov.list_prints(), "saved": ov.saved_counts(),
            "unsorted": len(ov.unsorted_list()),
            "model": ok, "model_why": why, "vad": _vad_ready(),
            "threshold": ov.DEFAULT_THRESHOLD, "enroll_s": ENROLL_S, "short_s": ov.SHORT_S,
            "gate": {"profiles": sorted(getattr(settings, "one_voice_profiles", ()) or ()),
                     "name": getattr(settings, "one_voice_name", ""),
                     "threshold": getattr(settings, "one_voice_threshold", ov.DEFAULT_THRESHOLD),
                     "attachable": attach, "why": why_not}}


def enroll(pcm16: bytes, name: str, mic: str, wall_s: float | None = None) -> dict:
    """A print from one recording (PCM16 16 kHz) that took `wall_s` to arrive. Never returns the vector."""
    name = ov.safe_name(name)
    raw = ov.pcm_to_float(pcm16)
    peak = float(np.abs(raw).max()) if len(raw) else 0.0
    if peak == 0.0:
        return {"ok": False, "why": "no_sound", "seconds": round(len(raw) / ov.RATE, 1)}
    audio_s = len(raw) / ov.RATE
    if wall_s is not None and too_fast(audio_s, wall_s):
        logger.warning("voice-lock page: enrolment refused, %.1f s of audio arrived in %.1f s "
                       "(the page is sending a stream twice), mic %r", audio_s, wall_s, (mic or "?")[:80])
        return {"ok": False, "why": "too_fast", "seconds": round(audio_s, 1), "wall_s": round(wall_s, 1),
                "mic": (mic or "")[:80]}
    seg = _segmenter()
    parts = seg.feed(raw) + seg.flush()
    chunks = [ov.trim(s) for _, s in parts]
    speech = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    speech_s = len(speech) / ov.RATE
    clip = float((np.abs(raw) >= ov.CLIP_AT).mean()) if len(raw) else 0.0
    base = {"name": name, "speech_s": round(speech_s, 1), "seconds": round(len(raw) / ov.RATE, 1),
            "clip": round(clip, 4), "peak": round(peak, 4), "mic": (mic or "")[:80]}
    if speech_s < ov.ENROLL_MIN_SPEECH_S:
        # 20 Nov: the page recorded the wrong input (LCS_USB_AUDIO, dither ~3e-5) and said "read more,
        # closer" -- a near-silent recording is a mic problem, not a reading problem. Numbers only, no audio.
        logger.info("voice-lock page: enrolment refused, %.1f s of speech, peak %.4f, mic %r",
                    speech_s, peak, (mic or "?")[:80])
        why = "too_quiet" if peak < TOO_QUIET_PEAK else "too_short"
        return {"ok": False, "why": why, "need_s": ov.ENROLL_MIN_SPEECH_S, **base}
    embed = _embedder()
    built = ov.build_print([embed(w) for w in ov.speech_windows(speech)])
    if not built["ok"]:
        return {**{k: v for k, v in built.items() if k != "vector"}, **base}
    level = ov.level_stats(speech)["rms_db"]
    meta = {"speech_s": round(speech_s, 1), "level_db": level, "clip": round(clip, 4),
            "source": f"page:{(mic or '?')[:80]}", "consent": "self, ticked on the page",
            "created": datetime.now().isoformat(timespec="seconds")}
    ov.save_print(name, built, meta)
    logger.info("voice-lock page: a print was stored (%.1f s of speech, %d/%d windows)",
                speech_s, built["kept"], built["windows"])
    return {"ok": True, "kept": built["kept"], "windows": built["windows"], "worst": built["worst"],
            "self_mean": built["self_mean"], "halves": built["halves"], "level_db": level, **base}


def evaluate(name: str, threshold: float) -> dict:
    voice, meta = ov.load_print(name)
    if not ov.UTTERANCES.is_dir():
        return {"type": "eval", "verdict": "UNKNOWN", "lines": ["ยังไม่มีเสียงที่บันทึกไว้ทดสอบ"], "numbers": {}}
    rows = ov.eval_rows(ov.UTTERANCES, _embedder(), voice, threshold, name)
    lines, numbers = ov.report(rows, threshold)
    counts: dict = {}
    for r in rows:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    return {"type": "eval", "verdict": numbers.get("verdict"), "numbers": numbers, "lines": lines,
            "counts": counts, "name": name}


class _Conn:
    """One page's socket: idle, enrolling, or listening."""

    def __init__(self) -> None:
        self.mode = "idle"
        self.buf = bytearray()
        self.name = ""
        self.mic = ""
        self.seg = None
        self.voice = None
        self.threshold = ov.DEFAULT_THRESHOLD
        self.label: str | None = None
        self.session = ""
        self.n = 0
        self.tally = {"ACCEPT": 0, "REJECT": 0, "NONE": 0}
        self.started = 0.0            # monotonic, when this enrolment / listening run began
        self.got = 0                  # bytes of audio since then

    async def audio(self, ws, data: bytes) -> None:
        if len(data) > MAX_FRAME_BYTES or len(data) % 2:
            return
        if self.mode == "enroll":
            if len(self.buf) < ENROLL_MAX_S * 2 * ov.RATE:
                self.buf.extend(data)
        elif self.mode == "listen":
            self.got += len(data)
            wall = time.monotonic() - self.started
            if too_fast(self.got / 2 / ov.RATE, wall):
                logger.warning("voice-lock page: listening stopped, %.1f s of audio in %.1f s",
                               self.got / 2 / ov.RATE, wall)
                self.mode, self.seg = "idle", None
                await _send(ws, {"type": "error", "code": "too_fast", "message":
                                 "หน้าเว็บส่งเสียงเร็วกว่าเวลาจริง (ส่งไมค์ซ้ำ) — ผลฟังใช้ไม่ได้ กด F5 แล้วลองใหม่"})
                return
            # Silero and the model run off the event loop (review 19 Nov): this server's loop is
            # also a live call's audio path.
            try:
                for start_s, x in await asyncio.to_thread(self.seg.feed, ov.pcm_to_float(data)):
                    await self._judge(ws, start_s, x)
            except (ov.PrintError, OSError) as exc:
                self.mode = "idle"
                await _send(ws, {"type": "error", "message": f"ฟังต่อไม่ได้: {exc}"})

    async def _judge(self, ws, start_s: float, x) -> None:
        self.n += 1
        voice, threshold = self.voice, self.threshold
        row = await asyncio.to_thread(lambda: ov.judge_utterance(x, _embedder(), voice, threshold))
        saved = None
        if self.label == UNSORTED:
            saved = await asyncio.to_thread(ov.save_unsorted, x, self.session, self.n, start_s, row["speech_s"])
        elif self.label:
            saved = await asyncio.to_thread(ov.save_utterance, self.label, x, self.session, self.n,
                                            start_s, row["speech_s"])
        self.tally[row["decision"]] += 1
        await _send(ws, {"type": "utterance", "n": self.n, "label": self.label,
                         "speech_s": round(row["speech_s"], 2), "rms_db": row["rms_db"], "peak": row["peak"],
                         "clip": row["clip"], "score": row["score"], "decision": row["decision"],
                         "short": row["short"], "saved": bool(saved), "tally": dict(self.tally)})

    async def command(self, ws, cmd: dict) -> None:
        kind = cmd.get("type")
        try:
            if kind == "hello":
                await _send(ws, await asyncio.to_thread(state))
            elif kind == "enroll_start":
                if not cmd.get("consent"):
                    raise ov.PrintError("ต้องติ๊กว่าเจ้าของเสียงยินยอมก่อนบันทึก")
                ok, why = await asyncio.to_thread(ov.model_ready)
                if not ok:
                    raise ov.PrintError("ไม่มีโมเดลจำเสียงในเครื่อง (scripts/fetch_voiceprint_model.py)")
                await self._stop_listening(ws, quiet=True)
                self.name = ov.safe_name(cmd.get("name") or "")
                self.mic = str(cmd.get("mic") or "")
                self.buf = bytearray()
                self.started = time.monotonic()
                self.mode = "enroll"
                await _send(ws, {"type": "enrolling", "max_s": ENROLL_MAX_S, "name": self.name})
            elif kind == "enroll_stop":
                if self.mode != "enroll":
                    raise ov.PrintError("ไม่ได้กำลังบันทึกอยู่")
                self.mode = "idle"
                pcm, self.buf = bytes(self.buf), bytearray()
                out = await asyncio.to_thread(enroll, pcm, self.name, self.mic, time.monotonic() - self.started)
                await _send(ws, {"type": "enrolled", **out})
                await _send(ws, await asyncio.to_thread(state))
            elif kind == "enroll_cancel":
                self.mode, self.buf = "idle", bytearray()
                await _send(ws, {"type": "cancelled"})
            elif kind == "listen_start":
                await self._stop_listening(ws, quiet=True)
                name = ov.safe_name(cmd.get("name") or "")
                label = cmd.get("label") or None
                if label is not None and label not in LISTEN_LABELS + (UNSORTED,):
                    raise ov.PrintError(f"ป้ายต้องเป็น {', '.join(LISTEN_LABELS + (UNSORTED,))}")
                self.voice, _ = await asyncio.to_thread(ov.load_print, name)
                self.threshold = min(0.95, max(0.05, float(cmd.get("threshold") or ov.DEFAULT_THRESHOLD)))
                self.seg = await asyncio.to_thread(_segmenter)
                self.label = label
                self.session = datetime.now().strftime("%Y%m%d-%H%M%S-%f")   # two runs in one second
                self.n = 0
                self.tally = {"ACCEPT": 0, "REJECT": 0, "NONE": 0}
                self.started, self.got = time.monotonic(), 0
                self.mode = "listen"
                await _send(ws, {"type": "listening", "label": label, "threshold": self.threshold, "name": name})
            elif kind == "listen_stop":
                await self._stop_listening(ws)
            elif kind == "unsorted_list":
                await _send(ws, {"type": "unsorted", "items": await asyncio.to_thread(ov.unsorted_list)})
            elif kind == "clip":
                # Played back on this page for a person to say whose voice it is. The same socket and
                # guard as the recording itself; 16 kHz PCM16, at most one utterance (<= 30 s).
                x = await asyncio.to_thread(ov.unsorted_audio, cmd.get("file"))
                pcm = (np.clip(x, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                await _send(ws, {"type": "clip", "file": cmd.get("file"), "rate": ov.RATE,
                                 "pcm": base64.b64encode(pcm).decode("ascii")})
            elif kind == "sort":
                out = await asyncio.to_thread(ov.sort_unsorted, cmd.get("file"), cmd.get("label"))
                await _send(ws, {"type": "sorted", **out})
                await _send(ws, await asyncio.to_thread(state))
            elif kind == "eval":
                name = ov.safe_name(cmd.get("name") or "")
                threshold = float(cmd.get("threshold") or ov.DEFAULT_THRESHOLD)
                await _send(ws, await asyncio.to_thread(evaluate, name, threshold))
            elif kind == "forget":
                await self._stop_listening(ws, quiet=True)
                out = await asyncio.to_thread(ov.forget, ov.safe_name(cmd.get("name") or ""),
                                              audio=bool(cmd.get("audio")))
                await _send(ws, {"type": "forgotten", **out})
                await _send(ws, await asyncio.to_thread(state))
            else:
                raise ov.PrintError(f"ไม่รู้จักคำสั่ง {kind!r}")
        except ov.PrintError as exc:
            await _send(ws, {"type": "error", "message": str(exc)})
        except (TypeError, ValueError, AttributeError) as exc:
            await _send(ws, {"type": "error", "message": f"คำสั่งไม่ถูกต้อง ({type(exc).__name__})"})
        except OSError as exc:
            await _send(ws, {"type": "error", "message": f"เขียน/อ่านไฟล์ไม่ได้ ({type(exc).__name__})"})

    async def _stop_listening(self, ws, quiet: bool = False) -> None:
        if self.mode != "listen":
            if not quiet:
                await _send(ws, {"type": "stopped", "tally": dict(self.tally)})
            return
        self.mode = "idle"
        for start_s, x in await asyncio.to_thread(self.seg.flush):
            await self._judge(ws, start_s, x)
        self.seg = None
        if not quiet:
            await _send(ws, {"type": "stopped", "tally": dict(self.tally)})
            await _send(ws, await asyncio.to_thread(state))


async def handle(ws) -> None:
    from starlette.websockets import WebSocketDisconnect

    await ws.accept()
    conn = _Conn()
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data is not None:
                await conn.audio(ws, data)
                continue
            text = msg.get("text")
            if not text:
                continue
            try:
                cmd = json.loads(text)
            except ValueError:
                await _send(ws, {"type": "error", "message": "ข้อความไม่ใช่ JSON"})
                continue
            if isinstance(cmd, dict):
                await conn.command(ws, cmd)
    except WebSocketDisconnect:
        pass
    except RuntimeError:              # sending on a socket the page already closed
        pass
