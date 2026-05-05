#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any

from lineage import build_compaction_manifest, describe_lineage


SESSION_ROOT = pathlib.Path.home() / ".codex" / "sessions"
DEFAULT_OUTPUT_ROOT = pathlib.Path("/home/marcos/apps-codex/session-survivor/outputs/codex-chat-resume-boundary-safe")
PLACEHOLDER = "[Compacted Codex chat message"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a Codex chat-resume copy: keep user/assistant text, "
            "turn boundaries, and the latest compacted anchor."
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


def is_turn_boundary(obj: dict[str, Any]) -> bool:
    """Turn boundaries give the model temporal structure."""
    if obj.get("type") != "event_msg":
        return False
    ptype = obj.get("payload", {}).get("type", "")
    return ptype in ("task_started", "task_complete")


def compact_chat_records(
    records: list[dict[str, Any]],
    args: argparse.Namespace,
    state: dict[str, int],
) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    last_compacted_record: dict[str, Any] | None = None

    for obj in records:
        # Preserve turn boundaries (timestamps that mark when turns happened).
        if is_turn_boundary(obj):
            compacted.append(obj)
            state["kept_turn_boundaries"] += 1
            continue

        # Keep the most recent compacted record as the "you are here" anchor.
        if obj.get("type") == "compacted":
            last_compacted_record = obj
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

    if last_compacted_record is not None:
        compacted.append(last_compacted_record)
        state["kept_compacted_anchor"] += 1

    return compacted


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
        "kept_header_records": 0,
        "kept_turn_boundaries": 0,
        "kept_compacted_anchor": 0,
        "dropped_non_response_item": 0,
        "dropped_non_message": 0,
        "dropped_non_chat_role": 0,
        "dropped_non_text": 0,
        "dropped_bootstrap_noise": 0,
        "dropped_meta_noise": 0,
        "messages_truncated": 0,
        "synthetic_timestamp_assigned": 0,
    }

    # Preserve native header records (session_meta etc.) — required for Codex CLI resume.
    header_rows: list[dict[str, Any]] = []
    for obj in records:
        if obj.get("type") == "event_msg" and obj.get("payload", {}).get("type") == "task_started":
            break
        header_rows.append(obj)
    state["kept_header_records"] = len(header_rows)

    chat_rows = compact_chat_records(records, args, state)
    if not chat_rows:
        raise SystemExit("No chat records survived filtering; refusing to write empty output file.")

    with compacted_copy.open("w", encoding="utf-8") as dst:
        for row in header_rows:
            dst.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        for row in chat_rows:
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
        "warnings": [],
        "policy": {
            "profile": "codex-chat-resume-boundary-safe",
            "max_message_chars": args.max_message_chars,
            "kept_roles": ["user", "assistant"],
            "kept_boundary_events": ["task_started", "task_complete"],
            "kept_compacted_anchor": "latest_only",
            "output_record_types": ["session_meta/header", "response_item.message", "event_msg.task_*", "compacted(latest)"],
        },
    }

    manifest = build_compaction_manifest(
        source=source,
        original_copy=original_copy,
        compacted_copy=compacted_copy,
        report_path=report_path,
        source_sha256=original_sha256,
        compacted_sha256=compacted_sha256,
        profile="codex-chat-resume-boundary-safe",
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
    manifest["policy"]["kept_boundary_events"] = ["task_started", "task_complete"]
    manifest["policy"]["kept_compacted_anchor"] = "latest_only"
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
