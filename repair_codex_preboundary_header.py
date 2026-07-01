#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

from compact_codex_session import (
    load_context_terms,
    sha256_bytes,
    synthetic_compacted_turn,
    validate_jsonl_bytes,
)


MAX_CHECKPOINT_MESSAGE_CHARS = 7600


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair Codex JSONL files with oversized chat before first task_started."
    )
    parser.add_argument("session", help="Path to a Codex rollout JSONL file.")
    parser.add_argument("--output", help="Output JSONL path. Default: timestamped copy beside input.")
    parser.add_argument(
        "--max-replacement-records",
        type=int,
        default=16,
        help="High-value messages to preserve inside the synthetic compacted checkpoint.",
    )
    return parser.parse_args()


def is_task_started(obj: dict) -> bool:
    return obj.get("type") == "event_msg" and obj.get("payload", {}).get("type") == "task_started"


def default_output_path(source: pathlib.Path) -> pathlib.Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return source.with_name(f"{source.stem}.preboundary-repaired-{stamp}{source.suffix}")


def checkpoint_as_message(row: dict) -> dict:
    payload = row.get("payload", {})
    message = payload.get("message") if isinstance(payload, dict) else None
    if row.get("type") != "compacted" or not isinstance(message, str) or not message:
        return row
    if len(message) > MAX_CHECKPOINT_MESSAGE_CHARS:
        message = (
            message[:MAX_CHECKPOINT_MESSAGE_CHARS].rstrip()
            + f"\n... [Pre-boundary repair checkpoint truncated; original length={len(message)} chars]"
        )
    return {
        "timestamp": row.get("timestamp"),
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": message}],
        },
    }


def main() -> int:
    args = parse_args()
    source = pathlib.Path(args.session).expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"Session file not found: {source}")
    if args.max_replacement_records < 1:
        raise SystemExit("max-replacement-records must be >= 1.")

    output = pathlib.Path(args.output).expanduser().resolve() if args.output else default_output_path(source)
    if output.exists():
        raise SystemExit(f"Output already exists: {output}")

    original = source.read_bytes()
    validate_jsonl_bytes(original)
    records = [json.loads(line) for line in original.splitlines()]

    first_turn = next((idx for idx, obj in enumerate(records) if is_task_started(obj)), None)
    if first_turn is None:
        raise SystemExit("No task_started row found; cannot repair pre-boundary history.")

    prefix = records[:first_turn]
    header = [obj for obj in prefix if obj.get("type") == "session_meta"]
    old_history = [obj for obj in prefix if obj.get("type") != "session_meta"]
    if not old_history:
        raise SystemExit("No pre-boundary history found; no repair needed.")

    state = {
        "duplicated_instruction_messages": 0,
        "scratch_artifacts_removed": 0,
        "semantic_turns_compacted": 0,
        "semantic_records_replaced": 0,
        "semantic_replacement_records": 0,
        "checkpoint_sections_emitted": 0,
    }
    compact_args = argparse.Namespace(
        profile="preboundary-repair",
        keep_last_turns=1,
        max_replacement_records=args.max_replacement_records,
    )
    checkpoint_turn = synthetic_compacted_turn(
        [old_history],
        load_context_terms(),
        compact_args,
        state,
        source=source,
        source_sha256=sha256_bytes(original),
        original_line_count=len(records),
    )
    repaired = (
        header
        + [checkpoint_as_message(obj) for obj in checkpoint_turn]
        + records[first_turn:]
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n" for obj in repaired),
        encoding="utf-8",
    )
    validate_jsonl_bytes(output.read_bytes())
    print(
        json.dumps(
            {
                "source": str(source),
                "output": str(output),
                "pre_boundary_records_replaced": len(old_history),
                "output_bytes": output.stat().st_size,
                "changes": state,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
