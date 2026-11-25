"""The event morning's GO / NO-GO on the staff phone (owner 19 Nov, after watching a night of
scripts: "ตรวจสอบให้ด้วยวันงานถ้าจะใช้ปุ่มกดเพื่อทำแต่ละซีนจะไม่มีปัญหาใช่ไหม ไม่จำเป็นต้องมาโค้ดแบบนี้").

scripts/event_preflight.py says GO or NO-GO from the PC -- the run sheet's step 3, a terminal.
This is the same file checks, run by the server ON THE ROBOT (its own files, its own recorded
spots: no adb, nothing compared with a PC), plus what only the robot knows now: the board, the
battery, the dock re-learnt this morning, the app on the robot, the internet. Read-only, in Thai,
one row each: ok True (ผ่าน), False (ไม่ผ่าน = NO-GO), None (หมายเหตุ, never blocks).

What it does NOT check (the PC's preflight does, before the day): that the robot's code and
secrets are the PC's (`robot_server.py deploy/secrets --dry-run`). The phone page says so.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path

from app.config import settings

logger = logging.getLogger("condo_voice.event_readiness")

ROOT = Path(__file__).resolve().parent.parent
#: The run sheet's "แบตหุ่น ≥ 80% ก่อนพิธี".
BATTERY_MIN = 80
#: A morning check is not re-read on every poll: the files' half hashes recordings.
CACHE_S = 20.0
_cache: dict = {"at": 0.0, "doc": None}
#: Said on the card under the rows: what this cannot see from the robot.
NOT_HERE_TH = ("ตรวจจากในหุ่นเอง — ไม่ได้เทียบว่าโค้ดบนหุ่นตรงกับคอมผู้ดูแล (ผู้ดูแลเช็คด้วย event_preflight.py ก่อนวันงาน)")


def _row(ok, th: str, detail_th: str = "") -> dict:
    return {"ok": ok, "th": th, "detail_th": detail_th}


def _ceremony_rows() -> list[dict]:
    from app import event_script

    doc = event_script.load()
    if doc is None:
        return [_row(False, "บทพิธี", "ไม่พบไฟล์บทพิธี (CEREMONY_FILE)")]
    lang = event_script.speak(doc)
    lines = [c for c in event_script.cues(doc) if c.get("th")]
    not_ready, not_heard, no_voice = [], [], []
    try:
        from app import event_voice

        clip_sha = getattr(event_voice, "clip_sha", None)
        voice_status = getattr(event_voice, "status", None)
    except ImportError:
        clip_sha = voice_status = None
    for c in lines:
        if not event_script.approved(c) or event_script._problems_in(c, lang):
            not_ready.append(c["id"])
        if voice_status is not None:
            # no clip that would play (missing on the robot, a pin that fails, another voice/model):
            # Gemini reads it -- said as its own row (review 20 Nov: clip_sha None was "heard" ✓)
            try:
                playable = (voice_status(c, lang) if lang != "th" else voice_status(c)).get("recorded")
            except Exception:                            # noqa: BLE001
                playable = False
            if playable is False:
                no_voice.append(c["id"])
        sha = None
        if clip_sha is not None:
            try:
                sha = clip_sha(c, lang) if lang != "th" else clip_sha(c)
            except Exception:                            # noqa: BLE001 -- a recording that cannot be read
                sha = None
        if sha and c.get("approved_voice_sha256") != sha:
            not_heard.append(c["id"])
    rows = [_row(not not_ready, "บทพิธีอนุมัติแล้ว %d/%d บท" % (len(lines) - len(not_ready), len(lines)),
                 ("บทที่กดแล้ว Emma ไม่พูด: %s — เจ้าของต้องเซ็นบทที่คอมผู้ดูแลก่อนวันงาน แล้ว deploy" % ", ".join(not_ready))
                 if not_ready else ""),
            _row(not not_heard, "เสียงอัดของบทพิธีฟังแล้ว",
                 ("บทที่ Gemini จะอ่านเองแทนเสียงอัด: %s — เจ้าของฟังแล้วเซ็น --listened ที่คอมผู้ดูแล" % ", ".join(not_heard))
                 if not_heard else ""),
            _row(not no_voice, "เสียงอัดของบทพิธีเล่นได้ครบบนหุ่น",
                 ("บทที่ไม่มีเสียงอัดที่เล่นได้ (Gemini อ่านเอง): %s — ไฟล์เสียงไม่อยู่บนหุ่น/ไม่ตรงที่ปัก หรือเสียง/โมเดลเปลี่ยน "
                  "· ผู้ดูแลแก้ที่คอมแล้ว deploy" % ", ".join(no_voice)) if no_voice else "")]
    try:
        probs = event_script.order_problems(doc)
    except Exception as exc:                             # noqa: BLE001
        probs = ["ตรวจไม่ได้: %s" % type(exc).__name__]
    rows.append(_row(not probs, "ลำดับพิธี", "; ".join(probs)[:300]))
    try:
        from app import ceremony_slides

        sp = ceremony_slides.path()
        slides = ceremony_slides.load(sp) if sp is not None and sp.is_file() else None
        if slides is None:
            rows.append(_row(False, "หนังพิธีบนจอใหญ่", "ไม่พบไฟล์สไลด์"))
        else:
            signed = ceremony_slides.approved(slides)
            broken = [r["id"] for r in ceremony_slides.status(slides) if r["problems"]]
            rows.append(_row(signed and not broken, "หนังพิธีบนจอใหญ่ (ไฟล์สไลด์เซ็นแล้ว)",
                             ("" if signed else "ไฟล์สไลด์ยังเป็นร่าง = จอใหญ่ไม่ขึ้นหนัง — เจ้าของเซ็นที่คอมผู้ดูแลแล้ว deploy")
                             + ((" · สไลด์ที่ตรวจไม่ผ่าน: " + ", ".join(broken)) if broken else "")))
    except Exception:                                    # noqa: BLE001
        logger.exception("readiness: the slides")
        rows.append(_row(False, "หนังพิธีบนจอใหญ่", "ตรวจไม่ได้"))
    return rows


def _spot_rows() -> list[dict]:
    from app import event_host, event_script

    doc = event_host.load()
    if doc is None:
        return [_row(False, "ไฟล์งาน", "เครื่องนี้ไม่มีไฟล์งาน (EVENT_FILE)")]
    if event_host.spots_file_broken():
        # every recorded spot and the re-learnt dock read as none (review 20 Nov)
        return [_row(False, "ไฟล์จุดที่บันทึกบนหุ่น", event_host.SPOTS_UNREADABLE_TH)]
    used: dict[str, list[str]] = {}
    for sc in doc["scenes"]:
        for name in [sc.get("spot")] + list(sc.get("stops") or []) + list((sc.get("roam") or {}).get("stops") or []):
            if isinstance(name, str):
                used.setdefault(name, []).append(sc["title_th"])
        tour = sc.get("tour") if settings.event_tour else None
        # card 1's tour (app/event_tour.py, 23 Nov): a guest spot with no position = no guest round at all
        for name in ([tour["counter"]] + list(tour["guests"])) if tour else []:
            used.setdefault(name, []).append(sc["title_th"] + " ต่อเนื่อง")
    script = event_script.load()
    for part in (script or {}).get("order") or []:
        for st in part.get("steps") or []:
            if st.get("do") == "walk" and isinstance(st.get("spot"), str):
                used.setdefault(st["spot"], []).append("พิธีฉาก %s" % part.get("n", "?"))
    missing = []
    for name, where in sorted(used.items()):
        spot = doc["spots"].get(name)
        if spot is None or not (spot.get("dock") or event_host.spot_steps(spot)):
            missing.append("%s (%s)" % (spot["name_th"] if spot else name, ", ".join(dict.fromkeys(where))))
    rows = [_row(not missing, "ทุกจุดที่ฉาก/พิธีเดินไปมีตำแหน่ง",
                 ("ยังไม่ได้บันทึก: %s — ขับหุ่นไปยืนที่จุด แล้วกด 'บันทึกตำแหน่งตอนนี้' ใน 'ตั้งค่าจุด'" % "; ".join(missing))
                 if missing else "")]
    bad = []
    for sc in doc["scenes"]:
        # the scene's own line and its tour's lines (app/event_tour.py `lines`, 24 Nov)
        recs = [sc.get("recorded"), *((sc.get("tour") or {}).get("lines") or {}).values()]
        for rec in recs:
            if not isinstance(rec, dict):
                continue
            try:
                sha = hashlib.sha256((ROOT / str(rec.get("file", ""))).read_bytes()).hexdigest()
            except OSError:
                sha = None
            if sha != rec.get("sha256"):
                bad.append(sc["title_th"])
                break
    if bad:
        rows.append(_row(False, "เสียงอัดของฉาก", "ไฟล์ไม่ตรงที่ปักไว้: %s (Emma จะพูดเองแทน)" % ", ".join(bad)))
    return rows


def _venue_rows() -> list[dict]:
    """The venue's lights and curtains the robot drives (app/venue_devices.py), reached FROM THE ROBOT'S
    own Wi-Fi -- the hub on another subnet is the likeliest way this fails on the day. An auth or an
    http GET, nothing sent: it runs here, in the thread, with the file checks. [] while VENUE_DEVICES
    is off (the card as before)."""
    from app import venue_devices

    return venue_devices.readiness_rows()


def _files() -> list[dict]:
    rows = []
    for part in (_ceremony_rows, _spot_rows, _venue_rows):
        try:
            rows.extend(part())
        except Exception:                                # noqa: BLE001 -- one check that cannot run is a row
            logger.exception("readiness: %s", part.__name__)
            rows.append(_row(False, "ตรวจไฟล์", "ตรวจไม่ได้ (%s)" % part.__name__))
    return rows


def _live() -> list[dict]:
    """What only the robot knows, now (cheap: what the staff page's poll has already read)."""
    from app import event_host, event_walk

    rows = []
    face = event_host._face_state()
    mode = event_host.mode_snapshot()
    rows.append(_row(face["connected"], "หน้าหุ่นเชื่อมต่อ",
                     "" if face["connected"] else "เปิดแอป Emma Event บนหุ่น"))
    h = event_walk.health_view()
    if not h.get("known"):
        rows.append(_row(False, "บอร์ดเดิน", "ยังไม่ได้อ่านบอร์ด — รอสักครู่แล้วกด 'ตรวจอีกครั้ง'"))
    elif not h.get("board"):
        rows.append(_row(False, "บอร์ดเดิน", "อ่านบอร์ดเดินไม่ได้ — ปิด-เปิดหุ่นตามคู่มือซ้อม"))
    else:
        battery = h.get("battery")
        rows.append(_row(isinstance(battery, (int, float)) and battery >= BATTERY_MIN,
                         "แบต ≥ %d%% ก่อนพิธี" % BATTERY_MIN,
                         "ตอนนี้ %s%%%s" % ("?" if battery is None else round(battery), " ชาร์จอยู่" if h.get("charging") else "")))
        on_dock = h.get("docking") == "on_dock"
        rows.append(_row(None, "หุ่นอยู่บนแท่น" if on_dock else "หุ่นไม่ได้อยู่บนแท่น",
                         "" if on_dock else "วัดแท่น/ตั้งตำแหน่งที่แท่นต้องทำตอนหุ่นอยู่บนแท่น"))
        rows.append(_row(not h.get("lost"), "หุ่นรู้ตำแหน่งตัวเอง (Q%s)" % h.get("q"),
                         "หุ่นไม่แน่ใจตำแหน่ง — ให้หุ่นอยู่บนแท่น แล้วกด 'ตั้งตำแหน่งที่แท่น'" if h.get("lost") else ""))
    relearn = event_host.relearn_view()
    rows.append(_row(relearn["today"], "วัดแท่นแล้ววันนี้",
                     ("วัดเมื่อ %s" % relearn["recorded_at"]) if relearn["today"] else
                     "แท่นถูกตั้งใหม่ทุกครั้งที่จัดงาน — หุ่นอยู่บนแท่นแล้วกด 'วัดแท่นใหม่' (หุ่นออก 0.8 ม. แล้วถอยกลับเอง)"
                     + ((" · ครั้งล่าสุด %s" % relearn["recorded_at"]) if relearn["recorded_at"] else "")))
    rows.append(_row(settings.event_walk_enabled, "การเดินเปิดอยู่",
                     "" if settings.event_walk_enabled else "EVENT_WALK_ENABLED ปิด — ฉากจะเล่นตรงที่หุ่นยืน"))
    net = event_host._net_view()
    if net is not None:
        level = net.get("level")
        rows.append(_row(level != "down" if level else None, "อินเทอร์เน็ตของหุ่น",
                         {"ok": "", "slow": "ช้า", "flaky": "สะดุด", "down": "หลุด — Emma ตอบไม่ได้"}.get(level, "ยังไม่รู้")))
    if mode.get("warnings"):
        rows.append(_row(None, "หมายเหตุ", " · ".join(mode["warnings"])))
    try:
        from app import event_box

        # the box (second review 23 Nov): armed / the arm locked / not said -- and when it clears itself (the morning)
        rows.append(event_box.readiness_row())
    except Exception:                                    # noqa: BLE001 -- the card must still come up
        rows.append(_row(None, "กล่อง", "อ่านสถานะกล่องไม่ได้"))
    return rows


#: The lidar at the board's own pose, on the dock (board_walk --find-dock-pose's `board_fit`): Q alone
#: says 50+ on a stale pose after a power cycle (review 20 Nov), so GO needs the lidar's word too.
#: board_walk's own bar for writing the dock's pose.
FIT_MIN = 0.45
FIT_TH = "ตำแหน่งหุ่นตรงกับเลเซอร์"
#: The last fit row, kept while she stays on the dock (a power cycle restarts the server: forgotten).
_fit: dict = {"row": None}


async def _fit_row(fresh: bool) -> dict | None:
    """Run only on "ตรวจอีกครั้ง" (`fresh`): it is a board_walk process, and it holds the board's pose
    while it reads (event_walk.pose_hold) -- a background refresh every 30 s would refuse scene walks
    all day. Until it has run on the dock: NO-GO, with the button to press."""
    from app import event_host, event_walk

    h = event_walk.health_view()
    if not (h.get("known") and h.get("board")):
        return None                                      # the board's own row says it
    if h.get("docking") != "on_dock":
        _fit["row"] = None
        return _row(None, FIT_TH, "ตรวจด้วยเลเซอร์ได้เมื่อหุ่นอยู่บนแท่น")
    if not fresh or event_walk.walking() or event_walk.pose_busy():
        return _fit["row"] or _row(False, FIT_TH, "ยังไม่ได้ตรวจด้วยเลเซอร์ — กด 'ตรวจอีกครั้ง' (หุ่นอยู่บนแท่น ไม่ขยับ)")
    look = await event_walk.find_dock_pose(event_host.dock_doc(event_host.load() or {}), set_it=False)
    fit = (look.get("found") or {}).get("board_fit")
    if not look.get("ok") or not isinstance(fit, (int, float)):
        row = _row(False, FIT_TH, "ตรวจด้วยเลเซอร์ไม่ได้: %s" % (look.get("error_th") or "ไม่รู้ผล"))
    elif fit >= FIT_MIN:
        row = _row(True, FIT_TH, "เลเซอร์ตรงแผนที่ %.2f" % fit)
    else:
        row = _row(False, FIT_TH, "เลเซอร์ตรงแผนที่แค่ %.2f — ตำแหน่งในบอร์ดไม่ใช่ที่หุ่นอยู่ (เช่นหลังรีบูต): "
                                  "กด 'ตั้งตำแหน่งที่แท่น'" % fit)
    _fit["row"] = row
    return row


async def check(*, fresh: bool = False) -> dict:
    """{"go": bool, "rows": [...], "at": epoch, "not_here_th"}. The file checks are cached
    CACHE_S (they hash recordings) and run off the loop; the live rows are always now; the
    lidar's row only on `fresh` (_fit_row)."""
    now = time.monotonic()
    if fresh or _cache["doc"] is None or now - _cache["at"] > CACHE_S:
        _cache.update(doc=await asyncio.to_thread(_files), at=now)
    fit = await _fit_row(fresh)
    rows = list(_cache["doc"]) + _live() + ([fit] if fit else [])
    return {"go": not any(r["ok"] is False for r in rows), "rows": rows, "at": time.time(), "not_here_th": NOT_HERE_TH}


def reset() -> None:
    _cache.update(at=0.0, doc=None)
    _fit.update(row=None)
