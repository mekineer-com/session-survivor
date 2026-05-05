# session-survivor

Tools for compacting and continuing long AI agent sessions.

## Status

This repo is actively used script tooling, not a packaged release.

Current support:

- Codex JSONL
  - `safe`
  - `resume`
  - `--show-lineage`
- Claude JSONL
  - `safe`
  - `chat-resume`
  - `--show-summary`
  - `--show-lineage`
- Codex JSONL
  - `chat-resume-hybrid-safe-tail`
  - `--show-summary`
  - `--show-lineage`
- Gemini JSON
  - `safe`
  - `--show-summary`
  - `--show-lineage`

Current non-goals:

- fully automated swap/rollback
- polished packaging

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

Codex `safe` + `resume` + `chat-resume-hybrid-safe-tail` from the latest session:

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
python3 chat_codex_session.py --latest --show-summary
python3 compact_claude_session.py /path/to/claude.jsonl
python3 chat_claude_session.py /path/to/claude.jsonl
python3 compact_gemini_session.py /path/to/gemini-session.json

# Claude safe depth controls (optional overrides)
python3 compact_claude_session.py /path/to/claude.jsonl --warn-depth 8 --max-depth 12
```

Safe forensics workflow (Codex stuck / context-rot investigation):

```sh
# 1) Freeze first (never analyze the live mutable file directly)
cp /path/to/rollout-*.jsonl /path/to/rollout-*.jsonl.freeze

# 2) Timeline only (small/structured, low contamination risk)
tail -n 200 /path/to/rollout-*.jsonl.freeze | jq -r '.timestamp+" | "+.type'

# 3) Error scan (avoid huge raw dumps)
rg -n '"status":"failed"|"type":"error"|429|timeout|task_complete' /path/to/rollout-*.jsonl.freeze

# 4) Compact from the frozen snapshot (safe keeps chat content intact)
python3 compact_codex_session.py --profile safe /path/to/rollout-*.jsonl.freeze
# Optional aggressive path:
# python3 compact_codex_session.py --profile resume /path/to/rollout-*.jsonl.freeze
```

Session markers:

- Codex: when `CODEX_THREAD_ID` is present, `compact_codex_session.py` appends a marker line to `~/.codex/session-survivor/thread-markers.jsonl`.
- Claude: `compact_claude_session.py` appends markers to `~/.claude/session-survivor/thread-markers.jsonl`.
- Gemini: `compact_gemini_session.py` appends markers to `~/.gemini/session-survivor/thread-markers.jsonl`.
- Marker writes are de-duped by `{session_or_thread_id}:{source_sha256}:{profile}`.
- Each report now includes `thread_marker_path`.
- In `resume` profile, synthetic compacted turn IDs are deterministic for same input/options.
- Report compatibility alias: top-level `profile` is emitted (mirrors `policy.profile`).
- Format-drift warnings: when core Codex record shapes are missing, warnings are emitted to stderr and included as `warnings[]` in the report.

## What each script does

- `compact_codex_session.py`
  - main Codex compactor
  - supports `safe`, `resume`, and `--show-lineage`
- `chat_codex_session.py`
  - Codex hybrid chat extractor for resume: chat-only old history + native safe tail
  - safe tail rows are compacted with Codex `safe` rules (tool/output trimming, reasoning cleanup)
  - supports `--latest`, `--show-summary`, and `--show-lineage`
- `compact_claude_session.py`
  - conservative Claude compactor
  - currently `safe` only, plus `--show-summary` and `--show-lineage`
- `chat_claude_session.py`
  - aggressive Claude chat-only compactor intended for `/resume`
  - emits dialogue (`user`/`assistant` text) plus minimal resume-discovery metadata
  - single behavior (`claude-chat-resume`), plus `--show-summary` and `--show-lineage`
- `compact_gemini_session.py`
  - conservative Gemini compactor
  - currently `safe` only, plus `--show-summary` and `--show-lineage`
- `codex_safety.py`
  - depth guard and model switch detection helpers for Codex compactor
- `fix-codex-session.py`
  - one-off scrubber for model contamination inside `compacted.replacement_history`
  - two importable functions: `scrub_replacement_history_model`, `scrub_replacement_history_phrases`
  - covers a scope `--normalize-model` does not reach (replacement_history items, not turn_context)
- `lineage.py`
  - provenance and parent/child session lineage helpers
- `reproduce_codex_session_profiles.sh`
  - runs `safe`, then `resume` from the same frozen snapshot, plus `chat-resume-hybrid-safe-tail` from source
- `reproduce_claude_safe.sh`
  - runs Claude `safe` against the latest JSONL in the active Claude project folder

## Claude long-lived hook config (manual)

If you want the Claude long-lived behavior from this project, set these hook entries in `~/.claude/settings.json`.

Use two path placeholders:

- `path-to-project-root`: your active project root (where `HANDOFF.md` lives)
- `path-to-session-survivor`: your local clone of this repo (where `_tools/hooks/` lives)

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "compact",
        "hooks": [
          {
            "type": "command",
            "command": "echo '--- Recent HANDOFF (post-compaction refresh) ---' && tail -30 path-to-project-root/HANDOFF.md"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Read",
        "hooks": [
          {
            "type": "command",
            "command": "path-to-session-survivor/_tools/hooks/claude-read-before-write-gate.sh"
          }
        ]
      },
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "path-to-session-survivor/_tools/hooks/claude-read-before-write-gate.sh"
          },
          {
            "type": "command",
            "command": "path-to-session-survivor/_tools/hooks/doc-backup.sh"
          }
        ]
      }
    ]
  }
}
```

