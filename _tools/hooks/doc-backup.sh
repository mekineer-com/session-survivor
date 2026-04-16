#!/bin/sh
# Pre-Write/Edit hook: back up protected docs before overwrite.
# Protected: CLAUDE.md, HANDOFF.md, ROADMAP_REFERENCE.md, MEMU_Architecture_Onboarding.md, any SKILL.md

set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)

input=$(cat)
file_path=$(
    printf '%s' "$input" | python3 -c '
import json
import sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
tool_input = d.get("tool_input") or {}
print(tool_input.get("file_path", ""))
'
)

[ -z "$file_path" ] && exit 0
[ ! -f "$file_path" ] && exit 0

basename=$(basename "$file_path")

case "$basename" in
    CLAUDE.md|HANDOFF.md|ROADMAP_REFERENCE.md|MEMU_Architecture_Onboarding.md|SKILL.md)
        ;;
    *)
        exit 0
        ;;
esac

archive_dir="${DOC_BACKUP_ARCHIVE_DIR:-$repo_root/_archive/doc-versions}"
mkdir -p "$archive_dir"
date_stamp=$(date +%y%m%d-%H%M)
stem="${basename%.md}"
dest="$archive_dir/${stem}-${date_stamp}.md"
cp "$file_path" "$dest"
echo "Backed up $basename → $dest" >&2
exit 0
