# Codex Session Compaction Reproduction

## Purpose

Operational runbook for reproducing Codex compaction profiles from the same frozen source snapshot.

## Tools

- Main compactor: [compact_codex_session.py](/home/marcos/apps-codex/session-survivor/compact_codex_session.py)
- Hybrid chat compactor: [chat_codex_session.py](/home/marcos/apps-codex/session-survivor/chat_codex_session.py)
- Reproduction wrapper: [reproduce_codex_session_profiles.sh](/home/marcos/apps-codex/session-survivor/reproduce_codex_session_profiles.sh)

## Profiles

### `safe`

Use as first swap candidate.

Behavior:

- keeps full turn structure
- keeps line count stable relative to source
- trims bulky fields only (reasoning blobs, large tool output/input, duplicated historical AGENTS payloads)

### `resume`

Use for aggressive continuation experiments.

Behavior:

- keeps a recent native tail intact
- collapses older turns into a synthetic compacted checkpoint
- keeps bounded `replacement_history`

### `chat-resume-hybrid-safe-tail`

Use when context rot is driven by heavy non-chat history.

Behavior:

- keeps chat text for older history (`user` and `assistant` messages)
- keeps the newest native compacted anchor from old history
- drops old boundary-event spam from historical section
- keeps a native safe-compacted tail (`--safe-tail-turns`, default `1`)

## Critical Reproduction Rule

Do not compare profiles from independently captured live files.

Correct method:

1. Freeze once.
2. Run all profile variants from that frozen source.

The wrapper script does this by running `safe` first, then using `safe/original/...` for `resume`.

## One-Command Reproduction

From `/home/marcos/apps-codex/session-survivor`:

```sh
./reproduce_codex_session_profiles.sh --latest
```

Outputs:

- `source=...`
- `outroot=...`
- `safe_report=...`
- `resume_report=...`
- `chat_resume_hybrid_safe_tail_report=...`

Run root format:

- `/home/marcos/apps-codex/session-survivor/outputs/repro/<timestamp>/...`

## Report Review Order

For `safe`:

1. `warnings` is empty or expected.
2. `original_lines == compacted_lines`.
3. `changes` shows bulk trimming without semantic collapse.

For `resume`:

1. `checkpoint_preview`
2. `policy`
3. `changes`
4. compacted JSONL only if needed

For `chat-resume-hybrid-safe-tail`:

1. `changes.kept_safe_tail_turns` is expected
2. `changes.kept_compacted_anchor == 1`
3. `policy.chat_history_dropped_event_types` matches expected dropped old events

## Manual Swap and Rollback

Do this only when the target Codex session is closed and not writing.

Example pattern:

```sh
TARGET="/home/marcos/.codex/sessions/<...>/rollout-...jsonl"
RUN="/home/marcos/apps-codex/session-survivor/outputs/repro/<timestamp>"

cp "$TARGET" "$TARGET.pre-swap.bak"
cp "$RUN/safe/compacted/<matching-relative-path>.jsonl" "$TARGET"
```

Rollback:

```sh
cp "$TARGET.pre-swap.bak" "$TARGET"
```

## Troubleshooting

- If a report path is unexpected, trust the printed `*_report=` lines from the wrapper output.
- If `resume` artifacts are basename-only paths, that is expected when source is a frozen snapshot outside `~/.codex/sessions`.
- If chat compaction aborts with format drift/no turns, use `safe` first and inspect warnings.
