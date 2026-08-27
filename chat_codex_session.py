#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any

from compact_codex_session import MAX_COMPACTED_REPLACEMENT_HISTORY, compact_record, core_format_warnings
from lineage import build_compaction_manifest, describe_lineage


SESSION_ROOT = pathlib.Path.home() / ".codex" / "sessions"
DEFAULT_OUTPUT_ROOT = pathlib.Path("/home/marcos/apps-codex/session-survivor/outputs/codex-chat-resume-hybrid-safe-tail")
PLACEHOLDER = "[Compacted Codex chat message"
MAX_PRE_BOUNDARY_HEADER_BYTES = 512_000
MAX_PRE_BOUNDARY_HEADER_RECORDS = 128
DEFAULT_REPLACEMENT_HISTORY_USER_MESSAGES = 50


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a Codex hybrid chat-resume copy: keep conversation text "
            "for old turns and preserve a native safe tail for continuity."
        )
    )
    parser.add_argument("session", nargs="?", help="Path to a Codex rollout JSONL file.")
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Use the latest rollout JSONL under ~/.codex/sessions (mutually exclusive with SESSION path).",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Root directory for original/compacted/report outputs.",
    )
    parser.add_argument(
        "--max-message-chars",
        type=int,
        default=20000,
        help="Keep at most this many chars per chat message.",
    )
    parser.add_argument(
        "--safe-tail-turns",
        type=int,
        default=1,
        help="Keep this many most recent turns in native safe-compacted Codex schema.",
    )
    parser.add_argument(
        "--max-tool-input-chars",
        type=int,
        default=400,
        help="Keep at most this many chars of bulky function/custom tool inputs in safe tail.",
    )
    parser.add_argument(
        "--max-reasoning-chars",
        type=int,
        default=240,
        help="Keep at most this many chars of agent reasoning text in safe tail.",
    )
    parser.add_argument(
        "--keep-replacement-history-user-messages",
        type=int,
        default=DEFAULT_REPLACEMENT_HISTORY_USER_MESSAGES,
        help="Keep this many newest non-summary user messages inside compacted replacement_history.",
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


def is_continuity_summary_text(text: str) -> bool:
    text = text.lstrip()
    if text.startswith("[Codex]"):
        text = text[len("[Codex]") :].lstrip()
    return text.startswith("## Week of ")


def prune_compacted_replacement_history(
    obj: dict[str, Any],
    args: argparse.Namespace,
    state: dict[str, int],
) -> dict[str, Any]:
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return obj
    history = payload.get("replacement_history")
    if not isinstance(history, list):
        return obj

    ordinary_user_indices: list[int] = []
    summary_user_indices: set[int] = set()
    for idx, item in enumerate(history):
        if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "user":
            continue
        if is_continuity_summary_text(extract_message_text(item.get("content"))):
            summary_user_indices.add(idx)
        else:
            ordinary_user_indices.append(idx)

    keep_count = max(0, args.keep_replacement_history_user_messages)
    recent_user_indices = set(ordinary_user_indices[-keep_count:]) if keep_count else set()
    new_history: list[Any] = []
    pruned = kept_summary = kept_recent = kept_other = 0
    for idx, item in enumerate(history):
        is_user = isinstance(item, dict) and item.get("type") == "message" and item.get("role") == "user"
        if not is_user:
            new_history.append(item)
            kept_other += 1
        elif idx in summary_user_indices:
            new_history.append(item)
            kept_summary += 1
        elif idx in recent_user_indices:
            new_history.append(item)
            kept_recent += 1
        else:
            pruned += 1

    state["pruned_replacement_history_user_messages"] += pruned
    state["kept_replacement_history_summary_user_messages"] += kept_summary
    state["kept_replacement_history_recent_user_messages"] += kept_recent
    state["kept_replacement_history_other_items"] += kept_other
    if not pruned:
        return obj
    new_payload = dict(payload)
    new_payload["replacement_history"] = new_history
    new_obj = dict(obj)
    new_obj["payload"] = new_payload
    return new_obj


def turn_boundary_type(obj: dict[str, Any]) -> str:
    """Return boundary event type that shapes turn timeline, else empty string."""
    if obj.get("type") != "event_msg":
        return ""
    ptype = obj.get("payload", {}).get("type", "")
    if ptype in ("task_started", "task_complete", "turn_aborted"):
        return ptype
    return ""


def split_session_objects(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    """Split records into pre-turn header rows and turn groups keyed by task_started."""
    header: list[dict[str, Any]] = []
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] | None = None

    for obj in records:
        if turn_boundary_type(obj) == "task_started":
            if current is not None:
                turns.append(current)
            current = [obj]
            continue
        if current is None:
            header.append(obj)
        else:
            current.append(obj)

    if current is not None:
        turns.append(current)
    return header, turns


def pre_boundary_payload_rows(header: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [obj for obj in header if obj.get("type") != "session_meta"]


def closing_task_complete(turn: list[dict[str, Any]], state: dict[str, int]) -> dict[str, Any]:
    started = next((obj for obj in turn if turn_boundary_type(obj) == "task_started"), None)
    if started is None:
        raise ValueError("Cannot close turn without task_started.")
    payload = started.get("payload", {})
    turn_id = payload.get("turn_id") if isinstance(payload, dict) else None
    if not isinstance(turn_id, str) or not turn_id:
        raise ValueError("Cannot close turn without task_started turn_id.")

    timestamp = next(
        (
            obj.get("timestamp")
            for obj in reversed(turn)
            if isinstance(obj.get("timestamp"), str) and obj.get("timestamp")
        ),
        "1970-01-01T00:00:00.000Z",
    )
    state["synthetic_old_task_complete"] += 1
    return {
        "type": "event_msg",
        "timestamp": timestamp,
        "payload": {
            "type": "task_complete",
            "turn_id": turn_id,
            "last_agent_message": None,
        },
    }


def compact_chat_records(
    records: list[dict[str, Any]],
    args: argparse.Namespace,
    state: dict[str, int],
) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for idx, obj in enumerate(records):
        boundary = turn_boundary_type(obj)
        if boundary == "turn_aborted":
            state["dropped_old_boundary_events"] += 1
            continue
        if boundary in {"task_started", "task_complete"}:
            if boundary == "task_complete" and isinstance(obj.get("payload"), dict) and "error" in obj["payload"]:
                obj = dict(obj)
                obj["payload"] = dict(obj["payload"])
                obj["payload"].pop("error")
                state["dropped_old_task_complete_errors"] += 1
            # Preserve turn boundaries so resume can retain chronological
            # structure in older compacted history.
            compacted.append(obj)
            state["kept_old_boundary_events"] += 1
            continue

        # Preserve compacted records here; old-history anchor policy is applied
        # once across header + all old turns so "latest" means latest globally.
        if obj.get("type") == "compacted":
            compacted.append(obj)
            continue

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

        # Emit native Codex message records so resume can parse the file.
        content_type = "output_text" if role == "assistant" else "input_text"
        new_payload: dict[str, Any] = {
            "type": "message",
            "role": role,
            "content": [{"type": content_type, "text": text}],
        }
        phase = payload.get("phase")
        if isinstance(phase, str) and phase:
            new_payload["phase"] = phase
        compacted.append(
            {
                "type": "response_item",
                "timestamp": timestamp,
                "payload": new_payload,
            }
        )
        state["kept_chat_records"] += 1

    return compacted


def apply_old_compacted_anchor_policy(
    rows: list[dict[str, Any]],
    args: argparse.Namespace,
    state: dict[str, int],
) -> list[dict[str, Any]]:
    """Apply compacted-anchor policy across all old-history rows."""
    last_compacted_index = None
    for idx, obj in enumerate(rows):
        if obj.get("type") == "compacted":
            last_compacted_index = idx

    filtered: list[dict[str, Any]] = []
    for idx, obj in enumerate(rows):
        if obj.get("type") != "compacted":
            filtered.append(obj)
            continue
        if idx == last_compacted_index:
            filtered.append(prune_compacted_replacement_history(obj, args, state))
            state["kept_compacted_anchor"] += 1
        else:
            append_compacted_anchor(filtered, strip_compacted_replacement_history(obj, state), state)
    return filtered


def append_compacted_anchor(
    rows: list[dict[str, Any]],
    obj: dict[str, Any],
    state: dict[str, int],
) -> None:
    payload = obj.get("payload")
    message = payload.get("message") if isinstance(payload, dict) else None
    if isinstance(message, str) and message.strip():
        rows.append(obj)
        state["kept_compacted_anchor"] += 1
        return
    state["dropped_empty_compacted_anchor"] += 1


def strip_compacted_replacement_history(obj: dict[str, Any], state: dict[str, int]) -> dict[str, Any]:
    payload = obj.get("payload")
    if not isinstance(payload, dict) or "replacement_history" not in payload:
        return obj
    new_payload = dict(payload)
    new_payload.pop("replacement_history", None)
    new_obj = dict(obj)
    new_obj["payload"] = new_payload
    state["stripped_compacted_replacement_history"] += 1
    return new_obj


def compact_old_turn(
    turn: list[dict[str, Any]],
    args: argparse.Namespace,
    state: dict[str, int],
) -> list[dict[str, Any]]:
    rows = compact_chat_records(turn, args, state)
    if not any(turn_boundary_type(obj) == "task_complete" for obj in turn):
        rows.append(closing_task_complete(turn, state))

    messages = [
        obj
        for obj in rows
        if obj.get("type") == "response_item"
        and isinstance(obj.get("payload"), dict)
        and obj["payload"].get("type") == "message"
    ]
    user = next((obj for obj in messages if obj["payload"].get("role") == "user"), None)
    assistants = [obj for obj in messages if obj["payload"].get("role") == "assistant"]
    agent = next(
        (obj for obj in reversed(assistants) if obj["payload"].get("phase") == "final_answer"),
        assistants[-1] if assistants else None,
    )

    if user is not None:
        payload = user["payload"]
        event = {
            "type": "event_msg",
            "timestamp": user.get("timestamp"),
            "payload": {"type": "user_message", "message": extract_message_text(payload.get("content"))},
        }
        start_index = next((i for i, obj in enumerate(rows) if turn_boundary_type(obj) == "task_started"), -1)
        rows.insert(start_index + 1, event)
        state["synthetic_old_user_message_events"] += 1

    if agent is not None:
        payload = agent["payload"]
        event = {
            "type": "event_msg",
            "timestamp": agent.get("timestamp"),
            "payload": {
                "type": "agent_message",
                "message": extract_message_text(payload.get("content")),
                "phase": payload.get("phase"),
            },
        }
        complete_index = next(
            (i for i, obj in enumerate(rows) if turn_boundary_type(obj) == "task_complete"),
            len(rows),
        )
        rows.insert(complete_index, event)
        state["synthetic_old_agent_message_events"] += 1
    return rows


def main() -> int:
    args = parse_args()
    if args.max_message_chars < 80:
        raise SystemExit("max-message-chars must be >= 80.")
    if args.safe_tail_turns < 1:
        raise SystemExit("safe-tail-turns must be >= 1.")
    if args.max_tool_input_chars < 40:
        raise SystemExit("max-tool-input-chars must be >= 40.")
    if args.max_reasoning_chars < 40:
        raise SystemExit("max-reasoning-chars must be >= 40.")
    if args.latest and args.session:
        raise SystemExit("Use either --latest or a session path, not both.")

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
        "kept_header_records": 0,
        "kept_compacted_anchor": 0,
        "dropped_compacted_anchor": 0,
        "dropped_empty_compacted_anchor": 0,
        "stripped_compacted_replacement_history": 0,
        "kept_old_boundary_events": 0,
        "kept_safe_tail_turns": 0,
        "kept_safe_tail_records": 0,
        "chat_compacted_old_turns": 0,
        "dropped_non_response_item": 0,
        "dropped_non_message": 0,
        "dropped_non_chat_role": 0,
        "dropped_non_text": 0,
        "dropped_bootstrap_noise": 0,
        "dropped_meta_noise": 0,
        "dropped_old_boundary_events": 0,
        "dropped_old_task_complete_errors": 0,
        "synthetic_old_task_complete": 0,
        "synthetic_old_user_message_events": 0,
        "synthetic_old_agent_message_events": 0,
        "messages_truncated": 0,
        "synthetic_timestamp_assigned": 0,
        "safe_tail_reasoning_encrypted_removed": 0,
        "safe_tail_tool_outputs_truncated": 0,
        "safe_tail_tool_inputs_truncated": 0,
        "safe_tail_agent_reasoning_truncated": 0,
        "safe_tail_duplicated_instruction_messages": 0,
        "safe_tail_scratch_artifacts_removed": 0,
        "pruned_replacement_history_user_messages": 0,
        "kept_replacement_history_summary_user_messages": 0,
        "kept_replacement_history_recent_user_messages": 0,
        "kept_replacement_history_other_items": 0,
    }

    format_warnings = core_format_warnings(records)
    if format_warnings:
        raise SystemExit("Input session format drift: " + " | ".join(format_warnings))

    header_rows, turns = split_session_objects(records)
    if not turns:
        raise SystemExit("Input session has no task_started turns; refusing chat compaction output.")
    pre_boundary_rows = pre_boundary_payload_rows(header_rows)
    pre_boundary_bytes = sum(len(json.dumps(obj, ensure_ascii=False)) + 1 for obj in pre_boundary_rows)
    if len(pre_boundary_rows) > MAX_PRE_BOUNDARY_HEADER_RECORDS or pre_boundary_bytes > MAX_PRE_BOUNDARY_HEADER_BYTES:
        raise SystemExit(
            "Pre-boundary history is too large for chat compaction "
            f"({len(pre_boundary_rows)} records, {pre_boundary_bytes} bytes before first task_started). "
            "Run repair_codex_preboundary_header.py first, then rerun chat_codex_session.py."
        )
    state["kept_header_records"] = len(header_rows)

    safe_tail_turns = min(args.safe_tail_turns, len(turns))
    old_turns = turns[:-safe_tail_turns] if safe_tail_turns else []
    tail_turns = turns[-safe_tail_turns:] if safe_tail_turns else []
    state["chat_compacted_old_turns"] = len(old_turns)
    state["kept_safe_tail_turns"] = len(tail_turns)

    chat_rows = []
    for turn in old_turns:
        chat_rows.extend(compact_old_turn(turn, args, state))

    old_history_rows = apply_old_compacted_anchor_policy(header_rows + chat_rows, args, state)

    safe_tail_state = {
        "reasoning_encrypted_removed": 0,
        "tool_outputs_truncated": 0,
        "tool_inputs_truncated": 0,
        "agent_reasoning_truncated": 0,
        "duplicated_instruction_messages": 0,
        "scratch_artifacts_removed": 0,
    }
    safe_tail_rows: list[dict[str, Any]] = []
    for turn in tail_turns:
        for obj in turn:
            row = compact_record(obj, args, safe_tail_state)
            if row.get("type") == "compacted":
                row = prune_compacted_replacement_history(row, args, state)
            safe_tail_rows.append(row)

    state["kept_safe_tail_records"] = len(safe_tail_rows)
    state["safe_tail_reasoning_encrypted_removed"] = safe_tail_state["reasoning_encrypted_removed"]
    state["safe_tail_tool_outputs_truncated"] = safe_tail_state["tool_outputs_truncated"]
    state["safe_tail_tool_inputs_truncated"] = safe_tail_state["tool_inputs_truncated"]
    state["safe_tail_agent_reasoning_truncated"] = safe_tail_state["agent_reasoning_truncated"]
    state["safe_tail_duplicated_instruction_messages"] = safe_tail_state["duplicated_instruction_messages"]
    state["safe_tail_scratch_artifacts_removed"] = safe_tail_state["scratch_artifacts_removed"]

    if not old_history_rows and not safe_tail_rows:
        raise SystemExit("No records survived filtering; refusing to write empty output file.")

    with compacted_copy.open("w", encoding="utf-8") as dst:
        for row in old_history_rows:
            dst.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        for row in safe_tail_rows:
            dst.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    compacted_validation = validate_jsonl(compacted_copy)
    compacted_bytes = compacted_copy.read_bytes()
    compacted_sha256 = sha256_bytes(compacted_bytes)
    generated_at = None
    if safe_tail_rows:
        generated_at = safe_tail_rows[-1].get("timestamp")
    elif chat_rows:
        generated_at = chat_rows[-1].get("timestamp")
    elif header_rows:
        generated_at = header_rows[-1].get("timestamp")

    compacted_anchor_policy = "latest_replacement_history_pruned_older_stripped"
    old_history_output_type = "chat-compacted-history(response_item.message + compacted(shells, latest pruned))"

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
        "warnings": [],
        "policy": {
            "profile": "codex-chat-resume-hybrid-safe-tail",
            "max_message_chars": args.max_message_chars,
            "safe_tail_turns": args.safe_tail_turns,
            "max_tool_input_chars": args.max_tool_input_chars,
            "max_reasoning_chars": args.max_reasoning_chars,
            "keep_replacement_history_user_messages": args.keep_replacement_history_user_messages,
            "kept_roles": ["user", "assistant"],
            "kept_compacted_anchor": compacted_anchor_policy,
            "chat_history_kept_boundary_event_types": ["task_started", "task_complete"],
            "chat_history_dropped_event_types": ["turn_aborted"],
            "chat_history_synthetic_event_types": ["user_message", "agent_message"],
            "safe_tail_kept_record_types": ["event_msg", "response_item", "turn_context", "world_state", "compacted"],
            "output_record_types": [
                "session_meta/header",
                old_history_output_type,
                "event_msg.user_message + event_msg.agent_message(summary replay)",
                "safe-tail(native turn records)",
            ],
        },
    }

    manifest = build_compaction_manifest(
        source=source,
        original_copy=original_copy,
        compacted_copy=compacted_copy,
        report_path=report_path,
        source_sha256=original_sha256,
        compacted_sha256=compacted_sha256,
        profile="codex-chat-resume-hybrid-safe-tail",
        generated_at=generated_at,
        original_lines=original_validation["line_count"],
        compacted_lines=compacted_validation["line_count"],
        bytes_saved=len(original_bytes) - len(compacted_bytes),
        keep_last_turns=0,
        max_replacement_records=MAX_COMPACTED_REPLACEMENT_HISTORY,
    )
    manifest.setdefault("policy", {})
    manifest["policy"]["max_message_chars"] = args.max_message_chars
    manifest["policy"]["safe_tail_turns"] = args.safe_tail_turns
    manifest["policy"]["max_tool_input_chars"] = args.max_tool_input_chars
    manifest["policy"]["max_reasoning_chars"] = args.max_reasoning_chars
    manifest["policy"]["keep_replacement_history_user_messages"] = args.keep_replacement_history_user_messages
    manifest["policy"]["kept_roles"] = ["user", "assistant"]
    manifest["policy"]["kept_compacted_anchor"] = compacted_anchor_policy
    manifest["policy"]["compacted_replacement_history"] = "latest_native_checkpoint_pruned_older_stripped"
    manifest["policy"]["chat_history_kept_boundary_event_types"] = ["task_started", "task_complete"]
    manifest["policy"]["chat_history_dropped_event_types"] = ["turn_aborted"]
    manifest["policy"]["chat_history_synthetic_event_types"] = ["user_message", "agent_message"]
    manifest["policy"]["safe_tail_kept_record_types"] = [
        "event_msg",
        "response_item",
        "turn_context",
        "world_state",
        "compacted",
    ]
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
