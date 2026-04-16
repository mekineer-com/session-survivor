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


DEFAULT_OUTPUT_ROOT = pathlib.Path(__file__).resolve().parent / "outputs" / "gemini"
TOOL_OUTPUT_PLACEHOLDER = "[Compacted Gemini tool output"
THINKING_PLACEHOLDER = "[Compacted Gemini thinking"
MESSAGE_PLACEHOLDER = "[Compacted Gemini message content"
DISPLAY_PLACEHOLDER = "[Compacted Gemini display content"


def is_existing_compaction_placeholder(text: str) -> bool:
    return (
        "\n... [Compacted Gemini" in text
        and "; original length=" in text
        and text.rstrip().endswith(" chars]")
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create conservative compacted copies of Gemini CLI JSON session logs."
    )
    parser.add_argument("session", help="Path to a Gemini session JSON file.")
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Root directory for original/compacted/report outputs.",
    )
    parser.add_argument(
        "--max-tool-output-chars",
        type=int,
        default=800,
        help="Keep at most this many chars in bulky tool output fields.",
    )
    parser.add_argument(
        "--max-thinking-chars",
        type=int,
        default=240,
        help="Keep at most this many chars of thought descriptions.",
    )
    parser.add_argument(
        "--max-message-content-chars",
        type=int,
        default=12000,
        help="Keep at most this many chars in oversized message/display content.",
    )
    parser.add_argument(
        "--max-tool-args-chars",
        type=int,
        default=4000,
        help="Keep at most this many chars in oversized tool call args strings.",
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
    if is_existing_compaction_placeholder(text):
        return text, False
    if len(text) <= max_chars:
        return text, False
    kept = text[:max_chars].rstrip()
    compacted = f"{kept}\n... {label}; original length={len(text)} chars]"
    return compacted, True


def validate_json_bytes(data: bytes) -> dict[str, int]:
    parsed = json.loads(data.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("Session JSON root must be an object.")
    line_count = len(data.splitlines()) or 1
    return {"line_count": line_count}


def validate_json_file(path: pathlib.Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8")
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Session JSON root must be an object.")
    line_count = len(text.splitlines()) or 1
    return {"line_count": line_count}


def validate_with_jq(path: pathlib.Path) -> bool:
    jq = shutil.which("jq")
    if not jq:
        return False
    result = subprocess.run(
        [jq, "-c", ".", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def relative_output_path(path: pathlib.Path) -> pathlib.Path:
    parts = path.resolve().parts
    if ".gemini" in parts:
        idx = parts.index(".gemini")
        return pathlib.Path(*parts[idx + 1 :])
    return pathlib.Path(path.name)


def write_thread_marker(
    source: pathlib.Path,
    compacted_copy: pathlib.Path,
    report_path: pathlib.Path,
    manifest_path: pathlib.Path,
    profile: str,
    source_sha256: str,
    session_id: str,
) -> pathlib.Path:
    marker_root = pathlib.Path.home() / ".gemini" / "session-survivor"
    marker_path = marker_root / "thread-markers.jsonl"
    marker_key_dir = marker_root / "thread-marker-keys"
    marker_root.mkdir(parents=True, exist_ok=True)
    marker_key_dir.mkdir(parents=True, exist_ok=True)

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


def compact_nested_strings(
    value: Any, max_chars: int, label: str, state: dict[str, int], counter_key: str
) -> Any:
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


def compact_tool_call(item: dict[str, Any], args: argparse.Namespace, state: dict[str, int]) -> dict[str, Any]:
    out = copy.deepcopy(item)

    if "resultDisplay" in out:
        result_display = out.get("resultDisplay")
        if isinstance(result_display, str):
            compacted, changed = shorten(result_display, args.max_tool_output_chars, TOOL_OUTPUT_PLACEHOLDER)
            out["resultDisplay"] = compacted
            if changed:
                state["tool_result_display_truncated"] += 1
        else:
            out["resultDisplay"] = compact_nested_strings(
                result_display,
                args.max_tool_output_chars,
                TOOL_OUTPUT_PLACEHOLDER,
                state,
                "tool_result_display_nested_truncated",
            )

    if "result" in out:
        out["result"] = compact_nested_strings(
            out.get("result"),
            args.max_tool_output_chars,
            TOOL_OUTPUT_PLACEHOLDER,
            state,
            "tool_result_strings_truncated",
        )

    if "args" in out:
        out["args"] = compact_nested_strings(
            out.get("args"),
            args.max_tool_args_chars,
            TOOL_OUTPUT_PLACEHOLDER,
            state,
            "tool_args_strings_truncated",
        )
    return out


def compact_message(item: dict[str, Any], args: argparse.Namespace, state: dict[str, int]) -> dict[str, Any]:
    out = copy.deepcopy(item)

    if "content" in out:
        content = out.get("content")
        if isinstance(content, str):
            compacted, changed = shorten(content, args.max_message_content_chars, MESSAGE_PLACEHOLDER)
            out["content"] = compacted
            if changed:
                state["message_content_truncated"] += 1
        elif isinstance(content, list):
            out["content"] = compact_nested_strings(
                content,
                args.max_message_content_chars,
                MESSAGE_PLACEHOLDER,
                state,
                "message_content_nested_truncated",
            )

    if isinstance(out.get("displayContent"), str):
        compacted, changed = shorten(out["displayContent"], args.max_message_content_chars, DISPLAY_PLACEHOLDER)
        out["displayContent"] = compacted
        if changed:
            state["display_content_truncated"] += 1

    thoughts = out.get("thoughts")
    if isinstance(thoughts, list):
        compacted_thoughts = []
        for thought in thoughts:
            if not isinstance(thought, dict):
                compacted_thoughts.append(thought)
                continue
            thought_copy = copy.deepcopy(thought)
            description = thought_copy.get("description")
            if isinstance(description, str):
                compacted, changed = shorten(description, args.max_thinking_chars, THINKING_PLACEHOLDER)
                thought_copy["description"] = compacted
                if changed:
                    state["thinking_text_truncated"] += 1
            compacted_thoughts.append(thought_copy)
        out["thoughts"] = compacted_thoughts

    tool_calls = out.get("toolCalls")
    if isinstance(tool_calls, list):
        out["toolCalls"] = [compact_tool_call(tc, args, state) if isinstance(tc, dict) else tc for tc in tool_calls]

    return out


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
    source_obj = json.loads(original_bytes.decode("utf-8"))
    if not isinstance(source_obj, dict):
        raise SystemExit(f"Unexpected Gemini session format (root is not object): {source}")

    original_copy.parent.mkdir(parents=True, exist_ok=True)
    compacted_copy.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    original_copy.write_bytes(original_bytes)

    original_sha256 = sha256_bytes(original_bytes)
    original_validation = validate_json_bytes(original_bytes)

    state = {
        "tool_result_display_truncated": 0,
        "tool_result_display_nested_truncated": 0,
        "tool_result_strings_truncated": 0,
        "tool_args_strings_truncated": 0,
        "thinking_text_truncated": 0,
        "message_content_truncated": 0,
        "message_content_nested_truncated": 0,
        "display_content_truncated": 0,
    }

    transformed = copy.deepcopy(source_obj)
    messages = transformed.get("messages")
    if isinstance(messages, list):
        transformed["messages"] = [compact_message(m, args, state) if isinstance(m, dict) else m for m in messages]

    compacted_copy.write_text(json.dumps(transformed, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    compacted_validation = validate_json_file(compacted_copy)
    jq_ok = validate_with_jq(compacted_copy)
    compacted_bytes = compacted_copy.read_bytes()
    compacted_sha256 = sha256_bytes(compacted_bytes)

    generated_at = transformed.get("lastUpdated")
    original_messages = len(source_obj.get("messages", [])) if isinstance(source_obj.get("messages"), list) else 0
    compacted_messages = len(transformed.get("messages", [])) if isinstance(transformed.get("messages"), list) else 0

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
        "original_messages": original_messages,
        "compacted_messages": compacted_messages,
        "jq_valid": jq_ok,
        "manifest_path": str(manifest_path),
        "changes": state,
        "policy": {
            "profile": "safe",
            "max_tool_output_chars": args.max_tool_output_chars,
            "max_thinking_chars": args.max_thinking_chars,
            "max_message_content_chars": args.max_message_content_chars,
            "max_tool_args_chars": args.max_tool_args_chars,
        },
    }

    manifest = build_compaction_manifest(
        source=source,
        original_copy=original_copy,
        compacted_copy=compacted_copy,
        report_path=report_path,
        source_sha256=original_sha256,
        compacted_sha256=compacted_sha256,
        profile="gemini-safe",
        generated_at=generated_at,
        original_lines=original_validation["line_count"],
        compacted_lines=compacted_validation["line_count"],
        bytes_saved=len(original_bytes) - len(compacted_bytes),
        keep_last_turns=0,
        max_replacement_records=0,
    )

    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    session_id = transformed.get("sessionId") if isinstance(transformed.get("sessionId"), str) else source.stem
    marker_path = write_thread_marker(
        source=source,
        compacted_copy=compacted_copy,
        report_path=report_path,
        manifest_path=manifest_path,
        profile="gemini-safe",
        source_sha256=original_sha256,
        session_id=session_id,
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
