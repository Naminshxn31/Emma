#!/usr/bin/env python3
"""ไฟ/ม่านของสถานที่จัดงานที่หุ่นสั่ง (app/venue_devices.py) — ตั้งค่า สอนรหัส และลองส่ง

    python scripts/venue_devices.py list                         # อุปกรณ์ทั้งหมด สอนรหัสแล้วหรือยัง อะไรยังขาด
    python scripts/venue_devices.py find                         # หากล่อง Broadlink ในวงนี้ แล้วพิมพ์ 3 บรรทัดให้ก๊อปลง .env
    python scripts/venue_devices.py learn lobby_lights off       # สอนรหัสรีโมต IR (เล็งรีโมตที่กล่อง)
    python scripts/venue_devices.py learn lobby_curtains open --rf   # รีโมต RF 433/315 MHz (ม่าน)
    python scripts/venue_devices.py send lobby_lights off        # ส่งจริงหนึ่งครั้ง ด้วยทางเดียวกับที่หุ่นใช้ (ถามก่อน)
    python scripts/venue_devices.py check                        # จากเครื่องนี้ติดต่อกล่อง/ระบบควบคุมได้ไหม (ไม่ส่งคำสั่ง)

Run by the owner (or whoever set the venue up), in the lobby, on the SAME Wi-Fi as the robot (owner
20 Nov 2026: "หุ่นอาจจะต้องสั่งปิดไฟ ปิดม่านได้ ... ทำระบบเผื่อไว้"). Learning happens on a PC; the codes
file goes to the robot with the next `robot_server.py deploy`, the hub lines with `secrets`.

What this guarantees, each because a learning session can quietly break it (scripts/learn_ir.py's
rules, for the venue's own file):

- **The sales room's IR codes are never written.** The venue's codes live in VENUE_CODES_FILE; this
  refuses to run when that is the same file as IR_CODES_FILE.
- **Only the code name the devices file gives that action is written**, saved the moment it is
  accepted (temp file + os.replace), the file as it was kept once as `.bak`, its own layout kept.
- **RF is two steps** (python-broadlink's own way): hold the button while the hub sweeps for the
  frequency, let go, then press it ONCE while the hub records the frame. A curtain remote with one
  button that cycles open / stop / close cannot be told apart from here: use a remote with its own
  open, close and stop buttons (or ask the owner which one the motor has).
- `send` asks before it sends (a real light goes off): `--yes` to skip the question.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SKIP = {"3", "ข", "s", "skip", "ข้าม"}
_QUIT = {"q", "ๆ", "quit", "ออก"}


def _learn_ir():
    """scripts/learn_ir.py's capture and atomic save, one copy of them."""
    name = "_venue_learn_ir"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "learn_ir.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod                      # its dataclass looks itself up there while it is built
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return "q"


# ---- pure (tested) -------------------------------------------------------------------------------------

def codes_path(cfg) -> Path | None:
    from app import venue_devices

    return venue_devices._resolve(cfg.codes_file)


def same_as_sales_room(path: Path | None) -> bool:
    """The venue's codes file IS the sales room's IR codes file: refused, never written."""
    from app.config import settings
    from app import venue_devices

    sales = venue_devices._resolve(settings.ir_codes_file)
    if path is None or sales is None:
        return False
    try:
        return path.resolve() == sales.resolve()
    except OSError:
        return str(path) == str(sales)


