"""Pin hashes of local presentation/print assets without committing the assets.

Run --write only after reviewing a new export bundle. A checksum proves that
deployment copied the same bytes; it does not approve the content for guests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data/registry/source_registry.json"
OUTPUT = ROOT / "data/registry/external_asset_manifest.json"


def _local_path(value: str) -> Path:
    if not value or "\\" in value or Path(value).is_absolute() or ":" in value:
        raise ValueError(f"unsafe asset path: {value!r}")
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f"asset path escapes repository: {value!r}")
    return path


def build_manifest() -> dict:
    sources = json.loads(REGISTRY.read_text(encoding="utf-8"))["sources"]
    by_id = {item["id"]: item for item in sources}
    groups = {}
    for source in sources:
        if source.get("asset_manifest") != "data/registry/external_asset_manifest.json":
            continue
        source_id = source["id"]
        index_source = by_id[source["asset_index_source"]]
        index = json.loads(_local_path(index_source["location"]).read_text(encoding="utf-8"))
        names = [item[source["asset_index_field"]]
                 for item in index[source["asset_index_array"]]]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate asset name in {source_id} index")
        base = _local_path(source["location"])
        files = []
        for name in sorted(names):
            if not isinstance(name, str) or not name or "/" in name or "\\" in name or ":" in name:
                raise ValueError(f"unsafe asset name in {source_id}: {name!r}")
            path = (base / name).resolve()
            if not path.is_relative_to(base) or not path.is_file():
                raise FileNotFoundError(f"{source_id}: missing/unsafe asset {name!r}")
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            files.append({"path": name, "bytes": path.stat().st_size, "sha256": digest})
        groups[source_id] = {"project_id": source.get("project_id"), "files": files}
    return {"schema_version": 1, "sources": groups}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="pin current local assets")
    mode.add_argument("--check", action="store_true", help="compare current assets with pinned hashes")
    args = parser.parse_args()
    actual = build_manifest()
    if args.write:
        OUTPUT.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {OUTPUT.relative_to(ROOT)}: "
              + ", ".join(f"{name}={len(group['files'])}" for name, group in actual["sources"].items()))
        return 0
    expected = json.loads(OUTPUT.read_text(encoding="utf-8"))
    if actual != expected:
        print("external asset bytes differ from manifest")
        return 1
    print("external asset manifest matches local files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
