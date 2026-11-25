"""Run the whole Emma server ON THE ROBOT, not on a PC (owner, 9 Oct 2026:
"ลองทำให้ทุกอย่างทำจากบนหุ่นเลย ... API key อยู่บนหุ่นได้ แต่ต้องมีการป้องกัน").

Until now the face app loaded https://127.0.0.1:8001 through `adb reverse` to
this PC: the PC off, asleep, or its adb losing the robot = a face that cannot
talk. Now the same server runs inside the robot at that same address (the
LAN cert already lists 127.0.0.1), so nothing on the robot's side changes.

    python scripts/robot_server.py lock      # PC: pin the robot's wheels (data/robot-server/wheels.lock.json)
    python scripts/robot_server.py install   # robot downloads + verifies its runtime (~300 MB, ~1 min)
    python scripts/robot_server.py deploy    # code + data, only the files that changed (--dry-run; --force
                                             #   also overwrites files the robot's server changed itself)
    python scripts/robot_server.py secrets   # .env (from this PC's, + data/robot-server/robot.env) + TLS key;
                                             #   a running server reads them on its next restart
    python scripts/robot_server.py start | stop | restart | status | logs
    python scripts/robot_server.py forward   # this PC's https://127.0.0.1:18001 -> the robot's server (desk pages)

Layout on the robot -- protection for the keys is the point of most of it:

    /data/local/emma-server          root:nobody 0750  (no other app/uid can even list it)
      runtime/                       root-owned, read-only to the server
      code/                          root-owned code the server cannot rewrite
      code/data/                     nobody-owned: the only place the server writes
      code/.env, code/certs/*key*    root:nobody 0640 -- readable by the server, written only by root
      run/                           root 0700 (pid file)

The server runs as uid `nobody` (9999) + group `inet` (3003): no root, and this
build's su refuses every uid but root and shell, so a compromised server
cannot climb. It binds 127.0.0.1 only -- off the LAN entirely; the desk pages
are reached through `forward` (adb, i.e. only from a PC the robot already
trusts). Secrets go over `adb exec-in` straight into their file, never through
/data/local/tmp. What this cannot stop: anyone who has adb + su on this robot
can read everything -- protect adb (USB debugging / adb-over-Wi-Fi) like the key.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(1, str(ROOT))
import robot_python as rp  # noqa: E402  (adb plumbing, Debian pins, the nav runtime)
from app import embed_onnx  # noqa: E402  (the embedding model's pins; stdlib only at import)
from app.offline import asr as offline_asr  # noqa: E402  (the offline ASR pins; stdlib only at import)

BASE = "/data/local/emma-server"
RUNTIME = BASE + "/runtime"
CODE = BASE + "/code"
RUN = BASE + "/run"
STAGE = "/data/local/tmp/emma-server-stage"      # shell-owned, holds NO secrets
SETUP_DIR = "/data/local/tmp/emma-server-setup"
NAV_PY = rp.REMOTE + "/py"                       # the only Python before install
UID = "9999"                                     # nobody
SU_SERVER = "9999,9999,3003"                     # nobody, nobody, inet
SU = rp.SU
PORT = 8001
FORWARD_PORT = 18001

CONF = ROOT / "data" / "robot-server"
LOCK = CONF / "wheels.lock.json"
REQS = CONF / "requirements.txt"
OVERRIDES = CONF / "robot.env"
WHEEL_CACHE = ROOT / ".tmp" / "robot-server" / "wheels"

PYTHON = {
    "file": "cpython-3.14.8+20261003-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz",
    "url": "https://github.com/astral-sh/python-build-standalone/releases/download/20261003/"
           "cpython-3.14.8%2B20261003-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz",
    "sha256": "ece2bf3d4b47c80c2403c5f32fa51a9458abaee93c10def8acafb5f45163e77d",
    "version": "3.14",
}
#: opencv-python (with GUI) is what insightface's metadata asks for; the robot
#: gets opencv-python-headless instead -- same `cv2`, no libGL to go missing.
#: Nothing resolves dependencies at runtime (wheels are only unpacked).
SKIP_WHEELS = re.compile(r"^opencv_python-\d")

#: What a deploy copies besides git-tracked app/ client/ (no client/rive: no
#: robot page loads it) -- the server's entry points and the data it reads.
#: scripts/board_walk.py + data/robot/waypoints/: the event host's walks
#: (app/event_walk.py, owner 30 Oct) run floor 1's walker from the code
#: directory -- the rest of data/robot/ (floor 2's lanes, logs) stays home.
CODE_FILES = ["run_server.py", "scripts/emma_server.py", "scripts/emma_server.sh", "scripts/robot_dns.py",
              "scripts/board_walk.py"]
TRACKED_DIRS = ["app", "client", "data"]
TRACKED_SKIP = re.compile(r"^(client/rive/|data/robot/(?!waypoints/)|data/eval|data/review/|data/robot-inspection/|"
                          r"data/canva-render/|data/slides-backup/|data/showroom/|data/call_debug/|"
                          r"data/wake_debug/|data/wake_enroll/|data/logs/|data/emma-corpus/)")
#: Untracked data the server needs (gitignored on purpose: staff faces,
#: customer-facing documents, the unit plans, models).
EXTRA_GLOBS = [
    "data/personal-docs/**/*",
    "data/faces/gallery.npz", "data/faces/people.json", "data/faces/voice/**/*",
    "data/faces/models/auraface/scrfd_10g_bnkps.onnx", "data/faces/models/auraface/glintr100.onnx",
    "data/faces/models/auraface/LICENSE.md",
    "data/projects/*/sales/unit-plans/**/*",
    # The report FAQ's embeddings (app/project_report.py), built on the PC.
    # Never shipped before 15 Oct: every FAQ change (deny_terms) left the robot
    # to embed 299 questions itself when the app opened -- and from 17:53 the
    # robot hard-reset ~25 s into every app start, mid-embedding (bootloader
    # reason "(none)", no Android shutdown), so the cache was never saved and
    # the next start tried again. Same space on both sides (local MiniLM; the
    # robot's ONNX measured equal), so the PC's file loads as is.
    "data/projects/*/sales/faq_embeddings.npz",
    # slide text/index only: no robot tool shows slide images (slides is not
    # in the robot's TOOL_GROUPS), and the 183 JPEGs are 78 MB over adb
    "data/projects/*/presentations/**/*.json", "data/projects/*/presentations/**/*.npz",
    "data/wake/silero_vad.onnx",
    # The wake word's model, for the in-call ear (FOCUS_WAKE, app/call_wake.py):
    # only the int8 files it loads (~5.4 MB once), not the fp32 set beside them.
    "data/wake/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01/*.int8.onnx",
    "data/wake/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01/tokens.txt",
    "data/wake/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01/bpe.model",
    "data/slides/**/*",
    "certs/lan-cert.pem",
    # Floor 1's remembered points (gitignored with the rest of data/robot/):
    # the dock pose and approach the event host's walks dock with.
    "data/robot/waypoints/*.json",
    # The Emma Ceremony app's script and running order (CEREMONY_FILE, owner 9 Nov: the
    # ceremony is an app of its own): tracked once the lead commits it, shipped by name until
    # then -- a robot without it is a ceremony app with nothing to press. Only the top level:
    # its voice/ folder ships by its index (`event_voice_files`), never by a glob.
    "data/ceremony/*.json",
]
#: Big files the robot downloads itself (pinned) instead of 0.3 MB/s over adb.
#: Shipped only if this PC has the file (its sha256 is the pin), so fetch here
#: first. Staged on the robot by BASENAME (STAGE/models/<name>) -- names must
#: stay unique across this map (tests check).
FETCH_ON_ROBOT = {
    "data/faces/models/auraface/glintr100.onnx":
        "https://huggingface.co/fal/AuraFace-v1/resolve/main/glintr100.onnx",
    "data/faces/models/auraface/scrfd_10g_bnkps.onnx":
        "https://huggingface.co/fal/AuraFace-v1/resolve/main/scrfd_10g_bnkps.onnx",
    # Semantic slide search without torch (app/embed_onnx.py): the ONNX export
    # of the PC's MiniLM at the same commit -- 470 MB + 9 MB, ~80 s on the
    # robot. scripts/fetch_embed_model.py on this PC first.
    **embed_onnx.fetch_map(),
    # Listening focus (docs/listening-focus-2026-10-09.md). Active speaker
    # (app/active_speaker.py): 106-point landmarks, 5 MB -- Landmarker checks
    # the sha, the point indices belong to this exact model.
    "data/faces/models/auraface/2d106det.onnx":
        "https://huggingface.co/fal/AuraFace-v1/resolve/main/2d106det.onnx",
    # Is that a person (app/person_vision.py): YOLOX-nano, 3.7 MB, Apache-2.0, pinned there too.
    # scripts/fetch_person_model.py on this PC first.
    "data/models/person/yolox_nano.onnx":
        "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx",
    # Voice prints (app/voiceprint.py): 3D-Speaker CAM++ zh+en, 28 MB, Apache-2.0.
    # scripts/fetch_voiceprint_model.py on this PC first. ("recongition" is how
    # the release tag is really spelled.)
    "data/models/voiceprint/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx":
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
        "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
}
#: .env keys that never leave this PC (they do nothing on the robot, or
#: they are keys the robot has no use for -- fewer secrets on the robot).
#: ELEVENLABS_API_KEY (10 Nov): scripts/voice_line.py renders a scene's recorded line on this PC; the
#: robot only plays the file -- a key it never uses is a key it can only leak.
#: BOOTH_* and the booth's side of GUEST_SYNC (23 Nov): the scan booth may be this
#: PC; the robot needs only GUEST_SYNC_KEY. BOOTH_PIN is a staff secret the robot
#: never asks for.
ENV_DROP = {"OPENAI_API_KEY", "PRINTER_NAME", "PDF_PRINT_EXE", "ROBOT_ARM_ADB", "ROBOT_ARM_ADB_SERIAL",
            "ELEVENLABS_API_KEY", "BOOTH_PIN", "BOOTH_CONSENT_UNTIL", "BOOTH_CONTROLLER", "BOOTH_CONTACT",
            "BOOTH_STATION", "BOOTH_RETURN_URL", "GUEST_SYNC_URL", "GUEST_SYNC_CERT"}
#: .env keys whose robot value is robot.env's alone: this PC's line is never
#: carried over. OFFLINE_FALLBACK (review 26 Oct, reproduced): the owner
#: trying the backup on this PC (docs/emma-offline-mode.md step 3) and then
#: running `secrets`/`deploy` for anything at all turned it on for the robot
#: -- 0.9 GB of recognizers fetched, beside a lines file of drafts -- while
#: the lock test, which reads robot.env, stayed green. Now robot.env is the
#: only switch for the robot and the lock reads the file that decides.
ROBOT_ENV_ONLY = {"OFFLINE_FALLBACK"}
SECRET_FILES = {"certs/lan-key.pem": "certs/lan-key.pem"}
#: Pushed by `secrets` when this PC has them. data/room-screens.json holds each
#: room PC's agent key (app/room_screens.py; robot.env points ROOM_SCREENS_FILE
#: at the copy beside the robot's .env, root-owned like it -- under data/ the
#: server could rewrite it).
OPTIONAL_SECRET_FILES = {"data/room-screens.json": "room-screens.json"}


class Fail(Exception):
    pass


# -- pure (tested) ---------------------------------------------------------------

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_env(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out.append((k.strip(), v))
    return out


def compose_env(pc_env: str, overrides: str) -> str:
    """The robot's .env: this PC's, minus ENV_DROP, with robot.env on top
    (robot.env holds no secrets; it is tracked). Key order is the PC's.

    A key written twice keeps its FIRST position and its LAST value: that is
    what python-dotenv (app/config.py load_dotenv) reads on the PC. Keeping the
    first value instead meant a .env with a corrected line further down ran
    one setting on the PC and the old one on the robot."""
    over = dict(parse_env(overrides))
    pc: dict[str, str] = {}
    for k, v in parse_env(pc_env):
        if k not in ENV_DROP and k not in ROBOT_ENV_ONLY:
            pc[k] = v                     # dict: first position, last value
    out = ["%s=%s" % (k, over.pop(k) if k in over else v) for k, v in pc.items()]
    for k, v in over.items():
        out.append("%s=%s" % (k, v))
    return "\n".join(out) + "\n"


#: Never shipped as code/data. Secrets reach the robot only through `secrets`
#: (root:nobody 0640, hash-checked); a deployed copy would be 0644 under code/.
_SECRET_NAME = re.compile(r"(^|/)(\.env[^/]*|[^/]*\.env|[^/]*key[^/]*\.pem|[^/]*\.key|room-screens\.json)$",
                          re.IGNORECASE)
_JUNK_SUFFIX = (".pyc", ".lock", ".bak", ".part")
#: Slide pictures (78 MB): no robot tool shows them (`slides` is not in the
#: robot's TOOL_GROUPS; Canva needs a desktop Chromium). Today only
#: .gitignore keeps them out of `--others`; this keeps them out when one gets
#: tracked or the ignore line moves.
_SLIDE_IMAGE = re.compile(r"^data/(projects/[^/]+/presentations/slides|slides)/.*\.jpe?g$", re.IGNORECASE)


def _deployable(rel: str) -> bool:
    return (not TRACKED_SKIP.match(rel) and "__pycache__/" not in rel
            and not rel.endswith(_JUNK_SUFFIX) and not _SECRET_NAME.search(rel)
            and not _SLIDE_IMAGE.match(rel))


def tracked_files(root: Path = ROOT) -> list[str]:
    # --others --exclude-standard: new files nobody has committed yet are part of
    # the server too (the first start died on an uncommitted app/log_redaction.py).
    r = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--"] + TRACKED_DIRS, cwd=root,
                       capture_output=True, check=True)
    names = [n for n in r.stdout.decode("utf-8").split("\0") if n]
    # _deployable here too, not only on EXTRA_GLOBS: --others also brings an
    # untracked editor backup (app/session.py.bak -- *.bak is not gitignored
    # outside presentations/) or a faces gallery.lock along.
    return [n for n in names if _deployable(n) and (root / n).is_file()]


def answer_image_files(root: Path = ROOT) -> set[str]:
    """The slide pictures the robot needs once it shows answer pictures.

    Since 2026-10-11 the robot puts the one approved picture that matches an
    answer on its screen (ANSWER_IMAGES in robot.env; owner: "ทุกเรื่องที่มี
    ภาพตรง"), so `_SLIDE_IMAGE`'s "no robot tool shows them" stops being
    true for exactly the pictures the asset manifest pins — 48 files, 33 MB.
    The other JPEGs in the folder (the old deck, variants) still stay home,
    and with the switch off nothing changes. A pinned file this PC does not
    have is left out; the server then refuses that picture at its hash check
    instead of showing a wrong one.
    """
    env = root / "data" / "robot-server" / "robot.env"
    if not env.is_file() or not any(
            re.fullmatch(r"\s*ANSWER_IMAGES\s*=\s*(1|true|yes|on)\s*", line, re.IGNORECASE)
            for line in env.read_text(encoding="utf-8").splitlines()):
        return set()
    manifest = json.loads((root / "data" / "registry" / "external_asset_manifest.json").read_text(encoding="utf-8"))
    source = manifest["sources"]["slide_assets"]
    folder = f"data/projects/{source['project_id']}/presentations/slides"
    return {f"{folder}/{item['path']}" for item in source.get("files", [])
            if "/" not in item["path"] and (root / folder / item["path"]).is_file()}


def _catwalk_files(root) -> set[str]:
    """The catwalk music the room's screen plays (app/catwalk_music.py), when it is switched on."""
    try:
        from app import catwalk_music

        return {str(p.relative_to(root)).replace("\\", "/") for p in catwalk_music.files(root).values()}
    except (ImportError, AttributeError, ValueError):
        return set()


def event_clip_files(root: Path = ROOT) -> set[str]:
    """The event's films (app/event_clips.py) and its scenes' recorded lines (app/event_host.py
    `recorded`, 10 Nov): what its file names, from its one folder. Gitignored and big, so nothing
    else ships them, and no event file names one on any other day. Same shape as
    answer_image_files."""
    try:
        doc = json.loads((root / "data" / "events" / "floor1_event.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    out: set[str] = set()
    named = [clip.get("file") for clip in (doc.get("clips") or {}).values() if isinstance(clip, dict)]
    named += [scene["recorded"].get("file") for scene in (doc.get("scenes") or [])
              if isinstance(scene, dict) and isinstance(scene.get("recorded"), dict)]
    # ... and a tour's recorded lines (app/event_tour.py `lines`, 24 Nov): never shipped, she is silent at that handshake
    named += [rec.get("file") for scene in (doc.get("scenes") or []) if isinstance(scene, dict)
              and isinstance(scene.get("tour"), dict) and isinstance(scene["tour"].get("lines"), dict)
              for rec in scene["tour"]["lines"].values() if isinstance(rec, dict)]
    # ... and a spot's `show` lines (app/event_host.py go_spot, 25 Nov: card 3's globe) -- rehearsal 4 found them
    # missing on the robot: the tease said nothing and the walk went on without it
    named += [spot["show"][k].get("file") for spot in (doc.get("spots") or {}).values() if isinstance(spot, dict)
              and isinstance(spot.get("show"), dict) for k in ("first", "arrive") if isinstance(spot["show"].get(k), dict)]
    for name in named:
        if isinstance(name, str) and name.startswith("data/events/clips/") and ".." not in name and (root / name).is_file():
            out.add(name)
    return out | _catwalk_files(root)


#: Beside the ceremony's own file since 9 Nov (app/event_voice.py `folder`; it was
#: data/events/voice/ while the ceremony was a scene of the event file).
EVENT_VOICE_DIR = "data/ceremony/voice"


def event_voice_files(root: Path = ROOT) -> set[str]:
    """Emma's recorded ceremony lines (app/event_voice.py, scripts/render_event_
    script.py): the index and the WAVs it names -- a bare file name inside the
    folder, nothing else (no path, no "..", .wav only), and only those this PC
    has. Gitignored, so nothing else ships them. The robot re-hashes each clip
    and checks its model, voice and transcript before playing it, so a stray or
    stale one ships harmlessly; one the index names and this PC lacks is simply
    not there, and that cue is read live."""
    folder = root / EVENT_VOICE_DIR
    try:
        index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    clips = index.get("clips") if isinstance(index, dict) else None
    if not isinstance(clips, dict):
        return set()
    out = {EVENT_VOICE_DIR + "/index.json"}
    for entry in clips.values():
        name = entry.get("file") if isinstance(entry, dict) else None
        if (isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]+\.wav", name)
                and (folder / name).is_file()):
            out.add("%s/%s" % (EVENT_VOICE_DIR, name))
    return out


def event_voice_summary(files: list[str], push: list[str]) -> str | None:
    """deploy's line about the recorded ceremony lines (shown on --dry-run too)."""
    clips = [f for f in files if f.startswith(EVENT_VOICE_DIR + "/") and f.endswith(".wav")]
    if not clips:
        return None
    pushing = [f for f in push if f.startswith(EVENT_VOICE_DIR + "/")]
    return "ceremony voice: %d recorded line(s) in %s, %d file(s) to push" % (len(clips), EVENT_VOICE_DIR, len(pushing))


def _robot_env_on(root: Path, key: str) -> bool:
    env = root / "data" / "robot-server" / "robot.env"
    return env.is_file() and any(
        re.fullmatch(r"\s*%s\s*=\s*(1|true|yes|on)\s*" % re.escape(key), line, re.IGNORECASE)
        for line in env.read_text(encoding="utf-8").splitlines())


def story_asset_files(root: Path = ROOT) -> set[str]:
    """The robot face's story pictures (app/stories.py), once it tells stories.

    Generated and gitignored (scripts/build_story_assets.py), so nothing
    else ships them. Only while STORIES_ENABLED is on in robot.env -- with it
    off nothing changes -- and only the pictures the catalog pins and this PC
    has: a pinned file missing here is left out, and the robot then refuses
    that story at its hash check instead of showing half of it. Same shape as
    answer_image_files above.
    """
    if not _robot_env_on(root, "STORIES_ENABLED"):
        return set()
    out: set[str] = set()
    for catalog_path in sorted((root / "data" / "projects").glob("*/stories/stories.json")):
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        folder = catalog_path.parent / "assets"
        for entry in (catalog.get("assets") or {}).values():
            name = entry.get("file") if isinstance(entry, dict) else None
            if isinstance(name, str) and "/" not in name and "\\" not in name and (folder / name).is_file():
                out.add((folder / name).relative_to(root).as_posix())
    return out


#: The ceremony film's opening clip (app/ceremony_slides.py IDLE_CLIP).
CEREMONY_IDLE_CLIP = "c_logo_reveal"
#: The debut on card 1 (app/ceremony_reveal.py): its settings name its sound and its video
#: (app/ceremony_slides.py reveal_sound_id / reveal_video_id; these ids when they do not).
CEREMONY_REVEAL = ("data", "ceremony", "reveal.json")
CEREMONY_REVEAL_IDS = {"sound": "reveal_debut", "video": "debut_wave"}


def _reveal_ids(root: Path) -> set[str]:
    try:
        doc = json.loads(root.joinpath(*CEREMONY_REVEAL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    doc = doc if isinstance(doc, dict) else {}
    return {doc.get(k) if isinstance(doc.get(k), str) else v for k, v in CEREMONY_REVEAL_IDS.items()}


def _asset_ids(value, out: set[str]) -> set[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("asset", "backdrop") and isinstance(item, str):
                out.add(item)
            else:
                _asset_ids(item, out)
    elif isinstance(value, list):
        for item in value:
            _asset_ids(item, out)
    return out


def ceremony_film_files(root: Path = ROOT) -> set[str]:
    """The ceremony film's own files (data/ceremony/assets/, gitignored: 80 MB of full-size
    pictures, depth maps, candidates and a clip) -- ONLY those a slide names, with their depth
    map and licence, and the idle clip: 17 Nov night the folder held 265 files, the slides
    use a fraction. Every one is pinned in index.json (tracked); the server re-hashes before
    it serves one, so a file missing here only costs its picture its sharper copy."""
    folder = root / "data" / "ceremony" / "assets"
    try:
        rows = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    rows = rows.get("assets") if isinstance(rows, dict) else rows
    used: set[str] = {CEREMONY_IDLE_CLIP} | _reveal_ids(root)
    for path in [root / "data" / "ceremony" / "floor1_ceremony.slides.json"] + sorted((root / "data" / "ceremony" / "slides").glob("*.json")):
        try:
            _asset_ids(json.loads(path.read_text(encoding="utf-8")), used)
        except (OSError, ValueError):
            continue
    out: set[str] = set()

    def take(name) -> None:
        if isinstance(name, str) and name and ".." not in name.split("/") and not name.startswith("/") and ":" not in name:
            if (folder / name).is_file():
                out.add((folder / name).relative_to(root).as_posix())

    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not (row.get("id") in used or row.get("from_pin") in used):
            continue
        take(row.get("file"))
        depth = row.get("depth")
        if isinstance(depth, dict):
            take((depth.get("soft") or {}).get("file") if isinstance(depth.get("soft"), dict) else None)
            take(depth.get("file"))
        else:
            take(depth)
        licence = row.get("licence")
        for name in (licence.get("files") or []) if isinstance(licence, dict) else []:
            take(name)
    return out


#: The ceremony film's face (owner 22 Nov: "เปลี่ยนทั้งหมดเลยนะ" -- every word of the film and the
#: event screen's Thai subtitles in Jipatype's Kantalad; licensed by the owner for the event's
#: presentation only, so the four files are gitignored, never in the repository). `--others
#: --exclude-standard` leaves a gitignored file out without a word, and ceremony-show.css's
#: @font-face would then point at nothing on the robot: shipped by name here, and a deploy
#: from a PC without them stops and says so (`ceremony_font_missing`).
CEREMONY_FONT_DIR = "client/assets/ceremony-show/fonts"
CEREMONY_FONT_FILES = ("Kantalad-Regular.otf", "Kantalad-Medium.otf", "Kantalad-SemiBold.otf", "Kantalad-Bold.otf")


def _font_problem(path: Path) -> str | None:
    """Why `path` is not a face the film can use, or None. Review 22 Nov night: a deploy checked
    only that the four names existed -- an empty file, a half-copied one or a .woff2 renamed
    .otf would have shipped, and Chrome drops a face it cannot read without a word (the screen
    then draws in Inter). The four are CFF OpenType: their first four bytes are "OTTO"."""
    size = path.stat().st_size
    if size == 0:
        return "%s is empty" % path.name
    with path.open("rb") as fh:
        head = fh.read(4)
    if head != b"OTTO":
        return "%s is not a CFF OpenType font (it starts %r, not b'OTTO')" % (path.name, head)
    return None


def ceremony_font_problems(root: Path = ROOT) -> list[str]:
    """The Kantalad faces this PC has that are not fonts (`_font_problem`), one line each."""
    out = []
    for name in CEREMONY_FONT_FILES:
        path = root / CEREMONY_FONT_DIR / name
        if path.is_file():
            why = _font_problem(path)
            if why:
                out.append(why)
    return out


def ceremony_font_files(root: Path = ROOT) -> set[str]:
    """The Kantalad faces this PC has (only those names, only in that folder) -- and only real
    ones: a face that is empty or not OpenType is never shipped (ceremony_font_problems says why)."""
    return {"%s/%s" % (CEREMONY_FONT_DIR, name) for name in CEREMONY_FONT_FILES
            if (root / CEREMONY_FONT_DIR / name).is_file() and _font_problem(root / CEREMONY_FONT_DIR / name) is None}


def ceremony_font_kept(gone: list[str]) -> list[str]:
    """The Kantalad faces a deploy would delete from the robot because this PC lacks them: never.
    Review 22 Nov night: `deploy --without-ceremony-font` deleted the robot's copies (everything in
    the robot's MANIFEST that the PC lacks goes) -- the flag is for a PC without the licensed
    files, and that PC must not take them off a robot that has them. A deploy only stops pushing
    them; the robot keeps what it had (and its MANIFEST keeps naming them)."""
    faces = {"%s/%s" % (CEREMONY_FONT_DIR, name) for name in CEREMONY_FONT_FILES}
    return sorted(f for f in gone if f in faces)


def ceremony_font_missing(files) -> list[str]:
    """The Kantalad faces a deploy of `files` would leave out."""
    have = set(files)
    return [name for name in CEREMONY_FONT_FILES if "%s/%s" % (CEREMONY_FONT_DIR, name) not in have]


def ceremony_font_summary(files: list[str], push: list[str], problems: list[str] | None = None) -> str:
    missing = ceremony_font_missing(files)
    if missing:
        bad = ("; " + "; ".join(problems)) if problems else ""
        return ("ceremony font: MISSING on this PC: %s%s -- the event screen would draw the film and its subtitles "
                "in a fallback face. Copy the four Kantalad faces (CFF OpenType .otf) into %s, or deploy "
                "--without-ceremony-font: nothing of the font is pushed, and the faces already on the robot stay "
                "there (a robot that never had them draws the film in Inter)"
                % (", ".join(missing), bad, CEREMONY_FONT_DIR))
    faces = {"%s/%s" % (CEREMONY_FONT_DIR, name) for name in CEREMONY_FONT_FILES}
    return "ceremony font: %d Kantalad face(s), %d to push" % (len(faces), len([f for f in push if f in faces]))


def ceremony_film_summary(files: list[str], push: list[str]) -> str:
    film = [f for f in files if f.startswith("data/ceremony/assets/") and not f.endswith("index.json")]
    pushing = [f for f in push if f in set(film)]
    total = sum((ROOT / f).stat().st_size for f in film if (ROOT / f).is_file())
    return "ceremony film: %d file(s) the slides name (%.1f MB), %d to push" % (len(film), total / 1e6, len(pushing))


_TRUE = {"1", "true", "yes", "on"}


def offline_on(root: Path = ROOT) -> bool:
    """OFFLINE_FALLBACK as the robot will run it: the `secrets` merge
    (compose_env), in which this key is robot.env's alone (ROBOT_ENV_ONLY) --
    this PC's .env turning it on for a desk trial does not reach the robot.
    Only the flag is read."""
    pc = root / ".env"
    over = root / "data" / "robot-server" / "robot.env"
    env = compose_env(pc.read_text(encoding="utf-8") if pc.is_file() else "",
                      over.read_text(encoding="utf-8") if over.is_file() else "")
    value = dict(parse_env(env)).get("OFFLINE_FALLBACK", "")
    return value.split("#", 1)[0].strip().strip("'\"").lower() in _TRUE


def offline_lines_warning(root: Path = ROOT) -> str | None:
    """A line for `deploy` when the robot will run offline mode beside fixed
    lines nobody signed (drafts are never said: the backup would answer only
    with stories, and nobody at the robot would know why). The lock test
    covers the committed files; this covers a working tree edited by hand."""
    if not offline_on(root):
        return None
    from app.config import settings
    from app.offline import lines as offline_lines

    status = offline_lines.status(offline_lines.load(
        root / "data" / "projects" / settings.project_id / "offline" / "lines.json"))
    if status["approved"] >= len(offline_lines.IDS) and not status["missing"]:
        return None
    return ("WARNING: OFFLINE_FALLBACK is on in robot.env but only %d of %d offline lines are approved -- "
            "drafts are never said (python scripts/approve_offline_lines.py --status)"
            % (status["approved"], len(offline_lines.IDS)))


def offline_voice_files(root: Path = ROOT) -> set[str]:
    """Emma's pre-rendered offline clips + their index (scripts/render_offline_
    voice.py; gitignored, so nothing else ships them) -- only while the robot
    runs OFFLINE_FALLBACK. Off, nothing changes. A clip the index pins and this
    PC lacks is simply not there: the robot then shows that line without its
    voice instead of a wrong one (app/offline/voice.py re-hashes every clip)."""
    if not offline_on(root):
        return set()
    folder = root / "data" / "offline" / "voice"
    out: set[str] = set()
    if folder.is_dir():
        for p in folder.iterdir():
            rel = p.relative_to(root).as_posix()
            if p.is_file() and (p.suffix == ".wav" or p.name == "index.json") and _deployable(rel):
                out.add(rel)
    return out


def offline_asr_pins(root: Path = ROOT) -> dict[str, str]:
    """data/offline/asr/<model>/<file> -> sha256, for the two recognizers the
    robot downloads and unpacks ITSELF (0.9 GB of archives at ~0.3 MB/s over
    adb Wi-Fi is most of an hour; the robot fetches at ~6 MB/s) -- only while
    the robot runs OFFLINE_FALLBACK. The pins are app/offline/asr.py's, so the
    robot gets exactly the files this PC's code loads."""
    if not offline_on(root):
        return {}
    return {"data/offline/asr/%s/%s" % (m["dir"], name): sha
            for m in offline_asr.MODELS.values() for name, sha in m["files"].items()}


def deploy_files(root: Path = ROOT) -> list[str]:
    files = set(tracked_files(root)) | {f for f in CODE_FILES if (root / f).is_file()}
    for pattern in EXTRA_GLOBS:
        for p in root.glob(pattern):
            if p.is_file():
                rel = p.relative_to(root).as_posix()
                if _deployable(rel):
                    files.add(rel)
    files |= answer_image_files(root)
    files |= event_clip_files(root)
    files |= event_voice_files(root)
    files |= story_asset_files(root)
    files |= ceremony_film_files(root)
    files |= ceremony_font_files(root)
    files |= offline_voice_files(root)
    # the recognizers never go over adb (the robot fetches them itself), even
    # when a future glob or tracked rule would catch them
    return sorted(f for f in files if f not in FETCH_ON_ROBOT and not f.startswith("data/offline/asr/"))


def manifest(root: Path, files: list[str]) -> dict[str, str]:
    return {f: sha256((root / f).read_bytes()) for f in files}


def changed(local: dict[str, str], remote: dict[str, str]) -> tuple[list[str], list[str]]:
    """(to push, to delete). Delete only what a previous deploy put there --
    never the server's own writes (logs, enrolled faces) under data/."""
    push = sorted(f for f, h in local.items() if remote.get(f) != h)
    gone = sorted(f for f in remote if f not in local)
    return push, gone


def robot_edited(local: dict[str, str], remote: dict[str, str], previous: dict[str, str]) -> list[str]:
    """Deployed files the SERVER has changed since the last deploy, and this PC
    has a different version of: the robot's copy is neither what we pushed nor
    what we would push. The server on the robot writes some of what deploy
    ships -- data/faces/people.json and gallery.npz (face scans at the robot),
    data/registry/approval_audit.jsonl and the facts (the data console) -- and a
    plain hash diff pushed the PC's older copy over them: enrolments and
    approvals made on the robot, gone without a word. These are kept unless
    `deploy --force`.

    Only under data/: the server runs as uid 9999 and can write nowhere else
    (code is root's). A mismatch outside data/ is a deploy that broke after the
    push and before the MANIFEST -- a Wi-Fi adb drop mid-tar, a failed model
    step -- and "keeping" it pinned half-written code on the robot for good
    (review 12 Oct; that day's first deploy kept three stale code files)."""
    return sorted(f for f, h in remote.items()
                  if f.startswith("data/") and f in local and h != local[f] and h != previous.get(f))


def spared_deletes(gone: list[str], remote: dict[str, str], previous: dict[str, str]) -> list[str]:
    """Files a deploy would delete (in the last MANIFEST, gone from this PC)
    that it must leave alone without --force: anything under data/ the robot's
    server changed since (same rule as robot_edited), and anything under
    data/faces/ at all -- the gallery, people.json and voice prints are
    gitignored, so a checkout that simply lacks them (another PC, a fresh
    clone) used to delete the enrolments and consent records made at the robot."""
    return sorted(f for f in gone if f.startswith("data/") and f in remote
                  and (f.startswith("data/faces/") or remote[f] != previous.get(f)))


def bundle(root: Path, files: list[str], mtime: int | None = None) -> bytes:
    """`mtime` defaults to now. It used to be 0 for every file, and tar on the
    robot restores it: a redeployed file with the same size then looked
    unchanged to every (mtime, size) cache in the running server -- the mydocs
    index, the face-gallery follower, the unit-plan digest cache (the guard
    that serves a plan only while its bytes still hash would have answered
    from the old file's digest)."""
    stamp = int(time.time()) if mtime is None else int(mtime)
    buf = io.BytesIO()
    # Uncompressed on purpose: through `su` the robot's stdin is a TTY, and
    # toybox's zcat refuses one ("need -f to read TTY") -- tar -xzf extracted
    # nothing while the deploy wrote its MANIFEST anyway (9 Oct). The payload is
    # mostly webp/png/onnx, which gzip barely shrinks.
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as t:
        for f in files:
            data = (root / f).read_bytes()
            if f.endswith(".sh"):   # a Windows checkout may hand us CRLF; Android's sh reads "\r" as part of words
                data = data.replace(b"\r\n", b"\n")
            info = tarfile.TarInfo(f)
            info.size = len(data)
            info.mode = 0o755 if f.endswith(".sh") else 0o644
            info.mtime = stamp
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def forward_line() -> str:
    return "https://127.0.0.1:%d/ -> robot 127.0.0.1:%d" % (FORWARD_PORT, PORT)


# -- adb ---------------------------------------------------------------------------

class Robot(rp.Robot):
    def _adb(self, *args: str) -> list[str]:
        return [self.adb, "-s", self.serial, *args]

    def adb_out(self, *args: str) -> str:
        return self._run(*args)

    #: su hands its child umask 0 (measured 9 Oct: what the server created came
    #: out drwxrwxrwx / -rw-rw-rw-). Every root script here starts from 022, so
    #: a directory that `mkdir -p` or tar creates (code/certs before the first
    #: deploy, new data/ folders) is never world-writable even for the moment
    #: before _permissions; a script that wants tighter (write_secret) says so.
    UMASK = "umask 022; "

    def root(self, script: str, check: bool = True) -> str:
        """`script` as root, through the same su the face app uses."""
        return self.shell("%s 0 sh -c %s" % (SU, sh_quote(self.UMASK + script)), check=check)

    def feed(self, script: str, data: bytes, timeout: float = 1800) -> None:
        """Stream `data` into ONE command run as root (adb exec-in): how secrets
        and bundles arrive without a copy in /data/local/tmp. Measured 9 Oct:
        exec-in returns no output and no exit status, and the shell is gone once
        stdin closes -- `cat > f; chmod ... f` never ran the chmod. So: one
        consumer per call, and every caller checks the result with a hash."""
        subprocess.run(self._adb("exec-in", "%s 0 sh -c %s" % (SU, sh_quote(self.UMASK + script))),
                       input=data, capture_output=True, timeout=timeout)

    def hashes(self, base: str, files: list[str]) -> dict[str, str]:
        """sha256 of each file as it is on the robot (missing ones absent)."""
        out: dict[str, str] = {}
        # Small chunks, a short timeout and retries: on 10 Oct one 200-file
        # chunk over a Wi-Fi adb that dropped hung for the whole 600 s default
        # and killed the deploy. A dropped link now costs one chunk's retry.
        for i in range(0, len(files), 60):
            chunk = files[i:i + 60]
            cmd = "%s 0 sh -c %s" % (SU, sh_quote("umask 022; cd %s && sha256sum %s 2>/dev/null"
                                                  % (base, " ".join(sh_quote(f) for f in chunk))))
            raw = ""
            for attempt in range(3):
                try:
                    raw = self.shell(cmd, check=False, timeout=90)
                    break
                except subprocess.TimeoutExpired:
                    if attempt == 2:
                        raise Fail("robot did not answer a hash check 3 times -- adb link unstable")
                    subprocess.run(self._adb("reconnect"), capture_output=True, timeout=30)
                    time.sleep(3)
            for line in raw.splitlines():
                h, _, name = line.partition("  ")
                if len(h) == 64 and name:
                    out[name.strip()] = h
        return out

    def write_secret(self, rel: str, data: bytes) -> None:
        """CODE/rel = data, root:nobody 0640, verified by hash. umask 077 first,
        so the half-written file is never readable by anyone but root."""
        dst = "%s/%s" % (CODE, rel)
        self.root("mkdir -p %s && rm -f %s.new" % (os.path.dirname(dst), dst))
        self.feed("umask 077; cat > %s.new" % dst, data)
        got = self.hashes(CODE, [rel + ".new"]).get(rel + ".new")
        if got != sha256(data):
            self.root("rm -f %s.new" % dst, check=False)
            raise Fail("%s did not arrive whole -- nothing replaced" % rel)
        self.root("set -e; chown 0:%s %s.new; chmod 0640 %s.new; mv %s.new %s" % (UID, dst, dst, dst, dst))


# -- commands ------------------------------------------------------------------------

def cmd_lock() -> None:
    """Pin the wheels the robot needs: pip download for aarch64/cp314 into
    .tmp/robot-server/wheels, then the PyPI URL + sha256 of each file."""
    WHEEL_CACHE.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:",
                    "--platform", "manylinux_2_28_aarch64", "--platform", "manylinux_2_27_aarch64",
                    "--platform", "manylinux_2_17_aarch64", "--platform", "manylinux2014_aarch64",
                    "--python-version", "3.14", "--implementation", "cp",
                    "--no-deps", "-d", str(WHEEL_CACHE), "-r", str(REQS)], check=True)
    wheels = []
    for p in sorted(WHEEL_CACHE.glob("*.whl")):
        if SKIP_WHEELS.match(p.name):
            continue
        name, version = p.name.split("-")[:2]
        with urllib.request.urlopen("https://pypi.org/pypi/%s/%s/json" % (name, version), timeout=60) as r:
            files = json.load(r)["urls"]
        hit = next((f for f in files if f["filename"] == p.name), None)
        digest = sha256(p.read_bytes())
        if hit is None or hit["digests"]["sha256"] != digest:
            raise Fail("%s: not on PyPI with this sha256" % p.name)
        wheels.append({"file": p.name, "url": hit["url"], "sha256": digest})
    debs = [{"file": path.rsplit("/", 1)[-1], "url": rp.DEBIAN + path, "sha256": h} for path, h in rp.DEBS.items()]
    LOCK.write_text(json.dumps({"python": PYTHON, "debs": debs, "wheels": wheels}, indent=1) + "\n",
                    encoding="utf-8")
    print("locked %d wheels -> %s" % (len(wheels), LOCK.relative_to(ROOT)))


def cmd_install(robot: Robot) -> None:
    if "py" not in robot.shell("ls %s 2>/dev/null" % rp.REMOTE, check=False).split():
        raise Fail("the route runtime (%s) is missing -- run `robot_python.py install` first: "
                   "it is the Python that downloads this one" % rp.REMOTE)
    robot.shell("mkdir -p %s %s" % (SETUP_DIR, STAGE))
    for name in ("robot_server_setup.py", "robot_dns.py"):
        robot.put((ROOT / "scripts" / name).read_bytes(), "%s/%s" % (SETUP_DIR, name))
    robot.put(LOCK.read_bytes(), SETUP_DIR + "/wheels.lock.json")
    out = robot.shell("cd %s && %s %s/robot_server_setup.py --lock %s/wheels.lock.json --stage %s "
                      "--runtime %s --home %s/data --tmp %s/data/tmp"
                      % (SETUP_DIR, NAV_PY, SETUP_DIR, SETUP_DIR, STAGE, RUNTIME, CODE, CODE), check=False)
    print(out.strip().splitlines()[-1] if out.strip() else "(no output)")
    if '"ok": true' not in out:
        raise Fail("staging failed:\n" + out[-2000:])
    robot.root(
        "set -e; umask 022; mkdir -p %(b)s; chown 0:%(u)s %(b)s; chmod 0750 %(b)s;"
        " rm -rf %(b)s/runtime.new; cp -a %(s)s/runtime %(b)s/runtime.new;"
        " chown -R 0:0 %(b)s/runtime.new; chmod -R go-w %(b)s/runtime.new;"
        " rm -rf %(b)s/runtime.old; if [ -d %(b)s/runtime ]; then mv %(b)s/runtime %(b)s/runtime.old; fi;"
        " mv %(b)s/runtime.new %(b)s/runtime; rm -rf %(b)s/runtime.old;"
        " mkdir -p %(b)s/run; chmod 0700 %(b)s/run;"
        # HOME and TMPDIR of the launcher: the server's own (writable) data/
        " mkdir -p %(c)s/data/tmp; chown -R %(u)s:%(u)s %(c)s/data; chmod 0750 %(c)s/data"
        % {"b": BASE, "s": STAGE, "u": UID, "c": CODE})
    smoke = robot.root("cd %s && %s %s %s/py -c %s 2>&1" % (
        BASE, SU, SU_SERVER, RUNTIME,
        sh_quote("import numpy, onnxruntime, sherpa_onnx, cv2, insightface, fastapi, uvicorn, "
                 "google.genai, pythainlp, httpx, certifi, tokenizers, sentencepiece; print('runtime ok', numpy.__version__, "
                 "onnxruntime.__version__, cv2.__version__, tokenizers.__version__)")), check=False)
    print(smoke.strip())
    if "runtime ok" not in smoke:
        raise Fail("runtime installed but an import failed (see above)")


def remote_manifest(robot: Robot) -> dict[str, str]:
    raw = robot.root("cat %s/MANIFEST.json 2>/dev/null" % CODE, check=False).strip()
    try:
        return json.loads(raw).get("files", {}) if raw else {}
    except ValueError:
        return {}


def refresh_faq_cache() -> str:
    """Make the report FAQ's embeddings current on this PC before they ship.

    The robot no longer builds a stale index (EMBED_BUILD=false in robot.env
    -- building it there hard-reset the robot, 15 Oct), so a stale cache
    shipped from here = FAQ matched by words only on the robot. This builds
    it here when the FAQ changed (a deny_terms edit is enough) and says so;
    "" when there is nothing to do. Only in the robot's space (local MiniLM).
    """
    from app import project_report as pr
    from app.tools.retrieval import choose_provider

    m = pr.load_manifest()
    if not m or not pr.approved(m):
        return ""
    if choose_provider() != "local":
        return ("WARNING: EMBED_PROVIDER on this PC is not local -- the report FAQ cache was not "
                "refreshed; the robot matches FAQ by words if it is stale")
    index = pr._get_index(m)            # loads the cache, or builds and saves it
    if index.get("faq") and index.get("faq_semantic") is None:
        return "WARNING: the report FAQ cache could not be built on this PC (see the log above)"
    return ""


def cmd_deploy(robot: Robot, dry: bool = False, force: bool = False, without_font: bool = False) -> None:
    note = refresh_faq_cache()
    if note:
        print(note)
    warning = offline_lines_warning()
    if warning:
        print(warning)
    files = deploy_files()
    # (before anything is pushed: a film whose @font-face points at nothing -- or at a file that is
    # not a font -- is not shipped quietly)
    font_problems = ceremony_font_problems(ROOT)
    if ceremony_font_missing(files) and not dry and not without_font:
        raise Fail(ceremony_font_summary(files, [], font_problems))
    local = manifest(ROOT, files)
    # Diff against what is REALLY on the robot, not against our MANIFEST: the
    # first deploy wrote a MANIFEST for files that never arrived (see bundle()).
    previous = remote_manifest(robot)
    remote = robot.hashes(CODE, files)
    expect = {f: (sha256((ROOT / f).read_bytes().replace(b"\r\n", b"\n")) if f.endswith(".sh") else h)
              for f, h in local.items()}
    push, _ = changed(expect, remote)
    kept = [] if force else robot_edited(expect, remote, previous)
    push = [f for f in push if f not in kept]
    asr_pins = offline_asr_pins()
    gone = sorted(f for f in previous if f not in local and f not in FETCH_ON_ROBOT and f not in asr_pins)
    spared = [] if force else spared_deletes(gone, robot.hashes(CODE, gone), previous)
    gone = [f for f in gone if f not in spared]
    # (the robot's Kantalad faces are never deleted, --force or not: ceremony_font_kept)
    font_kept = ceremony_font_kept(gone)
    gone = [f for f in gone if f not in font_kept]
    size = sum((ROOT / f).stat().st_size for f in push)
    print("%d files, %d to push (%.1f MB), %d to delete" % (len(files), len(push), size / 1e6, len(gone)))
    voice_line = event_voice_summary(files, push)
    if voice_line:
        print(voice_line)
    print(ceremony_film_summary(files, push))
    print(ceremony_font_summary(files, push, font_problems))
    if font_kept:
        print("ceremony font: keeping the %d Kantalad face(s) already on the robot (this PC lacks them; "
              "a deploy never deletes them): %s" % (len(font_kept), ", ".join(f.rsplit("/", 1)[1] for f in font_kept)))
    if kept:
        print("kept %d file(s) the robot's server changed since the last deploy (deploy --force overwrites): %s"
              % (len(kept), ", ".join(kept[:8]) + (" ..." if len(kept) > 8 else "")))
    if spared:
        print("not deleting %d robot data file(s) this PC lacks (deploy --force deletes): %s"
              % (len(spared), ", ".join(spared[:8]) + (" ..." if len(spared) > 8 else "")))
    if dry:
        # the names, not just the count (10 Nov: "check first" -- what goes up is what was checked)
        for f in push[:60]:
            print("  push   %s" % f)
        for f in gone[:60]:
            print("  delete %s" % f)
        return
    if push:
        blob = bundle(ROOT, push)
        t0 = time.time()
        robot.root("mkdir -p %s" % CODE)
        robot.feed("cd %s && tar -xf -" % CODE, blob)
        print("pushed %.1f MB in %.0f s" % (len(blob) / 1e6, time.time() - t0))
        landed = robot.hashes(CODE, push)
        bad = [f for f in push if landed.get(f) != expect[f]]
        if bad:
            raise Fail("%d of %d files did not arrive (first: %s) -- MANIFEST not written"
                       % (len(bad), len(push), ", ".join(bad[:3])))
    for f in gone:
        robot.root("rm -f %s/%s" % (CODE, sh_quote(f)), check=False)
    fetch = {f: u for f, u in FETCH_ON_ROBOT.items() if (ROOT / f).is_file()}
    pins = {f: {"url": u, "sha256": sha256((ROOT / f).read_bytes())} for f, u in fetch.items()}
    have = robot.hashes(CODE, list(pins))
    need = {f: p for f, p in pins.items() if have.get(f) != p["sha256"]}
    if need:
        robot.put(json.dumps(need).encode(), SETUP_DIR + "/models.json")
        robot.put((ROOT / "scripts" / "robot_dns.py").read_bytes(), SETUP_DIR + "/robot_dns.py")
        out = robot.shell("cd %s && %s -c %s" % (SETUP_DIR, NAV_PY, sh_quote(_MODEL_FETCH % {"stage": STAGE})),
                          check=False)
        print(out.strip()[-400:])
        if "models ok" not in out:
            raise Fail("model download failed")
        for f in need:
            robot.root("set -e; mkdir -p %s/%s; cp %s/models/%s %s/%s"
                       % (CODE, os.path.dirname(f), STAGE, os.path.basename(f), CODE, f))
        have = robot.hashes(CODE, list(need))
        bad = [f for f, p in need.items() if have.get(f) != p["sha256"]]
        if bad:
            raise Fail("model copy failed: %s" % bad)
    if asr_pins:
        _fetch_offline_asr(robot, asr_pins)
    # A kept file stays recorded as what we LAST pushed (or not at all), so the
    # next deploy still sees the robot's edit instead of adopting it as ours.
    deployed = {f: h for f, h in expect.items() if f not in kept}
    deployed.update({f: previous[f] for f in kept if f in previous})
    # (the faces left on the robot stay named in its MANIFEST as what is there)
    deployed.update({f: previous[f] for f in font_kept})
    man = json.dumps({"deployed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                      "files": {**deployed, **{f: p["sha256"] for f, p in pins.items()}, **asr_pins}},
                     sort_keys=True).encode()
    robot.root("rm -f %s/MANIFEST.json.new" % CODE)
    robot.feed("cat > %s/MANIFEST.json.new" % CODE, man)
    if robot.hashes(CODE, ["MANIFEST.json.new"]).get("MANIFEST.json.new") != sha256(man):
        raise Fail("MANIFEST did not arrive")
    robot.root("mv %s/MANIFEST.json.new %s/MANIFEST.json" % (CODE, CODE))
    _permissions(robot)
    print("deployed")


def _fetch_offline_asr(robot: Robot, asr_pins: dict[str, str]) -> None:
    """The offline recognizers onto the robot: the robot downloads the pinned
    archives (scripts/fetch_offline_models.py under the nav Python -- the same
    script this PC uses: sha256 checked BEFORE extracting, only the pinned
    files taken, a member outside its model folder refused), into the shell-
    owned stage; root copies them under code/data/offline/asr; every file is
    hash-checked where it lands. Skipped when they are already there."""
    have = robot.hashes(CODE, list(asr_pins))
    need = {name: pin for name, pin in offline_asr.fetch_map().items()
            if any(have.get("data/offline/asr/%s/%s" % (pin["dir"], f)) != h for f, h in pin["files"].items())}
    if not need:
        return
    robot.shell("mkdir -p %s %s" % (SETUP_DIR, STAGE))
    robot.put(json.dumps(need).encode(), SETUP_DIR + "/offline_pins.json")
    robot.put((ROOT / "scripts" / "fetch_offline_models.py").read_bytes(), SETUP_DIR + "/fetch_offline_models.py")
    robot.put((ROOT / "scripts" / "robot_dns.py").read_bytes(), SETUP_DIR + "/robot_dns.py")
    out = robot.shell("cd %s && %s fetch_offline_models.py --pins offline_pins.json --dest %s/offline-asr "
                      "--stage %s/offline-archives" % (SETUP_DIR, NAV_PY, STAGE, STAGE),
                      check=False, timeout=3600)
    print(out.strip()[-400:])
    if "offline models ok" not in out:
        raise Fail("offline ASR download failed (see above) -- nothing copied")
    for pin in need.values():
        for name in pin["files"]:
            robot.root("set -e; mkdir -p %s/data/offline/asr/%s; cp %s/offline-asr/%s/%s %s/data/offline/asr/%s/%s"
                       % (CODE, pin["dir"], STAGE, pin["dir"], name, CODE, pin["dir"], name))
    landed = robot.hashes(CODE, list(asr_pins))
    bad = [f for f, h in asr_pins.items() if landed.get(f) != h]
    if bad:
        raise Fail("offline ASR copy failed: %s" % bad[:3])
    robot.shell("rm -rf %s/offline-asr" % STAGE, check=False)


#: Runs on the robot under the nav Python: pinned model downloads into STAGE/models.
_MODEL_FETCH = """
import hashlib, json, os, sys
sys.path.insert(0, '.')
import robot_dns; robot_dns.install()
import httpx
pins = json.load(open('models.json'))
os.makedirs('%(stage)s/models', exist_ok=True)
for f, p in pins.items():
    dst = '%(stage)s/models/' + os.path.basename(f)
    if os.path.exists(dst) and hashlib.sha256(open(dst, 'rb').read()).hexdigest() == p['sha256']:
        continue
    h = hashlib.sha256()
    with httpx.stream('GET', p['url'], follow_redirects=True, timeout=300) as r, open(dst + '.part', 'wb') as o:
        r.raise_for_status()
        for c in r.iter_bytes(1 << 20):
            h.update(c); o.write(c)
    if h.hexdigest() != p['sha256']:
        os.remove(dst + '.part'); sys.exit('sha256 mismatch: ' + f)
    os.replace(dst + '.part', dst)
print('models ok')
"""


def _permissions(robot: Robot) -> None:
    """Code root-owned and read-only to the server; data/ the server's own;
    secrets readable by the server, writable by root only."""
    robot.root(
        "set -e; chown -R 0:0 %(c)s; chmod -R go-w %(c)s; mkdir -p %(c)s/data/tmp;"
        " chown -R %(u)s:%(u)s %(c)s/data; chmod 0750 %(c)s/data;"
        " for f in %(c)s/.env %(c)s/certs/lan-key.pem %(c)s/room-screens.json; do"
        " if [ -f $f ]; then chown 0:%(u)s $f; chmod 0640 $f; fi; done;"
        " chown 0:%(u)s %(b)s; chmod 0750 %(b)s"
        % {"c": CODE, "u": UID, "b": BASE})


def _secrets_preview(robot: Robot, env: str) -> None:
    """What `secrets` would change, writing nothing: key NAMES added, removed
    and changed in the robot's .env (never a value), and which secret files
    differ by hash. 3 Nov 2026: `--dry-run secrets` was ignored -- argparse
    took the flag, only deploy read it -- and the real .env was written while
    the check before a deploy was meant to look only."""
    want = dict(parse_env(env))
    have = dict(parse_env(robot.root("cat %s/.env 2>/dev/null" % CODE, check=False)))
    added = sorted(set(want) - set(have))
    removed = sorted(set(have) - set(want))
    changed = sorted(k for k in set(want) & set(have) if want[k] != have[k])
    print("secrets --dry-run: .env %d keys (robot has %d) -- added: %s; removed: %s; changed: %s"
          % (len(want), len(have), ", ".join(added) or "-", ", ".join(removed) or "-",
             ", ".join(changed) or "-"))
    files = {dst: (ROOT / src).read_bytes() for src, dst in SECRET_FILES.items()}
    files.update({dst: (ROOT / src).read_bytes() for src, dst in OPTIONAL_SECRET_FILES.items()
                  if (ROOT / src).is_file()})
    on_robot = robot.hashes(CODE, sorted(files))
    differ = [dst for dst, data in sorted(files.items()) if on_robot.get(dst) != sha256(data)]
    print("secrets --dry-run: files that would change: %s -- nothing written" % (", ".join(differ) or "-"))


def cmd_secrets(robot: Robot, dry_run: bool = False) -> None:
    env = compose_env((ROOT / ".env").read_text(encoding="utf-8"), OVERRIDES.read_text(encoding="utf-8"))
    if dry_run:
        _secrets_preview(robot, env)
        return
    robot.write_secret(".env", env.encode("utf-8"))
    sent = []
    for src, dst in SECRET_FILES.items():
        robot.write_secret(dst, (ROOT / src).read_bytes())
        sent.append(dst)
    for src, dst in OPTIONAL_SECRET_FILES.items():
        if (ROOT / src).is_file():
            robot.write_secret(dst, (ROOT / src).read_bytes())
            sent.append(dst)
    # Say which keys went, never their values.
    print("secrets: .env with %d keys, %s (root:nobody 0640, hash-checked) -- `restart` to apply"
          % (len(parse_env(env)), ", ".join(sent)))


def server(robot: Robot, action: str) -> str:
    return robot.root("sh %s/scripts/emma_server.sh %s" % (CODE, action), check=False).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=["lock", "install", "deploy", "secrets", "start", "stop", "restart",
                                     "status", "logs", "forward"])
    ap.add_argument("--serial", default="192.168.0.2:5555")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="deploy: overwrite files the robot's server changed (faces, approvals)")
    ap.add_argument("--without-ceremony-font", action="store_true",
                    help="deploy even though this PC lacks the ceremony film's Kantalad faces (or has one that is "
                         "not a font): nothing of the font is pushed, and the faces already on the robot stay there "
                         "-- no deploy deletes them. A robot that never had them draws the film in Inter")
    a = ap.parse_args(argv)
    # A flag a command does not read is a check that did not happen: `secrets
    # --dry-run` once wrote the robot's .env (3 Nov). Those two preview; status
    # and logs only read anyway; everything else refuses rather than acting.
    if a.dry_run and a.what not in ("deploy", "secrets", "status", "logs"):
        print("--dry-run previews only deploy and secrets; `%s` would act -- nothing done" % a.what,
              file=sys.stderr)
        return 2
    try:
        if a.what == "lock":
            cmd_lock()
            return 0
        robot = Robot(a.serial)
        if a.what == "install":
            cmd_install(robot)
        elif a.what == "deploy":
            cmd_deploy(robot, a.dry_run, a.force, a.without_ceremony_font)
        elif a.what == "secrets":
            cmd_secrets(robot, a.dry_run)
        elif a.what == "forward":
            # adb prints the bound port ("18001") on success -- not an address
            # anybody can open, and it used to replace the line below
            robot.adb_out("forward", "tcp:%d" % FORWARD_PORT, "tcp:%d" % PORT)
            print(forward_line())
        elif a.what == "logs":
            print(robot.root("tail -n 80 %s/data/server.log" % CODE, check=False))
        else:
            print(server(robot, a.what))
    except (Fail, rp.Fail) as exc:
        print("FAILED:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
