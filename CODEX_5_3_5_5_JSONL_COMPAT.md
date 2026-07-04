# Codex JSONL compatibility: `gpt-5.3-codex` and `gpt-5.5`

Last verified: 2026-06-03

## Bottom line

`chat_codex_session.py` is compatible with both `gpt-5.3-codex` and `gpt-5.5` JSONL sessions.

Switching a session between these models is safe with current tooling, including switching back and forth.

## What was compared

Representative sessions:

- `gpt-5.3-codex`: `/home/marcos/.codex/sessions/2026/05/29/rollout-2026-05-29T09-45-16-019e7432-5663-7e11-bae0-a407b21dd025.jsonl`
- `gpt-5.5`: `/home/marcos/.codex/sessions/2026/06/02/rollout-2026-06-02T17-55-20-019e8a8c-737b-7f42-83a2-3f588bdb0e36.jsonl`

Dry-run compaction was executed on both with `chat_codex_session.py`.

## Same skeleton (important)

Both keep the same core record shape used by `chat_codex_session.py`:

- top-level records: `session_meta`, `turn_context`, `event_msg`, `response_item` (and sometimes `compacted`)
- turn boundaries: `event_msg.payload.type` with `task_started` / `task_complete`
- chat messages: `response_item.payload.type == "message"` and `role in {user, assistant}`

This is the critical reason model switching works.

## Observed differences

These differences were seen in real files, but they do not break chat compaction:

- `turn_context`
  - `gpt-5.5` often has `workspace_roots`
  - `gpt-5.3-codex` may have `file_system_sandbox_policy`
- `session_meta`
  - `gpt-5.5` may include `thread_source`
- `event_msg` payload
  - `gpt-5.5` may include `duration`, `invocation`, `result`
- `response_item` payload
  - `gpt-5.5` may include `namespace` on tool-call records
- value-level drift
  - model name and effort differ (`gpt-5.3-codex`/`xhigh` vs `gpt-5.5`/`high` in sampled files)
  - `turn_context.summary` appears as `auto` more often in the `gpt-5.5` sample

## Why current script still works

- Old history compaction uses stable message shape only (`response_item.message` user/assistant text).
- Safe tail compaction uses `compact_record()`, which deep-copies records and only edits known heavy fields.
- Unknown keys (like `namespace`, `workspace_roots`, `thread_source`) are preserved unless explicitly touched.
- Turn boundary logic depends on `task_started`/`task_complete`, which are present in both.

## Practical guidance

- For normal live maintenance, keep using `chat_codex_session.py`.
- Keep `--safe-tail-turns >= 1` (default already does this).
- Default `--drop-compacted-anchor` keeps compacted row shells but strips bulky `payload.replacement_history`.
- Use `--keep-compacted-anchor` only when you specifically need the newest full native replacement-history checkpoint.
- `--normalize-model` in `compact_codex_session.py` is optional and only for explicit model-field cleanup.

## Caveat

Most observed differences look tied to Codex CLI version/runtime metadata, not to model response format itself. If future CLI releases remove `task_started`/`task_complete` or `response_item.message`, that would be a real schema break and should trigger script updates.
