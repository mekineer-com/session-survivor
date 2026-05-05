#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any

from compact_codex_session import compact_record
from lineage import build_compaction_manifest, describe_lineage


SESSION_ROOT = pathlib.Path.home() / ".codex" / "sessions"
DEFAULT_OUTPUT_ROOT = pathlib.Path("/home/marcos/apps-codex/session-survivor/outputs/codex-chat-plus-window")
PLACEHOLDER = "[Compacted Codex chat message"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a Codex continuity copy: keep full user/assistant conversation text, "
            "plus a safe-compacted sidecar for the latest pre-compaction window."
        )
    )
    parser.add_argument("session", nargs="?", help="Path to a Codex rollout JSONL file.")
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Use the latest rollout JSONL under ~/.codex/sessions.",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Root directory for original/compacted/report outputs.",
    )
    parser.add_argument(
        "--max-message-chars",
        type=int,
        default=4000,
        help="Keep at most this many chars per chat message.",
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


def latest_session(root: pathlib.Path) -> pathlib.Path:
    files = sorted(root.rglob("rollout-*.jsonl"))
    if not files:
        raise SystemExit(f"No rollout JSONL files found under {root}")
    return max(files, key=lambda p: p.stat().st_mtime)


def is_existing_compaction_placeholder(text: str) -> bool:
    return (
        "\n... [Compacted Codex chat message; original length=" in text
        and text.rstrip().endswith(" chars]")
    )


def shorten(text: str, max_chars: int) -> tuple[str, bool]:
    if is_existing_compaction_placeholder(text):
        return text, False
    if len(text) <= max_chars:
        return text, False
    kept = text[:max_chars].rstrip()
    compacted = f"{kept}\n... {PLACEHOLDER}; original length={len(text)} chars]"
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


def relative_output_path(path: pathlib.Path) -> pathlib.Path:
    try:
        return path.resolve().relative_to(SESSION_ROOT.resolve())
    except Exception:
        return pathlib.Path(path.name)


def is_meta_noise(text: str) -> bool:
    s = text.strip()
    if not s:
        return True
    if s.startswith("<subagent_notification>"):
        return True
    if s.startswith("{\"agent_id\":"):
        return True
    if s.startswith("\"status\":{\"completed\":"):
        return True
    return False


def is_bootstrap_noise(role: str, text: str) -> bool:
    if role != "user":
        return False
    s = text.strip()
    if not s:
        return False
    if s.startswith("[Compacted duplicate AGENTS instructions."):
        return True
    if s.startswith("# AGENTS.md instructions for "):
        return True
    if s.startswith("<environment_context>") and s.endswith("</environment_context>"):
        return True
    if "[Compacted duplicate AGENTS instructions." in s and "<environment_context>" in s:
        return True
    return False


def _extract_entry_text(entry: dict[str, Any]) -> str:
    for key in ("text", "input_text", "output_text"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = _extract_entry_text(item)
        if text:
            chunks.append(text)
    return "\n\n".join(chunks).strip()


def compact_chat_records(
    records: list[dict[str, Any]],
    args: argparse.Namespace,
    state: dict[str, int],
) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for obj in records:
        if obj.get("type") != "response_item":
            state["dropped_non_response_item"] += 1
            continue

        payload = obj.get("payload")
        if not isinstance(payload, dict):
            state["dropped_non_message"] += 1
            continue
        if payload.get("type") != "message":
            state["dropped_non_message"] += 1
            continue

        role = str(payload.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            state["dropped_non_chat_role"] += 1
            continue

        text = extract_message_text(payload.get("content"))
        if not text:
            state["dropped_non_text"] += 1
            continue
        if is_bootstrap_noise(role, text):
            state["dropped_bootstrap_noise"] += 1
            continue
        if is_meta_noise(text):
            state["dropped_meta_noise"] += 1
            continue

        text, changed = shorten(text, args.max_message_chars)
        if changed:
            state["messages_truncated"] += 1

        timestamp = obj.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp:
            timestamp = "1970-01-01T00:00:00.000Z"
            state["synthetic_timestamp_assigned"] += 1

        compacted.append(
            {
                "type": "chat_message",
                "timestamp": timestamp,
                "role": role,
                "phase": payload.get("phase"),
                "text": text,
            }
        )
        state["kept_chat_records"] += 1
    return compacted


def find_compaction_markers(records: list[dict[str, Any]]) -> list[int]:
    markers: list[int] = []
    for idx, obj in enumerate(records):
        if obj.get("type") == "compacted":
            markers.append(idx)
    return markers


def continuity_window_bounds(records: list[dict[str, Any]]) -> tuple[int | None, int | None, str]:
    markers = find_compaction_markers(records)
    if len(markers) >= 2:
        return markers[-2] + 1, markers[-1], "between_last_two_compactions"
    if len(markers) == 1:
        return markers[0] + 1, len(records), "after_last_compaction_fallback"
    return None, None, "no_compaction_marker"


def build_safe_continuity_records(
    records: list[dict[str, Any]],
    start_idx: int,
    end_idx: int,
    state: dict[str, int],
) -> list[dict[str, Any]]:
    safe_args = argparse.Namespace(
        profile="safe",
        max_tool_input_chars=400,
        max_reasoning_chars=240,
    )
    safe_state = {
        "reasoning_encrypted_removed": 0,
        "tool_outputs_truncated": 0,
        "tool_inputs_truncated": 0,
        "agent_reasoning_truncated": 0,
        "duplicated_instruction_messages": 0,
        "scratch_artifacts_removed": 0,
    }
    out: list[dict[str, Any]] = []
    for idx in range(start_idx, end_idx):
        safe_obj = compact_record(records[idx], safe_args, safe_state)
        timestamp = safe_obj.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp:
            timestamp = "1970-01-01T00:00:00.000Z"
            state["synthetic_timestamp_assigned"] += 1
        out.append(
            {
                "type": "continuity_record",
                "timestamp": timestamp,
                "source_window_index": idx,
                "record": safe_obj,
            }
        )
    state["continuity_records_kept"] = len(out)
    state["continuity_reasoning_encrypted_removed"] = safe_state["reasoning_encrypted_removed"]
    state["continuity_tool_outputs_compacted"] = safe_state["tool_outputs_truncated"]
    state["continuity_tool_inputs_compacted"] = safe_state["tool_inputs_truncated"]
    state["continuity_agent_reasoning_compacted"] = safe_state["agent_reasoning_truncated"]
    state["continuity_dup_instruction_compacted"] = safe_state["duplicated_instruction_messages"]
    state["continuity_scratch_compacted"] = safe_state["scratch_artifacts_removed"]
    return out


def main() -> int:
    args = parse_args()
    if args.max_message_chars < 80:
        raise SystemExit("max-message-chars must be >= 80.")

    if args.latest:
        source = latest_session(SESSION_ROOT)
    elif args.session:
        source = pathlib.Path(args.session).expanduser().resolve()
    else:
        raise SystemExit("Provide a session path or use --latest")

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
    records = [json.loads(line) for line in original_bytes.splitlines()]

    state = {
        "kept_chat_records": 0,
        "dropped_non_response_item": 0,
        "dropped_non_message": 0,
        "dropped_non_chat_role": 0,
        "dropped_non_text": 0,
        "dropped_bootstrap_noise": 0,
        "dropped_meta_noise": 0,
        "messages_truncated": 0,
        "synthetic_timestamp_assigned": 0,
        "continuity_records_kept": 0,
        "continuity_reasoning_encrypted_removed": 0,
        "continuity_tool_outputs_compacted": 0,
        "continuity_tool_inputs_compacted": 0,
        "continuity_agent_reasoning_compacted": 0,
        "continuity_dup_instruction_compacted": 0,
        "continuity_scratch_compacted": 0,
    }
    chat_rows = compact_chat_records(records, args, state)
    if not chat_rows:
        raise SystemExit("No chat records survived filtering; refusing to write empty output file.")
    window_start, window_end, window_kind = continuity_window_bounds(records)
    continuity_rows: list[dict[str, Any]] = []
    if window_start is not None and window_end is not None and window_end > window_start:
        continuity_rows = build_safe_continuity_records(records, window_start, window_end, state)

    compacted = chat_rows + continuity_rows
    with compacted_copy.open("w", encoding="utf-8") as dst:
        for row in compacted:
            dst.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    compacted_validation = validate_jsonl(compacted_copy)
    compacted_bytes = compacted_copy.read_bytes()
    compacted_sha256 = sha256_bytes(compacted_bytes)
    generated_at = chat_rows[-1].get("timestamp")

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
        "warnings": [] if window_kind == "between_last_two_compactions" else [
            f"Continuity window fallback used: {window_kind}"
        ],
        "continuity_window": {
            "kind": window_kind,
            "start_index": window_start,
            "end_index": window_end,
            "record_count": len(continuity_rows),
        },
        "policy": {
            "profile": "codex-chat-plus-last-window-safe",
            "max_message_chars": args.max_message_chars,
            "kept_roles": ["user", "assistant"],
            "kept_chat_record_shape": "chat_message",
            "continuity_record_shape": "continuity_record",
            "continuity_window_rule": "between last compaction marker and previous one; fallback to after-last marker",
        },
    }

    manifest = build_compaction_manifest(
        source=source,
        original_copy=original_copy,
        compacted_copy=compacted_copy,
        report_path=report_path,
        source_sha256=original_sha256,
        compacted_sha256=compacted_sha256,
        profile="codex-chat-plus-last-window-safe",
        generated_at=generated_at,
        original_lines=original_validation["line_count"],
        compacted_lines=compacted_validation["line_count"],
        bytes_saved=len(original_bytes) - len(compacted_bytes),
        keep_last_turns=0,
        max_replacement_records=0,
    )
    manifest.setdefault("policy", {})
    manifest["policy"]["max_message_chars"] = args.max_message_chars
    manifest["policy"]["kept_roles"] = ["user", "assistant"]
    manifest["policy"]["continuity_window"] = {
        "kind": window_kind,
        "start_index": window_start,
        "end_index": window_end,
        "record_count": len(continuity_rows),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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
