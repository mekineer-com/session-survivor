# Codex CLI Session Memory

Source studied: fresh upstream OpenAI Codex clone at `/home/marcos/gemini-cli/codex-upstream-latest`, commit `be33f80bc6`.

This file is about Codex CLI's JSONL memory mechanics, not `AGENTS.md` instruction loading.

## Source Map

- row schema: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/protocol/src/protocol.rs:3153`
- compacted item shape: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/protocol/src/protocol.rs:3188`
- turn context purpose: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/protocol/src/protocol.rs:3227`
- resume opens/materializes old rollout for append: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/rollout/src/recorder.rs:813`
- rollout read/resume: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/rollout/src/recorder.rs:933`
- reconstruction entry point: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/core/src/session/rollout_reconstruction.rs:112`
- replacement-history reverse scan: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/core/src/session/rollout_reconstruction.rs:154`
- live history replacement: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/core/src/session/rollout_reconstruction.rs:317`
- compaction installs `replacement_history`: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/core/src/session/mod.rs:2978`
- TUI replay text: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/tui/src/chatwidget/replay.rs:170`

## Evidence By Conclusion

- Codex session rows are not free-form chat. `RolloutItem` is a typed enum with `session_meta`, `response_item`, inter-agent rows, `compacted`, `turn_context`, `world_state`, and `event_msg`: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/protocol/src/protocol.rs:3153`.
- A compacted row has readable `message`, optional `replacement_history`, and window identity fields: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/protocol/src/protocol.rs:3188`.
- `turn_context` is explicitly persisted so resume/fork can recover the latest durable baseline: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/protocol/src/protocol.rs:3227`.
- Resume reads rollout items, keeps the first `session_meta` as canonical identity, and pushes every parsed item in original order: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/rollout/src/recorder.rs:933`.
- Resume returns `InitialHistory::Resumed` with the parsed rollout history: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/rollout/src/recorder.rs:998`.
- Reconstruction scans newest-to-oldest and stops when it has a surviving `replacement_history` checkpoint plus resume metadata: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/core/src/session/rollout_reconstruction.rs:118`.
- When a compacted row has `replacement_history`, reconstruction records it as the base and replays only newer suffix rows: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/core/src/session/rollout_reconstruction.rs:181`.
- The base `replacement_history` replaces the in-memory model history: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/core/src/session/rollout_reconstruction.rs:317`.
- A compacted row without `replacement_history` is treated as legacy and rebuilt from collected user messages plus `compacted.message`: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/core/src/session/rollout_reconstruction.rs:341`.
- Current compaction install forces `replacement_history: Some(items.clone())` into the persisted `CompactedItem`: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/core/src/session/mod.rs:2991`.
- TUI replay renders context compaction as `Context compacted`: `/home/marcos/gemini-cli/codex-upstream-latest/codex-rs/tui/src/chatwidget/replay.rs:170`.

## Memory Model First

Codex does not remember a session by rereading the terminal transcript as prose.

On resume, Codex rebuilds in-memory `ResponseItem` history from the JSONL rollout, then uses that reconstructed history as model input for future turns.

That means the important question for session-survivor is not "what rows look tidy?" It is:

> what rows affect Codex's reconstructed model history, resume state, and UI replay?

Only after answering that should we decide what formatting can be reduced.

## Loader Contract

Codex session files are append-only JSONL rollouts. Each row is a `RolloutItem` with top-level `type` and `payload`.

Known row types are defined in `codex-rs/protocol/src/protocol.rs`:

- `session_meta`
- `response_item`
- `inter_agent_communication`
- `inter_agent_communication_metadata`
- `compacted`
- `turn_context`
- `world_state`
- `event_msg`

Resume reads the full JSONL with `RolloutRecorder::load_rollout_items()`. Parse failures are skipped, but malformed/unknown semantic rows are still dangerous because reconstruction depends on row order and row type. Current upstream can also materialize compressed rollouts before appending.

