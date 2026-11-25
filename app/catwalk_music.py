"""The music of card 1's walk on the hall's screen (owner 24 Nov, after a full run on the robot: the walk was silent in the
hall). Which files there are, and their addresses for the screen -- app/face_mirror.py `catwalk` says when, the page
(client/assets/catwalk-music.js) plays them from its own clock, aloud only where that screen plays sound.

Three files in data/events/clips/ (gitignored, shipped by the deploy while CATWALK_MUSIC is on): `catwalk_walk` (a loop, to
Khun Miki), `catwalk_guests` (a loop, to the guests), `catwalk_hit` (the short end of either). Swapping one = the same name in
any of the formats below; nothing in code. Off (the default) = no address is ever sent and the route answers 404."""
from __future__ import annotations

from pathlib import Path

from app.config import settings

ROOT = Path(__file__).resolve().parent.parent
NAMES = ("walk", "guests", "hit")
#: Opus first: Chromium decodes it to exactly the source's samples (measured 24 Nov: same length, lag 0 -- the loop has no
#: gap) at a tenth of the FLAC's bytes, over the hall's Wi-Fi.
TYPES = {".opus": "audio/ogg", ".flac": "audio/flac", ".mp3": "audio/mpeg", ".wav": "audio/wav"}


def files(root: Path = ROOT) -> dict[str, Path]:
    """{name: its file} -- only what is there. No switch here: the deploy asks before the robot's is read."""
    out: dict[str, Path] = {}
    for name in NAMES:
        for suffix in TYPES:
            path = root / "data" / "events" / "clips" / ("catwalk_" + name + suffix)
            if path.is_file():
                out[name] = path
                break
    return out


def addresses() -> dict[str, str]:
    """{name: address} for the screen, {} while the switch is off. The address moves with the file (its size and time): a
    swapped file is never the browser's old copy."""
    if not settings.catwalk_music:
        return {}
    out = {}
    for name, path in files().items():
        try:
            st = path.stat()
        except OSError:
            continue
        out[name] = "/catwalk/%s?v=%x-%x" % (name, st.st_size, int(st.st_mtime))
    return out


#: The pictures on the hall's screen while card 1 walks (owner 25 Nov 01:1x: "ทำไมจอไม่ขึ้นตอนเดิน จอแกลรอลี่"): the owner's own
#: final pictures that the signed film already names (so they are on the robot), landscape, no people in them, no plan.
GALLERY_IDS = ("fin_entrance", "fin_building_a", "fin_lobby_01", "fin_pool_v1", "fin_pool_v4_1610", "fin_relax_1120",
               "c_unit_studio_bed", "c_unit_1br_living", "c_unit_2br_master", "fin_pool_v2_hd", "fin_front_1120")


#: One short English place word per picture, drawn on the screen with it (owner 25 Nov: motion + a label). Fixed here; no free text.
GALLERY_LABELS = {"fin_entrance": "Entrance", "fin_building_a": "Building A", "fin_lobby_01": "Lobby",
                  "fin_pool_v1": "Water Lagoon", "fin_pool_v4_1610": "Pool Courtyard", "fin_relax_1120": "By the Water",
                  "c_unit_studio_bed": "Studio", "c_unit_1br_living": "One Bedroom", "c_unit_2br_master": "Two Bedroom",
                  "fin_pool_v2_hd": "Water Lagoon", "fin_front_1120": "Front"}


def gallery() -> list[dict]:
    """The gallery, in order, as [{"src": "/ceremony-asset/<sha>", "label": "Lobby"}] -- only pictures the film may show now
    (its own gate), none with a person box. [] while the switch is off."""
    if not settings.catwalk_music:
        return []
    try:
        from app import ceremony_slides

        index = {e["id"]: e for e in ceremony_slides.asset_index().values()}
        out = []
        for asset_id in GALLERY_IDS:
            entry = index.get(asset_id)
            if (entry is None or entry.get("kind") != "picture" or entry.get("people") or entry.get("keep_out")
                    or not ceremony_slides._servable(entry)):
                continue
            out.append({"src": "/ceremony-asset/" + entry["sha256"], "label": GALLERY_LABELS.get(asset_id, "")})
        return out
    except Exception:  # noqa: BLE001 -- the music plays without its pictures
        return []


def body(name: str) -> tuple[Path, str] | None:
    """The file behind an address, or None: the switch off, another name, no file."""
    if not settings.catwalk_music or name not in NAMES:
        return None
    path = files().get(name)
    return (path, TYPES[path.suffix]) if path else None
