"""One-time, checked move of the existing Embassy World data into project folders.

This migration changes paths and adds provenance metadata only. It does not
approve or rewrite any sales claim. Run with --check first, then --apply.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
WORLD = DATA / "projects" / "embassy_world"
REVIEW = DATA / "review" / "other_projects" / "presentations" / "slides"
OLD_SLIDES = DATA / "slides"
WORLD_SLIDES = WORLD / "presentations" / "slides"
LEGACY_SLIDES = DATA / "review" / "unindexed_slides"

SINGLE_FILES = (
    (DATA / "condo_facts.json", WORLD / "facts" / "condo_facts.json", "project_facts"),
    (DATA / "project_knowledge.json", WORLD / "routing" / "project_knowledge.json", "project_vocabulary"),
    (DATA / "floor-plan-assets.json", WORLD / "sales" / "floor-plan-assets.json", "floor_plan_assets"),
    (OLD_SLIDES / "canva_pages.json", WORLD_SLIDES / "canva_pages.json", "canva_page_mapping"),
    (DATA / "source_registry.json", DATA / "registry" / "source_registry.json", None),
)


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def plan() -> tuple[list[dict], list[dict], list[tuple[Path, Path]]]:
    catalog = _read(OLD_SLIDES / "index.json")
    images = catalog.get("images")
    if not isinstance(images, list) or len(images) != 150:
        raise ValueError("expected the 150-entry original slide catalog; refusing an ambiguous migration")
    world = [image for image in images if image.get("type") != "other-project"]
    review = [image for image in images if image.get("type") == "other-project"]
    if len(world) != 135 or len(review) != 15:
        raise ValueError("slide classification changed; review it before moving anything")

    moves = [(source, target) for source, target, _ in SINGLE_FILES]
    names: set[str] = set()
    for image in images:
        name = image.get("file")
        if not isinstance(name, str) or Path(name).name != name or name in names:
            raise ValueError(f"unsafe or duplicate slide file: {name!r}")
        names.add(name)
        target_dir = REVIEW if image.get("type") == "other-project" else WORLD_SLIDES
        moves.append((OLD_SLIDES / name, target_dir / name))
    for source, target in moves:
        if not source.is_file() or target.exists():
            raise ValueError(f"source missing or destination occupied: {source} -> {target}")
        if not source.resolve().is_relative_to(DATA.resolve()):
            raise ValueError(f"source escapes data directory: {source}")
        if not target.resolve().is_relative_to(DATA.resolve()):
            raise ValueError(f"destination escapes data directory: {target}")
    for target in (WORLD_SLIDES / "index.json", REVIEW / "index.json"):
        if target.exists():
            raise ValueError(f"destination occupied: {target}")
    leftovers = {p.name for p in OLD_SLIDES.iterdir() if p.is_file()} - names - {"index.json", "canva_pages.json"}
    expected_legacy = {f"deck_{i:03d}.jpg" for i in range(1, 60)} | {
        "embeddings.npz", "index.json.bak",
    }
    if leftovers != expected_legacy or LEGACY_SLIDES.exists():
        raise ValueError("legacy slide folder differs from the inspected files")
    return world, review, moves


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the validated migration")
    args = parser.parse_args()
    world, review, moves = plan()
    print(f"checked: {len(world)} Embassy World slides, {len(review)} other-project slides, {len(moves)} file moves")
    if not args.apply:
        return

    for source, target in moves:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))

    for source, target, source_id in SINGLE_FILES:
        if source_id is None:
            continue
        payload = _read(target)
        payload["source_id"] = source_id
        payload["project_id"] = "embassy_world"
        _write(target, payload)

    for image in world:
        image["source_id"] = "slide_catalog"
        image["project_id"] = "embassy_world"
    for image in review:
        image["source_id"] = "other_project_slide_review"
        image["project_id"] = None  # mixed developers/projects: owner must classify
    _write(WORLD_SLIDES / "index.json", {
        "source_id": "slide_catalog", "project_id": "embassy_world", "images": world,
    })
    _write(REVIEW / "index.json", {
        "source_id": "other_project_slide_review", "project_id": None, "images": review,
    })
    (OLD_SLIDES / "index.json").unlink()
    shutil.move(str(OLD_SLIDES), str(LEGACY_SLIDES))
    print("moved project sources; unindexed legacy images retained under data/review")


if __name__ == "__main__":
    main()