Notes:

- Merge these entries into your existing `hooks` object; do not overwrite unrelated hooks.
- Optional: set `CLAUDE_READ_FRESHNESS_SECONDS` to tune read freshness window (default 3600).
- Optional: set `DOC_BACKUP_ARCHIVE_DIR` to override backup destination; default is `path-to-session-survivor/_archive/doc-versions`.

## Current behavior

### Codex

Use this order:

1. `safe` first (lowest risk).
2. `resume` if you need stronger compaction.
3. `chat-resume-hybrid-safe-tail` when old non-chat history is the main source of context rot.

`safe` (first live-swap candidate):

- keeps normal turn structure
- keeps chat messages as-is
- trims heavy payloads (reasoning blobs, large tool output/input)
- compacts repeated AGENTS/scratch text in metadata and synthetic paths

`resume` (more aggressive):

- keeps recent turns native
- compresses older turns into one compacted checkpoint span
- keeps bounded `replacement_history`
- writes report/manifest metadata for auditing

`chat-resume-hybrid-safe-tail` (`chat_codex_session.py`):

- old history becomes chat-focused (`user`/`assistant` text)
- keeps newest old-history native compacted anchor (`type="compacted"`)
- keeps a native safe-compacted recent tail (`--safe-tail-turns`, default `1`)
- drops old boundary-event spam from the historical section
- fails loud on format drift or missing `task_started` turns
- tail compaction knobs: `--max-tool-input-chars`, `--max-reasoning-chars`
- source selection rule: use exactly one source (`--latest` or explicit path)
- usage:
  - `python3 chat_codex_session.py --latest --show-summary`
  - `python3 chat_codex_session.py /path/to/rollout.jsonl`
  - `python3 chat_codex_session.py /path/to/rollout.jsonl --safe-tail-turns 8`

Codex guardrails in `compact_codex_session.py`:

- depth policy: warn at `--warn-depth` (default `6`), stop at `--max-depth` (default `10`) unless `--force`
- model-switch detection is always on and recorded in report/manifest
- model rewriting is opt-in with `--normalize-model MODEL`

Codex AGENTS handling:

- no AGENTS refresh/injection from disk during compaction
- historical AGENTS copies are compacted away; a fresh AGENTS block is naturally reintroduced on later live turns

Runtime note:

- on long sessions, native background compaction can raise the "context remaining" meter more than expected
- treat the jump as directional, not literal proof of a larger true context window
- the real check is factual continuity after the jump

### Claude

Current `safe` trimming targets:

- remove all `thinking` blocks from `message.content` (avoids signed-thinking compaction failures)
- long `tool_result` string content
- nested oversized strings anywhere inside `toolUseResult`
- oversized plain string `message.content`
- oversized `system/local_command` content
- reduce `message.usage` to core counters/tier
- compact oversized `file-history-snapshot.trackedFileBackups` maps to a bounded entry set + truncation metadata
- depth guard for safe-on-safe chains:
  - warning at depth `>= 8`
  - hard stop at depth `>= 12` (non-zero exit; start fresh from handover)
