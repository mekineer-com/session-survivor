#!/usr/bin/env python3

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pathlib
import sys
from typing import Any

from artifact_publish import publish_artifacts
from lineage import build_compaction_manifest


DEFAULT_OUTPUT_ROOT = pathlib.Path("/home/marcos/apps-codex/session-survivor/outputs/claude")
TOOL_OUTPUT_PLACEHOLDER = "[Compacted Claude tool result"
LOCAL_COMMAND_PLACEHOLDER = "[Compacted Claude local command"
FILE_HISTORY_PLACEHOLDER = "[Compacted Claude file history"
DEFAULT_ASSISTANT_MODEL = "claude-sonnet-4-6"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create conservative compacted copies of Claude Code JSONL session logs."
    )
    parser.add_argument("session", help="Path to a Claude session JSONL file.")
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Root directory for original/compacted/report outputs.",
    )
    parser.add_argument(
        "--max-tool-output-chars",
        type=int,
        default=400,
        help="Keep at most this many chars of bulky tool-result or toolUseResult fields.",
    )
    parser.add_argument(
        "--max-file-history-entries",
        type=int,
        default=8,
        help="Keep at most this many tracked file backups per file-history snapshot.",
    )
    parser.add_argument(
        "--show-summary",
        action="store_true",
        help="Print only a compact summary JSON to stdout.",
    )
    return parser.parse_args()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_existing_compaction_placeholder(text: str) -> bool:
    return (
        "\n... [Compacted Claude" in text
        and "; original length=" in text
        and text.rstrip().endswith(" chars]")
    )


def shorten(text: str, max_chars: int, label: str) -> tuple[str, bool]:
    if is_existing_compaction_placeholder(text):
        return text, False
    if len(text) <= max_chars:
        return text, False
    kept = text[:max_chars].rstrip()
    compacted = f"{kept}\n... {label}; original length={len(text)} chars]"
    return compacted, True


def validate_jsonl_bytes(data: bytes) -> dict[str, int]:
    line_count = 0
    for line_count, line in enumerate(data.splitlines(), 1):
        json.loads(line)
    return {"line_count": line_count}


def relative_output_path(path: pathlib.Path) -> pathlib.Path:
    parts = path.resolve().parts
    if ".claude" in parts:
        idx = parts.index(".claude")
        return pathlib.Path(*parts[idx + 1 :])
    return pathlib.Path(path.name)


def compact_nested_strings(value: Any, max_chars: int, label: str, state: dict[str, int], counter_key: str) -> Any:
    if isinstance(value, str):
        compacted, changed = shorten(value, max_chars, label)
        if changed:
            state[counter_key] += 1
        return compacted
    if isinstance(value, list):
        return [compact_nested_strings(v, max_chars, label, state, counter_key) for v in value]
    if isinstance(value, dict):
        return {k: compact_nested_strings(v, max_chars, label, state, counter_key) for k, v in value.items()}
    return value


def detect_project_root(records: list[dict[str, Any]]) -> pathlib.Path | None:
    for item in records:
        if not isinstance(item, dict):
            continue
        cwd = item.get("cwd")
        if isinstance(cwd, str) and cwd.startswith("/"):
            path = pathlib.Path(cwd).expanduser()
            try:
                return path.resolve()
            except Exception:
                return path
    return None


