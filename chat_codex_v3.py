#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from compact_codex_session import core_format_warnings
from lineage import build_compaction_manifest, describe_lineage


SESSION_ROOT = pathlib.Path.home() / ".codex" / "sessions"
DEFAULT_OUTPUT_ROOT = pathlib.Path(
    "/home/marcos/apps-codex/session-survivor/outputs/codex-chat-v3-weekly"
)
DEFAULT_SAFE_TAIL_TURNS = 1
PROFILE = "codex-chat-v3-weekly-summary"
MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}
WEEK_HEADER_RE = re.compile(r"^##\s+Week of\s+(.+?)\s*$")
WEEK_RANGE_RE = re.compile(
    r"^\s*([A-Za-z]{3})\s+(\d{1,2})\s*[–-]\s*(?:([A-Za-z]{3})\s+)?(\d{1,2})(?:,\s*(\d{4}))?\s*$"
)


@dataclass
class WeekBlock:
    heading: str
    body: str
    start: date
    end: date
    index: int

    @property
    def key(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"

    def as_markdown(self) -> str:
        if self.body.strip():
            return f"{self.heading}\n\n{self.body.strip()}"
        return self.heading


@dataclass
class TurnInfo:
    rows: list[dict[str, Any]]
    start_dt: datetime
    end_dt: datetime
    start_ts: str
    end_ts: str
    model_context_window: int | None
    collaboration_mode_kind: str | None

    @property
    def start_day(self) -> date:
        return self.start_dt.date()


@dataclass
class OldUnit:
    rows: list[dict[str, Any]]
    matchable: bool
    start_dt: datetime | None
    end_dt: datetime | None
    model_context_window: int | None
    collaboration_mode_kind: str | None

    @property
    def start_day(self) -> date | None:
        if self.start_dt is None:
            return None
        return self.start_dt.date()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rewrite Codex old-history turns into weekly summary turns using "
            "WEEKLY_SUMMARIES.md while keeping a structurally native safe tail."
        )
    )
    parser.add_argument("session", nargs="?", help="Path to Codex rollout JSONL.")
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Use latest rollout JSONL from ~/.codex/sessions.",
    )
    parser.add_argument(
        "--summary-file",
        help="Path to WEEKLY_SUMMARIES.md. If omitted, infer from outputs/codex-summary-source.",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Root directory for original/compacted/report outputs.",
    )
    parser.add_argument(
        "--safe-tail-turns",
        type=int,
        default=DEFAULT_SAFE_TAIL_TURNS,
        help="Keep this many most recent turns unchanged.",
    )
    parser.add_argument(
        "--dry-run-only",
        action="store_true",
        help="Do not write compacted/manifests; print report only.",
    )
    parser.add_argument(
        "--force-empty-map",
        action="store_true",
        help="Allow output when weeks parse but no turns match date ranges.",
    )
    parser.add_argument(
        "--show-summary",
        action="store_true",
        help="Print compact summary JSON to stdout.",
    )
    parser.add_argument(
        "--show-lineage",
        action="store_true",
        help="Print lineage for source session and exit.",
    )
    return parser.parse_args()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def latest_session(root: pathlib.Path) -> pathlib.Path:
    files = sorted(root.rglob("rollout-*.jsonl"))
    if not files:
        raise SystemExit(f"No rollout JSONL files found under {root}")
    return max(files, key=lambda p: p.stat().st_mtime)


def relative_output_path(path: pathlib.Path) -> pathlib.Path:
    try:
        return path.resolve().relative_to(SESSION_ROOT.resolve())
    except Exception:
        return pathlib.Path(path.name)


def validate_jsonl_bytes(data: bytes) -> dict[str, int]:
    line_count = 0
    for line_count, line in enumerate(data.splitlines(), 1):
        json.loads(line)
    return {"line_count": line_count}