def save_code(path: Path, key: str, code_hex: str, *, allowed: set[str], make_backup: bool) -> dict:
    """One learned frame into the venue codes file on disk; the new contents back. Only a code name the
    devices file gives (`allowed`); re-read each time so a hand edit made meanwhile is kept."""
    li = _learn_ir()
    if key not in allowed:
        raise ValueError("refusing to write %r: not a code name in the venue devices file" % key)
    if same_as_sales_room(path):
        raise ValueError("refusing to write the sales room's IR codes file (VENUE_CODES_FILE == IR_CODES_FILE)")
    try:
        payload = bytes.fromhex(code_hex)
    except (TypeError, ValueError):
        raise ValueError("%s: not a hex code" % key) from None
    if not payload:
        raise ValueError("%s: empty code" % key)
    raw = path.read_bytes() if path.is_file() else b""
    current = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    if not isinstance(current, dict):
        raise ValueError("%s is not a JSON object" % path)
    merged = dict(current)
    merged[key] = payload.hex()
    if make_backup and raw:
        li.atomic_write(li.backup_path(path), raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    li.atomic_write(path, li.render_codes(merged, like=raw))
    return merged


def capture_rf(device, *, say: Callable[[str], None] = print, ask: Callable[[str], str] = _ask,
               wait_s: float = 20.0, poll_s: float = 1.0, clock: Callable[[], float] = time.monotonic,
               sleep: Callable[[float], None] = time.sleep) -> bytes | None:
    """An RF frame, python-broadlink's two steps: the frequency (button held), then the frame (one press).
    None = nothing found in time (the sweep is cancelled)."""
    device.sweep_frequency()
    say("  กดปุ่มบนรีโมตค้างไว้ ห่างกล่อง ~10 ซม. จนกว่าจะบอกให้ปล่อย (รอได้ %d วินาที)" % int(wait_s))
    deadline, freq = clock() + wait_s, None
    while clock() < deadline:
        sleep(poll_s)
        try:
            found, value = device.check_frequency()
        except Exception:                                # noqa: BLE001 -- nothing yet / a dropped packet
            continue
        if found:
            freq = value
            break
    if freq is None:
        try:
            device.cancel_sweep_frequency()
        except Exception:                                # noqa: BLE001
            pass
        return None
    say("  เจอความถี่ %.2f MHz — ปล่อยปุ่มได้" % freq)
    if ask("  พร้อมแล้วกด Enter แล้วกดปุ่มเดิม 'ครั้งเดียว' สั้นๆ   (q = ออก): ") in _QUIT:
        return None
    try:
        device.find_rf_packet(freq)
    except TypeError:                                    # an older python-broadlink: no frequency argument
        device.find_rf_packet()
    deadline = clock() + wait_s
    while clock() < deadline:
        sleep(poll_s)
        try:
            data = device.check_data()
        except Exception:                                # noqa: BLE001
            continue
        if data:
            return bytes(data)
    return None


# ---- talking to the hub ---------------------------------------------------------------------------------

def connect(cfg, say: Callable[[str], None] = print):
    """The venue hub from .env (VENUE_BROADLINK_*), through the server's own auth path. IP only printed."""
    from app import venue_devices

    if not venue_devices._package():
        say("ยังไม่ได้ลงแพ็กเกจ broadlink: pip install broadlink")
        return None
    if not venue_devices._hub_set(cfg):
        say("ยังไม่ได้ตั้งกล่องใน .env — รัน python scripts/venue_devices.py find แล้วก๊อป 3 บรรทัดลง .env")
        return None
    say("ต่อกล่อง Broadlink ที่ %s ..." % cfg.hub_host)
    hub = venue_devices._hub(cfg)
    if hub is None:
        say("ต่อกล่องไม่ได้ — %s" % venue_devices.route_hint(cfg.hub_host))
        return None
    hub.timeout = 2                                      # learning is interactive: a slower answer is fine
    return hub


def cmd_list(cfg) -> int:
    from app import venue_devices

    print("VENUE_DEVICES=%s%s · ไฟล์ %s · รหัส %s" % (
        "true" if cfg.enabled else "false", " (DRY_RUN = จำลอง)" if cfg.dry_run else "",
        venue_devices._resolve(cfg.devices_file), codes_path(cfg)))
    probs = venue_devices.problems(cfg)
    if probs:
        print("ไฟล์อุปกรณ์มีปัญหา: " + "; ".join(probs))
    codes = venue_devices.load_codes(cfg)
    on = replace(cfg, enabled=True)
    for dev in venue_devices.devices(cfg).values():
        print("\n%s (%s) — %s · adapter %s%s" % (dev.name_th, dev.id, dev.kind, dev.adapter,
                                                 "" if dev.adapter != "manual" else " = ทีมงานทำเอง ไม่อยู่ในลำดับพิธี"))
        for action in venue_devices.KIND_ACTIONS[dev.kind]:
            bits = []
            if action in dev.codes:
                bits.append("รหัส %s: %s" % (dev.codes[action], ("สอนแล้ว %d ไบต์" % (len(codes[dev.codes[action]]) // 2))
                                              if codes.get(dev.codes[action]) else "ยังไม่ได้สอน"))
            if dev.http and action in dev.http["actions"]:
                spec = dev.http["actions"][action]
                bits.append("http %s %s" % (spec["method"], spec["path"] or ("$" + spec["path_env"])))
            why = venue_devices.why_not(dev.id, action, on) if dev.adapter != "manual" else None
            print("  %-6s %s%s" % (action, " · ".join(bits) or "-", ("  ✗ " + why) if why else ""))
    return 0


def cmd_find(say: Callable[[str], None] = print) -> int:
    """Broadlink hubs this PC can see, with the .env lines for each. Printed for a person to copy: the
    script never writes .env (a key is copied by a person -- the inventory key's rule)."""
    try:
        import broadlink
    except ImportError:
        say("ยังไม่ได้ลงแพ็กเกจ broadlink: pip install broadlink")
        return 1
    from app.tools import broadlink_ir

    found = []
    for ip in broadlink_ir._local_ipv4s() or [None]:
        say("สแกนจากวง %s ..." % ip)
        try:
            found.extend(broadlink.discover(timeout=6, local_ip_address=ip))
        except OSError:
            continue
    if not found:
        say("ไม่เจอกล่อง — กล่องเสียบไฟไหม · อยู่ Wi-Fi 2.4 GHz วงเดียวกับเครื่องนี้ไหม · Windows Firewall บัง UDP ไหม")
        return 1
    for dev in found:
        say("\nเจอ %s (%s) ที่ %s" % (getattr(dev, "model", "") or type(dev).__name__, getattr(dev, "manufacturer", ""),
                                      dev.host[0]))
        say("ก๊อปลง .env ของคอมเครื่องนี้ (แล้ว robot_server.py secrets ส่งให้หุ่น):")
        say("VENUE_BROADLINK_HOST=%s" % dev.host[0])
        say("VENUE_BROADLINK_MAC=%s" % dev.mac.hex())
        say("VENUE_BROADLINK_DEVTYPE=0x%04x" % dev.devtype)
    say("\nตั้ง IP คงที่ให้กล่องในเราเตอร์ (DHCP reservation) — ระบบนี้ไม่หากล่องใหม่เองเมื่อ IP เปลี่ยน")
    return 0


def cmd_learn(cfg, device_id: str, action: str, *, rf: bool, wait_s: float,
              ask: Callable[[str], str] = _ask, say: Callable[[str], None] = print, hub=None) -> int:
    from app import venue_devices

    dev = venue_devices.device(device_id, cfg)
    if dev is None:
        say("ไม่มีอุปกรณ์ '%s' — ที่มี: %s" % (device_id, ", ".join(venue_devices.devices(cfg)) or "-"))
        return 2
    key = dev.codes.get(action)
    if not key:
        say("%s ไม่มีชื่อรหัสของคำสั่ง '%s' ในไฟล์อุปกรณ์ (broadlink.codes)" % (dev.name_th, action))
        return 2
    path = codes_path(cfg)
    if path is None:
        say("ไม่ได้ตั้ง VENUE_CODES_FILE")
        return 2
    if same_as_sales_room(path):
        say("VENUE_CODES_FILE ชี้ไปที่ไฟล์รหัสของห้องขาย — ไม่เขียนทับ")
        return 2
    allowed = {code for d in venue_devices.devices(cfg).values() for code in d.codes.values()}
    hub = hub if hub is not None else connect(cfg, say)
    if hub is None:
        return 1
    li = _learn_ir()
    say("\nสอนรหัส %s → %s (%s)" % (dev.name_th, action, "RF" if rf else "IR"))
    backed = False
    while True:
        if rf:
            data = capture_rf(hub, say=say, ask=ask, wait_s=wait_s)
        else:
            if ask("  เล็งรีโมตที่กล่อง ~30 ซม. พร้อมแล้วกด Enter แล้วกดปุ่ม '%s' ครั้งเดียว   (q = ออก): "
                   % venue_devices.ACTION_TH.get(action, action)) in _QUIT:
                return 1
            data = li.capture(hub, wait_s=wait_s)
        if data is None:
            say("  ไม่ได้รับสัญญาณ" + (" — RF: กดค้างจนเจอความถี่ก่อน แล้วค่อยกดครั้งเดียว" if rf else
                                       " — เล็งรีโมตใกล้กล่องขึ้น"))
            if ask("  1 = ลองใหม่   q = ออก : ") in _QUIT:
                return 1
            continue
        say("  ได้รหัสแล้ว ยาว %d ไบต์" % len(data))
        answer = ask("  1 = ใช้   2 = อัดใหม่   4 = ลองส่งให้อุปกรณ์ก่อน   q = ออก : ")
        if answer in {"4", "t"}:
            ok = li.send_once(hub, data)
            say("  ส่งแล้ว — %s ไหม? (ดูเอง: สัญญาณทางเดียว)" % venue_devices.SEEN_TH.get((dev.kind, action), "อุปกรณ์เปลี่ยน")
                if ok else "  ส่งไม่ออก (กล่องไม่ตอบ)")
            answer = ask("  1 = ใช้รหัสนี้   2 = อัดใหม่   q = ออก : ")
        if answer in _QUIT:
            return 1
        if answer in {"1", "u", "y", "ใช้"}:
            save_code(path, key, data.hex(), allowed=allowed, make_backup=not backed)
            say("  บันทึก %s ลง %s แล้ว — deploy เพื่อส่งให้หุ่น" % (key, path))
            return 0


def cmd_send(cfg, device_id: str, action: str, *, yes: bool, ask: Callable[[str], str] = _ask,
             say: Callable[[str], None] = print) -> int:
    """One real command, through the server's own path (app/venue_devices.run), switch forced on for this
    run only -- nothing written to .env."""
    from app import venue_devices

    on = replace(cfg, enabled=True)
    why = venue_devices.why_not(device_id, action, on)
    if why:
        say("ส่งไม่ได้: %s" % why)
        return 2
    if not yes and ask("ส่ง '%s' จริงตอนนี้? พิมพ์ y : " % venue_devices.step_label(device_id, action)) not in {"y", "yes", "ใช่"}:
        say("ไม่ได้ส่ง")
        return 1
    out = asyncio.run(venue_devices.run(device_id, action, by="script", cfg=on))
    say("hardware=%s ok=%s%s%s" % (out["hardware"], out["ok"], (" — " + out["error_th"]) if out.get("error_th") else "",
                                   (" · state=%s" % out["state"]) if out.get("state") else ""))
    if out["hardware"] == "sent":
        say("ส่งแล้วไม่ได้แปลว่า%sแล้ว — ดูห้องเอง" % venue_devices.seen_th(device_id, action))
    return 0 if out["ok"] else 1


def cmd_check(cfg, say: Callable[[str], None] = print) -> int:
    """From THIS machine: each device's hub / controller reached? Nothing sent. The robot's own answer
    (from its Wi-Fi) is the phone's morning card, or event_preflight.py --serial."""
    from app import venue_devices

    on = replace(cfg, enabled=True)
    used = [d for d in venue_devices.devices(on).values() if d.adapter != "manual"]
    if not used:
        say("ไม่มีอุปกรณ์ที่ต่อกับหุ่น (ทุกตัว adapter manual)")
        return 0
    bad = 0
    for dev in used:
        ok, detail = venue_devices.probe(dev, on)
        bad += not ok
        say("%s %s (%s): %s" % ("OK" if ok else "NO", dev.name_th, dev.adapter, detail))
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("find")
    sub.add_parser("check")
    p = sub.add_parser("learn")
    p.add_argument("device")
    p.add_argument("action")
    p.add_argument("--rf", action="store_true", help="รีโมต RF 433/315 MHz (ไม่ใช่ IR)")
    p.add_argument("--wait", type=float, default=20.0)
    p = sub.add_parser("send")
    p.add_argument("device")
    p.add_argument("action")
    p.add_argument("--yes", action="store_true", help="ไม่ต้องถามก่อนส่ง")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    from app import venue_devices

    cfg = venue_devices.Config.from_settings()
    if args.cmd == "list":
        return cmd_list(cfg)
    if args.cmd == "find":
        return cmd_find()
    if args.cmd == "check":
        return cmd_check(cfg)
    if args.cmd == "learn":
        return cmd_learn(cfg, args.device, args.action, rf=args.rf, wait_s=args.wait)
    return cmd_send(cfg, args.device, args.action, yes=args.yes)


if __name__ == "__main__":
    raise SystemExit(main())
