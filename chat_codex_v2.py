#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from typing import Any

from compact_codex_session import core_format_warnings
from lineage import build_compaction_manifest, describe_lineage


SESSION_ROOT = pathlib.Path.home() / ".codex" / "sessions"
DEFAULT_OUTPUT_ROOT = pathlib.Path(
    "/home/marcos/apps-codex/session-survivor/outputs/codex-chat-v2-inline-continuity"
)
SUMMARY_SUFFIX = " [Continuity summary"

SIGNAL_PATTERNS = (
    re.compile(r"/home/marcos/"),
    re.compile(r"\b(fix|fixed|bug|failed|error|regression|risk|blocked|passed|verified)\b", re.I),
    re.compile(r"\b(commit|pushed|sha|hash|branch|tag)\b", re.I),
    re.compile(r"`[^`]+`"),
    re.compile(r"^\s*(\d+\.|- |\* )", re.M),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a Codex continuity copy by rewriting older user/assistant "
            "messages in place while preserving JSONL row order/types."
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
        "--safe-tail-turns",
        type=int,
        default=1,
        help="Keep this many most recent turns fully unchanged.",
    )
    parser.add_argument(
        "--target-ratio",
        type=float,
        default=0.20,
        help="Target ratio for old-history chat text (0 < ratio <= 1).",
    )
    parser.add_argument(
        "--min-summary-chars",
        type=int,
        default=220,
        help="Minimum chars to keep when summarizing a message.",
    )
    parser.add_argument(
        "--max-summary-chars",
        type=int,
        default=1600,
        help="Maximum chars to keep when summarizing a message.",
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


def _extract_entry_text(entry: dict[str, Any]) -> str:
    for key in ("text", "input_text", "output_text"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def extract_message_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = _extract_entry_text(item)
        if text:
            chunks.append(text)
    return "\n\n".join(chunks)


def inject_message_text(payload: dict[str, Any], text: str) -> None:
    content = payload.get("content")
    if isinstance(content, str):
        payload["content"] = text
        return

    if isinstance(content, list):
        written = False
        for item in content:
            if not isinstance(item, dict):
                continue
            for key in ("text", "input_text", "output_text"):
                if isinstance(item.get(key), str):
                    if not written:
                        item[key] = text
                        written = True
                    else:
                        item[key] = ""
        if written:
            return

    role = str(payload.get("role") or "").strip()
    content_type = "output_text" if role == "assistant" else "input_text"
    payload["content"] = [{"type": content_type, "text": text}]


def turn_boundary_type(obj: dict[str, Any]) -> str:
    if obj.get("type") != "event_msg":
        return ""
    ptype = obj.get("payload", {}).get("type", "")
    if ptype in ("task_started", "task_complete", "turn_aborted"):
        return ptype
    return ""


def split_session_objects(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
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


def looks_already_summarized(text: str) -> bool:
    return SUMMARY_SUFFIX in text and text.rstrip().endswith(" chars]")


def split_segments(text: str) -> list[str]:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if blocks:
        return blocks
    return [line.strip() for line in text.splitlines() if line.strip()]


def score_segment(segment: str, index: int) -> int:
    score = 0
    if index == 0:
        score += 25
    length = len(segment)
    if 80 <= length <= 500:
        score += 8
    elif length > 500:
        score += 3
    for pattern in SIGNAL_PATTERNS:
        if pattern.search(segment):
            score += 12
    return score


def summarize_text(text: str, keep_chars: int) -> tuple[str, bool]:
    if looks_already_summarized(text):
        return text, False
    if len(text) <= keep_chars:
        return text, False

    segments = split_segments(text)
    if not segments:
        return text[:keep_chars].rstrip(), True

    ranked = sorted(
        ((score_segment(seg, idx), idx, seg) for idx, seg in enumerate(segments)),
        key=lambda item: (-item[0], item[1]),
    )

    kept: list[str] = []
    used: set[str] = set()
    total = 0
    for _, _, seg in ranked:
        key = seg.casefold()
        if key in used:
            continue
        projected = total + len(seg) + (2 if kept else 0)
        if projected > keep_chars and kept:
            continue
        kept.append(seg)
        used.add(key)
        total = projected
        if total >= keep_chars:
            break

    if not kept:
        kept = [text[:keep_chars].rstrip()]

    kept.sort(key=lambda seg: next(i for i, s in enumerate(segments) if s == seg))
    compacted = "\n\n".join(kept).rstrip()
    compacted += f"\n...{SUMMARY_SUFFIX}; original length={len(text)} chars]"
    return compacted, True


def old_turn_message_rows(turns: list[list[dict[str, Any]]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for turn in turns:
        for obj in turn:
            if obj.get("type") != "response_item":
                continue
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue
            if payload.get("type") != "message":
                continue
            role = str(payload.get("role") or "").strip()
            if role not in {"user", "assistant"}:
                continue
            rows.append((obj, payload))
    return rows


def main() -> int:
    args = parse_args()
    if args.safe_tail_turns < 1:
        raise SystemExit("safe-tail-turns must be >= 1.")
    if not (0.0 < args.target_ratio <= 1.0):
        raise SystemExit("target-ratio must satisfy 0 < ratio <= 1.")
    if args.min_summary_chars < 80:
        raise SystemExit("min-summary-chars must be >= 80.")
    if args.max_summary_chars < args.min_summary_chars:
        raise SystemExit("max-summary-chars must be >= min-summary-chars.")
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

    warnings = core_format_warnings(records)
    if warnings:
        raise SystemExit("Input session format drift: " + " | ".join(warnings))

    _, turns = split_session_objects(records)
    if not turns:
        raise SystemExit("Input session has no task_started turns; refusing continuity rewrite.")

    safe_tail_turns = min(args.safe_tail_turns, len(turns))
    old_turns = turns[:-safe_tail_turns] if safe_tail_turns else []
    tail_turns = turns[-safe_tail_turns:] if safe_tail_turns else []

    rows = old_turn_message_rows(old_turns)
    original_old_chars = 0
    for _, payload in rows:
        original_old_chars += len(extract_message_text(payload))
    target_old_chars = int(original_old_chars * args.target_ratio)

    if target_old_chars <= 0:
        target_old_chars = args.min_summary_chars * len(rows)

    changed_count = 0
    summarized_count = 0
    already_summarized_count = 0
    new_old_chars = 0

    # Largest messages first to meet global ratio with fewer rewrites.
    rows_with_lengths: list[tuple[int, dict[str, Any], dict[str, Any], str]] = []
    for obj, payload in rows:
        text = extract_message_text(payload)
        rows_with_lengths.append((len(text), obj, payload, text))
    rows_with_lengths.sort(key=lambda item: item[0], reverse=True)

    keep_per_message = args.max_summary_chars
    if rows:
        keep_per_message = max(
            args.min_summary_chars,
            min(args.max_summary_chars, target_old_chars // max(1, len(rows))),
        )

    # Pre-calc current chars for unchanged path.
    new_old_chars = sum(length for length, _, _, _ in rows_with_lengths)

    for length, _obj, payload, text in rows_with_lengths:
        if not text:
            continue
        if looks_already_summarized(text):
            already_summarized_count += 1
            continue
        if new_old_chars <= target_old_chars:
            continue
        compacted, did_compact = summarize_text(text, keep_per_message)
        if not did_compact:
            continue
        delta = length - len(compacted)
        if delta <= 0:
            continue
        inject_message_text(payload, compacted)
        new_old_chars -= delta
        changed_count += 1
        summarized_count += 1

    with compacted_copy.open("w", encoding="utf-8") as dst:
        for row in records:
            dst.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    compacted_validation = validate_jsonl(compacted_copy)
    compacted_bytes = compacted_copy.read_bytes()
    compacted_sha256 = sha256_bytes(compacted_bytes)

    generated_at = records[-1].get("timestamp") if records else None
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
        "changes": {
            "old_turns_total": len(old_turns),
            "safe_tail_turns_kept_native": len(tail_turns),
            "old_messages_seen": len(rows),
            "old_messages_rewritten": changed_count,
            "old_messages_summarized": summarized_count,
            "old_messages_already_summarized": already_summarized_count,
            "old_chars_before": original_old_chars,
            "old_chars_after": new_old_chars,
            "target_old_chars": target_old_chars,
        },
        "warnings": [],
        "policy": {
            "profile": "codex-chat-v2-inline-continuity",
            "safe_tail_turns": args.safe_tail_turns,
            "target_ratio": args.target_ratio,
            "min_summary_chars": args.min_summary_chars,
            "max_summary_chars": args.max_summary_chars,
            "preserved_row_order": True,
            "preserved_record_types": True,
            "rewrite_scope": "old_turns_message_text_only",
            "kept_roles": ["user", "assistant"],
        },
    }

    manifest = build_compaction_manifest(
        source=source,
        original_copy=original_copy,
        compacted_copy=compacted_copy,
        report_path=report_path,
        source_sha256=original_sha256,
        compacted_sha256=compacted_sha256,
        profile="codex-chat-v2-inline-continuity",
        generated_at=generated_at,
        original_lines=original_validation["line_count"],
        compacted_lines=compacted_validation["line_count"],
        bytes_saved=len(original_bytes) - len(compacted_bytes),
        keep_last_turns=0,
        max_replacement_records=0,
    )
    manifest.setdefault("policy", {})
    manifest["policy"]["safe_tail_turns"] = args.safe_tail_turns
    manifest["policy"]["target_ratio"] = args.target_ratio
    manifest["policy"]["min_summary_chars"] = args.min_summary_chars
    manifest["policy"]["max_summary_chars"] = args.max_summary_chars
    manifest["policy"]["preserved_row_order"] = True
    manifest["policy"]["preserved_record_types"] = True
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.show_summary:
        print(
            json.dumps(
                {
                    "source": str(source),
                    "bytes_saved": report["bytes_saved"],
                    "changes": report["changes"],
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
