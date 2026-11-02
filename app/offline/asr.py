"""Hearing on the machine: what the guest said, without Gemini.

Two sherpa-onnx models (the runtime the wake word and the VAD already use, so
nothing new is installed on the robot), chosen by measurement (models
research, 26 Oct -- FLEURS real Thai speech + synthetic sets, this PC):

* **Thai: sherpa-onnx-zipformer-thai-2024-06-20, int8.** CER 10.2% on real
  Thai speech; the next best was 16.3% (the 8-language streaming model) and
  Whisper base/small read Thai at 55-65%. PC RTF 0.032, about 0.1 estimated
  on one robot core: a 6.6 s (p90) Thai question decodes in ~0.7 s.
  modified_beam_search + the project's words as hotwords kept keywords 50 ->
  53 of 80 synthetic clips with FLEURS CER unchanged.
* **English and language ID: sherpa-onnx-whisper-base, int8.** English WER
  0.5% clean / 3.7% noisy; language ID with the binary rule "en or not"
  right on 170 of 170 clips. Never for Thai.

The language is the conversation's (`app/talk_lang.py`) when it is known --
then no language-ID pass at all -- else Whisper's language ID, en / not-en.

Input is exactly the segment the local VAD closed (`VadGate.take_closed`):
16 kHz PCM16, pre-roll through the hangover, the bytes Gemini would have got.

Models live under OFFLINE_ASR_DIR (gitignored); scripts/fetch_offline_models.py
downloads the pinned archives and keeps only the files below, and every file
is re-hashed before it is loaded (app/embed_onnx.py's rule: a different file
in the same folder is a different model with no error). Missing or changed
files = this machine cannot understand speech offline: one log line, no
crash, and the offline provider still gives its notices.

Stdlib-only at import: robot_server.py and the fetch script read the pins.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("condo_voice.offline.asr")

ROOT = Path(__file__).resolve().parents[2]
_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"

#: The two archives and the files kept from each, pinned (sha256 measured on
#: the archives downloaded 26 Oct 2026; sizes match the release's own listing).
#:
#: Licences (review 26 Oct -- the Thai VITS voice was turned down over a
#: non-commercial licence, so the recognizers get the same look):
#: * whisper-base: MIT (OpenAI's weights), exported by sherpa-onnx.
#: * zipformer-thai: the weights are Apache-2.0 (model card of
#:   yfyeung/icefall-asr-gigaspeech2-th-zipformer-2024-06-20), but they are
#:   trained on GigaSpeech 2, whose Terms of Access allow "non-commercial
#:   research and educational purposes" only; the dataset card says a model
#:   trained on it "may be eligible for commercial license" and leaves the
#:   check to the user. NOT verified for a sales room: the owner / company
#:   decides before OFFLINE_FALLBACK goes on (docs/emma-offline-mode.md). The
#:   measured fallback is the 8-language streaming zipformer (Thai CER 16.3%),
#:   whose licence needs the same check.
MODELS: dict[str, dict] = {
    "thai": {
        "dir": "sherpa-onnx-zipformer-thai-2024-06-20",
        "url": _RELEASE + "sherpa-onnx-zipformer-thai-2024-06-20.tar.bz2",
        "archive_sha256": "48819951fc31dde9339d44b7a91c60be8e74f928f9959066a0a0827f400049da",
        "archive_size": 695605000,
        "files": {
            "encoder-epoch-12-avg-5.int8.onnx": "9ceb14519103b854bafb41b952ac0aaedc037825c9d5aa991a8849f93d8cfc7d",
            "decoder-epoch-12-avg-5.int8.onnx": "08c81d3be3d55c5b54094267785ca90ceb397d76c79c68250c63f136a088a187",
            "joiner-epoch-12-avg-5.int8.onnx": "6ee983924b39ed7d5a57a57963daeaaeafcfc2095f0eae5bb0446fbba34842cf",
            "tokens.txt": "dd5fc99e7251a7dff28d459d812607c6dd4a3acfd248bdd8d869a5c251fc44f5",
            # for the hotwords' vocabulary (bpe.vocab is written from it)
            "bpe.model": "86fdc938bde6610a0e03920aac1942072b1544a89dcfcde3f192664fd2b147db",
        },
    },
    "whisper": {
        "dir": "sherpa-onnx-whisper-base",
        "url": _RELEASE + "sherpa-onnx-whisper-base.tar.bz2",
        "archive_sha256": "911b2083efd7c0dca2ac3b358b75222660dc09fb716d64fbfc417ba6c99ff3de",
        "archive_size": 207557382,
        "files": {
            "base-encoder.int8.onnx": "0b8fb1304b6109976038efff5ace81720e00386f3ff6b54ee8c75291ca0a1e11",
            "base-decoder.int8.onnx": "9759d217388a01b3a4c7c15533201067b48ae819c4daafc8624e64b9409dc02d",
            "base-tokens.txt": "b34b360dbb493e781e479794586d661700670d65564001f23024971d1f2fa126",
        },
    },
}

#: Project words for the Thai decoder's hotwords (models research: these 20
#: kept keywords 50 -> 53 of 80 synthetic clips, FLEURS CER unchanged). Words
#: a guest says, not facts: they bias what is HEARD, never what is said.
HOTWORDS_TH = ("ฟิตเนส", "ซาวน่า", "สระว่ายน้ำ", "จอมเทียน", "อู่ตะเภา", "ที่จอดรถ", "ส่วนกลาง", "ห้องสมุด",
               "ห้องนอน", "ตารางเมตร", "วิวทะเล", "โรงพยาบาล", "สะดวกซื้อ", "ผังห้อง", "ต่างชาติ",
               "ราคาเริ่มต้น", "ผ่อนดาวน์", "เอ็มบาสซี่", "เวิลด์", "สนามบิน")
HOTWORDS_SCORE = 1.5
RATE = 16000
#: Shorter than this is a click or a breath, not words: not decoded.
MIN_SECONDS = 0.25


def base_dir() -> Path:
    from app.config import settings

    base = Path(settings.offline_asr_dir).expanduser()
    return base if base.is_absolute() else ROOT / base


def model_dir(name: str, base: Path | None = None) -> Path:
    return (base or base_dir()) / MODELS[name]["dir"]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def problems(name: str, base: Path | None = None) -> list[str]:
    """The model's files that are missing or not the pinned bytes ([] = ok)."""
    folder = model_dir(name, base)
    out = []
    for file, pin in MODELS[name]["files"].items():
        path = folder / file
        if not path.is_file():
            out.append("missing " + file)
        elif _sha256(path) != pin:
            out.append("changed " + file)
    return out


