#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import uuid
from typing import Any

from lineage import build_compaction_manifest, describe_lineage


DEFAULT_OUTPUT_ROOT = pathlib.Path("/home/marcos/apps-codex/session-survivor/outputs/claude-chat-resume")
PLACEHOLDER = "[Compacted Claude chat message"
COMMAND_WRAPPER_RE = re.compile(
    r"^\s*<command-name>.*?</command-name>\s*"
    r"<command-message>.*?</command-message>\s*"
    r"<command-args>.*?</command-args>\s*$",
    re.DOTALL,
)


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


def compact_chat_records(
    records: list[dict[str, Any]],
    source: pathlib.Path,
    args: argparse.Namespace,
    state: dict[str, int],
) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    session_defaults = detect_session_defaults(records, source)
    custom_title = latest_custom_title_record(records)
    if custom_title is not None:
        kept_title: dict[str, Any] = {"type": "custom-title", "customTitle": custom_title["customTitle"]}
        session_id = custom_title.get("sessionId")
        if isinstance(session_id, str) and session_id:
            kept_title["sessionId"] = session_id
        elif session_defaults.get("sessionId"):
            kept_title["sessionId"] = session_defaults["sessionId"]
        compacted.append(kept_title)
        state["kept_custom_title"] += 1

    last_kept_uuid: str | None = None

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
        row.update(chat_envelope_fields(obj, session_defaults))
        # Rewrite parentUuid to point to previous kept record, not removed intermediates.
        if last_kept_uuid is not None:
            row["parentUuid"] = last_kept_uuid
        else:
            row.pop("parentUuid", None)
        last_kept_uuid = message_uuid

        compacted.append(row)
        state["kept_chat_records"] += 1

    return compacted


def main() -> int:
    args = parse_args()
    if args.max_message_chars < 80:
        raise SystemExit("max-message-chars must be >= 80.")

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
    records = [json.loads(line) for line in original_bytes.splitlines()]

    state = {
        "kept_chat_records": 0,
        "kept_custom_title": 0,
        "dropped_non_chat_type": 0,
        "dropped_non_text": 0,
        "dropped_meta_noise": 0,
        "messages_truncated": 0,
        "synthetic_uuid_assigned": 0,
        "synthetic_timestamp_assigned": 0,
    }
    compacted = compact_chat_records(records, source, args, state)
    if not compacted:
        raise SystemExit("No chat records survived filtering; refusing to write empty resume file.")
    warnings: list[str] = []

    with compacted_copy.open("w", encoding="utf-8") as dst:
        for row in compacted:
            dst.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    compacted_validation = validate_jsonl(compacted_copy)
    compacted_bytes = compacted_copy.read_bytes()
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
            "required_resume_identity": "uuid",
            "meta_noise_filter": True,
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
        keep_last_turns=0,
        max_replacement_records=0,
    )
    manifest.setdefault("policy", {})
    manifest["policy"]["required_resume_identity"] = "uuid"
    manifest["policy"]["max_message_chars"] = args.max_message_chars
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    marker_path = write_thread_marker(
        source=source,
        compacted_copy=compacted_copy,
        report_path=report_path,
        manifest_path=manifest_path,
        profile="claude-chat-resume",
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
