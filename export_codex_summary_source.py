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
    text: str


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
            rows.append(ChatRow(ts=ts, role=role, turn_id=turn_id, text=text))
    rows.sort(key=lambda r: r.ts)
    return rows


def write_exports(rows: list[ChatRow], source: pathlib.Path, output_root: pathlib.Path) -> pathlib.Path:
    if not rows:
        raise SystemExit("No user/assistant rows found in source session.")

    rel = relative_output_path(source)
    dest_dir = output_root / rel.with_suffix("")
    dest_dir.mkdir(parents=True, exist_ok=True)

    by_day: dict[str, list[ChatRow]] = {}
    for row in rows:
        key = row.ts.date().isoformat()
        by_day.setdefault(key, []).append(row)

    day_keys = sorted(by_day.keys())
    index_lines: list[str] = []
    index_lines.append("# Codex Summary Source Index")
    index_lines.append("")
    index_lines.append(f"- Source: `{source}`")
    index_lines.append(f"- Export root: `{dest_dir}`")
    index_lines.append(f"- Total days: `{len(day_keys)}`")
    index_lines.append(f"- Total messages: `{len(rows)}`")
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
                out.write(f"## {row.ts.isoformat()} | turn {row.turn_id} | {row.role}\n\n")
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
    dest_dir = write_exports(rows, source, output_root)
    print(json.dumps({"source": str(source), "export_dir": str(dest_dir), "messages": len(rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