def parse_iso_timestamp(ts: str | None) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    raw = ts
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def dt_to_iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def infer_summary_file(source: pathlib.Path) -> pathlib.Path:
    rel = relative_output_path(source).with_suffix("")
    candidate = (
        pathlib.Path("/home/marcos/apps-codex/session-survivor/outputs/codex-summary-source")
        / rel
        / "WEEKLY_SUMMARIES.md"
    )
    if not candidate.exists():
        raise SystemExit(
            "Could not infer WEEKLY_SUMMARIES.md. Provide --summary-file explicitly. "
            f"Tried: {candidate}"
        )
    return candidate


def turn_boundary_type(obj: dict[str, Any]) -> str:
    if obj.get("type") != "event_msg":
        return ""
    payload = obj.get("payload", {})
    ptype = payload.get("type", "")
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


def extract_turn_info(turn: list[dict[str, Any]], fallback_dt: datetime) -> TurnInfo:
    dts: list[datetime] = []
    model_context_window: int | None = None
    collaboration_mode_kind: str | None = None
    for row in turn:
        dt = parse_iso_timestamp(row.get("timestamp"))
        if dt is not None:
            dts.append(dt)
        if row.get("type") == "event_msg" and row.get("payload", {}).get("type") == "task_started":
            payload = row.get("payload", {})
            mcw = payload.get("model_context_window")
            if isinstance(mcw, int):
                model_context_window = mcw
            cmk = payload.get("collaboration_mode_kind")
            if isinstance(cmk, str) and cmk:
                collaboration_mode_kind = cmk
    if dts:
        start_dt = min(dts)
        end_dt = max(dts)
    else:
        start_dt = fallback_dt
        end_dt = fallback_dt
    return TurnInfo(
        rows=turn,
        start_dt=start_dt,
        end_dt=end_dt,
        start_ts=dt_to_iso_z(start_dt),
        end_ts=dt_to_iso_z(end_dt),
        model_context_window=model_context_window,
        collaboration_mode_kind=collaboration_mode_kind,
    )


def is_user_assistant_message_row(row: dict[str, Any]) -> bool:
    if row.get("type") != "response_item":
        return False
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return False
    if payload.get("type") != "message":
        return False
    role = str(payload.get("role") or "").strip()
    return role in {"user", "assistant"}


def make_old_units(
    header_rows: list[dict[str, Any]],
    old_turns: list[list[dict[str, Any]]],
    all_turn_dts: list[datetime],
) -> list[OldUnit]:
    units: list[OldUnit] = []
    fallback_dt = min(all_turn_dts)

    for row in header_rows:
        if is_user_assistant_message_row(row):
            dt = parse_iso_timestamp(row.get("timestamp")) or fallback_dt
            units.append(
                OldUnit(
                    rows=[row],
                    matchable=True,
                    start_dt=dt,
                    end_dt=dt,
                    model_context_window=None,
                    collaboration_mode_kind=None,
                )
            )
            fallback_dt = dt
            continue
        static_dt = parse_iso_timestamp(row.get("timestamp"))
        units.append(
            OldUnit(
                rows=[row],
                matchable=False,
                start_dt=static_dt,
                end_dt=static_dt,
                model_context_window=None,
                collaboration_mode_kind=None,
            )
        )

    for turn in old_turns:
        info = extract_turn_info(turn, fallback_dt=fallback_dt)
        units.append(
            OldUnit(
                rows=info.rows,
                matchable=True,
                start_dt=info.start_dt,
                end_dt=info.end_dt,
                model_context_window=info.model_context_window,
                collaboration_mode_kind=info.collaboration_mode_kind,
            )
        )
        fallback_dt = info.end_dt
    return units


def parse_week_range(raw: str, current_year: int, previous_start: date | None) -> tuple[date, date, int]:
    m = WEEK_RANGE_RE.match(raw.strip())
    if not m:
        raise ValueError(f"Unsupported week heading format: {raw!r}")
    m1_name, d1_raw, m2_name, d2_raw, year_raw = m.groups()
    if m1_name not in MONTHS:
        raise ValueError(f"Unknown month in week heading: {m1_name!r}")
    month1 = MONTHS[m1_name]
    day1 = int(d1_raw)
    month2 = MONTHS[m2_name] if m2_name else month1
    day2 = int(d2_raw)

    year = int(year_raw) if year_raw else current_year
    start = date(year, month1, day1)
    end_year = year + 1 if month2 < month1 else year
    end = date(end_year, month2, day2)

    if year_raw is None and previous_start is not None:
        if start < previous_start and (previous_start - start).days > 180:
            year += 1
            start = date(year, month1, day1)
            end_year = year + 1 if month2 < month1 else year
            end = date(end_year, month2, day2)
    if end < start:
        raise ValueError(f"Invalid week heading range: {raw!r}")
    return start, end, year


