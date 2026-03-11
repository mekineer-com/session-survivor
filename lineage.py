#!/usr/bin/env python3

from __future__ import annotations

import json
import pathlib
from typing import Any


CHECKPOINT_PREFIX = "ROLL_OUT_CHECKPOINT\n"


def infer_source_kind(path: pathlib.Path) -> str:
    parts = path.resolve().parts
    joined = str(path.resolve())
    if "/.codex/sessions/" in joined:
        return "live"
    if "compacted" in parts:
        return "compacted"
    if "original" in parts:
        return "snapshot"
    return "file"


def extract_checkpoint_provenance(path: pathlib.Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                obj = json.loads(line)
                if obj.get("type") != "compacted":
                    continue
                payload = obj.get("payload", {})
                message = payload.get("message")
                if not isinstance(message, str) or not message.startswith(CHECKPOINT_PREFIX):
                    continue
                checkpoint = json.loads(message.split("\n", 1)[1])
                provenance = checkpoint.get("provenance")
                if isinstance(provenance, dict):
                    return provenance
    except Exception:
        return None
    return None


def lineage_chain(path: pathlib.Path, max_depth: int = 8) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    current = path.resolve()
    depth = 0
    seen: set[str] = set()
    while depth < max_depth:
        provenance = extract_checkpoint_provenance(current)
        if not provenance:
            break
        source_path = provenance.get("source_path")
        node = {
            "path": str(current),
            "profile": provenance.get("profile"),
            "generated_at": provenance.get("generated_at"),
            "source_path": source_path,
            "source_sha256": provenance.get("source_sha256"),
        }
        chain.append(node)
        if not isinstance(source_path, str) or not source_path.strip():
            break
        source_resolved = str(pathlib.Path(source_path).expanduser().resolve())
        if source_resolved in seen:
            break
        seen.add(source_resolved)
        current = pathlib.Path(source_resolved)
        depth += 1
    return chain


def build_compaction_manifest(
    *,
    source: pathlib.Path,
    original_copy: pathlib.Path,
    compacted_copy: pathlib.Path,
    report_path: pathlib.Path,
    source_sha256: str,
    compacted_sha256: str,
    profile: str,
    generated_at: str | None,
    original_lines: int,
    compacted_lines: int,
    bytes_saved: int,
    keep_last_turns: int,
    max_replacement_records: int,
) -> dict[str, Any]:
    source_resolved = source.resolve()
    parent_provenance = extract_checkpoint_provenance(source_resolved)
    source_kind = infer_source_kind(source_resolved)
    source_chain = lineage_chain(source_resolved)
    parent_depth = len(source_chain)
    return {
        "version": 1,
        "tool": "session-survivor",
        "kind": "compaction_manifest",
        "generated_at": generated_at,
        "profile": profile,
        "source": {
            "path": str(source_resolved),
            "sha256": source_sha256,
            "kind": source_kind,
            "line_count": original_lines,
        },
        "artifacts": {
            "original_copy": str(original_copy),
            "compacted_copy": str(compacted_copy),
            "report": str(report_path),
        },
        "lineage": {
            "parent_provenance": parent_provenance,
            "ancestor_depth": parent_depth,
            "chain": source_chain,
        },
        "policy": {
            "keep_last_turns": keep_last_turns,
            "max_replacement_records": max_replacement_records,
        },
        "result": {
            "compacted_sha256": compacted_sha256,
            "compacted_line_count": compacted_lines,
            "bytes_saved": bytes_saved,
        },
    }
