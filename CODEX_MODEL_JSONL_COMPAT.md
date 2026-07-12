# Codex JSONL model compatibility

Last verified: 2026-07-12

## Stable loader contract

All models checked so far keep the core records used by `chat_codex_session.py`:

- top-level records: `session_meta`, `turn_context`, `event_msg`, `response_item`, and sometimes `compacted`
- turn boundaries: `event_msg.payload.type` with `task_started` / `task_complete`
- chat messages: `response_item.payload.type == "message"` and `role in {user, assistant}`

Unknown fields are preserved in the native safe tail because `compact_record()` deep-copies records and edits only known heavy fields.

## `gpt-5.3-codex` and `gpt-5.5`

Verified: 2026-06-03

### What was compared

Representative sessions:

- `gpt-5.3-codex`: `/home/marcos/.codex/sessions/2026/05/29/rollout-2026-05-29T09-45-16-019e7432-5663-7e11-bae0-a407b21dd025.jsonl`
- `gpt-5.5`: `/home/marcos/.codex/sessions/2026/06/02/rollout-2026-06-02T17-55-20-019e8a8c-737b-7f42-83a2-3f588bdb0e36.jsonl`

Dry-run compaction was executed on both with `chat_codex_session.py`.

### Result

`chat_codex_session.py` is compatible with both models. Switching a session between them, including switching back, preserves the loader contract above.

### Observed differences

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

### Why current script still works

- Old history compaction uses stable message shape only (`response_item.message` user/assistant text).
- Unknown keys such as `namespace`, `workspace_roots`, and `thread_source` are preserved in the native safe tail.
- Turn boundary logic depends on `task_started`/`task_complete`, which are present in both.

## `gpt-5.5` to `gpt-5.6-sol`

Observed: 2026-07-11; compaction verified: 2026-07-12

Runtime boundary:

- before restart: `gpt-5.5`, Codex CLI `0.142.3`
- after restart: `gpt-5.6-sol`, Codex CLI `0.144.1`

Because both the model and CLI changed, only the model value and context-window change can confidently be called model-related. The new metadata records below are attributed to Codex CLI `0.144.1`.

### Model-related observations

- `turn_context.payload.model` changed from `gpt-5.5` to `gpt-5.6-sol`.
- The configured context window is `372000`; runtime `task_started` and token-count records report an effective window of `353400` (95%).
- Message, reasoning, function-call, and custom-tool-call payload shapes remained compatible.

### Codex CLI `0.144.1` observations

- `event_msg.payload.type == "thread_settings_applied"` is written before turns.
- A top-level `world_state` record stores the current AGENTS/environment baseline.
- `turn_context` includes `approvals_reviewer`.
- Existing compacted rows already had `window_id`, `previous_window_id`, `first_window_id`, and `window_number`; those fields are not a 5.6 addition.

The startup injected one 25,176-character `model_switch` developer message. It is not repeated each turn. Later 5.6 checkpoints exclude this developer row.

### Session-survivor compatibility

- Core format checks still pass: `task_started`, `task_complete`, and `response_item.message` remain present.
- Both function-call and custom-tool-call records are already handled.
- New fields and records are preserved while they remain in the native safe tail.
- Once `world_state` leaves that tail, chat-focused old-history compaction drops it. Codex emits a fresh full snapshot after compaction/resume, so this does not lose conversation memory.

### Native 5.6 compaction

Two dedicated compaction turns were inspected in Codex session `019cc298-733e-7610-a564-58fdd8969d48`:

- window 9: 103 user-message items plus one encrypted compaction item; 136,164 replacement-history bytes
- window 10: 129 user-message items plus one encrypted compaction item; 150,873 replacement-history bytes

Both retain the existing six-field compacted payload (`message`, `replacement_history`, and four window fields). `payload.message` is empty; the machine summary is the encrypted compaction item. There are no assistant or developer message rows, and the one-time `model_switch` block is absent. This confirms current `chat_codex_session.py` checkpoint pruning remains compatible with native 5.6 output.

## Practical guidance

- For normal live maintenance, keep using `chat_codex_session.py`.
- Keep `--safe-tail-turns >= 1` (default already does this).
- Default behavior keeps the newest checkpoint row, preserves continuity-summary user messages, keeps the 50 newest ordinary user messages in its `replacement_history`, and strips older checkpoint bulk.
- `--normalize-model` in `compact_codex_session.py` is optional and only for explicit model-field cleanup.

## Caveat

Most observed differences are tied to Codex CLI version/runtime metadata, not model response format. If a future CLI removes `task_started`/`task_complete` or `response_item.message`, that is a real schema break and requires script updates.