def parse_weekly_summaries(summary_text: str, anchor_year: int) -> list[WeekBlock]:
    lines = summary_text.splitlines()
    blocks: list[tuple[str, list[str]]] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in lines:
        m = WEEK_HEADER_RE.match(line)
        if m:
            if current_heading is not None:
                blocks.append((current_heading, current_lines))
            current_heading = line.strip()
            current_lines = []
            continue
        if current_heading is not None:
            current_lines.append(line)
    if current_heading is not None:
        blocks.append((current_heading, current_lines))

    weeks: list[WeekBlock] = []
    current_year = anchor_year
    previous_start: date | None = None
    for idx, (heading, body_lines) in enumerate(blocks):
        raw = heading[len("## Week of ") :].strip()
        start, end, current_year = parse_week_range(raw, current_year, previous_start)
        previous_start = start
        weeks.append(
            WeekBlock(
                heading=heading,
                body="\n".join(body_lines).strip(),
                start=start,
                end=end,
                index=idx,
            )
        )
    return weeks


def build_synthetic_week_turn(
    week: WeekBlock,
    matched_units: list[OldUnit],
    source: pathlib.Path,
    sequence: int,
) -> list[dict[str, Any]]:
    first = matched_units[0]
    last = matched_units[-1]
    if first.start_dt is None or last.end_dt is None:
        raise SystemExit("Internal error: matched weekly units missing timestamps.")
    start_dt = first.start_dt
    end_dt = last.end_dt
    turn_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{source.resolve()}::{week.key}::{sequence}",
        )
    )
    model_context_window = first.model_context_window if first.model_context_window is not None else 258400
    started_at = int(start_dt.timestamp())
    completed_at = int(end_dt.timestamp())
    duration_ms = max(0, int((end_dt - start_dt).total_seconds() * 1000))
    summary_markdown = week.as_markdown()
    completion_preview = f"Inserted weekly continuity summary for {week.heading[3:]}."

    started_payload: dict[str, Any] = {
        "type": "task_started",
        "turn_id": turn_id,
        "started_at": started_at,
        "model_context_window": model_context_window,
    }
    if first.collaboration_mode_kind:
        started_payload["collaboration_mode_kind"] = first.collaboration_mode_kind

    return [
        {
            "timestamp": dt_to_iso_z(start_dt),
            "type": "event_msg",
            "payload": started_payload,
        },
        {
            "timestamp": dt_to_iso_z(start_dt),
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": summary_markdown}],
            },
        },
        {
            "timestamp": dt_to_iso_z(end_dt),
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": turn_id,
                "last_agent_message": completion_preview,
                "completed_at": completed_at,
                "duration_ms": duration_ms,
            },
        },
    ]


