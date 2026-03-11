#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
TOOL="$ROOT/compact_codex_session.py"
SESSION_ROOT="/home/marcos/.codex/sessions"
STAMP="$(date +%Y%m%dT%H%M%S)"
OUTROOT="$ROOT/outputs/repro/$STAMP"

latest_session() {
  python3 - <<'PY'
from pathlib import Path
root = Path("/home/marcos/.codex/sessions")
files = list(root.rglob("rollout-*.jsonl"))
if not files:
    raise SystemExit("No rollout files found")
print(max(files, key=lambda p: p.stat().st_mtime))
PY
}

if [ "${1:-}" = "" ] || [ "${1:-}" = "--latest" ]; then
  SOURCE="$(latest_session)"
else
  SOURCE="$1"
fi

mkdir -p "$OUTROOT"

python3 "$TOOL" --profile safe "$SOURCE" --output-root "$OUTROOT/safe" > "$OUTROOT/safe-run.json"
SAFE_REPORT="$(find "$OUTROOT/safe/reports" -type f -name '*.json' | head -n 1)"
SAFE_SNAPSHOT="$(jq -r '.original_copy' "$SAFE_REPORT")"

python3 "$TOOL" --profile resume "$SAFE_SNAPSHOT" --output-root "$OUTROOT/resume" > "$OUTROOT/resume-run.json"
RESUME_REPORT="$(find "$OUTROOT/resume/reports" -type f -name '*.json' | head -n 1)"

printf 'source=%s\n' "$SOURCE"
printf 'outroot=%s\n' "$OUTROOT"
printf 'safe_report=%s\n' "$SAFE_REPORT"
printf 'resume_report=%s\n' "$RESUME_REPORT"