On resume, Codex opens the existing rollout file for append. It does not rewrite old rows.

## What Actually Becomes Model Memory

Resume calls `Session::record_initial_history()`, which calls `reconstruct_history_from_rollout()`.

That reconstruction scans newest-to-oldest looking for the newest surviving `compacted.payload.replacement_history`.

If found:

- `replacement_history` becomes the complete model-history base.
- older rollout rows before that checkpoint do not matter for live model memory.
- rows after that checkpoint are replayed forward on top of it.

If no `replacement_history` is found:

- Codex rebuilds from surviving `response_item` rows and legacy `compacted` rows.
- a `compacted` row without `replacement_history` is treated as legacy compaction and rebuilt from its `message` plus collected user messages.

Plain English:

- `compacted.payload.message` is readable summary text.
- `compacted.payload.replacement_history` is the machine checkpoint that actually resets the model's resumed memory.
- a compacted row without `replacement_history` is still a real Codex row shape; do not delete it just because it looks empty.

## Why Empty Compacted Rows Cause Walls

The TUI replay path renders each context-compaction item as `Context compacted`.

So preserving hundreds of empty `compacted` shells can produce a visible wall of:

```text
Context compacted
Context compacted
Context compacted
```

That wall is UI replay noise. It does not prove the rows are useful model memory, but it also does not prove they are safe to delete from every file.

## Fields That Matter To Codex Memory

Keep:

- original `session_meta`
- original row order when a clean source exists
- `response_item` rows that form reconstructed model history
- recent native tail rows
- `turn_context` rows in the native tail
- `world_state` rows in the native tail
- `event_msg` rows that define user-turn boundaries, rollback, token usage, and visible resume history
- `compacted` rows from a clean original source, even if the readable `message` is empty
- compacted `window_number` / window ids from clean original structure
- non-empty `compacted.payload.message` summaries when present

Strip:

- bulky old `compacted.payload.replacement_history` when reducing context pressure

Drop only when proven:

- duplicated/flooded compacted rows introduced by our own bad repair
- rows that are artifacts of marker mining/sorting, not rows from a clean original structure

Do not mine compacted shells from backups just to create timeline dividers. But if a clean original source has those rows in-place, preserving original structure is safer than guessing they are useless.

## Safe Strategy For `chat_codex_session.py`

Default should stay:

- old history becomes compact chat-focused rows
- recent native tail stays native
- readable compacted summaries stay
- `replacement_history` is stripped from old compacted rows
- compacted rows from clean structure are preserved unless proven to be duplicated flood artifacts
- `safe_tail_turns=1`

Best operator timing:

1. Let Codex compact.
2. Send one normal turn and let Codex answer.
3. Exit Codex.
4. Run `chat_codex_session.py`.

Running right before Codex compacts is less useful because Codex immediately creates a fresh bulky native checkpoint.

## Formatting Is A By-Product

The point of studying Codex memory is not to make prettier JSONL. The formatting rules follow from the memory rules:

- Preserve original structure first.
- Strip proven bulk second.
- Remove only proven artifacts third.

Do not flatten everything into plain chat without preserving enough native tail. Codex's reconstruction also uses:

- user-turn boundaries from `response_item` messages
- rollback markers from `event_msg`
- latest model/cwd/sandbox state from `turn_context`
- world-state replay from `world_state` and compacted window fields
- token usage from latest token-count `event_msg`

For old summarized history, exact native structure can be reduced only after we know what memory is being replaced. For the live tail, preserve native shape.

## Practical Check After Any Rewrite

Before swapping a candidate session live:

- JSONL parses line by line.
- first row still includes the original session id.
- readable compacted summaries are still present if they existed.
- old `replacement_history` bulk is gone unless intentionally kept.
- no marker wall was introduced by our rewrite.
- latest native tail still contains relevant `turn_context`, `world_state`, `event_msg`, and `response_item` rows.
