#!/bin/sh
# Pre-Write/Edit hook: require a recent Read on target file in this Claude session.
# Also records Read events as freshness evidence.

set -eu

state_dir="${CLAUDE_HOOK_STATE_DIR:-$HOME/.claude/hook-state}"
evidence_log="${state_dir}/read-evidence.jsonl"
max_age="${CLAUDE_READ_FRESHNESS_SECONDS:-3600}"

mkdir -p "$state_dir"

input=$(cat)
tmp_input=$(mktemp "${state_dir}/hook-input.XXXXXX.json")
printf '%s' "$input" > "$tmp_input"
trap 'rm -f "$tmp_input"' EXIT

python3 - "$evidence_log" "$max_age" "$tmp_input" <<'PY'
import json
import os
import pathlib
import sys
import time

if len(sys.argv) < 4:
    sys.exit(0)

evidence_log = sys.argv[1]
try:
    max_age = int(sys.argv[2])
except Exception:
    max_age = 3600

try:
    raw = pathlib.Path(sys.argv[3]).read_text(encoding="utf-8")
except Exception:
    sys.exit(0)
try:
    event = json.loads(raw)
except Exception:
    sys.exit(0)

tool_name = event.get("tool_name", "")
session_id = event.get("session_id", "")
cwd = event.get("cwd") or os.getcwd()
tool_input = event.get("tool_input") or {}
now = int(time.time())

def normalize_path(path_value: object) -> str:
    if not isinstance(path_value, str) or not path_value.strip():
        return ""
    path = pathlib.Path(path_value).expanduser()
    if not path.is_absolute():
        path = pathlib.Path(cwd) / path
    try:
        return str(path.resolve())
    except Exception:
        return str(path)

def append_event(record: dict) -> None:
    with open(evidence_log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

def has_recent_read(target_path: str) -> bool:
    try:
        with open(evidence_log, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except FileNotFoundError:
        return False
    except Exception:
        return False

    # Scan newest-first with a bounded window to keep hook latency low.
    for line in reversed(lines[-5000:]):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("type") != "read_evidence":
            continue
        if rec.get("file_path") != target_path:
            continue
        if session_id and rec.get("session_id") != session_id:
            continue
        ts = int(rec.get("ts", 0))
        if now - ts <= max_age:
            return True
        return False
    return False

if tool_name == "Read":
    file_path = normalize_path(tool_input.get("file_path", ""))
    if file_path:
        append_event({
            "type": "read_evidence",
            "ts": now,
            "session_id": session_id,
            "file_path": file_path,
            "tool_name": tool_name,
        })
    sys.exit(0)

if tool_name in ("Write", "Edit"):
    file_path = normalize_path(tool_input.get("file_path", ""))
    if not file_path:
        sys.exit(0)

    # Allow new file creation without requiring a prior read.
    if not os.path.exists(file_path):
        sys.exit(0)

    if has_recent_read(file_path):
        append_event({
            "type": "write_allow",
            "ts": now,
            "session_id": session_id,
            "file_path": file_path,
            "tool_name": tool_name,
            "reason": "recent_read_present",
        })
        sys.exit(0)

    append_event({
        "type": "write_block",
        "ts": now,
        "session_id": session_id,
        "file_path": file_path,
        "tool_name": tool_name,
        "reason": "missing_recent_read",
        "max_age_seconds": max_age,
    })
    deny = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Blocked {tool_name} on stale file context. "
                f"Read this file first in the current session, then retry: {file_path}"
            ),
        }
    }
    print(json.dumps(deny, ensure_ascii=False))
    sys.exit(0)

sys.exit(0)
PY
