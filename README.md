# session-survivor

Tools for compacting and continuing long AI agent sessions.

## Current scope

This repo currently contains a practical prototype for compacting long Codex-style JSONL rollout logs.

It supports two profiles:

- `safe`
  - trims bulky fields
  - keeps full turn structure
  - intended as the first real swap candidate
- `resume`
  - compacts older history into a checkpointed summary span
  - keeps recent turns intact
  - experimental

It also supports:

- `--show-lineage`
  - prints the current file's checkpoint provenance and parent-chain view
  - useful for verifying derived-session relationships

Claude support:

- `compact_claude_session.py`
  - separate conservative adapter for Claude JSONL
  - currently `safe` only
  - trims obvious bulk like:
    - `thinking.signature`
    - long `tool_result` content
    - large `toolUseResult.*` string fields
    - oversized `system/local_command` content

## Files

- `compact_codex_session.py`
  - main compactor
- `lineage.py`
  - provenance and parent/child lineage helpers
- `reproduce_codex_session_profiles.sh`
  - runs `safe` and `resume` from the same frozen snapshot
- `CODEX_COMPACTION.md`
  - design notes
- `CODEX_SESSION_COMPACTION_REPRO.md`
  - exact reproduction procedure

## Current state

This is a working local-first prototype, not a polished published package yet.

Known limitations:

- paths are still tuned to the current local workspace
- docs still use some Codex-specific naming
- no standalone packaging yet
- no swap/resume automation yet

## Lineage

The repo now emits a per-run manifest and preserves parent/child provenance.

Current model:

- original live session or frozen snapshot = parent
- compacted output = child
- manifests record:
  - source path
  - source hash
  - profile
  - artifact paths
  - parent provenance
  - ancestor depth

## Why this exists

Long coding-agent sessions accumulate a lot of low-value bulk:

- encrypted reasoning blobs
- oversized tool output
- repeated instruction payloads
- stale exploratory chatter

The goal is to reduce that bulk while preserving what matters for continuation:

- decisions
- constraints
- tool outcomes
- current task state
