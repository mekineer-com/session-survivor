#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
TOOL="$ROOT/compact_claude_session.py"

[ "${1:-}" != "" ] && [ "${1:-}" != "--latest" ] || {
  printf 'Usage: %s /path/to/closed-claude-session.jsonl\n' "$0" >&2
  exit 2
}
SOURCE="$1"

mkdir -p "$ROOT/outputs/claude-repro"
OUTROOT="$(mktemp -d "$ROOT/outputs/claude-repro/run.XXXXXX")"
python3 "$TOOL" "$SOURCE" --output-root "$OUTROOT" > "$OUTROOT/run.json"
REPORT="$(find "$OUTROOT/reports" -type f -name '*.json' | head -n 1)"
MANIFEST="$(find "$OUTROOT/manifests" -type f -name '*.json' | head -n 1)"

printf 'source=%s\n' "$SOURCE"
printf 'outroot=%s\n' "$OUTROOT"
printf 'report=%s\n' "$REPORT"
printf 'manifest=%s\n' "$MANIFEST"
