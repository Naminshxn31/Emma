#!/usr/bin/env python3
"""
สอนรหัสรีโมตของแอร์ตัวที่เพิ่ม (เช่น Samsung) ให้กล่อง Broadlink ทีละปุ่ม

    python scripts/learn_ir.py --unit samsung                 # สอนรหัสที่ยังไม่มี
    python scripts/learn_ir.py --unit samsung --list          # ดูว่าสอนอะไรไปแล้ว
    python scripts/learn_ir.py --unit samsung --only temp_24  # อัดใหม่เฉพาะตัว (คั่นด้วย ,)
    python scripts/learn_ir.py --unit samsung --redo          # อัดใหม่ทั้งหมด

Run by the owner, in the room, with the remote in hand (owner, 2026-10-09:
"เพิ่มแอร์ ... samsung" / "เพิ่มเป็นตัวที่สอง"). The unit and its code prefix
come from IR_DEVICES_FILE (data/ir_devices.json); the codes go into
IR_CODES_FILE next to the original AC's, under that prefix.

What this script guarantees, because each is what a learning session can
quietly break:

- **The original AC's codes are never written.** `ac_*` and `light_*` keys
  are refused at the merge, checked again before every write, and the
  original unit cannot be chosen at all. They are the sales room's codes.
- **Every code is saved the moment it is accepted**, atomically (temp file
  + os.replace), so an interrupted session loses at most the press in
  progress, and the file on disk is always whole JSON. Before the first
  write of a session the file as it was is kept as `<codes>.bak`. The file
  keeps its own layout (indent, CRLF, no final newline), so the git diff of
  a session is the new keys and nothing else.
- **It resumes.** By default only codes not yet learned are walked;
  `--only` re-learns named ones, `--redo` everything.
- **The MAC is never printed** — only the hub's IP.

Why the instructions say "set the remote one step BEFORE, then press once":
a Samsung remote sends its whole state (mode, fan, temperature, power) with
every press, and the hub keeps only the FIRST frame after learning starts.
So "press ▼ until it shows 16" would store whatever the first press showed.
The state the remote must show is `learned_with` in the devices file — the
same values smarthome.py assumes each code carries, so what the tool reports
after sending a code is what the code actually sets.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: Never written by this script: the original AC and the lamp.
PROTECTED_PREFIXES = ("ac_", "light_")

MODE_TH = {"cool": "เย็น (Cool)", "dry": "แห้ง/ลดความชื้น (Dry)", "fan": "พัดลม (Fan)",
           "heat": "ร้อน (Heat)", "auto": "อัตโนมัติ (Auto)"}
FAN_TH = {"auto": "อัตโนมัติ (Auto)", "1": "เบา (Low)", "2": "กลาง (Med)", "3": "แรง (High)"}

#: Typed answers. Numbers work on any keyboard layout; "ๆ" is q on Thai.
_SKIP = {"3", "ข", "s", "skip", "ข้าม"}
_QUIT = {"q", "ๆ", "quit", "ออก"}


# ---- pure: what to learn and how to say it ----


@dataclass
class Step:
    suffix: str          # e.g. "temp_24" — the same name smarthome.py sends
    key: str             # prefix + suffix, e.g. "samsung_ac_temp_24"
    title: str
    prep: str            # what the remote must show before learning starts
    press: str           # the ONE press to make after "กดเลย"


def _shows(mode: str, fan: str, temp: int) -> str:
    return f"โหมด {MODE_TH[mode]} · พัดลม {FAN_TH[fan]} · {temp} องศา"


def plan(unit) -> list[Step]:
    """Every code of `unit`, in the order the owner presses them: power off,
    power on, modes, temperatures low to high, fan speeds."""
    from app.tools.smarthome import AC_TEMP_MIN, unit_code_suffixes

    base = unit.learned_with
    bm, bf, bt = base["mode"], base["fan"], base["temp"]
    steps: list[Step] = []
    for suffix in unit_code_suffixes():
        if suffix == "off":
            title = "ปิดแอร์ (Power off)"
            prep = f"แอร์เปิดอยู่ จอรีโมตขึ้น {_shows(bm, bf, bt)}"
            press = "กดปุ่ม Power หนึ่งครั้ง (แอร์ปิด)"
        elif suffix == "on":
            title = "เปิดแอร์ (Power on)"
            prep = f"แอร์ปิดอยู่ และจอรีโมตยังตั้งไว้ที่ {_shows(bm, bf, bt)}"
            press = "กดปุ่ม Power หนึ่งครั้ง (แอร์เปิด)"
        elif suffix.startswith("mode_"):
            m = suffix.removeprefix("mode_")
            title = f"โหมด {MODE_TH[m]}"
            prep = (f"แอร์เปิดอยู่ พัดลม {FAN_TH[bf]} · {bt} องศา แล้วกดปุ่ม Mode ทีละครั้ง"
                    f" จนจอขึ้นโหมดที่อยู่ 'ก่อน' {MODE_TH[m]} หนึ่งขั้น")
            press = (f"กดปุ่ม Mode หนึ่งครั้ง ให้จอขึ้น {MODE_TH[m]}"
                     " (ถ้ารีโมตมีปุ่มโหมดนี้แยก กดปุ่มนั้นได้เลย)")
        elif suffix.startswith("temp_"):
            n = int(suffix.removeprefix("temp_"))
            title = f"อุณหภูมิ {n} องศา"
            if n == AC_TEMP_MIN:
                prep = f"แอร์เปิดอยู่ จอรีโมตขึ้น {_shows(bm, bf, n + 1)}"
                press = f"กดปุ่มลดอุณหภูมิ (▼) หนึ่งครั้ง ให้จอขึ้น {n}"
            else:
                prep = f"แอร์เปิดอยู่ จอรีโมตขึ้น {_shows(bm, bf, n - 1)}"
                press = f"กดปุ่มเพิ่มอุณหภูมิ (▲) หนึ่งครั้ง ให้จอขึ้น {n}"
        else:
            f = suffix.removeprefix("fan_")
            title = f"พัดลม {FAN_TH[f]}"
            prep = (f"แอร์เปิดอยู่ โหมด {MODE_TH[bm]} · {bt} องศา แล้วกดปุ่ม Fan ทีละครั้ง"
                    f" จนจอขึ้นระดับที่อยู่ 'ก่อน' {FAN_TH[f]} หนึ่งขั้น")
            press = f"กดปุ่ม Fan หนึ่งครั้ง ให้จอขึ้นพัดลม {FAN_TH[f]}"
        steps.append(Step(suffix, unit.prefix + suffix, title, prep, press))
    return steps


def select_steps(steps: list[Step], codes: dict, *, only: str = "",
                 redo: bool = False) -> list[Step]:
    """`--only` names (with or without the prefix) exactly; otherwise every
    step with `--redo`, else just the ones not on this machine yet."""
    if only.strip():
        by_name = {s.suffix: s for s in steps}
        by_name.update({s.key: s for s in steps})
        wanted, unknown = [], []
        for token in (t.strip() for t in only.split(",")):
            if not token:
                continue
            (wanted if token in by_name else unknown).append(token)
        if unknown:
            raise ValueError("ไม่รู้จัก: %s — ชื่อที่ใช้ได้: %s" % (
                ", ".join(unknown), ", ".join(s.suffix for s in steps)))
        chosen = {by_name[t].suffix for t in wanted}
        return [s for s in steps if s.suffix in chosen]
    if redo:
        return list(steps)
    return [s for s in steps if not codes.get(s.key)]


def protected_view(codes: dict) -> dict:
    return {k: v for k, v in codes.items() if k.startswith(PROTECTED_PREFIXES)}


def merge_code(codes: dict, key: str, code_hex: str, prefix: str) -> dict:
    """A copy of `codes` with one learned code added or replaced.

    Refuses anything that is not this unit's own key — above all the
    original AC's `ac_*` and the lamp's `light_*`.
    """
    if (not prefix or prefix.startswith(PROTECTED_PREFIXES)
            or not key.startswith(prefix) or key.startswith(PROTECTED_PREFIXES)):
        raise ValueError(f"refusing to write {key!r}: not a key of unit prefix {prefix!r}")
    try:
        payload = bytes.fromhex(code_hex)
    except (TypeError, ValueError):
        raise ValueError(f"{key}: not a hex IR code") from None
    if not payload:
        raise ValueError(f"{key}: empty IR code")
    merged = dict(codes)
    merged[key] = payload.hex()
    return merged


def render_codes(codes: dict, like: bytes = b"") -> bytes:
    """JSON in the layout the existing file already has — indent 2, its own
    line ending, its own final newline or lack of one — so a learning
    session's diff is the added keys only."""
    newline = "\r\n" if b"\r\n" in like else "\n"
    text = json.dumps(codes, indent=2, ensure_ascii=False).replace("\n", newline)
    if like.endswith(b"\n") or not like:
        text += newline
    return text.encode("utf-8")