def fetch_map() -> dict[str, dict]:
    """The archive pins, for scripts/robot_server.py (the robot downloads and
    unpacks them itself -- 0.9 GB over adb Wi-Fi would take most of an hour)."""
    return {name: {"dir": m["dir"], "url": m["url"], "sha256": m["archive_sha256"],
                   "size": m["archive_size"], "files": dict(m["files"])} for name, m in MODELS.items()}


@dataclass
class Transcript:
    text: str
    #: "th" / "en": which decoder heard it
    lang: str
    decode_ms: int
    #: time spent on language ID (0 when the conversation's language was known)
    lid_ms: int = 0


def _hotword_files(folder: Path) -> tuple[str, str] | None:
    """(hotwords file, bpe vocab) for the Thai decoder, written once beside
    the model (the server owns data/ on the robot), else in a temp folder.
    None = no sentencepiece here: decode without hotwords, say so once."""
    try:
        import sentencepiece as spm
    except ImportError:
        logger.warning("offline ASR: sentencepiece missing -- Thai decodes without project hotwords")
        return None
    for where in (folder / "_runtime", None):
        try:
            if where is None:
                import tempfile

                where = Path(tempfile.mkdtemp(prefix="emma-asr-"))
            where.mkdir(parents=True, exist_ok=True)
            vocab = where / "bpe.vocab"
            if not vocab.is_file():
                sp = spm.SentencePieceProcessor(model_file=str(folder / "bpe.model"))
                with vocab.open("w", encoding="utf-8") as f:
                    for i in range(sp.get_piece_size()):
                        f.write("%s\t%s\n" % (sp.id_to_piece(i), sp.get_score(i)))
            words = where / "hotwords.txt"
            words.write_text("\n".join(HOTWORDS_TH) + "\n", encoding="utf-8")
            return str(words), str(vocab)
        except OSError:
            continue
    return None


