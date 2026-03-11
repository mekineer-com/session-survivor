# Codex Session Compaction Reproduction

## Purpose

This file exists so future me can reproduce the current `safe` / `resume` rollout-compaction work without having to rediscover:

- which script does what
- how to run both profiles from the same frozen source snapshot
- where the reports and outputs land
- what to review first
- how to do a manual swap and rollback safely

This is not a theory note. It is an operations note.

## Current Tools

- Main compactor:
  - [compact_codex_session.py](/home/marcos/apps-codex/_tools/session-compaction/compact_codex_session.py)
- Reproduction wrapper:
  - [reproduce_codex_session_profiles.sh](/home/marcos/apps-codex/_tools/session-compaction/reproduce_codex_session_profiles.sh)

## Profiles

### `safe`

Use when you want the least risky swap candidate.

Behavior:

- keeps the original line count
- keeps all turns in place
- trims bulky fields only:
  - encrypted reasoning
  - long tool outputs
  - long custom tool inputs
  - long agent-reasoning text
  - duplicated pasted `AGENTS.md` instruction messages

What it does **not** do:

- does not collapse turns
- does not emit a synthetic checkpoint

### `resume`

Use when you want an experimental continuation-oriented compact state.

Behavior:

- keeps recent turns intact
- collapses older turns into one synthetic native-style compacted turn
- embeds a typed checkpoint in `compacted.payload.message`
- keeps a limited `replacement_history` of selected older high-value messages

Checkpoint sections currently written:

- `topics`
- `goals`
- `constraints`
- `tool_results`
- `task_state`
- `risks`
- `next_actions`
- `summary`

What it is for:

- resume experiments
- not yet the default safe swap target

## Most Important Reproduction Rule

Do **not** compare `safe` and `resume` if they were run from a live session file at different times.

Reason:

- the active rollout file keeps growing while Codex is working
- even minutes apart, the source bytes will differ

Correct comparison method:

1. run `safe` first on the live file
2. use the `safe/original/...` snapshot as the source for `resume`

The wrapper script does exactly this.

## One-Command Reproduction

From `/home/marcos/apps-codex`:

```sh
_tools/session-compaction/reproduce_codex_session_profiles.sh --latest
```

What it does:

1. finds the latest rollout file by modification time under `~/.codex/sessions`
2. runs `safe` against that live file
3. uses the `safe` original snapshot as the frozen source for `resume`
4. prints:
   - `source=...`
   - `outroot=...`
   - `safe_report=...`
   - `resume_report=...`

This is the preferred way to reproduce the work.

## Latest Clean Reproduction Run

Current reproducible run:

- run root:
  - `/home/marcos/apps-codex/_exports/sessions/repro/20260310T011719`
- live source at run time:
  - `/home/marcos/.codex/sessions/2026/03/06/rollout-2026-03-06T05-01-36-019cc298-733e-7610-a564-58fdd8969d48.jsonl`

Generated reports:

- `safe` report:
  - [safe report](/home/marcos/apps-codex/_exports/sessions/repro/20260310T011719/safe/reports/2026/03/06/rollout-2026-03-06T05-01-36-019cc298-733e-7610-a564-58fdd8969d48.report.json)
- `resume` report:
  - [resume report](/home/marcos/apps-codex/_exports/sessions/repro/20260310T011719/resume/reports/rollout-2026-03-06T05-01-36-019cc298-733e-7610-a564-58fdd8969d48.report.json)

Generated compacted files:

- `safe` compacted copy:
  - [safe compacted](/home/marcos/apps-codex/_exports/sessions/repro/20260310T011719/safe/compacted/2026/03/06/rollout-2026-03-06T05-01-36-019cc298-733e-7610-a564-58fdd8969d48.jsonl)
- `resume` compacted copy:
  - [resume compacted](/home/marcos/apps-codex/_exports/sessions/repro/20260310T011719/resume/compacted/rollout-2026-03-06T05-01-36-019cc298-733e-7610-a564-58fdd8969d48.jsonl)

## Interpreting the Latest Reports

### `safe`

Latest numbers from the reproducible run:

- source bytes: `26,949,852`
- compacted bytes: `13,315,874`
- saved: `13,633,978`
- original lines: `14,647`
- compacted lines: `14,647`

Meaning:

- structurally conservative
- best first live-swap candidate

### `resume`

Latest numbers from the reproducible run:

