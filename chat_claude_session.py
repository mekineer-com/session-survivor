#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import uuid
from types import SimpleNamespace
from typing import Any

from artifact_publish import publish_artifacts
from compact_claude_session import (
    backfill_assistant_models,
    compact_record as compact_native_record,
    validate_claude_records,
)
from lineage import build_compaction_manifest


DEFAULT_OUTPUT_ROOT = pathlib.Path("/home/marcos/apps-codex/session-survivor/outputs/claude-chat-resume")
PLACEHOLDER = "[Compacted Claude chat message"
COMMAND_WRAPPER_RE = re.compile(
    r"^\s*<command-name>.*?</command-name>\s*"
    r"<command-message>.*?</command-message>\s*"
    r"<command-args>.*?</command-args>\s*$",
    re.DOTALL,
)
CONTINUATION_SUMMARY_PREFIX = "This session is being continued from a previous conversation that ran out of context."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a Claude chat-resume copy: user/assistant dialogue only, "
            "with resume-safe envelope fields."
        )
    )
    parser.add_argument("session", help="Path to a Claude session JSONL file.")
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Root directory for original/compacted/report outputs.",
    )
    parser.add_argument(
        "--max-message-chars",
        type=int,
        default=2400,
        help="Keep at most this many chars per merged chat message.",
    )
    parser.add_argument(
        "--safe-tail-turns",
        type=int,
        default=1,
        help="Keep this many most recent user turns in native safe-compacted Claude schema.",
    )
    parser.add_argument(
        "--max-tail-tool-output-chars",
        type=int,
        default=400,
        help="Keep at most this many chars of bulky tool output in the native safe tail.",
    )
    parser.add_argument(
        "--max-tail-file-history-entries",
        type=int,
        default=8,
        help="Keep at most this many file-history entries in the native safe tail.",
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
        "\n... [Compacted Claude chat message; original length=" in text
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


def extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    chunks: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())
    return "\n\n".join(chunks).strip()


def is_meta_noise(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    if normalized.startswith("<local-command-caveat>") and normalized.endswith("</local-command-caveat>"):
        return True
    if normalized.startswith("<local-command-stdout>") and normalized.endswith("</local-command-stdout>"):
        return True
    if normalized.startswith("<local-command-stderr>") and normalized.endswith("</local-command-stderr>"):
        return True
    if COMMAND_WRAPPER_RE.match(normalized):
        return True
    if normalized.startswith("<task-notification>"):
        return True
    return False


def is_continuation_summary(text: str) -> bool:
    normalized = text.strip()
    if not normalized.startswith(CONTINUATION_SUMMARY_PREFIX):
        return False
    return "\n\nSummary:" in normalized


def stable_uuid(source: pathlib.Path, line_no: int) -> str:
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, str(source))
    return str(uuid.uuid5(namespace, f"line-{line_no}"))


def latest_custom_title_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidate: dict[str, Any] | None = None
    for item in records:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "custom-title":
            continue
        title = item.get("customTitle")
        if isinstance(title, str) and title.strip():
            candidate = item
    return candidate


def detect_session_defaults(records: list[dict[str, Any]], source: pathlib.Path) -> dict[str, str]:
    keys = ("sessionId", "userType", "entrypoint", "cwd", "version", "gitBranch", "slug")
    defaults: dict[str, str] = {}
    for key in keys:
        for item in records:
            if not isinstance(item, dict):
                continue
            value = item.get(key)
            if isinstance(value, str) and value:
                defaults[key] = value
                break
    defaults.setdefault("sessionId", source.stem)
    return defaults