def _build(base: Path, threads: int, usable: tuple[str, ...]) -> dict:
    """The recognizers of the `usable` models (files present and pinned).
    Blocking and heavy (~0.4 GB + ~0.5 GB); run in a thread. Tests replace it."""
    import sherpa_onnx as so

    out: dict = {}
    if "thai" in usable:
        folder = model_dir("thai", base)
        extra = {}
        hot = _hotword_files(folder)
        if hot is not None:
            extra = dict(hotwords_file=hot[0], hotwords_score=HOTWORDS_SCORE,
                         modeling_unit="bpe", bpe_vocab=hot[1])
        out["th"] = so.OfflineRecognizer.from_transducer(
            encoder=str(folder / "encoder-epoch-12-avg-5.int8.onnx"),
            decoder=str(folder / "decoder-epoch-12-avg-5.int8.onnx"),
            joiner=str(folder / "joiner-epoch-12-avg-5.int8.onnx"),
            tokens=str(folder / "tokens.txt"), num_threads=threads,
            decoding_method="modified_beam_search", **extra)
    if "whisper" in usable:
        folder = model_dir("whisper", base)
        enc, dec = str(folder / "base-encoder.int8.onnx"), str(folder / "base-decoder.int8.onnx")
        out["en"] = so.OfflineRecognizer.from_whisper(
            encoder=enc, decoder=dec, tokens=str(folder / "base-tokens.txt"), language="en",
            task="transcribe", num_threads=threads, tail_paddings=-1)
        out["lid"] = so.SpokenLanguageIdentification(so.SpokenLanguageIdentificationConfig(
            whisper=so.SpokenLanguageIdentificationWhisperConfig(encoder=enc, decoder=dec),
            num_threads=threads))
    return out


class Engine:
    """Loads once (in a thread), then decodes one segment at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._models: dict | None = None
        self._loading = False
        self.load_s: float | None = None

    @property
    def ready(self) -> bool:
        return bool(self._models)

    def languages(self) -> set[str]:
        return {k for k in (self._models or {}) if k in ("th", "en")}

    def load(self) -> bool:
        """Blocking. True = something can be decoded. Logged once either way."""
        with self._lock:
            if self._models is not None:
                return bool(self._models)
            from app.config import settings

            base = base_dir()
            started = time.monotonic()
            usable = []
            for name in MODELS:
                bad = problems(name, base)      # every file re-hashed, once per process
                if bad:
                    logger.warning("offline ASR: %s model not usable (%s) -- fetch it once with: "
                                   "python scripts/fetch_offline_models.py", name, "; ".join(bad[:3]))
                else:
                    usable.append(name)
            try:
                models = _build(base, max(1, int(settings.offline_asr_threads)), tuple(usable))
            except Exception:
                logger.exception("offline ASR could not load -- offline mode cannot understand speech")
                models = {}
            self._models = models or {}
            self.load_s = round(time.monotonic() - started, 1)
            if self._models:
                logger.info("offline ASR ready (%s) in %.1f s", ", ".join(sorted(self.languages())),
                            self.load_s)
            else:
                logger.warning("offline ASR: no model loaded -- offline mode gives notices but "
                               "cannot understand speech")
            return bool(self._models)

    def transcribe(self, pcm16: bytes, lang: str | None = None, rate: int = RATE) -> Transcript | None:
        """The words in one segment. `lang` = the conversation's language when
        known. None = nothing could decode it (no model, a decoder error)."""
        if not self.load():
            return None
        import numpy as np

        samples = np.frombuffer(pcm16 or b"", dtype="<i2").astype(np.float32) / 32768.0
        if len(samples) < MIN_SECONDS * rate:
            return Transcript("", lang if lang in ("th", "en") else "th", 0)
        models = self._models or {}
        lid_ms = 0
        if lang == "th":
            use = "th"
        elif lang:
            use = "en"                 # any other known language: the English decoder
        elif "lid" in models and "th" in models:
            t0 = time.perf_counter()
            try:
                stream = models["lid"].create_stream()
                stream.accept_waveform(rate, samples)
                found = models["lid"].compute(stream)
            except Exception:
                logger.debug("offline language ID failed", exc_info=True)
                found = ""
            lid_ms = round((time.perf_counter() - t0) * 1000)
            # the measured rule: "en" or not -- other labels (my/vi/zh/km on
            # Thai clips) were never English
            use = "en" if found == "en" else "th"
        else:
            use = "th" if "th" in models else "en"
        if use not in models:
            use = "th" if "th" in models else "en"
        if use not in models:
            return None
        t0 = time.perf_counter()
        try:
            stream = models[use].create_stream()
            stream.accept_waveform(rate, samples)
            models[use].decode_stream(stream)
            text = (stream.result.text or "").strip()
        except Exception:
            logger.exception("offline ASR decode failed")
            return None
        return Transcript(text, use, round((time.perf_counter() - t0) * 1000), lid_ms)


_engine: Engine | None = None
_engine_lock = threading.Lock()


def engine() -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = Engine()
        return _engine


def reset() -> None:
    """Tests: drop the loaded models (module state)."""
    global _engine
    with _engine_lock:
        _engine = None


def preload() -> None:
    """Load in a background thread (the server's startup, switch on): the
    first offline guest must not wait ~2-5 s for 0.9 GB of models."""
    threading.Thread(target=lambda: engine().load(), name="offline-asr-load", daemon=True).start()