def atomic_write(path: Path, data: bytes) -> None:
    """All or nothing: the old file stays whole until the new one is."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def save_code(path: Path, key: str, code_hex: str, prefix: str, *,
              make_backup: bool) -> dict:
    """Add one code to the codes file on disk and return the new contents.

    Re-reads the file each time instead of trusting what this session read
    at the start, so a hand edit made meanwhile is kept, not overwritten.
    """
    raw = path.read_bytes() if path.is_file() else b""
    current = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    if not isinstance(current, dict):
        raise ValueError(f"{path} is not a JSON object")
    merged = merge_code(current, key, code_hex, prefix)
    # Second line, independent of merge_code: the original AC's and the
    # lamp's codes leave this function byte-for-byte as they came in.
    if protected_view(merged) != protected_view(current):
        raise RuntimeError("refusing to save: the ac_*/light_* codes would change")
    if make_backup and raw:
        atomic_write(backup_path(path), raw)
    atomic_write(path, render_codes(merged, like=raw))
    return merged


def status_lines(unit, steps: list[Step], codes: dict) -> list[str]:
    from app.tools.smarthome import is_learned

    have = [s for s in steps if codes.get(s.key)]
    lines = [f"{unit.name_th} ({unit.id}) — สอนแล้ว {len(have)}/{len(steps)} รหัส"]
    for s in steps:
        code = codes.get(s.key)
        lines.append(f"  {'OK' if code else '--'}  {s.suffix:<10} "
                     + (f"{len(code) // 2} ไบต์" if code else "ยังไม่มี"))
    lines.append("Emma เห็นแอร์ตัวนี้แล้ว (มีรหัสเปิดและปิด)" if is_learned(unit, codes)
                 else "Emma ยังไม่เห็นแอร์ตัวนี้ — ต้องมีรหัส on และ off ก่อน")
    return lines


# ---- talking to the hub ----


def capture(device, *, wait_s: float = 20.0, poll_s: float = 1.0,
            clock: Callable[[], float] = time.monotonic,
            sleep: Callable[[float], None] = time.sleep) -> bytes | None:
    """Put the hub in learning mode and wait for one frame.

    `check_data` raises until something has been captured (ReadError /
    StorageError in python-broadlink) and the gallery link drops packets,
    so every error inside the wait is "nothing yet", not a failure.
    """
    for attempt in range(3):
        try:
            device.enter_learning()
            break
        except Exception:
            if attempt == 2:
                raise
            sleep(0.5)
    deadline = clock() + wait_s
    while clock() < deadline:
        sleep(poll_s)
        try:
            data = device.check_data()
        except Exception:
            continue
        if data:
            return bytes(data)
    return None


def send_once(device, data: bytes) -> bool:
    for attempt in range(2):
        try:
            device.send_data(data)
            return True
        except Exception:
            time.sleep(0.4)
    return False


def connect(say: Callable[[str], None] = print):
    """The hub from BROADLINK_DEVICE_FILE, through the same auth/retry/
    rediscover-by-MAC path the server uses. Prints the IP only."""
    from app.tools import broadlink_ir

    if not broadlink_ir.package_installed():
        say("ยังไม่ได้ลงแพ็กเกจ broadlink: pip install broadlink")
        return None
    path = broadlink_ir._device_path()
    if not path.is_file():
        say(f"ไม่พบ {path} — รัน python scripts/find_broadlink.py --save ก่อน")
        return None
    try:
        host = json.loads(path.read_text(encoding="utf-8")).get("host")
    except Exception:
        host = None
    say(f"ต่อกล่อง Broadlink ที่ {host} ...")
    device = broadlink_ir._get_device()
    if device is None:
        say("ต่อกล่องไม่ได้ — เช็คว่ากล่องเสียบไฟและอยู่วงเดียวกับคอม "
            "แล้วลอง python scripts/find_broadlink.py --save")
        return None
    device.timeout = 2      # learning is interactive; a dropped packet may retry a bit longer
    return device


# ---- the interactive walk ----


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return "q"


def learn_step(device, step: Step, save: Callable[[str, str], None], *,
               ask: Callable[[str], str] = _ask, say: Callable[[str], None] = print,
               wait_s: float = 20.0, capture_fn=capture, send_fn=send_once) -> str:
    """One code: prepare, capture, [use / redo / skip / test-send]. Returns
    "saved", "skipped" or "quit"."""
    say(f"  เตรียม: {step.prep}")
    answer = ask("  พร้อมแล้วกด Enter   (3 = ข้าม, q = ออก): ")
    if answer in _QUIT:
        return "quit"
    if answer in _SKIP:
        return "skipped"
    while True:
        say(f"  กดเลย: {step.press}   (รอได้ {int(wait_s)} วินาที)")
        data = capture_fn(device, wait_s=wait_s)
        if data is None:
            say("  ไม่ได้รับสัญญาณ — เล็งรีโมตไปที่กล่องให้ใกล้ขึ้น (~30 ซม.) แล้วลองอีกครั้ง")
            answer = ask("  1 = ลองใหม่   3 = ข้าม   q = ออก : ")
            if answer in _QUIT:
                return "quit"
            if answer in _SKIP:
                return "skipped"
            continue
        say(f"  ได้รหัสแล้ว ยาว {len(data)} ไบต์")
        if len(data) < 20:
            say("  (สั้นผิดปกติ อาจรับสัญญาณไม่ครบ — แนะนำอัดใหม่)")
        while True:
            answer = ask("  1 = ใช้   2 = อัดใหม่   3 = ข้าม   4 = ลองส่งให้แอร์ก่อน : ")
            if answer in {"1", "ใช้", "u", "y"}:
                save(step.key, data.hex())
                say("  บันทึกแล้ว")
                return "saved"
            if answer in {"2", "อัดใหม่", "r"}:
                break
            if answer in _SKIP:
                return "skipped"
            if answer in _QUIT:
                return "quit"
            if answer in {"4", "ลอง", "t"}:
                if not send_fn(device, data):
                    say("  ส่งไม่ออก (กล่องไม่ตอบ) — ลองอีกครั้งได้")
                    continue
                heard = ask("  แอร์ตอบรับ/ติ๊ดไหม?  1 = ติ๊ด/ทำตาม   2 = ไม่ตอบ : ")
                say("  ดี — กด 1 เพื่อใช้รหัสนี้" if heard in {"1", "ใช่", "y"}
                    else "  ลองอัดใหม่ (2) โดยเล็งรีโมตใกล้กล่องขึ้น หรือหันกล่องหาแอร์")


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _find_unit(unit_id: str, say: Callable[[str], None]):
    from app.tools.smarthome import configured_units

    units = configured_units()
    for unit in units:
        if unit.id == unit_id.strip().lower():
            if unit.original:
                say(f"{unit.name_th} ใช้รหัส ac_* เดิม — สคริปต์นี้ไม่เขียนทับรหัสชุดนั้น")
                return None
            return unit
    extra = [u.id for u in units if not u.original]
    say(f"ไม่มีแอร์ id '{unit_id}' ในไฟล์อุปกรณ์ — ที่มี: {', '.join(extra) or '(ไม่มีตัวที่เพิ่ม)'}")
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="สอนรหัสรีโมตแอร์ตัวที่เพิ่ม ให้กล่อง Broadlink")
    ap.add_argument("--unit", required=True, help="id ของแอร์ใน data/ir_devices.json เช่น samsung")
    ap.add_argument("--list", action="store_true", help="แสดงรหัสที่สอนแล้ว/ยังไม่มี")
    ap.add_argument("--only", default="", help="เฉพาะรหัสเหล่านี้ คั่นด้วย , เช่น temp_24,fan_3")
    ap.add_argument("--redo", action="store_true", help="อัดใหม่ทุกรหัส (ปกติข้ามที่สอนแล้ว)")
    ap.add_argument("--wait", type=float, default=20.0, help="วินาทีที่รอการกดแต่ละครั้ง")
    args = ap.parse_args(argv)
    _utf8_console()

    from app.config import settings

    unit = _find_unit(args.unit, print)
    if unit is None:
        return 2
    codes_path = Path(settings.ir_codes_file).expanduser()
    codes = json.loads(codes_path.read_text(encoding="utf-8")) if codes_path.is_file() else {}
    steps = plan(unit)
    if args.list:
        print("\n".join(status_lines(unit, steps, codes)))
        return 0
    try:
        todo = select_steps(steps, codes, only=args.only, redo=args.redo)
    except ValueError as exc:
        print(exc)
        return 2
    if not todo:
        print("\n".join(status_lines(unit, steps, codes)))
        print("สอนครบแล้ว — อัดใหม่เฉพาะตัวได้ด้วย --only เช่น --only temp_24")
        return 0

    device = connect()
    if device is None:
        return 1

    print()
    print(f"สอนรหัสรีโมต: {unit.name_th} ({unit.id}) — {len(todo)} รหัส")
    print("ทุกขั้น: 1) ตั้งรีโมตตาม 'เตรียม' (แอร์จะทำตามก็ไม่เป็นไร)  2) กด Enter")
    print("        3) พอขึ้น 'กดเลย' เล็งรีโมตไปที่กล่อง Broadlink แล้วกดปุ่ม 'ครั้งเดียว'")
    print("กล่องจำแค่การกดครั้งแรก และรีโมตนี้ส่งสถานะทั้งชุดทุกครั้ง")
    print("จึงต้องให้จอรีโมตขึ้นตาม 'เตรียม' ก่อนทุกครั้ง")
    print("บันทึกทันทีทีละรหัส ออกได้ทุกเมื่อ (q) แล้วสั่งคำเดิมเพื่อทำต่อ")

    saved, skipped = [], []
    first_save = [True]

    def _save(key: str, code_hex: str) -> None:
        save_code(codes_path, key, code_hex, unit.prefix, make_backup=first_save[0])
        if first_save[0]:
            print(f"  (สำรองไฟล์เดิมไว้ที่ {backup_path(codes_path).name})")
        first_save[0] = False

    try:
        for i, step in enumerate(todo, 1):
            print(f"\n[{i}/{len(todo)}] {step.title}   ({step.suffix})")
            result = learn_step(device, step, _save, wait_s=args.wait)
            if result == "quit":
                break
            (saved if result == "saved" else skipped).append(step.suffix)
    except KeyboardInterrupt:
        print("\nหยุดแล้ว — รหัสที่บันทึกไปแล้วยังอยู่")

    codes = json.loads(codes_path.read_text(encoding="utf-8")) if codes_path.is_file() else {}
    print()
    print(f"บันทึก {len(saved)} รหัส" + (f", ข้าม {len(skipped)}: {', '.join(skipped)}" if skipped else ""))
    print("\n".join(status_lines(unit, steps, codes)))
    if any(not codes.get(s.key) for s in steps):
        print(f"ทำต่อ: python scripts/learn_ir.py --unit {unit.id}")
    print("ใช้งาน: เริ่มสายใหม่กับ Emma แล้วลองพูด 'เปิดแอร์ Samsung'")
    return 0


if __name__ == "__main__":
    # Relative paths in .env (data/ir_codes.json, ...) mean what they mean to
    # the server, which runs from the repo root — wherever this was started.
    os.chdir(ROOT)
    sys.exit(main())
