"""Inspect one vendor AAR/APK without extracting or executing anything.

Structure checks are not signature verification or hardware certification.
Optional --expected-sha256 must come from a trusted vendor channel.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

MAX_JAR = 64 * 1024 * 1024


def inspect_package(path: Path, expected_sha256: str | None = None) -> dict:
    result = {"name": path.name, "kind": path.suffix.lower().lstrip("."),
              "structure_ok": False, "signature_verified": False,
              "hardware_compatibility": "NOT_VERIFIED", "issues": []}
    if not path.is_file():
        result["issues"].append("file_missing")
        return result
    if result["kind"] not in {"aar", "apk"}:
        result["issues"].append("expected_aar_or_apk")
        return result
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    result["sha256"] = digest
    if expected_sha256 is not None:
        matched = bool(re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256)) and digest == expected_sha256.lower()
        result["vendor_checksum_matches"] = matched
        if not matched:
            result["issues"].append("checksum_mismatch_or_invalid")
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = {item.filename for item in infos}
            if len(names) != len(infos):
                result["issues"].append("duplicate_zip_entries")
            if any(name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name)
                   or ".." in name.replace("\\", "/").split("/") for name in names):
                result["issues"].append("unsafe_archive_paths")
            if "AndroidManifest.xml" not in names or archive.getinfo("AndroidManifest.xml").file_size == 0:
                result["issues"].append("manifest_missing_or_empty")
            prefix = "jni" if result["kind"] == "aar" else "lib"
            result["native_abis"] = sorted({name.split("/")[1] for name in names
                                            if name.startswith(prefix + "/") and name.endswith(".so") and len(name.split("/")) >= 3})
            if result["kind"] == "apk":
                result["dex_files"] = sorted(name for name in names if re.fullmatch(r"classes\d*\.dex", name))
                if not result["dex_files"]:
                    result["issues"].append("dex_missing_not_standalone_demo")
                elif any(archive.getinfo(name).file_size == 0 for name in result["dex_files"]):
                    result["issues"].append("empty_dex")
            else:
                classes = set()
                jars = sorted(name for name in names if name == "classes.jar" or (name.startswith("libs/") and name.endswith(".jar")))
                if not jars:
                    result["issues"].append("sdk_jar_missing")
                for name in jars:
                    if archive.getinfo(name).file_size > MAX_JAR:
                        result["issues"].append("jar_exceeds_inspection_limit")
                        continue
                    with zipfile.ZipFile(io.BytesIO(archive.read(name))) as jar:
                        classes.update(jar.namelist())
                result["documented_classes_found"] = {
                    key: any(name.rsplit("/", 1)[-1] == key + ".class" for name in classes)
                    for key in ("AoboRobotManager", "AoboRobotListener", "SdkMode")}
                if not result["documented_classes_found"]["AoboRobotManager"]:
                    result["issues"].append("documented_manager_not_found_confirm_sdk_with_vendor")
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
        result["issues"].append("unreadable_archive")
    result["structure_ok"] = not result["issues"]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    try:
        result = inspect_package(args.path, args.expected_sha256)
    except OSError as error:
        result = {"structure_ok": False, "error_type": type(error).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["structure_ok"] else 2)
