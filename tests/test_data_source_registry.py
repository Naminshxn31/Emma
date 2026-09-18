import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.audit_data_sources import DEFAULT_REGISTRY, audit_registry, summary
from app.data_sources import require_project_payload, source_path


ROOT = Path(__file__).resolve().parents[1]


def _registry() -> dict:
    return json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))


def test_registry_has_unique_sources_and_decision_rules():
    registry = _registry()
    ids = [source["id"] for source in registry["sources"]]

    assert registry["schema_version"] == 1
    assert len(ids) == len(set(ids))
    assert {rule["id"] for rule in registry["decision_rules"]} >= {
        "commercial_numbers",
        "live_state_wins",
        "robot_capability_requires_evidence",
        "sample_never_becomes_truth",
    }


def test_core_runtime_sources_are_classified_explicitly():
    sources = {source["id"]: source for source in _registry()["sources"]}

    assert sources["project_facts"]["location"] == "data/projects/embassy_world/facts/condo_facts.json"
    assert sources["project_facts"]["project_id"] == "embassy_world"
    assert sources["project_facts"]["customer_facing"] == "conditional"
    assert sources["project_vocabulary"]["customer_facing"] == "forbidden"
    assert sources["slide_catalog"]["authority"] == "reference"
    assert sources["other_project_slide_review"]["customer_facing"] == "forbidden"
    assert sources["live_unit_inventory"]["authority"] == "operational"
    assert sources["robot_live_telemetry"]["authority"] == "telemetry"


def test_project_source_ids_cannot_resolve_for_a_different_project():
    assert source_path("project_facts", "embassy_world").is_file()
    with pytest.raises(ValueError):
        source_path("project_facts", "embassy_life")
    with pytest.raises(ValueError):
        require_project_payload(
            {"source_id": "project_facts", "project_id": "embassy_life"},
            "project_facts", "embassy_world",
        )


def test_sample_and_private_sources_cannot_become_customer_truth():
    sources = {source["id"]: source for source in _registry()["sources"]}

    assert sources["sample_unit_inventory"]["authority"] == "sample"
    assert sources["sample_unit_inventory"]["customer_facing"] == "forbidden"
    assert sources["private_runtime_state"]["authority"] == "private_runtime"
    assert sources["private_runtime_state"]["tracked"] is False
    assert sources["private_runtime_state"]["customer_facing"] == "forbidden"


def test_ci_requires_only_tracked_sources_and_valid_external_manifests():
    issues = audit_registry(mode="ci")

    assert summary(issues)["error"] == 0, issues
    for source in _registry()["sources"]:
        if source.get("availability") == "tracked" and source.get("location"):
            assert (ROOT / source["location"]).exists()


def test_known_incomplete_inputs_stay_visible_as_warnings():
    issues = audit_registry()
    warnings = {(issue.source, issue.message) for issue in issues if issue.severity == "warning"}

    assert any(source == "project_facts" and "approval metadata" in message
               for source, message in warnings)
    if (ROOT / "data/showroom/layout.json").exists():
        assert any(source == "showroom_layout" and "unverified" in message
                   for source, message in warnings)
    else:
        assert any(source == "showroom_layout" and "missing review_only" in message
                   for source, message in warnings)


def test_cli_emits_utf8_json_on_windows():
    result = subprocess.run(
        [sys.executable, "scripts/audit_data_sources.py", "--mode", "ci", "--json"],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary"]["error"] == 0
