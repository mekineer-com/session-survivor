# Codex Session Analysis

## Scope

This file explains what usually goes wrong in long Codex sessions and which protections are already implemented in `session-survivor`.
It is written for operators using agentic CLI day to day.

For the source-code-level loader contract, read `CODEX_CLI_SESSION_MEMORY.md`.
For model-specific JSONL differences, read `CODEX_MODEL_JSONL_COMPAT.md`.

## One Rule You Must Not Break

- `event_msg.payload.type == task_started` records are turn boundaries.
- Removing all `task_started` records breaks turn parsing for compaction logic.
- `task_complete` and `context_compacted` help continuity and should stay in native-tail workflows.
- Preserve JSONL shape before deleting rows. For Codex `compacted` rows, keep original row placement when a clean source exists, keep readable `payload.message` summaries, and strip bulky `payload.replacement_history`; do not mine extra empty compacted shells from backups just as timeline separators.

## Common Failure Modes (In Plain Terms)

- Model-switch contamination:
  - one model's behavior can leak into later context after compaction
  - stale traces can stick inside compacted replacement history
- Instruction duplication:
  - repeated AGENTS payloads make files bigger and can over-prime old rules
- Scratch/tool transcript contamination:
  - internal scratch text can pollute synthetic summaries
- Depth drift:
  - repeated compaction-on-compaction slowly degrades detail quality
- Compacted-anchor weight:
  - non-empty `payload.message` may contain readable summary text; observed 5.6 remote compactions leave it empty
  - `payload.replacement_history` is a bulky machine bundle and can consume too much context
  - dropping compacted rows with readable summaries may destroy useful memory
  - empty compacted shells should not be invented or multiplied; preserve clean original placement unless they are proven repair artifacts

## Current Safeguards (Implemented)

In [compact_codex_session.py](/home/marcos/apps-codex/session-survivor/compact_codex_session.py):

- Depth guard policy:
  - `--warn-depth` (default `6`)
  - `--max-depth` (default `10`), with `--force` override
- Model-switch detection:
  - always scans `turn_context` model changes
  - emits warnings and records switches in report/manifest
- Opt-in model normalization:
  - `--normalize-model MODEL` rewrites `turn_context.payload.model`
- Conservative-by-default profile behavior:
  - `safe` keeps full turn structure and trims bulky fields
  - `resume` collapses older history and preserves recent native turns

In [chat_codex_session.py](/home/marcos/apps-codex/session-survivor/chat_codex_session.py):

- Hybrid chat resume path:
  - old history becomes chat-focused (`user`/`assistant` message text)
  - compacted row handling keeps the newest checkpoint row, preserves continuity-summary user messages, keeps the 50 newest ordinary user messages in its `replacement_history`, and strips older checkpoint bulk
  - default anchor behavior avoids duplicated/flooded empty compacted shells
  - default native tail is `1` turn (`--safe-tail-turns`)
- Fail-loud guardrails:
  - aborts on Codex format drift
  - aborts when no `task_started` turns exist

In [fix-codex-session.py](/home/marcos/apps-codex/session-survivor/fix-codex-session.py):

- targeted replacement-history scrub helpers for contamination not covered by `--normalize-model`.

In [chat_codex_v3.py](/home/marcos/apps-codex/session-survivor/chat_codex_v3.py):

- Weekly-summary activation path:
  - replaces matched old turn ranges with LLM-authored weekly summaries
  - strips all compacted `replacement_history` fields so Codex rebuilds from visible summary rows
  - keeps compacted row shells/messages and lets the next native compact create a fresh checkpoint

## Operator Guidance

- Start with `safe` for live swaps.
- Use `resume` or `chat-resume-hybrid-safe-tail` only when you need deeper cleanup.
- For profile comparison, always freeze once and run all profiles from that frozen file.
- Keep destructive rewrites opt-in so forensics stay intact by default.

## Known Limits

- Codex path does not inject a fresh AGENTS file from disk during compaction.
- Swap/rollback remains manual.
- There is no `--strip-web-searches` flag in current Codex compactor.
