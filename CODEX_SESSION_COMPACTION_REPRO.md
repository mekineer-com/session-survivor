# Codex Session Compaction Reproduction

This is the practical runbook for comparing Codex compaction profiles safely.
Use it when you want repeatable tests, not one-off guesses.

## Plain-English Goal

You want to compare profile outputs from the same exact source file.
If you compare runs from different live moments, the session changed under you and results are not comparable.

## Tools

- Main compactor: [compact_codex_session.py](/home/marcos/apps-codex/session-survivor/compact_codex_session.py)
- Hybrid chat compactor: [chat_codex_session.py](/home/marcos/apps-codex/session-survivor/chat_codex_session.py)
- Reproduction wrapper: [reproduce_codex_session_profiles.sh](/home/marcos/apps-codex/session-survivor/reproduce_codex_session_profiles.sh)

## Profiles

### `safe`

Use this first.
It is the least risky live swap candidate.

What it does:

- keeps normal turn structure
- keeps line count stable relative to source
- trims bulk only (reasoning blobs, large tool output/input, repeated AGENTS payloads)

### `resume`

Use this when you need stronger size reduction and accept more change.

What it does:

- keeps a recent native tail
- compresses older turns into one synthetic compacted checkpoint
- keeps bounded `replacement_history`

### `chat-resume-hybrid-safe-tail`

Use this when old non-chat history is the main problem.

What it does:

- keeps old chat text (`user` and `assistant`)
- keeps the newest old-history native compacted anchor
- drops old boundary-event spam from old history
- keeps a native safe-compacted tail (`--safe-tail-turns`, default `1`)

## Critical Reproduction Rule

Do not compare runs from different live captures.
Freeze once, then run all profiles on that frozen source.

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

## How To Review Results Fast

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
