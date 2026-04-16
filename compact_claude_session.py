#!/usr/bin/env python3

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any

from lineage import build_compaction_manifest, describe_lineage


DEFAULT_OUTPUT_ROOT = pathlib.Path("/home/marcos/apps-codex/session-survivor/outputs/claude")
TOOL_OUTPUT_PLACEHOLDER = "[Compacted Claude tool result"
LOCAL_COMMAND_PLACEHOLDER = "[Compacted Claude local command"
FILE_HISTORY_PLACEHOLDER = "[Compacted Claude file history"


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
    parser.add_argument(
        "--show-lineage",
        action="store_true",
        help="Print lineage/provenance information for the input session and exit.",
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


def validate_jsonl(path: pathlib.Path) -> dict[str, int]:
    line_count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_count, line in enumerate(handle, 1):
            json.loads(line)
    return {"line_count": line_count}


def validate_jsonl_bytes(data: bytes) -> dict[str, int]:
    line_count = 0
    for line_count, line in enumerate(data.splitlines(), 1):
        json.loads(line)
    return {"line_count": line_count}


def validate_with_jq(path: pathlib.Path) -> bool:
    jq = shutil.which("jq")
    if not jq:
        return False
    result = subprocess.run(
        [jq, "-c", ".", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def relative_output_path(path: pathlib.Path) -> pathlib.Path:
    parts = path.resolve().parts
    if ".claude" in parts:
        idx = parts.index(".claude")
        return pathlib.Path(*parts[idx + 1 :])
    return pathlib.Path(path.name)


def write_thread_marker(
    source: pathlib.Path,
    compacted_copy: pathlib.Path,
    report_path: pathlib.Path,
    manifest_path: pathlib.Path,
    profile: str,
    source_sha256: str,
) -> pathlib.Path:
    marker_root = pathlib.Path.home() / ".claude" / "session-survivor"
    marker_path = marker_root / "thread-markers.jsonl"
    marker_key_dir = marker_root / "thread-marker-keys"
    marker_root.mkdir(parents=True, exist_ok=True)
    marker_key_dir.mkdir(parents=True, exist_ok=True)

    session_id = source.stem
    dedup_key = f"{session_id}:{source_sha256}:{profile}"
    key_hash = hashlib.sha256(dedup_key.encode("utf-8")).hexdigest()
    key_path = marker_key_dir / key_hash
    if key_path.exists():
        return marker_path

    marker = {
        "dedup_key": dedup_key,
        "session_id": session_id,
        "profile": profile,
        "source_sha256": source_sha256,
        "source": str(source),
        "compacted_copy": str(compacted_copy),
        "report_path": str(report_path),
        "manifest_path": str(manifest_path),
        "host": os.uname().nodename,
    }
    with marker_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(marker, ensure_ascii=False, separators=(",", ":")) + "\n")
    key_path.write_text(dedup_key + "\n", encoding="utf-8")
    return marker_path


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

    kept: dict[str, Any] = {}
    for idx, (file_path, backup_meta) in enumerate(tracked.items()):
        if idx >= max_entries:
            break
        if isinstance(backup_meta, dict):
            minimal: dict[str, Any] = {}
            if "version" in backup_meta:
                minimal["version"] = backup_meta["version"]
            if "backupTime" in backup_meta:
                minimal["backupTime"] = backup_meta["backupTime"]
            kept[file_path] = minimal if minimal else backup_meta
        else:
            kept[file_path] = backup_meta

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
            elif isinstance(content, str):
                compacted, changed = shorten(content, args.max_tool_output_chars, TOOL_OUTPUT_PLACEHOLDER)
                message["content"] = compacted
                if changed:
                    state["message_content_truncated"] += 1

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


def main() -> int:
    args = parse_args()
    source = pathlib.Path(args.session).expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"Session file not found: {source}")

    if args.show_lineage:
        print(json.dumps(describe_lineage(source), indent=2, ensure_ascii=False))
        return 0

    output_root = pathlib.Path(args.output_root).expanduser().resolve()
    rel = relative_output_path(source)
    original_copy = output_root / "original" / rel
    compacted_copy = output_root / "compacted" / rel
    report_path = output_root / "reports" / rel.with_suffix(".report.json")
    manifest_path = output_root / "manifests" / rel.with_suffix(".manifest.json")

    original_bytes = source.read_bytes()
    original_copy.parent.mkdir(parents=True, exist_ok=True)
    compacted_copy.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    original_copy.write_bytes(original_bytes)
    original_sha256 = sha256_bytes(original_bytes)
    original_validation = validate_jsonl_bytes(original_bytes)

    state = {
        "thinking_blocks_removed": 0,
        "tool_outputs_truncated": 0,
        "message_content_truncated": 0,
        "local_command_truncated": 0,
        "message_usage_compacted": 0,
        "file_history_snapshots_compacted": 0,
    }

    records = [json.loads(line) for line in original_bytes.splitlines()]
    transformed = [compact_record(obj, args, state) for obj in records]

    with compacted_copy.open("w", encoding="utf-8") as dst:
        for compacted in transformed:
            dst.write(json.dumps(compacted, ensure_ascii=False, separators=(",", ":")) + "\n")

    compacted_validation = validate_jsonl(compacted_copy)
    jq_ok = validate_with_jq(compacted_copy)
    compacted_bytes = compacted_copy.read_bytes()
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
        "jq_valid": jq_ok,
        "manifest_path": str(manifest_path),
        "changes": state,
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

    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    marker_path = write_thread_marker(
        source=source,
        compacted_copy=compacted_copy,
        report_path=report_path,
        manifest_path=manifest_path,
        profile="claude-safe",
        source_sha256=original_sha256,
    )
    report["thread_marker_path"] = str(marker_path)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.show_summary:
        print(
            json.dumps(
                {
                    "source": str(source),
                    "bytes_saved": report["bytes_saved"],
                    "changes": state,
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