def chat_envelope_fields(obj: dict[str, Any], defaults: dict[str, str]) -> dict[str, Any]:
    keep_fields = (
        "parentUuid",
        "isSidechain",
        "sessionId",
        "userType",
        "entrypoint",
        "cwd",
        "version",
        "gitBranch",
        "slug",
        "permissionMode",
        "isVisibleInTranscriptOnly",
        "isMeta",
    )
    out: dict[str, Any] = {}
    for key in keep_fields:
        value = obj.get(key)
        if value is not None:
            out[key] = value
            continue
        if key == "permissionMode":
            continue
        fallback = defaults.get(key)
        if fallback:
            out[key] = fallback
    return out


def is_user_turn_start(obj: dict[str, Any]) -> bool:
    if obj.get("type") != "user" or obj.get("isCompactSummary"):
        return False
    message = obj.get("message")
    if not isinstance(message, dict) or message.get("role") != "user":
        return False
    content = message.get("content")
    if isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content
    ):
        return False
    text = extract_message_text(content)
    return bool(text and not is_meta_noise(text) and not is_continuation_summary(text))


def conversation_roots(records: list[dict[str, Any]]) -> dict[str, str]:
    # Native compact links join history; imported parentless summaries stay separate.
    by_uuid = {row["uuid"]: row for row in records if row.get("uuid")}
    roots: dict[str, str] = {}
    for start in by_uuid:
        current = start
        path: set[str] = set()
        while current not in roots:
            if current in path:
                raise ValueError("Claude conversation ancestry has a cycle.")
            path.add(current)
            row = by_uuid[current]
            parent = row.get("parentUuid")
            compact = row.get("type") == "system" and row.get("subtype") == "compact_boundary"
            if parent is None and compact:
                parent = row.get("logicalParentUuid")
            if parent is None:
                roots[current] = current
                break
            if not isinstance(parent, str) or parent not in by_uuid:
                raise ValueError("Claude conversation has an unresolved logical parent." if compact
                                 else "Claude conversation has an unresolved parent UUID.")
            current = parent
        root = roots[current]
        roots.update((uid, root) for uid in path)
    return roots


def active_branch_records(records: list[dict[str, Any]], state: dict[str, int]) -> list[dict[str, Any]]:
    by_uuid: dict[str, dict[str, Any]] = {}
    for row in records:
        row_uuid = row.get("uuid")
        if not isinstance(row_uuid, str) or not row_uuid:
            continue
        if row_uuid in by_uuid:
            raise ValueError(f"Duplicate Claude UUID: {row_uuid}")
        by_uuid[row_uuid] = row

    roots = conversation_roots(records)
    leaves: dict[str, dict[str, Any]] = {}
    for row in reversed(records):
        if row.get("uuid") in by_uuid and row.get("type") in ("user", "assistant") and not row.get("isSidechain"):
            leaves.setdefault(roots[row["uuid"]], row)
    leaf = next(iter(leaves.values()), None)
    if leaf is None:
        if by_uuid:
            raise ValueError("Claude session has no main-conversation branch.")
        return records
    active: set[str] = set()
    for row in leaves.values():
        while row is not None and row["uuid"] not in active:
            active.add(row["uuid"])
            parent = row.get("parentUuid")
            if parent is None and row.get("type") == "system" and row.get("subtype") == "compact_boundary":
                parent = row.get("logicalParentUuid")
            row = by_uuid.get(parent)

    children: dict[str, list[str]] = {}
    for row in records:
        parent = row.get("parentUuid")
        if isinstance(parent, str) and row.get("uuid") in by_uuid and row.get("type") not in ("user", "assistant"):
            children.setdefault(parent, []).append(row["uuid"])
    pending = [leaf["uuid"]]
    support: set[str] = set()
    while pending:
        for child in children.get(pending.pop(), []):
            if child not in active:
                active.add(child)
                support.add(child)
                pending.append(child)

    selected = [row for row in records if not row.get("uuid") or row.get("uuid") in active]
    state["_post_leaf_start_index"] = next(
        (idx for idx, row in enumerate(selected) if row.get("uuid") in support), len(selected)
    )
    state["dropped_inactive_branch_records"] = len(records) - len(selected)
    return selected


