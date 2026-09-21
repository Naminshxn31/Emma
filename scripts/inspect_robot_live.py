"""Read-only robot inventory. GET requests and optional ADB read commands only.

No connection setup, motor commands, firmware changes, microphone recording,
map edits, or credential collection. A report is evidence of observations,
not approval for motion. Use --save-map to back up the existing composite map.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.parse
import urllib.request


ENDPOINTS = {
    "info": "/api/core/system/v1/robot/info",
    "power": "/api/core/system/v1/power/status",
    "health": "/api/core/system/v1/robot/health",
    "pose": "/api/core/slam/v1/localization/pose",
    "localization_quality": "/api/core/slam/v1/localization/quality",
    "mapping_enabled": "/api/core/slam/v1/mapping/:enable",
    "pois": "/api/core/artifact/v1/pois",
    "floor_pois": "/api/multi-floor/map/v1/pois",
    "current_action": "/api/core/motion/v1/actions/:current",
    "action_factories": "/api/core/motion/v1/action-factories",
}
ADB_READS = {
    "android": "getprop ro.build.version.release",
    "model": "getprop ro.product.model",
    "abi": "getprop ro.product.cpu.abi",
    "memory": "head -n 3 /proc/meminfo",
    "storage": "df -h /data",
    "sound_cards": "cat /proc/asound/cards",
    "usb_audio": "cat /proc/asound/card0/stream0",
    "serial_ports": "ls -l /dev/ttyUSB* /dev/ttyACM* /dev/ttyS*",
    "sdk_files": "find /sdcard/SysQS/sdk -maxdepth 4 -type f",
    "vendor_version": 'dumpsys package com.aobo.robot.ai3 | grep -E "versionName|versionCode"',
}


def get_bytes(base: str, path: str) -> bytes:
    request = urllib.request.Request(base.rstrip("/") + path, method="GET")
    # The ADB tunnel is local; do not send robot data through system proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=8) as response:
        return response.read()


def collect(base: str, adb: Path | None = None, serial: str | None = None) -> dict:
    report = {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only",
        "motion_tested": False,
        "physical_stop_verified": False,
        "chassis": {},
        "android": {},
    }
    for name, path in ENDPOINTS.items():
        try:
            report["chassis"][name] = {"ok": True, "value": json.loads(get_bytes(base, path))}
        except urllib.error.HTTPError as error:
            # 404 means no action only for this endpoint, not a disconnected robot.
            empty = name == "current_action" and error.code == 404
            report["chassis"][name] = {"ok": empty, "http_status": error.code, "value": None}
        except Exception as error:
            report["chassis"][name] = {"ok": False, "error_type": type(error).__name__}
    if adb is not None and serial:
        for name, command in ADB_READS.items():
            try:
                result = subprocess.run(
                    [str(adb.resolve()), "-s", serial, "shell", command],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=12, check=False,
                )
                report["android"][name] = {
                    "ok": result.returncode == 0,
                    "output": result.stdout.strip(), "stderr": result.stderr.strip(),
                }
            except Exception as error:
                report["android"][name] = {"ok": False, "error_type": type(error).__name__}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:11448")
    parser.add_argument("--adb", type=Path)
    parser.add_argument("--serial")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--save-map", action="store_true")
    args = parser.parse_args()
    address = urllib.parse.urlsplit(args.url)
    if address.scheme not in {"http", "https"} or not address.hostname or address.username or address.password or address.query or address.fragment or address.path not in {"", "/"}:
        parser.error("--url must be an HTTP(S) origin without credentials, path, query or fragment")
    if bool(args.adb) != bool(args.serial):
        parser.error("--adb and --serial must be supplied together")
    report = collect(args.url, args.adb, args.serial)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.save_map:
        try:
            data = get_bytes(args.url, "/api/core/slam/v1/maps/stcm")
            if not data:
                raise ValueError("empty map")
            target = args.output.with_suffix(".stcm")
            # Never overwrite an earlier backup.
            with target.open("xb") as stream:
                stream.write(data)
            report["map_backup"] = {"ok": True, "file": target.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        except Exception as error:
            report["map_backup"] = {"ok": False, "error_type": type(error).__name__}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    passed = all(item["ok"] for item in report["chassis"].values())
    if args.save_map:
        passed = passed and report["map_backup"]["ok"]
    print(json.dumps({"report": str(args.output), "chassis_reads_passed": passed,
                      "motion_tested": False, "physical_stop_verified": False}))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
