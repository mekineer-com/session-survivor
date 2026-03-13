# session-survivor

Tools for compacting and continuing long AI agent sessions.

## Status

This repo is a working prototype, not a packaged release.

Current support:

- Codex JSONL
  - `safe`
  - `resume`
  - `--show-lineage`
- Claude JSONL
  - `safe`
  - `--show-lineage`

Current non-goals:

- fully automated swap/rollback
- polished packaging
- Claude `resume`

## Why this exists

Long coding-agent sessions accumulate a lot of low-value bulk:

- encrypted or signed reasoning blobs
- oversized tool output
- repeated instruction payloads
- stale exploratory chatter

The goal is to reduce that bulk while preserving what matters for continuation:

- decisions
- constraints
- tool outcomes
- current task state

## Quick start

Codex `safe` + `resume` from the latest session:

```sh
./reproduce_codex_session_profiles.sh --latest
```

Claude `safe` from the latest active project session:

```sh
./reproduce_claude_safe.sh --latest
```

Inspect lineage/provenance for a compacted file:

```sh
python3 compact_codex_session.py --show-lineage /path/to/session.jsonl
python3 compact_claude_session.py --show-lineage /path/to/session.jsonl
```

Run one-off compaction directly:

```sh
python3 compact_codex_session.py --profile safe /path/to/codex.jsonl
python3 compact_codex_session.py --profile resume /path/to/codex.jsonl
python3 compact_claude_session.py /path/to/claude.jsonl
```

## What each script does

- `compact_codex_session.py`
  - main Codex compactor
  - supports `safe`, `resume`, and `--show-lineage`
- `compact_claude_session.py`
  - conservative Claude compactor
  - currently `safe` only
- `lineage.py`
  - provenance and parent/child session lineage helpers
- `reproduce_codex_session_profiles.sh`
  - runs `safe` first, then `resume` from the same frozen snapshot
- `reproduce_claude_safe.sh`
  - runs Claude `safe` against the latest JSONL in the active Claude project folder

## Current behavior

### Codex

`safe`:

- keeps full turn structure
- trims bulky fields only
- intended as the first real swap candidate

`resume`:

- collapses older history into a checkpointed compacted span
- keeps recent turns intact
- emits per-run manifest data
- still experimental

Observed runtime behavior:

- on very long live Codex sessions, native background compaction can sometimes raise the "context remaining" meter much more than expected
- in one real session, the jump was on the order of ~50%, whereas earlier background jumps had usually been much smaller
- do not interpret that as proof of a magically larger true context window
- the safer explanation is that the live context became much more compressible:
  - more native `compacted` / `context_compacted` history already in the rollout
  - less irreducible hot-state baggage
  - better external anchors like a shorter handoff file
- trust the jump directionally, not literally; the real test is whether factual continuity still holds after the jump

### Claude

Current `safe` trimming targets:

- `thinking.signature`
- long `tool_result` string content
- large `toolUseResult.*` string fields
- oversized plain string `message.content`
- oversized `system/local_command` content

## Lineage model

The repo now treats compaction as a parent/child derivation problem.

- original live session or frozen snapshot = parent
- compacted output = child

Per-run manifests record:

- source path
- source hash
- profile
- artifact paths
- parent provenance
- ancestor depth

## Files

- `CODEX_COMPACTION.md`
  - design notes from the original Codex-focused work
- `CODEX_SESSION_COMPACTION_REPRO.md`
  - exact reproduction procedure for the earlier Codex profile work

These docs still use Codex-specific naming and should be generalized later.

## Limits

- paths are still tuned to the current local workspace
- no standalone packaging yet
- no generic session schema across vendors yet
- no Claude `resume` policy yet
