"""Read-only local preflight. Never opens microphones or sends device commands.

Run with --health to also GET local health endpoints (strict TLS verification).
Exit 0: configured prerequisites pass; exit 2: missing prerequisite. Neither
result certifies a physical robot. Vendor SDK/APK paths must be supplied.
"""
from __future__ import annotations

import argparse
import json
import shutil
import ssl
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def health(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            value = json.load(response)
        if not isinstance(value, dict) or value.get("ok") is not True:
            return {"reachable": True, "ok": False}
        robot = value.get("robot", {})
        return {"reachable": True, "ok": True,
                "robot_connected": isinstance(robot, dict) and robot.get("app_connected") is True}
    except Exception as error:
        # Exception messages/health bodies may contain private paths or credentials.
        return {"reachable": False, "ok": False, "error_type": type(error).__name__}


def report(sdk: Path | None = None, apk: Path | None = None, probe: bool = False) -> dict:
    from app.config import settings
    from app.tools import broadlink_ir
    from app import wake

    groups = settings.enabled_tool_groups()
    cert, key = settings.ssl_certfile.strip(), settings.ssl_keyfile.strip()
    tls_valid = False
    if cert and key:
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            tls_valid = True
        except (OSError, ssl.SSLError):
            pass
    checks = {
        "provider_key_configured": bool(settings.api_key_for(settings.provider)),
        "ws_token_configured": bool(settings.ws_token),
        "robot_token_configured": bool(settings.robot_token),
        "robot_enabled": settings.robot_enabled,
        "robot_tools_enabled": groups is None or "robot" in groups,
        "single_session": not settings.multi_session,
        "tls_certificate_and_key_loadable": tls_valid,
        "sdk_aar_supplied": bool(sdk and sdk.suffix.lower() == ".aar" and sdk.is_file()),
        "bridge_apk_supplied": bool(apk and apk.suffix.lower() == ".apk" and apk.is_file()),
    }
    result = {
        "python": sys.version.split()[0], "provider": settings.provider,
        "production_port": settings.port, "checks": checks,
        "missing_prerequisites": [name for name, passed in checks.items() if not passed],
        "android_tools_in_path": {name: shutil.which(name) is not None for name in ("adb", "java", "gradle")},
        "local_adb_available": (ROOT / "tools/android/platform-tools/adb.exe").is_file(),
        "wake": {"enabled": settings.wake_enabled, "model_available": wake.available()},
        "ir_configuration": broadlink_ir.status(),
        "hardware_acceptance": "NOT_RUN",
        "limits": "File existence is not SDK/APK compatibility. No microphone, navigation, stop, charging or IR tested.",
    }
    if probe:
        scheme = "https" if cert and key else "http"
        result["health"] = {
            "production": health(f"{scheme}://localhost:{settings.port}/health"),
        }
        production = result["health"]["production"]
        if not production.get("ok"):
            result["missing_prerequisites"].append("production_health")
        if not production.get("robot_connected"):
            result["missing_prerequisites"].append("live_robot_connection")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, help="Vendor AAR file (existence check only)")
    parser.add_argument("--apk", type=Path, help="Bridge APK file (existence check only)")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    result = report(args.sdk, args.apk, args.health)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(2 if result["missing_prerequisites"] else 0)