def safe_tail_start_index(records: list[dict[str, Any]], tail_turns: int) -> int:
    if tail_turns <= 0:
        for idx in range(len(records) - 1, -1, -1):
            if records[idx].get("type") in ("user", "assistant"):
                return idx + 1
        return len(records)

    found = 0
    for idx in range(len(records) - 1, -1, -1):
        if is_user_turn_start(records[idx]):
            found += 1
            if found == tail_turns:
                return idx
    return 0


def compact_native_tail_records(
    records: list[dict[str, Any]],
    parent_uuid: str | None,
    args: argparse.Namespace,
    state: dict[str, int],
) -> list[dict[str, Any]]:
    tail_args = SimpleNamespace(
        max_tool_output_chars=args.max_tail_tool_output_chars,
        max_file_history_entries=args.max_tail_file_history_entries,
    )
    tail_state = {
        "thinking_blocks_removed": 0,
        "tool_outputs_truncated": 0,
        "local_command_truncated": 0,
        "message_usage_compacted": 0,
        "file_history_snapshots_compacted": 0,
    }

    compacted: list[dict[str, Any]] = []
    parent_rewritten = False
    for obj in records:
        if obj.get("type") == "custom-title":
            state["safe_tail_custom_title_dropped"] += 1
            continue
        row = compact_native_record(obj, tail_args, tail_state)
        if not parent_rewritten and isinstance(row.get("uuid"), str):
            if parent_uuid:
                row["parentUuid"] = parent_uuid
                state["safe_tail_parent_rewritten"] += 1
            else:
                row.pop("parentUuid", None)
            parent_rewritten = True
        compacted.append(row)

    state["safe_tail_thinking_blocks_removed"] = tail_state["thinking_blocks_removed"]
    state["safe_tail_tool_outputs_truncated"] = tail_state["tool_outputs_truncated"]
    state["safe_tail_local_command_truncated"] = tail_state["local_command_truncated"]
    state["safe_tail_message_usage_compacted"] = tail_state["message_usage_compacted"]
    state["safe_tail_file_history_snapshots_compacted"] = tail_state["file_history_snapshots_compacted"]
    return compacted


def compact_chat_records(
    records: list[dict[str, Any]],
    session_records: list[dict[str, Any]],
    source: pathlib.Path,
    args: argparse.Namespace,
    state: dict[str, int],
) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    session_defaults = detect_session_defaults(session_records, source)
    custom_title = latest_custom_title_record(session_records)
    if custom_title is not None:
        kept_title: dict[str, Any] = {"type": "custom-title", "customTitle": custom_title["customTitle"]}
        session_id = custom_title.get("sessionId")
        if isinstance(session_id, str) and session_id:
            kept_title["sessionId"] = session_id
        elif session_defaults.get("sessionId"):
            kept_title["sessionId"] = session_defaults["sessionId"]
        compacted.append(kept_title)
        state["kept_custom_title"] += 1

    roots = conversation_roots(session_records)
    last_kept_uuids: dict[str, str] = {}

    for line_no, obj in enumerate(records, 1):
        item_type = obj.get("type")
        if item_type == "custom-title":
            continue
        if item_type not in ("user", "assistant"):
            state["dropped_non_chat_type"] += 1
            continue

        message = obj.get("message")
        if not isinstance(message, dict):
            state["dropped_non_text"] += 1
            continue

        raw_content = message.get("content")
        role = message.get("role")
        if role not in ("user", "assistant"):
            state["dropped_non_text"] += 1
            continue

        text = extract_message_text(raw_content)
        if not text:
            state["dropped_non_text"] += 1
            continue

        if is_meta_noise(text):
            state["dropped_meta_noise"] += 1
            continue

        compact_summary = bool(obj.get("isCompactSummary")) or is_continuation_summary(text)

        changed = False
        if not compact_summary:
            text, changed = shorten(text, args.max_message_chars)
        if changed:
            state["messages_truncated"] += 1

        timestamp = obj.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp:
            timestamp = "1970-01-01T00:00:00.000Z"
            state["synthetic_timestamp_assigned"] += 1

        message_uuid = obj.get("uuid")
        if not isinstance(message_uuid, str) or not message_uuid:
            message_uuid = stable_uuid(source, line_no)
            state["synthetic_uuid_assigned"] += 1
        root = roots.get(message_uuid, message_uuid)

        if isinstance(raw_content, list):
            resume_content: Any = [{"type": "text", "text": text}]
        else:
            resume_content = text

        row: dict[str, Any] = {
            "type": item_type,
            "timestamp": timestamp,
            "uuid": message_uuid,
            "message": {
                "role": role,
                "content": resume_content,
            },
        }
        if role == "assistant":
            model = message.get("model")
            if isinstance(model, str) and model:
                row["message"]["model"] = model
        if compact_summary:
            row["isCompactSummary"] = True
            row["isVisibleInTranscriptOnly"] = True
        row.update(chat_envelope_fields(obj, session_defaults))
        # Keep compact-summary rows as hard resume anchors: no parent chain.
        if compact_summary:
            row.pop("parentUuid", None)
        # Reconnect within this segment, never across an archived root.
        elif root in last_kept_uuids:
            row["parentUuid"] = last_kept_uuids[root]
        else:
            row.pop("parentUuid", None)
        last_kept_uuids[root] = message_uuid

        compacted.append(row)
        state["kept_chat_records"] += 1

    return compacted


