#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

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


def find_agents_md_for_path(path: pathlib.Path) -> pathlib.Path | None:
    current = path.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for parent in (current, *current.parents):
        candidate = parent / "AGENTS.md"
        if candidate.exists():
            return candidate
    return None


def detect_project_root(records: list[dict], source: pathlib.Path) -> pathlib.Path | None:
    for obj in reversed(records):
        if obj.get("type") != "turn_context":
            continue
        payload = obj.get("payload", {})
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not cwd.startswith("/"):
            continue
        agents_path = find_agents_md_for_path(pathlib.Path(cwd))
        if agents_path is not None:
            return agents_path.parent
    fallback_agents = find_agents_md_for_path(source)
    if fallback_agents is not None:
        return fallback_agents.parent
    return None


def load_workspace_agents_md(project_root: pathlib.Path | None) -> tuple[str | None, pathlib.Path | None]:
    if project_root is None:
        return None, None
    agents_path = project_root / "AGENTS.md"
    if not agents_path.exists():
        return None, None
    return agents_path.read_text(encoding="utf-8", errors="ignore"), agents_path


def normalize_agents_instruction(scope: str, content: str, agents_prefix: str) -> str:
    body = content.rstrip("\n")
    return f"{agents_prefix}{scope}\n\n<INSTRUCTIONS>\n{body}\n</INSTRUCTIONS>"


def refresh_anchors(
    records: list[dict],
    state: dict[str, int],
    project_root: pathlib.Path | None,
    agents_prefix: str,
    agents_placeholder: str,
) -> tuple[str | None, pathlib.Path | None]:
    """Replace stale AGENTS.md copies in turn_context with current workspace version."""
    current_agents, agents_path = load_workspace_agents_md(project_root)
    if current_agents is None:
        return None, None
    refreshed = 0
    for obj in records:
        if obj.get("type") != "turn_context":
            continue
        payload = obj.get("payload", {})
        instructions = payload.get("user_instructions")
        if not isinstance(instructions, str):
            continue
        if instructions == agents_placeholder:
            continue
        if not instructions.startswith(agents_prefix):
            continue
        first_line = instructions.splitlines()[0]
        scope = first_line[len(agents_prefix) :].strip()
        if not scope:
            scope = str(project_root or "")
        desired = normalize_agents_instruction(scope, current_agents, agents_prefix)
        if instructions != desired:
            payload["user_instructions"] = desired
            refreshed += 1
    state["anchor_refreshed"] = refreshed
    return hashlib.sha256(current_agents.encode("utf-8")).hexdigest()[:16], agents_path