- source bytes: `26,949,852`
- compacted bytes: `2,472,736`
- saved: `24,477,116`
- original lines: `14,647`
- compacted lines: `487`

Meaning:

- much more aggressive
- intended for continuation experiments
- still not as trustworthy as `safe`

## What To Review First

### For `safe`

Check only:

- `jq_valid` is `true`
- `original_lines == compacted_lines`
- `changes` shows lots of bulky-field trimming and no semantic compaction

If those hold, the file is structurally doing what `safe` is supposed to do.

### For `resume`

Review this order:

1. `checkpoint_preview` in the report
2. `policy`
3. `changes`
4. only then the actual compacted JSONL if needed

`checkpoint_preview` is there specifically so future me does **not** need to inspect the full compacted file by hand first.

## Current State of Quality

### `safe`

Current verdict:

- good enough to try first for a manual swap

### `resume`

Current verdict:

- structurally valid
- conceptually useful
- still experimental

Remaining weak spots:

- `tool_results` still contain some summary/prose that should become cleaner factual outcomes
- `task_state` still carries some progress-style narration
- `constraints` still occasionally catch user messages that are not really lasting constraints

Important improvement already made:

- older history is no longer dominated by the old stop-button/server-management thread
- recent compaction work now dominates the checkpoint, which is correct

## Manual Swap Procedure

This cannot be done from the current Codex sandbox. It must be done manually.

### Before swapping

Only swap when the current Codex process is not actively writing that rollout file.

Practical rule:

- finish the current turn
- do not swap while the current Codex instance is still mid-work

### First live test should use `safe`

Template:

```sh
TARGET="/home/marcos/.codex/sessions/2026/03/06/rollout-2026-03-06T05-01-36-019cc298-733e-7610-a564-58fdd8969d48.jsonl"
RUN="/home/marcos/apps-codex/_exports/sessions/repro/20260310T011719"

cp "$TARGET" "$TARGET.pre-safe-swap.bak"
cp "$RUN/safe/compacted/2026/03/06/rollout-2026-03-06T05-01-36-019cc298-733e-7610-a564-58fdd8969d48.jsonl" "$TARGET"
```

Then:

- start a fresh Codex continuation
- observe whether context feels normal
- if anything seems wrong, roll back immediately

Rollback:

```sh
cp "$TARGET.pre-safe-swap.bak" "$TARGET"
```

### Only test `resume` after `safe`

Template:

```sh
TARGET="/home/marcos/.codex/sessions/2026/03/06/rollout-2026-03-06T05-01-36-019cc298-733e-7610-a564-58fdd8969d48.jsonl"
RUN="/home/marcos/apps-codex/_exports/sessions/repro/20260310T011719"

cp "$TARGET" "$TARGET.pre-resume-swap.bak"
cp "$RUN/resume/compacted/rollout-2026-03-06T05-01-36-019cc298-733e-7610-a564-58fdd8969d48.jsonl" "$TARGET"
```

Rollback:

```sh
cp "$TARGET.pre-resume-swap.bak" "$TARGET"
```

## Easy-To-Forget Details

1. The “latest” active session may live in an older date directory.
- The current active file kept resolving to `2026/03/06/...`
- do not assume today’s date directory is the one in use
- use modification time or the wrapper script

2. The compactor snapshots source bytes first.
- This was a necessary fix
- otherwise the live file can grow while being compacted

3. The `resume` report path may lose the date-directory structure.
- When `resume` is run from the frozen `safe/original/...` snapshot, its relative path is no longer under `~/.codex/sessions`
- so the `resume` report and compacted file may be stored by basename only
- this is expected

4. `checkpoint_preview` is the fastest review surface.
- use it first
- it is there to prevent needless reopening of the full compacted rollout

5. `safe` and `resume` answer different questions.
- `safe` = “can I trim bloat and keep the session shape?”
- `resume` = “can I compress old history into a stateful checkpoint and still continue?”

## What To Do Next

If returning to this later, use this order:

1. rerun the wrapper:
   - `_tools/session-compaction/reproduce_codex_session_profiles.sh --latest`
2. inspect the two reports
3. if doing a real swap, use `safe` first
4. only keep improving `resume` if swap/resume behavior is promising

If resuming development instead of swap testing, the next quality targets are:

1. make `tool_results` more factual and less prose-like
2. make `task_state` prefer settled state over process narration
3. tighten `constraints` further so they only contain durable user rules/preferences