def main() -> int:
    args = parse_args()
    if args.max_message_chars < 80:
        raise SystemExit("max-message-chars must be >= 80.")
    if args.safe_tail_turns < 0:
        raise SystemExit("safe-tail-turns must be >= 0.")
    if args.max_tail_tool_output_chars < 80:
        raise SystemExit("max-tail-tool-output-chars must be >= 80.")
    if args.max_tail_file_history_entries < 1:
        raise SystemExit("max-tail-file-history-entries must be >= 1.")

    source = pathlib.Path(args.session).expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"Session file not found: {source}")

    output_root = pathlib.Path(args.output_root).expanduser().resolve()
    rel = relative_output_path(source)
    original_copy = output_root / "original" / rel
    compacted_copy = output_root / "compacted" / rel
    report_path = output_root / "reports" / rel.with_suffix(".report.json")
    manifest_path = output_root / "manifests" / rel.with_suffix(".manifest.json")

    original_bytes = source.read_bytes()
    original_sha256 = sha256_bytes(original_bytes)
    original_validation = validate_jsonl_bytes(original_bytes)
    records = [json.loads(line) for line in original_bytes.splitlines()]
    validate_claude_records(records)

    state = {
        "kept_chat_records": 0,
        "kept_custom_title": 0,
        "dropped_non_chat_type": 0,
        "dropped_non_text": 0,
        "dropped_meta_noise": 0,
        "messages_truncated": 0,
        "synthetic_uuid_assigned": 0,
        "synthetic_timestamp_assigned": 0,
        "kept_safe_tail_turns": 0,
        "kept_safe_tail_records": 0,
        "safe_tail_custom_title_dropped": 0,
        "safe_tail_parent_rewritten": 0,
        "safe_tail_thinking_blocks_removed": 0,
        "safe_tail_tool_outputs_truncated": 0,
        "safe_tail_local_command_truncated": 0,
        "safe_tail_message_usage_compacted": 0,
        "safe_tail_file_history_snapshots_compacted": 0,
        "dropped_inactive_branch_records": 0,
        "assistant_models_backfilled": 0,
    }
    records = active_branch_records(records, state)
    roots = conversation_roots(records)
    leaf = next(row for row in reversed(records) if row.get("type") in ("user", "assistant") and not row.get("isSidechain"))
    current_root = roots[leaf["uuid"]]
    current_records = [row for row in records if roots.get(row.get("uuid")) == current_root]
    current_tail_start = safe_tail_start_index(current_records, args.safe_tail_turns)
    tail_start = (next(idx for idx, row in enumerate(records) if row is current_records[current_tail_start])
                  if current_tail_start < len(current_records)
                  else next(idx for idx, row in enumerate(records) if row is leaf) + 1)
    if state.pop("_post_leaf_start_index", len(records)) < tail_start:
        raise ValueError("Native post-leaf support precedes the requested tail; use a larger native tail or conservative compaction.")
    old_records = [row for idx, row in enumerate(records)
                   if idx < tail_start or (row.get("uuid") and roots[row["uuid"]] != current_root)]
    tail_records = [row for row in records[tail_start:]
                    if not row.get("uuid") or roots[row["uuid"]] == current_root]
    state["kept_safe_tail_turns"] = min(args.safe_tail_turns, sum(1 for obj in current_records if is_user_turn_start(obj)))

    chat_rows = compact_chat_records(old_records, records, source, args, state)
    tail_root = roots.get(next((row["uuid"] for row in tail_records if row.get("uuid")), None))
    tail_parent_uuid = next((row["uuid"] for row in reversed(chat_rows)
                             if row.get("uuid") and roots.get(row["uuid"]) == tail_root), None)
    safe_tail = compact_native_tail_records(tail_records, tail_parent_uuid, args, state)
    state["kept_safe_tail_records"] = len(safe_tail)
    chat_by_uuid = {row["uuid"]: row for row in chat_rows if row.get("uuid")}
    native_by_source = {id(source_row): row for source_row, row in zip(
        (row for row in tail_records if row.get("type") != "custom-title"), safe_tail)}
    compacted = [row for row in chat_rows if not row.get("uuid")]
    # Keep physical order even when archived dialogue is interleaved with the native tail.
    for source_row in records:
        row = native_by_source.get(id(source_row), chat_by_uuid.get(source_row.get("uuid")))
        if row is not None:
            compacted.append(row)
    state["assistant_models_backfilled"] = backfill_assistant_models(compacted, records)
    validate_claude_records(compacted)
    warnings: list[str] = []

    compacted_bytes = b"".join(
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        for row in compacted
    )
    compacted_validation = validate_jsonl_bytes(compacted_bytes)
    compacted_sha256 = sha256_bytes(compacted_bytes)
    generated_at = compacted[-1].get("timestamp") if compacted else None

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
        "policy": {
            "profile": "claude-chat-resume",
            "max_message_chars": args.max_message_chars,
            "safe_tail_turns": args.safe_tail_turns,
            "max_tail_tool_output_chars": args.max_tail_tool_output_chars,
            "max_tail_file_history_entries": args.max_tail_file_history_entries,
            "required_resume_identity": "uuid",
            "meta_noise_filter": True,
            "safe_tail_kept_record_types": "native Claude records after tail start, safe-compacted",
        },
    }

    manifest = build_compaction_manifest(
        source=source,
        original_copy=original_copy,
        compacted_copy=compacted_copy,
        report_path=report_path,
        source_sha256=original_sha256,
        compacted_sha256=compacted_sha256,
        profile="claude-chat-resume",
        generated_at=generated_at,
        original_lines=original_validation["line_count"],
        compacted_lines=compacted_validation["line_count"],
        bytes_saved=len(original_bytes) - len(compacted_bytes),
        keep_last_turns=args.safe_tail_turns,
        max_replacement_records=0,
    )
    manifest.setdefault("policy", {})
    manifest["policy"]["required_resume_identity"] = "uuid"
    manifest["policy"]["max_message_chars"] = args.max_message_chars
    manifest["policy"]["safe_tail_turns"] = args.safe_tail_turns
    manifest["policy"]["max_tail_tool_output_chars"] = args.max_tail_tool_output_chars
    manifest["policy"]["max_tail_file_history_entries"] = args.max_tail_file_history_entries
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
