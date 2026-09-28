# Codex Session Survivor Runbook

This is the canonical runbook for normal Codex session maintenance and safe
profile comparisons. It does not cover leaked-thought scrubbing or backups to
MEGA.

## Normal Chat Maintenance

Use `chat_codex_session.py` unless reviewed, authored summaries require the v3
flow documented in `README.md`.

1. Let Codex compact in-session, exchange one ordinary turn, then `/exit`.
2. Confirm the session process is gone. Never transform or swap an open session.
3. Run from this repository with the exact session path and a new output folder:

```sh
TARGET="/home/marcos/.codex/sessions/<...>/rollout-...jsonl"
STAMP="$(date +%Y%m%d-%H%M%S)"
RUN="outputs/codex-chat-maintenance-$STAMP"

python3 chat_codex_session.py "$TARGET" --output-root "$RUN" --show-summary
```

4. Use the paths printed by the script. Require all of these before replacement:
   - the manifest exists; it is the completion seal
   - report `warnings` is empty and `changes.messages_truncated` is `0`
   - the live source hash still equals `manifest.source.sha256`
   - candidate hash and line count equal `manifest.result`
   - candidate parses line by line as JSON
   - its first row retains the target session ID
   - existing authored summary rows remain
5. Keep the full original in the run's `original/` tree. Copy the candidate to a
   temporary file beside the live target, validate it again, then rename it over
   the target. A same-directory rename is the atomic swap.
6. Verify the installed hash and JSON again. Resume by session ID and ask one
   ordinary continuity question before considering maintenance accepted.

If any gate fails, do not swap. The run's `original/` copy is the rollback.

## Profile Comparison

Use the rest of this document when comparing profiles from one frozen source,
not for routine maintenance.

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
- keeps old-history compacted row shells/messages
- keeps the newest old-history checkpoint row, prunes its ordinary user-message bulk, and strips older checkpoint bulk
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
2. `changes.kept_compacted_anchor` preserves readable compacted summaries
3. `changes.stripped_compacted_replacement_history` shows superseded anchor fillings were removed
4. `policy.chat_history_dropped_event_types` matches expected dropped old events

## Manual Swap and Rollback

Follow the validation and atomic-swap gates in **Normal Chat Maintenance**, using
the chosen profile's candidate path. Roll back from that run's `original/` copy.

## Troubleshooting

- If a report path is unexpected, trust the printed `*_report=` lines from the wrapper output.
- If `resume` artifacts are basename-only paths, that is expected when source is a frozen snapshot outside `~/.codex/sessions`.
- If chat compaction aborts with format drift/no turns, use `safe` first and inspect warnings.
