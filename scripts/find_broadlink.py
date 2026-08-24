#!/usr/bin/env python3
"""
Find the Broadlink hub on THIS network and point the config at it.

    python scripts/find_broadlink.py            # scan + report
    python scripts/find_broadlink.py --save     # scan + update the device file

Written the day both the lights and the AC "failed" at once: the device
file remembered a hub at 192.168.0.2 that a full scan proved absent from
the network entirely — the hub was somewhere else (or unplugged), and no
amount of retrying reaches hardware that isn't there. When the hub comes
back (or a new one is set up), run this with --save and the tools work on
the next command, no restart.

If the hub is plugged in but this still finds nothing:
- Broadlink hubs are 2.4GHz-only — it must be on the same LAN as this PC.
- Windows Firewall can eat the UDP broadcast replies; allow python through
  or try once with the firewall briefly off.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true",
                    help="เขียนตัวที่เจอลง BROADLINK_DEVICE_FILE")
    args = ap.parse_args()

    import broadlink

    from app.config import settings

    device_file = Path(settings.broadlink_device_file)
    old = None
    if device_file.exists():
        old = json.loads(device_file.read_text(encoding="utf-8"))
        print(f"config เดิม: {old.get('host')} (mac {old.get('mac')})")

    import socket

    locals_ = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in locals_:
                locals_.append(ip)
    except OSError:
        pass
    found = []
    for ip in locals_ or [None]:
        print(f"สแกนจากวง {ip} ...")
        try:
            found.extend(broadlink.discover(timeout=6, local_ip_address=ip))
        except OSError:
            continue
    # de-dup by mac
    seen = set()
    found = [d for d in found if not (d.mac.hex() in seen or seen.add(d.mac.hex()))]
    if not found:
        print("ไม่พบอุปกรณ์ Broadlink บนเครือข่ายนี้")
        print("เช็ค: กล่องเสียบไฟไหม / อยู่ WiFi วงเดียวกับคอมไหม (2.4GHz) / firewall")
        return 1

    for d in found:
        mark = " <= ตัวเดิมใน config" if old and d.mac.hex() == old.get("mac") else ""
        print(f"พบ: {d.host[0]}  mac={d.mac.hex()}  devtype={hex(d.devtype)}{mark}")

    if args.save:
        d = found[0]
        payload = {"host": d.host[0], "mac": d.mac.hex(), "devtype": d.devtype}
        device_file.write_text(json.dumps(payload), encoding="utf-8")
        print(f"บันทึกแล้ว -> {device_file}")
        print("ใช้ได้เลยคำสั่งถัดไป ไม่ต้องรีสตาร์ตเซิร์ฟเวอร์")
    return 0


if __name__ == "__main__":
    sys.exit(main())
