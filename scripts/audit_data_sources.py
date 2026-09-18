"""Audit the data-source registry without reading secrets or private contents."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "data" / "registry" / "source_registry.json"
VALID_AUTHORITIES = {
    "curated", "operational", "reference", "derived", "sample",
    "private_runtime", "telemetry",
}
VALID_CUSTOMER_USE = {"allowed", "conditional", "forbidden"}


@dataclass(frozen=True)
class Issue:
    severity: str
    source: str
    message: str


def _repo_path(value: str) -> Path:
    candidate = (ROOT / value).resolve()
    if not candidate.is_relative_to(ROOT.resolve()):
        raise ValueError("path escapes the repository")
    return candidate


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("top level must be an object")
    return payload


def audit_registry(path: Path = DEFAULT_REGISTRY) -> list[Issue]:
    registry = _read_json(path)
    issues: list[Issue] = []
    sources = registry.get("sources")
    if not isinstance(sources, list):
        return [Issue("error", "registry", "sources must be an array")]

    seen: set[str] = set()
    for source in sources:
        source_id = str(source.get("id") or "(missing id)")
        if source_id in seen:
            issues.append(Issue("error", source_id, "duplicate source id"))
        seen.add(source_id)

        if source.get("authority") not in VALID_AUTHORITIES:
            issues.append(Issue("error", source_id, "unknown authority level"))
        if source.get("customer_facing") not in VALID_CUSTOMER_USE:
            issues.append(Issue("error", source_id, "unknown customer_facing value"))
        for field in ("label", "domain", "kind", "uses", "not_for"):
            if not source.get(field):
                issues.append(Issue("error", source_id, f"missing {field}"))

        location = source.get("location")
        if location is None:
            continue
        if not isinstance(location, str) or not location or "\\" in location:
            issues.append(Issue("error", source_id, "location must be a repo-relative POSIX path"))
            continue
        try:
            local = _repo_path(location)
        except ValueError as exc:
            issues.append(Issue("error", source_id, str(exc)))
            continue

        exists = local.exists()
        if not exists:
            severity = "error" if source.get("required") else "warning"
            issues.append(Issue(severity, source_id, f"missing optional path: {location}"))
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
                issues.append(Issue(
                    "warning", source_id,
                    "approval metadata is blank: " + ", ".join(missing_approval),
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
            if isinstance(manifest, dict):
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
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    issues = audit_registry(args.registry)
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
