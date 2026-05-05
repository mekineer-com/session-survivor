# Codex Session Analysis

## Scope

This file documents stable findings about Codex session behavior and what session-survivor currently does to reduce context rot safely.

## Structural Facts That Must Hold

- `event_msg.payload.type == task_started` records are turn boundaries.
- Removing all `task_started` records breaks turn parsing for compaction logic.
- `task_complete` and `context_compacted` are useful continuity signals and should be preserved in native-tail workflows.

## Common Failure Modes

- Model-switch contamination:
  - mixed-model stretches can bias later behavior after compaction
  - stale instructions and behavior traces can persist inside compacted replacement history
- Instruction duplication:
  - repeated AGENTS payloads bloat history and can over-prime stale rules
- Scratch/tool transcript contamination:
  - internal scratch text in assistant messages can pollute synthetic summaries
- Depth drift:
  - repeated compaction-on-compaction can gradually degrade detail quality

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
  - newest old-history compacted anchor is kept
  - default native tail is `1` turn (`--safe-tail-turns`)
- Fail-loud guardrails:
  - aborts on Codex format drift
  - aborts when no `task_started` turns exist

In [fix-codex-session.py](/home/marcos/apps-codex/session-survivor/fix-codex-session.py):

- targeted replacement-history scrub functions for contamination not covered by `--normalize-model`.

## Operational Guidance

- First-line live candidate: `safe`.
- Use `resume` and `chat-resume-hybrid-safe-tail` for explicit continuation recovery workflows.
- Always compact from a frozen snapshot when comparing profiles.
- Keep destructive rewrites opt-in; preserve forensics by default.

## Known Limits

- Codex path does not inject a fresh AGENTS file from disk during compaction.
- Swap/rollback remains manual.
- There is no `--strip-web-searches` flag in current Codex compactor.
