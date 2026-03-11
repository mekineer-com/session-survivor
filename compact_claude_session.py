#!/usr/bin/env python3

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
from typing import Any

from lineage import build_compaction_manifest, describe_lineage


DEFAULT_OUTPUT_ROOT = pathlib.Path("/home/marcos/apps-codex/session-survivor/outputs/claude")
TOOL_OUTPUT_PLACEHOLDER = "[Compacted Claude tool result"
THINKING_PLACEHOLDER = "[Compacted Claude thinking"
LOCAL_COMMAND_PLACEHOLDER = "[Compacted Claude local command"


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
        "--max-thinking-chars",
        type=int,
        default=240,
        help="Keep at most this many chars of Claude thinking text.",
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


def shorten(text: str, max_chars: int, label: str) -> tuple[str, bool]:
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


def compact_tool_use_result(value: Any, max_chars: int, state: dict[str, int]) -> Any:
    if not isinstance(value, dict):
        return value
    result = copy.deepcopy(value)
    for key, field in list(result.items()):
        if not isinstance(field, str):
            continue
        compacted, changed = shorten(field, max_chars, TOOL_OUTPUT_PLACEHOLDER)
        if changed:
            result[key] = compacted
            state["tool_outputs_truncated"] += 1
    return result


def compact_message_content(item: dict[str, Any], args: argparse.Namespace, state: dict[str, int]) -> dict[str, Any]:
    out = copy.deepcopy(item)
    item_type = out.get("type")

    if item_type == "thinking":
        if isinstance(out.get("signature"), str) and out["signature"]:
            out["signature"] = ""
            state["thinking_signatures_removed"] += 1
        if isinstance(out.get("thinking"), str):
            compacted, changed = shorten(out["thinking"], args.max_thinking_chars, THINKING_PLACEHOLDER)
            out["thinking"] = compacted
            if changed:
                state["thinking_text_truncated"] += 1

    elif item_type == "tool_result":
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
                message["content"] = [
                    compact_message_content(entry, args, state) if isinstance(entry, dict) else entry
                    for entry in content
                ]
            elif isinstance(content, str):
                compacted, changed = shorten(content, args.max_tool_output_chars, TOOL_OUTPUT_PLACEHOLDER)
                message["content"] = compacted
                if changed:
                    state["message_content_truncated"] += 1

        if "toolUseResult" in item:
            item["toolUseResult"] = compact_tool_use_result(item.get("toolUseResult"), args.max_tool_output_chars, state)

    elif item_type == "system" and item.get("subtype") == "local_command":
        content = item.get("content")
        if isinstance(content, str):
            compacted, changed = shorten(content, args.max_tool_output_chars, LOCAL_COMMAND_PLACEHOLDER)
            item["content"] = compacted
            if changed:
                state["local_command_truncated"] += 1

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
        "thinking_signatures_removed": 0,
        "thinking_text_truncated": 0,
        "tool_outputs_truncated": 0,
        "message_content_truncated": 0,
        "local_command_truncated": 0,
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
            "max_thinking_chars": args.max_thinking_chars,
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

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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