- per-run anchor digests from live project files:
  - `AGENTS.md`, `HANDOFF.md`, `CLAUDE.md`
  - report fields: `anchor_sources`, `anchor_hashes`, `anchor_missing`
- stale lineage pruning for status/history records:
  - lineage/status types are windowed to newest entries per type
  - duplicate/superseded lineage blobs are dropped
  - report fields: `pruned_lineage_entries`, `kept_lineage_entries`

Current Claude-safe optional flags:

- `--warn-depth` (default `8`)
- `--max-depth` (default `12`)
- `--lineage-window` (default `512`)

Claude chat-resume mode (`chat_claude_session.py`):

- purpose:
  - strip Claude session JSONL to chat dialogue only while keeping it resumable
- kept records:
  - latest `custom-title` record (`type=custom-title`, `customTitle`, optional `sessionId`)
  - top-level `type in {user, assistant}`
  - `message.role`
  - merged text content from string content or `message.content[*].type=text`
  - `timestamp`
  - `uuid` (chosen resume identity field)
  - lightweight envelope keys from each kept chat row when present:
    - `parentUuid`, `isSidechain`, `sessionId`, `userType`, `entrypoint`, `cwd`, `version`, `gitBranch`, `slug`, `permissionMode`
- dropped records:
  - attachments, queue/status lineage, most permission/status records, file-history snapshots, non-text tool payloads
  - command/meta wrapper chatter (`<local-command-caveat>`, `<command-name>`, task notifications)
- guardrails:
  - idempotent truncation (re-running chat-resume does not keep shortening already-compacted placeholders)
  - hard fail (non-zero exit) if filtering would produce an empty output file
- why `uuid` (not `parentUuid`):
  - controlled `claude -r <session_id> --fork-session -p` tests passed with `type+message+timestamp+uuid`
  - controlled tests also passed with `parentUuid`, but `uuid` is self-contained and does not depend on parent links to dropped records
- tested resume boundary (May 1, 2026):
  - passes: `type + message + timestamp + uuid`
  - passes: `type + message + timestamp + parentUuid`
  - fails: `type + message + timestamp` (and conversation-only ultra-minimal variants)

Usage:

```sh
# Build compacted chat-resume copy (does not swap live file by itself)
python3 chat_claude_session.py /path/to/claude.jsonl

# Optional: tighter per-message cap
python3 chat_claude_session.py /path/to/claude.jsonl --max-message-chars 1600
```

Post-swap hygiene for Claude sessions:

- if the target session was already open while you swapped the JSONL, restart Claude before testing (`/exit` all Claude terminals, then relaunch) so it reloads the file from disk
- Claude session discovery loads files that end with `.jsonl`; backup suffix variants like `*.jsonl.pre-*` and `*.jsonl.orig` are ignored
- still move backups out of `~/.claude/projects` for hygiene and to avoid operator confusion

### Gemini

Current `safe` trimming targets:

- oversized `messages[*].toolCalls[*].resultDisplay` text (including nested object forms like `originalContent` / `newContent`)
- oversized nested string fields inside `messages[*].toolCalls[*].result`
- oversized nested strings inside `messages[*].toolCalls[*].args`
- oversized `messages[*].thoughts[*].description`
- oversized `messages[*].content` (string and nested list/dict text) and `messages[*].displayContent`

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

Session chaining (what this means):

- each compacted Codex output includes a checkpoint provenance block (source path/hash/profile/time)
- if you compact that compacted output again, the new file becomes the next child in the chain
- `--show-lineage` follows those links backward so you can see ancestry from newest output to original source
- this gives an audit trail for multi-step compact/continue workflows instead of opaque one-off rewrites

## Files

- `CODEX_SESSION_ANALYSIS.md`
  - current Codex failure-mode analysis and implemented safeguards
- `CODEX_SESSION_COMPACTION_REPRO.md`
  - current Codex profile reproduction and manual swap runbook

## Limits

- paths are still tuned to the current local workspace
- no standalone packaging yet
- no generic session schema across vendors yet
- no full-fidelity Claude `resume` policy yet (only aggressive `chat-resume`)