def strip_compacted_replacement_history(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    stripped: list[dict[str, Any]] = []
    count = 0
    for row in rows:
        if row.get("type") != "compacted":
            stripped.append(row)
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict) or "replacement_history" not in payload:
            stripped.append(row)
            continue
        new_payload = dict(payload)
        new_payload.pop("replacement_history", None)
        new_row = dict(row)
        new_row["payload"] = new_payload
        stripped.append(new_row)
        count += 1
    return stripped, count


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.safe_tail_turns < 1:
        raise SystemExit("safe-tail-turns must be >= 1.")
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

    if args.show_lineage:
        print(json.dumps(describe_lineage(source), indent=2, ensure_ascii=False))
        raise SystemExit(0)

    summary_file = (
        pathlib.Path(args.summary_file).expanduser().resolve()
        if args.summary_file
        else infer_summary_file(source)
    )
    if not summary_file.exists():
        raise SystemExit(f"Summary file not found: {summary_file}")

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

    warnings = core_format_warnings(records)
    if warnings:
        raise SystemExit("Input session format drift: " + " | ".join(warnings))

    header_rows, turns = split_session_objects(records)
    if not turns:
        raise SystemExit("Input session has no task_started turns.")
    safe_tail_turns = min(args.safe_tail_turns, len(turns))
    old_turns = turns[:-safe_tail_turns] if safe_tail_turns else turns
    tail_turns = turns[-safe_tail_turns:] if safe_tail_turns else []

    all_turn_dts: list[datetime] = []
    for turn in turns:
        for row in turn:
            dt = parse_iso_timestamp(row.get("timestamp"))
            if dt is not None:
                all_turn_dts.append(dt)
    if not all_turn_dts:
        raise SystemExit("Could not infer session timeline from timestamps.")
    anchor_year = min(all_turn_dts).year

    summary_text = summary_file.read_text(encoding="utf-8")
    weeks = parse_weekly_summaries(summary_text, anchor_year=anchor_year)
    if not weeks:
        raise SystemExit(f"No week blocks parsed from summary file: {summary_file}")

    old_units = make_old_units(header_rows, old_turns, all_turn_dts)
    matchable_positions = [idx for idx, unit in enumerate(old_units) if unit.matchable and unit.start_day is not None]

    week_matches: list[dict[str, Any]] = []
    matched_positions: set[int] = set()
    week_by_start_position: dict[int, tuple[WeekBlock, int, int]] = {}
    for week in weeks:
        week_positions = [
            pos
            for pos in matchable_positions
            if old_units[pos].start_day is not None and week.start <= old_units[pos].start_day <= week.end
        ]
        if not week_positions:
            week_matches.append(
                {
                    "week": week.heading,
                    "start_date": week.start.isoformat(),
                    "end_date": week.end.isoformat(),
                    "matched_turns": 0,
                    "first_turn_index": None,
                    "last_turn_index": None,
                    "rows_removed": 0,
                    "rows_added": 0,
                }
            )
            continue
        first_pos = min(week_positions)
        last_pos = max(week_positions)
        rows_removed = sum(len(old_units[pos].rows) for pos in week_positions)
        week_matches.append(
            {
                "week": week.heading,
                "start_date": week.start.isoformat(),
                "end_date": week.end.isoformat(),
                "matched_turns": len(week_positions),
                "first_turn_index": first_pos,
                "last_turn_index": last_pos,
                "rows_removed": rows_removed,
                "rows_added": 3,
            }
        )
        week_by_start_position[first_pos] = (week, first_pos, last_pos)
        matched_positions.update(week_positions)

    if not matched_positions and not args.force_empty_map:
        raise SystemExit(
            "No old-history turns matched summary week ranges. "
            "Use --force-empty-map to proceed anyway."
        )

    rebuilt_old_rows: list[dict[str, Any]] = []
    i = 0
    inserted_week_summaries = 0
    while i < len(old_units):
        start_mapping = week_by_start_position.get(i)
        if start_mapping is None:
            rebuilt_old_rows.extend(old_units[i].rows)
            i += 1
            continue
        week, first_pos, last_pos = start_mapping
        matched = [old_units[pos] for pos in range(first_pos, last_pos + 1) if old_units[pos].matchable]
        synthetic = build_synthetic_week_turn(week, matched, source=source, sequence=inserted_week_summaries)
        rebuilt_old_rows.extend(synthetic)
        for pos in range(first_pos, last_pos + 1):
            if not old_units[pos].matchable:
                rebuilt_old_rows.extend(old_units[pos].rows)
        inserted_week_summaries += 1
        i = last_pos + 1

    tail_rows = [row for turn in tail_turns for row in turn]
    out_rows = [*rebuilt_old_rows, *tail_rows]
    out_rows, stripped_replacement_history = strip_compacted_replacement_history(out_rows)
    if not out_rows:
        raise SystemExit("Refusing to write empty output.")

    compacted_bytes = b"".join(
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        for row in out_rows
    )
    compacted_validation = validate_jsonl_bytes(compacted_bytes)
    compacted_sha256 = sha256_bytes(compacted_bytes)

    original_copy.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    compacted_copy.parent.mkdir(parents=True, exist_ok=True)
    original_copy.write_bytes(original_bytes)

    generated_at = None
    for row in reversed(out_rows):
        ts = row.get("timestamp")
        if isinstance(ts, str) and ts:
            generated_at = ts
            break

    manifest_path_value: str | None = None
    report = {
        "source": str(source),
        "summary_file": str(summary_file),
        "original_copy": str(original_copy),
        "compacted_copy": str(compacted_copy),
        "report_path": str(report_path),
        "original_sha256": original_sha256,
        "compacted_sha256": compacted_sha256,
        "original_bytes": len(original_bytes),
        "compacted_bytes": len(compacted_bytes),
        "bytes_saved": len(original_bytes) - len(compacted_bytes),
        "original_lines": original_validation["line_count"],
        "compacted_lines": compacted_validation["line_count"],
        "manifest_path": None,
        "changes": {
            "parsed_weeks": len(weeks),
            "inserted_week_summaries": inserted_week_summaries,
            "matched_old_turns": len(matched_positions),
            "old_turns_total": len(matchable_positions),
            "safe_tail_turns_kept_native": len(tail_turns),
            "kept_header_records": sum(1 for u in old_units if not u.matchable),
            "kept_safe_tail_records": len(tail_rows),
            "rebuilt_old_records": len(rebuilt_old_rows),
            "stripped_compacted_replacement_history": stripped_replacement_history,
        },
        "week_matches": week_matches,
        "warnings": [],
        "policy": {
            "profile": PROFILE,
            "safe_tail_turns": args.safe_tail_turns,
            "dry_run_only": bool(args.dry_run_only),
            "summary_format": "weekly-markdown-verbatim",
            "compacted_replacement_history": "stripped_to_activate_visible_weekly_summaries",
            "synthetic_turn_shape": [
                "event_msg.task_started",
                "response_item.message(role=assistant)",
                "event_msg.task_complete",
            ],
        },
    }

    for match in week_matches:
        if match["matched_turns"] == 0:
            report["warnings"].append(
                f"Unmapped week range: {match['week']} ({match['start_date']}..{match['end_date']})"
            )

    if not args.dry_run_only:
        compacted_copy.write_bytes(compacted_bytes)
        manifest = build_compaction_manifest(
            source=source,
            original_copy=original_copy,
            compacted_copy=compacted_copy,
            report_path=report_path,
            source_sha256=original_sha256,
            compacted_sha256=compacted_sha256,
            profile=PROFILE,
            generated_at=generated_at,
            original_lines=original_validation["line_count"],
            compacted_lines=compacted_validation["line_count"],
            bytes_saved=len(original_bytes) - len(compacted_bytes),
            keep_last_turns=args.safe_tail_turns,
            max_replacement_records=0,
        )
        manifest.setdefault("policy", {})
        manifest["policy"]["summary_file"] = str(summary_file)
        manifest["policy"]["safe_tail_turns"] = args.safe_tail_turns
        manifest["policy"]["synthetic_week_turn_rows"] = 3
        manifest["policy"]["compacted_replacement_history"] = "stripped_to_activate_visible_weekly_summaries"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        manifest_path_value = str(manifest_path)
    report["manifest_path"] = manifest_path_value
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    args = parse_args()
    report = run(args)
    if args.show_summary:
        print(
            json.dumps(
                {
                    "source": report["source"],
                    "summary_file": report["summary_file"],
                    "bytes_saved": report["bytes_saved"],
                    "inserted_week_summaries": report["changes"]["inserted_week_summaries"],
                    "matched_old_turns": report["changes"]["matched_old_turns"],
                    "warnings": report["warnings"],
                    "report_path": report["report_path"],
                    "manifest_path": report["manifest_path"],
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
