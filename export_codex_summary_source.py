#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import pathlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


SESSION_ROOT = pathlib.Path.home() / ".codex" / "sessions"
DEFAULT_OUTPUT_ROOT = pathlib.Path(
    "/home/marcos/apps-codex/session-survivor/outputs/codex-summary-source"
)


@dataclass
class ChatRow:
    ts: datetime
    role: str
    turn_id: int
    exchange_id: int
    phase: str
    text: str


PROGRESS_ONLY_PATTERNS = (
    "i'll do",
    "i’ll do",
    "i'll load",
    "i’ll load",
    "i'm fixing",
    "i’m fixing",
    "i'm compacting",
    "i’m compacting",
    "i'll check",
    "i’ll check",
    "i'm checking",
    "i’m checking",
    "i'm now",
    "i’m now",
    "next i’ll",
    "next i'll",
    "i'll now",
    "i’ll now",
)

FINALISH_MARKERS = (
    "done",
    "completed",
    "fixed",
    "applied",
    "committed",
    "pushed",
    "verified",
    "validation passed",
    "here are",
    "result",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export Codex user/assistant history into ordered daily markdown files "
            "for model-authored continuity summaries."
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
        help="Root output directory.",
    )
    parser.add_argument(
        "--mode",
        choices=("collapsed", "raw"),
        default="collapsed",
        help=(
            "Export mode: 'collapsed' keeps logical user->final-assistant exchanges "
            "(default), 'raw' keeps every user/assistant message row."
        ),
    )
    parser.add_argument(
        "--assistant-selection",
        choices=("phase_only", "phase_then_heuristic"),
        default="phase_then_heuristic",
        help=(
            "When mode=collapsed: 'phase_only' keeps only assistant rows with phase=final_answer; "
            "'phase_then_heuristic' falls back to chatter filtering when no final_answer exists."
        ),
    )
    return parser.parse_args()


def latest_session(root: pathlib.Path) -> pathlib.Path:
    files = sorted(root.rglob("rollout-*.jsonl"))
    if not files:
        raise SystemExit(f"No rollout JSONL files found under {root}")
    return max(files, key=lambda p: p.stat().st_mtime)


def parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    value = ts.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        for key in ("text", "input_text", "output_text"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
                break
    return "\n\n".join(parts).strip()


def relative_output_path(path: pathlib.Path) -> pathlib.Path:
    try:
        return path.resolve().relative_to(SESSION_ROOT.resolve())
    except Exception:
        return pathlib.Path(path.name)


def collect_rows(source: pathlib.Path) -> list[ChatRow]:
    rows: list[ChatRow] = []
    turn_id = 0
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            obj = json.loads(line)
            if obj.get("type") == "event_msg":
                if obj.get("payload", {}).get("type") == "task_started":
                    turn_id += 1
                continue
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
            ts = parse_ts(str(obj.get("timestamp") or ""))
            if ts is None:
                continue
            text = extract_text(payload.get("content"))
            if not text:
                continue
            phase = str(payload.get("phase") or "")
            rows.append(ChatRow(ts=ts, role=role, turn_id=turn_id, exchange_id=0, phase=phase, text=text))
    rows.sort(key=lambda r: r.ts)
    assign_exchange_ids(rows)
    return rows


def assign_exchange_ids(rows: list[ChatRow]) -> None:
    exchange_id = 0
    for row in rows:
        if row.role == "user":
            exchange_id += 1
        elif exchange_id == 0:
            exchange_id = 1
        row.exchange_id = exchange_id


def collapse_rows(rows: list[ChatRow], assistant_selection: str) -> tuple[list[ChatRow], dict[str, int]]:
    by_exchange: dict[int, list[ChatRow]] = {}
    for row in rows:
        by_exchange.setdefault(row.exchange_id, []).append(row)

    collapsed: list[ChatRow] = []
    assistant_rows_dropped = 0
    progress_only_rows_dropped = 0
    exchanges_without_final_answer = 0
    phase_only_assistant_rows_dropped = 0

    for exchange_id in sorted(by_exchange.keys()):
        items = by_exchange[exchange_id]
        items.sort(key=lambda r: r.ts)
        users = [r for r in items if r.role == "user"]
        assistants = [r for r in items if r.role == "assistant"]
        turn_id = max((r.turn_id for r in items), default=0)

        if users:
            user_text = "\n\n".join(r.text for r in users).strip()
            if user_text:
                collapsed.append(
                    ChatRow(
                        ts=users[0].ts,
                        role="user",
                        turn_id=turn_id,
                        exchange_id=exchange_id,
                        phase="",
                        text=user_text,
                    )
                )

        if assistants:
            finals = [a for a in assistants if a.phase == "final_answer"]
            if finals:
                final_assistant = finals[-1]
                assistant_rows_dropped += max(0, len(assistants) - 1)
            else:
                exchanges_without_final_answer += 1
                if assistant_selection == "phase_only":
                    phase_only_assistant_rows_dropped += len(assistants)
                    continue
                substantive = [a for a in assistants if not is_progress_only_assistant(a.text)]
                if substantive:
                    final_assistant = substantive[-1]
                    assistant_rows_dropped += max(0, len(assistants) - 1)
                else:
                    # If user asked something and all assistant rows are progress chatter,
                    # drop assistant rows for cleaner summarizer input.
                    if users:
                        progress_only_rows_dropped += len(assistants)
                        continue
                    final_assistant = assistants[-1]
                    progress_only_rows_dropped += max(0, len(assistants) - 1)
            collapsed.append(
                ChatRow(
                    ts=final_assistant.ts,
                    role="assistant",
                    turn_id=turn_id,
                    exchange_id=exchange_id,
                    phase=final_assistant.phase,
                    text=final_assistant.text,
                )
            )

    collapsed.sort(key=lambda r: r.ts)
    return collapsed, {
        "assistant_rows_dropped": assistant_rows_dropped,
        "progress_only_rows_dropped": progress_only_rows_dropped,
        "exchanges_without_final_answer": exchanges_without_final_answer,
        "phase_only_assistant_rows_dropped": phase_only_assistant_rows_dropped,
    }


def is_progress_only_assistant(text: str) -> bool:
    low = text.strip().lower()
    if not low:
        return True
    if any(marker in low for marker in FINALISH_MARKERS):
        return False
    return any(p in low for p in PROGRESS_ONLY_PATTERNS)


def write_exports(
    rows: list[ChatRow],
    source: pathlib.Path,
    output_root: pathlib.Path,
    mode: str,
    assistant_selection: str,
) -> pathlib.Path:
    if not rows:
        raise SystemExit("No user/assistant rows found in source session.")

    export_rows = rows
    mode_stats = {
        "assistant_rows_dropped": 0,
        "progress_only_rows_dropped": 0,
        "exchanges_without_final_answer": 0,
        "phase_only_assistant_rows_dropped": 0,
    }
    if mode == "collapsed":
        export_rows, mode_stats = collapse_rows(rows, assistant_selection)

    rel = relative_output_path(source)
    dest_dir = output_root / rel.with_suffix("")
    dest_dir.mkdir(parents=True, exist_ok=True)

    by_day: dict[str, list[ChatRow]] = {}
    for row in export_rows:
        key = row.ts.date().isoformat()
        by_day.setdefault(key, []).append(row)

    day_keys = sorted(by_day.keys())
    index_lines: list[str] = []
    index_lines.append("# Codex Summary Source Index")
    index_lines.append("")
    index_lines.append(f"- Source: `{source}`")
    index_lines.append(f"- Export root: `{dest_dir}`")
    index_lines.append(f"- Mode: `{mode}`")
    index_lines.append(f"- Assistant selection: `{assistant_selection}`")
    index_lines.append(f"- Total days: `{len(day_keys)}`")
    index_lines.append(f"- Total messages: `{len(export_rows)}`")
    if mode == "collapsed":
        index_lines.append(f"- Assistant intermediate rows dropped: `{mode_stats['assistant_rows_dropped']}`")
        index_lines.append(f"- Progress-only assistant rows dropped: `{mode_stats['progress_only_rows_dropped']}`")
        index_lines.append(f"- Exchanges without final_answer marker: `{mode_stats['exchanges_without_final_answer']}`")
        if assistant_selection == "phase_only":
            index_lines.append(
                f"- phase_only assistant rows dropped: `{mode_stats['phase_only_assistant_rows_dropped']}`"
            )
    index_lines.append("- Turn label policy: use native turn id when present; otherwise use exchange id.")
    index_lines.append("")
    index_lines.append("## Read Order")
    index_lines.append("")

    for day in day_keys:
        items = by_day[day]
        first_ts = items[0].ts.isoformat()
        last_ts = items[-1].ts.isoformat()
        char_count = sum(len(i.text) for i in items)
        file_name = f"{day}.md"
        file_path = dest_dir / file_name

        with file_path.open("w", encoding="utf-8") as out:
            out.write(f"# Codex Conversation Source: {day} (UTC)\n\n")
            out.write(f"- Messages: `{len(items)}`\n")
            out.write(f"- Time range: `{first_ts}` to `{last_ts}`\n")
            out.write(f"- Character volume: `{char_count}`\n\n")
            for row in items:
                display_turn = row.turn_id if row.turn_id > 0 else row.exchange_id
                out.write(
                    f"## {row.ts.isoformat()} | exchange {row.exchange_id} | turn {display_turn} | {row.role}\n\n"
                )
                out.write(row.text)
                out.write("\n\n")

        index_lines.append(
            f"- `{day}`: [{file_name}]({file_name}) | msgs `{len(items)}` | chars `{char_count}` | `{first_ts}` -> `{last_ts}`"
        )

    index_lines.append("")
    index_lines.append("## Sonnet Summary Prompting Notes")
    index_lines.append("")
    index_lines.append("- Read files in listed order.")
    index_lines.append("- Stop and summarize at natural day/week boundaries.")
    index_lines.append("- Keep both work outcomes and collaboration tone.")
    index_lines.append("- Separate observed facts from interpretation.")

    (dest_dir / "INDEX.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    return dest_dir


def main() -> int:
    args = parse_args()
    if args.latest and args.session:
        raise SystemExit("Use either --latest or a session path, not both.")

    if args.latest:
        source = latest_session(SESSION_ROOT)
    elif args.session:
        source = pathlib.Path(args.session).expanduser().resolve()
    else:
        raise SystemExit("Provide a session path or use --latest.")

    if not source.exists():
        raise SystemExit(f"Session file not found: {source}")

    output_root = pathlib.Path(args.output_root).expanduser().resolve()
    rows = collect_rows(source)
    dest_dir = write_exports(rows, source, output_root, args.mode, args.assistant_selection)
    print(
        json.dumps(
            {
                "source": str(source),
                "export_dir": str(dest_dir),
                "messages": len(rows),
                "mode": args.mode,
                "assistant_selection": args.assistant_selection,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