def anchor_digest(path: pathlib.Path) -> dict[str, Any]:
    stat = path.stat()
    data = path.read_bytes()
    return {
        "sha256": sha256_bytes(data),
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def compact_tool_use_result(value: Any, max_chars: int, state: dict[str, int]) -> Any:
    return compact_nested_strings(value, max_chars, TOOL_OUTPUT_PLACEHOLDER, state, "tool_outputs_truncated")


def compact_file_history_snapshot(snapshot: Any, max_entries: int, state: dict[str, int]) -> Any:
    if not isinstance(snapshot, dict):
        return snapshot
    out = copy.deepcopy(snapshot)
    tracked = out.get("trackedFileBackups")
    if not isinstance(tracked, dict):
        return out

    original_count = len(tracked)
    if original_count <= max_entries:
        return out

    kept = dict(list(tracked.items())[:max_entries])

    out["trackedFileBackups"] = kept
    out["trackedFileBackupsTruncated"] = {
        "original_count": original_count,
        "kept_count": len(kept),
        "marker": FILE_HISTORY_PLACEHOLDER,
    }
    state["file_history_snapshots_compacted"] += 1
    return out


def compact_message_content(item: dict[str, Any], args: argparse.Namespace, state: dict[str, int]) -> dict[str, Any]:
    out = copy.deepcopy(item)
    item_type = out.get("type")

    if item_type == "tool_result":
        content = out.get("content")
        if isinstance(content, str):
            compacted, changed = shorten(content, args.max_tool_output_chars, TOOL_OUTPUT_PLACEHOLDER)
            out["content"] = compacted
            if changed:
                state["tool_outputs_truncated"] += 1

    return out


def compact_record(obj: dict[str, Any], args: argparse.Namespace, state: dict[str, int]) -> dict[str, Any]:
    item = copy.deepcopy(obj)
    item_type = item.get("type")

    if item_type in ("assistant", "user"):
        message = item.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                compacted_content: list[Any] = []
                for entry in content:
                    if isinstance(entry, dict) and entry.get("type") == "thinking":
                        state["thinking_blocks_removed"] += 1
                        continue
                    if isinstance(entry, dict):
                        compacted_content.append(compact_message_content(entry, args, state))
                    else:
                        compacted_content.append(entry)
                message["content"] = compacted_content
            # Plain user/assistant strings are dialogue, not tool output.

            usage = message.get("usage")
            if isinstance(usage, dict):
                reduced = {
                    k: usage[k]
                    for k in (
                        "input_tokens",
                        "output_tokens",
                        "cache_creation_input_tokens",
                        "cache_read_input_tokens",
                        "service_tier",
                    )
                    if k in usage
                }
                if reduced != usage:
                    message["usage"] = reduced
                    state["message_usage_compacted"] += 1

        if "toolUseResult" in item:
            item["toolUseResult"] = compact_tool_use_result(item.get("toolUseResult"), args.max_tool_output_chars, state)

    elif item_type == "system" and item.get("subtype") == "local_command":
        content = item.get("content")
        if isinstance(content, str):
            compacted, changed = shorten(content, args.max_tool_output_chars, LOCAL_COMMAND_PLACEHOLDER)
            item["content"] = compacted
            if changed:
                state["local_command_truncated"] += 1
    elif item_type == "file-history-snapshot":
        item["snapshot"] = compact_file_history_snapshot(item.get("snapshot"), args.max_file_history_entries, state)

    return item


def validate_claude_records(records: list[dict[str, Any]]) -> None:
    uuids: set[str] = set()
    tool_uses: dict[str, set[str]] = {}
    tool_results: list[tuple[str, str]] = []
    meaningful = False

    for row in records:
        row_uuid = row.get("uuid")
        if isinstance(row_uuid, str) and row_uuid:
            if row_uuid in uuids:
                raise ValueError(f"Duplicate Claude UUID: {row_uuid}")
            uuids.add(row_uuid)
            if row.get("parentUuid") == row_uuid:
                raise ValueError(f"Claude row is its own parent: {row_uuid}")

        if row.get("type") not in ("user", "assistant"):
            continue
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            meaningful = meaningful or bool(content.strip())
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") == "thinking":
                continue
            block_type = block.get("type")
            if block_type == "text":
                meaningful = meaningful or bool(str(block.get("text", "")).strip())
            else:
                meaningful = True
            if block_type == "tool_use" and isinstance(block.get("id"), str):
                if not isinstance(row_uuid, str) or not row_uuid:
                    raise ValueError("Claude tool invocation has no row UUID.")
                tool_uses.setdefault(row_uuid, set()).add(block["id"])
            if block_type == "tool_result" and isinstance(block.get("tool_use_id"), str):
                if not isinstance(row_uuid, str) or not row_uuid:
                    raise ValueError("Claude tool result has no row UUID.")
                tool_results.append((row_uuid, block["tool_use_id"]))

    if not meaningful:
        raise ValueError("No meaningful Claude dialogue survived filtering.")
    missing_parents = {
        row["parentUuid"] for row in records
        if isinstance(row.get("parentUuid"), str) and row["parentUuid"] and row["parentUuid"] not in uuids
    }
    if missing_parents:
        raise ValueError(f"Claude output has unresolved parent UUIDs: {len(missing_parents)}")
    by_uuid = {row["uuid"]: row for row in records if isinstance(row.get("uuid"), str) and row["uuid"]}
    checked: set[str] = set()
    for start in by_uuid:
        if start in checked:
            continue
        seen: set[str] = set()
        current: str | None = start
        while current in by_uuid and current not in checked:
            if current in seen:
                raise ValueError("Claude output has a parent cycle.")
            seen.add(current)
            parent = by_uuid[current].get("parentUuid")
            current = parent if isinstance(parent, str) and parent else None
        checked.update(seen)
    for row_uuid, tool_id in tool_results:
        parent = by_uuid[row_uuid].get("parentUuid")
        while isinstance(parent, str) and parent in by_uuid:
            if tool_id in tool_uses.get(parent, set()):
                break
            parent = by_uuid[parent].get("parentUuid")
        else:
            raise ValueError("Claude output has a tool result without an invocation in its parent chain.")


def backfill_assistant_models(
    records: list[dict[str, Any]], reference_records: list[dict[str, Any]] | None = None
) -> int:
    fallback = next(
        (
            row["message"]["model"]
            for row in reversed(reference_records or records)
            if row.get("type") == "assistant"
            and isinstance(row.get("message"), dict)
            and isinstance(row["message"].get("model"), str)
            and row["message"]["model"]
            and "synthetic" not in row["message"]["model"].casefold()
            and not row["message"]["model"].startswith("<")
        ),
        DEFAULT_ASSISTANT_MODEL,
    )
    changed = 0
    for row in records:
        if row.get("type") != "assistant" or not isinstance(row.get("message"), dict):
            continue
        if not isinstance(row["message"].get("model"), str) or not row["message"]["model"]:
            row["message"]["model"] = fallback
            changed += 1
    return changed


def main() -> int:
    args = parse_args()

    source = pathlib.Path(args.session).expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"Session file not found: {source}")
    warnings: list[str] = []

    output_root = pathlib.Path(args.output_root).expanduser().resolve()
    rel = relative_output_path(source)
    original_copy = output_root / "original" / rel
    compacted_copy = output_root / "compacted" / rel
    report_path = output_root / "reports" / rel.with_suffix(".report.json")
    manifest_path = output_root / "manifests" / rel.with_suffix(".manifest.json")

    original_bytes = source.read_bytes()
    original_sha256 = sha256_bytes(original_bytes)
    original_validation = validate_jsonl_bytes(original_bytes)

    state = {
        "thinking_blocks_removed": 0,
        "tool_outputs_truncated": 0,
        "local_command_truncated": 0,
        "message_usage_compacted": 0,
        "file_history_snapshots_compacted": 0,
        "assistant_models_backfilled": 0,
    }

    records = [json.loads(line) for line in original_bytes.splitlines()]
    project_root = detect_project_root(records)

    anchor_sources: dict[str, str] = {}
    anchor_hashes: dict[str, dict[str, Any]] = {}
    anchor_missing: list[str] = []
    for name in ("AGENTS.md", "HANDOFF.md", "CLAUDE.md"):
        if project_root is None:
            anchor_missing.append(name)
            continue
        candidate = project_root / name
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved.exists():
            anchor_sources[name] = str(resolved)
            anchor_hashes[name] = anchor_digest(resolved)
        else:
            anchor_missing.append(str(resolved))
    if len(anchor_missing) == 3:
        warnings.append("All anchor files missing (AGENTS.md, HANDOFF.md, CLAUDE.md).")

    transformed = [compact_record(obj, args, state) for obj in records]
    state["assistant_models_backfilled"] = backfill_assistant_models(transformed, records)
    validate_claude_records(transformed)
    compacted_bytes = b"".join(
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        for row in transformed
    )
    compacted_validation = validate_jsonl_bytes(compacted_bytes)
    compacted_sha256 = sha256_bytes(compacted_bytes)
    generated_at = transformed[-1].get("timestamp") if transformed else None

    report = {
        "source": str(source),
        "original_copy": str(original_copy),
        "compacted_copy": str(compacted_copy),
        "original_sha256": original_sha256,
        "compacted_sha256": compacted_sha256,
        "original_bytes": len(original_bytes),
        "compacted_bytes": len(compacted_bytes),
        "bytes_saved": len(original_bytes) - len(compacted_bytes),
        "original_lines": original_validation["line_count"],
        "compacted_lines": compacted_validation["line_count"],
        "manifest_path": str(manifest_path),
        "changes": state,
        "warnings": warnings,
        "anchor_sources": anchor_sources,
        "anchor_hashes": anchor_hashes,
        "anchor_missing": anchor_missing,
        "policy": {
            "profile": "safe",
            "max_tool_output_chars": args.max_tool_output_chars,
            "max_file_history_entries": args.max_file_history_entries,
            "strip_thinking_blocks": True,
        },
    }

    manifest = build_compaction_manifest(
        source=source,
        original_copy=original_copy,
        compacted_copy=compacted_copy,
        report_path=report_path,
        source_sha256=original_sha256,
        compacted_sha256=compacted_sha256,
        profile="claude-safe",
        generated_at=generated_at,
        original_lines=original_validation["line_count"],
        compacted_lines=compacted_validation["line_count"],
        bytes_saved=len(original_bytes) - len(compacted_bytes),
        keep_last_turns=0,
        max_replacement_records=0,
    )

    report_bytes = (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    publish_artifacts(
        source,
        original_bytes,
        output_root,
        [
            (original_copy, original_bytes),
            (compacted_copy, compacted_bytes),
            (report_path, report_bytes),
            (manifest_path, manifest_bytes),
        ],
        manifest_path,
    )
    if args.show_summary:
        print(
            json.dumps(
                {
                    "source": str(source),
                    "bytes_saved": report["bytes_saved"],
                    "changes": state,
                    "warnings": warnings,
                    "report_path": str(report_path),
                    "manifest_path": str(manifest_path),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
