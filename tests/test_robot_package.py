"""Synthetic ZIP fixtures exercise inspection; these are not vendor SDK/APKs."""
import hashlib
import io
import zipfile

import pytest

from scripts.inspect_robot_package import inspect_package


def package(tmp_path, suffix, entries):
    path = tmp_path / ("synthetic" + suffix)
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return path


def test_aar_reports_classes_abis_without_extracting(tmp_path):
    jar = io.BytesIO()
    with zipfile.ZipFile(jar, "w") as archive:
        for name in ("AoboRobotManager", "AoboRobotListener", "SdkMode"):
            archive.writestr("example/" + name + ".class", b"fixture only")
    path = package(tmp_path, ".aar", {"AndroidManifest.xml": b"manifest fixture",
                    "classes.jar": jar.getvalue(), "jni/arm64-v8a/libfixture.so": b"fixture"})
    before = set(tmp_path.iterdir())
    result = inspect_package(path)
    assert result["structure_ok"]
    assert all(result["documented_classes_found"].values())
    assert result["native_abis"] == ["arm64-v8a"]
    assert result["signature_verified"] is False
    assert result["hardware_compatibility"] == "NOT_VERIFIED"
    assert before == set(tmp_path.iterdir())


def test_apk_checksum_must_match_trusted_supplied_value(tmp_path):
    path = package(tmp_path, ".apk", {"AndroidManifest.xml": b"fixture", "classes.dex": b"fixture"})
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert inspect_package(path, digest.upper())["vendor_checksum_matches"]
    result = inspect_package(path, "0" * 64)
    assert not result["structure_ok"]
    assert not result["vendor_checksum_matches"]
    assert inspect_package(path)["signature_verified"] is False


@pytest.mark.parametrize("entries,issue", [
    ({"classes.dex": b"fixture"}, "manifest_missing_or_empty"),
    ({"AndroidManifest.xml": b"fixture"}, "dex_missing_not_standalone_demo"),
    ({"AndroidManifest.xml": b"fixture", "classes.dex": b""}, "empty_dex"),
])
def test_incomplete_apk_is_not_marked_structurally_complete(tmp_path, entries, issue):
    result = inspect_package(package(tmp_path, ".apk", entries))
    assert not result["structure_ok"] and issue in result["issues"]


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:\\outside"])
def test_suspicious_archive_paths_are_flagged_without_extraction(tmp_path, name):
    path = package(tmp_path, ".apk", {"AndroidManifest.xml": b"fixture", "classes.dex": b"fixture", name: b"data"})
    result = inspect_package(path)
    assert "unsafe_archive_paths" in result["issues"]
    assert not result["structure_ok"]


def test_invalid_archive_or_missing_file_is_reported(tmp_path):
    path = tmp_path / "broken.aar"
    assert inspect_package(path)["issues"] == ["file_missing"]
    path.write_text("not an archive")
    assert "unreadable_archive" in inspect_package(path)["issues"]


def test_wrong_sdk_does_not_pass_aobo_check(tmp_path):
    jar = io.BytesIO()
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr("unrelated/Manager.class", b"fixture")
    path = package(tmp_path, ".aar", {"AndroidManifest.xml": b"fixture", "classes.jar": jar.getvalue()})
    result = inspect_package(path)
    assert not result["structure_ok"]
    assert "documented_manager_not_found_confirm_sdk_with_vendor" in result["issues"]
