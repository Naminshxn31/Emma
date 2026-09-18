"""Audit the data-source registry without reading secrets or private contents."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.knowledge_policy import evaluate_claim  # noqa: E402

DEFAULT_REGISTRY = ROOT / "data" / "registry" / "source_registry.json"
VALID_AUTHORITIES = {
    "curated", "operational", "reference", "derived", "sample",
    "private_runtime", "telemetry",
}
VALID_CUSTOMER_USE = {"allowed", "conditional", "forbidden"}
VALID_AVAILABILITY = {"tracked", "external_required", "generated", "review_only"}
INTEGRITY_EXEMPTIONS = {"remote_mutable", "sales_owned_mutable", "private_documents"}
SCOPE_EXCEPTIONS = {"mixed_private_library", "unassigned_print_catalogue", "device_telemetry"}
MODES = {"ci", "runtime", "full"}
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class Issue:
    severity: str
    source: str
    message: str


def _repo_path(value: str) -> Path:
    if (not value or "\\" in value or Path(value).is_absolute()
            or re.match(r"^[A-Za-z]:", value)):
        raise ValueError("path must be repository-relative POSIX")
    candidate = (ROOT / value).resolve()
    if not candidate.is_relative_to(ROOT.resolve()):
        raise ValueError("path escapes the repository")
    return candidate


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("top level must be an object")
    return payload


def _needed(source: dict, mode: str, features: set[str]) -> bool:
    if mode == "full":
        return True
    if mode == "ci":
        return source.get("availability") == "tracked"
    return bool(set(source.get("required_for", [])) & ({"runtime", "presentation"} | features))


def _check_asset_manifest(source: dict, by_id: dict[str, dict], mode: str,
                          features: set[str]) -> list[Issue]:
    source_id = source["id"]
    issues: list[Issue] = []
    manifest_name = source["asset_manifest"]
    try:
        manifest_path = _repo_path(manifest_name)
        manifest = _read_json(manifest_path)
        group = manifest["sources"][source_id]
        records = group["files"]
        index_source = by_id[source["asset_index_source"]]
        index = _read_json(_repo_path(index_source["location"]))
        names = [item[source["asset_index_field"]]
                 for item in index[source["asset_index_array"]]]
        if not isinstance(records, list) or group.get("project_id") != source.get("project_id"):
            raise ValueError("asset manifest group has wrong shape or project")
        files = {}
        for item in records:
            name = item["path"]
            if not isinstance(name, str) or not name or "/" in name or "\\" in name or ":" in name:
                raise ValueError("asset name must be a plain filename")
            if (name in files or not isinstance(item.get("bytes"), int)
                    or item["bytes"] < 0 or not isinstance(item.get("sha256"), str)
                    or not SHA256.fullmatch(item["sha256"])):
                raise ValueError("duplicate or invalid asset record")
            files[name] = item
        if len(names) != len(set(names)) or set(files) != set(names):
            raise ValueError("manifest files do not match registered index")
        if mode == "ci" or not _needed(source, mode, features):
            return issues
        base = _repo_path(source["location"])
        missing: list[str] = []
        size_mismatch: list[str] = []
        hash_mismatch: list[str] = []
        for name, item in files.items():
            path = (base / name).resolve()
            if not path.is_relative_to(base) or not path.is_file():
                missing.append(name)
                continue
            if path.stat().st_size != item["bytes"]:
                size_mismatch.append(name)
                continue
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != item["sha256"]:
                hash_mismatch.append(name)
        if missing:
            severity = "error" if mode == "runtime" or source.get("required") else "warning"
            issues.append(Issue(severity, source_id,
                                f"{len(missing)} external asset(s) missing: {', '.join(missing[:3])}"))
        if size_mismatch:
            issues.append(Issue("error", source_id,
                                f"{len(size_mismatch)} asset size mismatch: {', '.join(size_mismatch[:3])}"))
        if hash_mismatch:
            issues.append(Issue("error", source_id,
                                f"{len(hash_mismatch)} asset checksum mismatch: {', '.join(hash_mismatch[:3])}"))
    except (KeyError, TypeError, IndexError, OSError, ValueError, json.JSONDecodeError) as exc:
        issues.append(Issue("error", source_id, f"invalid asset manifest: {exc}"))
    return issues


def audit_registry(path: Path = DEFAULT_REGISTRY, *, mode: str = "full",
                   features: tuple[str, ...] = ()) -> list[Issue]:
    if mode not in MODES:
        raise ValueError(f"unknown audit mode: {mode}")
    registry = _read_json(path)
    issues: list[Issue] = []
    sources = registry.get("sources")
    if not isinstance(sources, list):
        return [Issue("error", "registry", "sources must be an array")]
    if registry.get("availability_schema_version") != 1:
        issues.append(Issue("error", "registry", "missing availability contract version"))
    by_id = {item.get("id"): item for item in sources if isinstance(item, dict)}
    active_features = set(features)

    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            issues.append(Issue("error", "registry", "source entry must be an object"))
            continue
        source_id = str(source.get("id") or "(missing id)")
        if source_id in seen:
            issues.append(Issue("error", source_id, "duplicate source id"))
        seen.add(source_id)

        if source.get("authority") not in VALID_AUTHORITIES:
            issues.append(Issue("error", source_id, "unknown authority level"))
        if source.get("customer_facing") not in VALID_CUSTOMER_USE:
            issues.append(Issue("error", source_id, "unknown customer_facing value"))
        availability = source.get("availability")
        if availability not in VALID_AVAILABILITY:
            issues.append(Issue("error", source_id, "unknown availability class"))
            continue
        required_for = source.get("required_for")
        if not isinstance(required_for, list) or any(
            not isinstance(feature, str) or not feature for feature in required_for
        ):
            issues.append(Issue("error", source_id, "required_for must be an array of feature names"))
            continue
        if availability == "tracked" and source.get("tracked") is not True:
            issues.append(Issue("error", source_id, "tracked source must have tracked=true"))
        if availability == "external_required" and (
            source.get("tracked") is not False or not source.get("materialization")
        ):
            issues.append(Issue("error", source_id, "external source needs tracked=false and materialization"))
        if availability == "external_required" and not source.get("asset_manifest"):
            if source.get("integrity_exemption") not in INTEGRITY_EXEMPTIONS:
                issues.append(Issue("error", source_id, "unpinned external source needs an explicit integrity exemption"))
        if source.get("customer_facing") in {"allowed", "conditional"} and not source.get("project_id"):
            if set(required_for) & {"runtime", "presentation"}:
                issues.append(Issue("error", source_id, "active customer source needs project_id"))
            elif source.get("scope_exception") not in SCOPE_EXCEPTIONS:
                issues.append(Issue("error", source_id, "customer source needs project_id or explicit scope exception"))
        for field in ("label", "domain", "kind", "uses", "not_for"):
            if not source.get(field):
                issues.append(Issue("error", source_id, f"missing {field}"))
        members = source.get("members", [])
        if not isinstance(members, list):
            issues.append(Issue("error", source_id, "members must be an array"))
        else:
            for member in members:
                try:
                    _repo_path(member)
                except (TypeError, ValueError) as exc:
                    issues.append(Issue("error", source_id, f"unsafe member path: {exc}"))

        location = source.get("location")
        if location is None:
            if availability == "tracked":
                issues.append(Issue("error", source_id, "tracked source needs a location"))
            continue
        if not isinstance(location, str) or not location or "\\" in location:
            issues.append(Issue("error", source_id, "location must be a repo-relative POSIX path"))
            continue
        try:
            local = _repo_path(location)
        except ValueError as exc:
            issues.append(Issue("error", source_id, str(exc)))
            continue
        if source.get("asset_manifest"):
            issues.extend(_check_asset_manifest(source, by_id, mode, active_features))
        if availability == "generated" or not _needed(source, mode, active_features):
            continue

        exists = local.exists()
        if not exists:
            severity = "warning" if availability == "review_only" else (
                "error" if mode == "runtime" or source.get("required") else "warning"
            )
            issues.append(Issue(severity, source_id, f"missing {availability} path: {location}"))
            continue

        payload: dict | None = None
        if source.get("kind") == "json_file":
            try:
                payload = _read_json(local)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                issues.append(Issue("error", source_id, f"invalid JSON: {exc}"))
                continue

        if payload is not None:
            if source.get("project_id") is not None:
                if (payload.get("source_id") != source_id
                        or payload.get("project_id") != source["project_id"]):
                    issues.append(Issue(
                        "error", source_id, "payload source_id/project_id does not match registry",
                    ))
            if source_id in {"slide_catalog", "other_project_slide_review"}:
                wrong_entries = [item.get("id") for item in payload.get("images", [])
                                 if (item.get("source_id") != source_id
                                     or item.get("project_id") != source.get("project_id"))]
                if wrong_entries:
                    issues.append(Issue(
                        "error", source_id, f"{len(wrong_entries)} slide entries have wrong scope",
                    ))
            approval_fields = source.get("approval_fields", [])
            missing_approval = [field for field in approval_fields if not payload.get(field)]
            if missing_approval:
                trace = (evaluate_claim(payload, source["project_id"]).trace()
                         if source_id in {"project_facts", "project_sales_context"}
                         else None)
                issues.append(Issue(
                    "warning", source_id,
                    "approval metadata is blank: " + ", ".join(missing_approval)
                    + ("; policy=%s allowed=%s reason=%s source_id=%s" % (
                        trace["policy"], str(trace["allowed"]).lower(),
                        trace["reason"], trace["source_id"]
                    ) if trace else ""),
                ))
            if source_id == "slide_catalog":
                decisions = [evaluate_claim(item, source["project_id"])
                             for item in payload.get("images", [])]
                blocked = [decision for decision in decisions if not decision.allowed]
                if blocked:
                    issues.append(Issue(
                        "warning", source_id,
                        "policy=customer_claim_v1 allowed=%d blocked=%d source_id=%s "
                        "(presentation assets are not approved facts)" % (
                            len(decisions) - len(blocked), len(blocked), source_id),
                    ))

            sample_field = source.get("sample_field")
            if sample_field and payload.get(sample_field) is not True:
                issues.append(Issue("error", source_id, f"{sample_field} must be true"))

            count_field = source.get("count_field")
            if count_field and not isinstance(payload.get(count_field), list):
                issues.append(Issue("error", source_id, f"{count_field} must be an array"))

            points_field = source.get("verified_points_field")
            if points_field and isinstance(payload.get(points_field), dict):
                unverified = [
                    name for name, point in payload[points_field].items()
                    if not isinstance(point, dict) or point.get("verified") is not True
                ]
                if unverified:
                    issues.append(Issue(
                        "warning", source_id,
                        f"{len(unverified)} navigation point(s) remain unverified: "
                        + ", ".join(unverified),
                    ))

            manifest = source.get("manifest_files")
            if mode == "full" and isinstance(manifest, dict):
                entries = payload.get(manifest.get("array"), [])
                base = local.parent
                missing_files = [
                    str(item.get(manifest.get("field")))
                    for item in entries
                    if isinstance(item, dict)
                    and item.get(manifest.get("field"))
                    and not (base / str(item[manifest["field"]])).is_file()
                ]
                if missing_files:
                    issues.append(Issue(
                        "warning", source_id,
                        "catalogue payload missing: " + ", ".join(missing_files),
                    ))

    return issues


def summary(issues: list[Issue]) -> dict[str, int]:
    return {
        severity: sum(issue.severity == severity for issue in issues)
        for severity in ("error", "warning")
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="ตรวจทะเบียนแหล่งข้อมูลของ Emma")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--mode", choices=sorted(MODES), default="full")
    parser.add_argument("--feature", action="append", default=[],
                        help="additional runtime feature such as printing or mydocs")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    issues = audit_registry(args.registry, mode=args.mode, features=tuple(args.feature))
    counts = summary(issues)
    if args.as_json:
        print(json.dumps(
            {"summary": counts, "issues": [asdict(issue) for issue in issues]},
            ensure_ascii=False, indent=2,
        ))
    else:
        for issue in issues:
            print(f"{issue.severity.upper():7} {issue.source}: {issue.message}")
        print(f"errors={counts['error']} warnings={counts['warning']}")
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
