# chat_codex_v3 Plan (Post-Compact Execution)

## Goal
Build `chat_codex_v3.py` that consumes `WEEKLY_SUMMARIES.md` directly and prepares a Codex session rewrite plan using weekly summary turns, while preserving Codex JSONL safety.

## Scope for v3 (this pass)
1. Parse weekly blocks from `WEEKLY_SUMMARIES.md` as-is (no forced reformat).
2. Map each week block to date boundaries.
3. Locate matching turn ranges in source session by timestamp.
4. Emit a dry-run report plus a transformed output file candidate.

## Explicit Non-Goals (this pass)
- No live swap into active session file.
- No in-place edits to currently open sessions.
- No re-authoring of summary prose.

## Inputs
- Source session JSONL (Codex rollout file).
- Weekly summaries markdown:
  - `outputs/codex-summary-source/.../WEEKLY_SUMMARIES.md`

## Output Artifacts
- Compacted candidate JSONL under `outputs/codex-chat-v3-weekly/compacted/...`
- Report JSON with:
  - weeks parsed
  - turn ranges replaced per week
  - rows removed/kept/added
  - bytes/line delta
  - warnings for unmapped week ranges
- Manifest JSON with lineage/provenance.

## Parsing Strategy (Summary File)
1. Detect week headers (e.g., `## Week of Mar 6–12`).
2. Capture full markdown block until next week header.
3. Preserve original block text verbatim for insertion.
4. Infer year from session timeline when omitted in header.

## Session Rewrite Strategy
1. Parse session JSONL into ordered records.
2. Build turn list from `task_started` boundaries.
3. For each week range:
  - identify all turns whose timestamps fall inside range
  - remove those turns from old-history region
  - insert one synthetic summary turn:
    - `event_msg` `task_started`
    - `response_item` assistant message (weekly summary markdown)
    - `event_msg` `task_complete`
4. Keep recent safe tail turns structurally native (configurable, default `1`).
5. Strip `compacted.payload.replacement_history` from the whole v3 output so Codex rebuilds from the visible weekly summaries.

## Safety/Validation
- Validate JSON decode on all output lines.
- Preserve chronological order.
- Ensure each inserted summary turn has start/complete boundaries.
- Preserve compacted row shells/messages while removing only `replacement_history`.
- Refuse write if no weeks parsed or no turns matched (unless `--force-empty-map`).

## CLI Design (initial)
- `chat_codex_v3.py SESSION_PATH --summary-file WEEKLY_SUMMARIES.md`
- Optional:
  - `--latest`
  - `--safe-tail-turns N` (default `1`)
  - `--show-summary`
  - `--show-lineage`
  - `--dry-run-only`

## Execution Order (after compact)
1. Implement parser + range mapping.
2. Implement synthetic weekly-turn insertion.
3. Add report/manifest output.
4. Run on Codex file in dry-run.
5. Inspect report and sample week replacements.
6. Stop for user approval before any swap.
