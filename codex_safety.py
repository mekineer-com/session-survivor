#!/usr/bin/env python3

from __future__ import annotations

import json
import pathlib

from lineage import extract_checkpoint_provenance


def detect_model_switches(records: list[dict]) -> list[dict]:
    """Scan turn_context records for model changes."""
    switches: list[dict] = []
    prev_model: str | None = None
    turn_idx = 0
    for obj in records:
        if obj.get("type") != "turn_context":
            continue
        model = obj.get("payload", {}).get("model")
        if model and model != prev_model:
            if prev_model is not None:
                switches.append({"turn": turn_idx, "from": prev_model, "to": model})
            prev_model = model
        turn_idx += 1
    return switches


def summarize_models_seen(model_switches: list[dict]) -> list[str]:
    if not model_switches:
        return []
    models = {s["to"] for s in model_switches} | {model_switches[0]["from"]}
    return sorted(models)


def find_compacted_source_manifest(source: pathlib.Path) -> pathlib.Path | None:
    parts = source.resolve().parts
    compacted_indexes = [idx for idx, part in enumerate(parts) if part == "compacted"]
    if not compacted_indexes:
        return None
    idx = compacted_indexes[-1]
    root = pathlib.Path(*parts[:idx])
    rel = pathlib.Path(*parts[idx + 1 :])
    candidate = root / "manifests" / rel.with_suffix(".manifest.json")
    if candidate.exists():
        return candidate
    return None


def read_compaction_depth_from_manifest(manifest_path: pathlib.Path) -> int | None:
    try:
        obj = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    policy = obj.get("policy")
    if isinstance(policy, dict):
        depth = policy.get("compaction_depth")
        if isinstance(depth, int) and depth >= 0:
            return depth
    lineage = obj.get("lineage")
    if isinstance(lineage, dict):
        depth = lineage.get("ancestor_depth")
        if isinstance(depth, int) and depth >= 0:
            return depth
    return None


def compute_ancestor_depth(source: pathlib.Path) -> int:
    manifest_path = find_compacted_source_manifest(source)
    if manifest_path is not None:
        prior_depth = read_compaction_depth_from_manifest(manifest_path)
        if prior_depth is None:
            return 1
        return prior_depth + 1

    provenance = extract_checkpoint_provenance(source)
    if not provenance:
        return 0
    try:
        return int(provenance.get("ancestor_depth") or 0) + 1
    except Exception:
        return 1
