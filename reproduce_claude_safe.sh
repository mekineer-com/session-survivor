#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
TOOL="$ROOT/compact_claude_session.py"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-/home/marcos/.claude/projects/-home-marcos-apps-codex}"
STAMP="$(date +%Y%m%dT%H%M%S)"
OUTROOT="$ROOT/outputs/claude-repro/$STAMP"

latest_session() {
  find "$PROJECT_DIR" -maxdepth 1 -type f -name '*.jsonl' | sort | tail -n 1
}

if [ "${1:-}" = "" ] || [ "${1:-}" = "--latest" ]; then
  SOURCE="$(latest_session)"
else
  SOURCE="$1"
fi

mkdir -p "$OUTROOT"

python3 "$TOOL" "$SOURCE" --output-root "$OUTROOT" > "$OUTROOT/run.json"
REPORT="$(find "$OUTROOT/reports" -type f -name '*.json' | head -n 1)"
MANIFEST="$(find "$OUTROOT/manifests" -type f -name '*.json' | head -n 1)"

printf 'source=%s\n' "$SOURCE"
printf 'outroot=%s\n' "$OUTROOT"
printf 'report=%s\n' "$REPORT"
printf 'manifest=%s\n' "$MANIFEST"
