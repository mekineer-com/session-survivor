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

## Transcript-Only Backup Restore (2026-10-02)

Tested installed Codex CLI **0.159.3** in disposable homes with fictional
dialogue, not copied real conversations. See [`probe_backup_resume.py`](probe_backup_resume.py).

- A fresh `CODEX_HOME` containing only the native rollout and minimal config,
  without any restored SQLite databases, listed the session and loaded its
  dialogue through `thread/read` and `thread/resume` by UUID.
- Native `turn_context` recovered model `gpt-6.1-sol` and effort `medium` without
  resume overrides. The CLI created fresh state databases itself.
- A name assigned through `thread/name/set` survived in the original test
  profile but **did not survive transcript-only restoration**. Custom names
  have separate `session_index.jsonl`/state storage. UUID resume still worked.
- A separate fresh profile, restored with only the fictional rollout plus
  authentication, completed a real `codex exec resume` turn on `gpt-6.1-sol`.
  Its final answer correctly recalled the marker from earlier dialogue, with
  the same thread UUID. No tools were called.

**Backup implication:** no old SQLite database is required for this tested
ordinary conversation-resume path. Keep native rollout metadata and turn
records intact. Config/authentication remain separate machine-backup concerns;
the checkbox app is not an account migration tool. Custom names are an optional
loss, not evidence that the conversation is unrecoverable. No backup scope was
expanded. Goals, queues, background workers, attachments and full-machine
recovery were not certified by this simple-dialogue test.

### 2026-10-09 - Aster: checkpoint history and metadata are paired

Current Codex serializes `replacement_history_metadata` beside
`replacement_history`. The loader rejects unequal lengths and metadata without
history (`codex-rs/history/src/rollout_payload.rs`, `CompactedItemWire` conversion).
Our chat cleanup pruned history but retained original metadata, making checkpoints
unloadable. Codex's resumed request used roughly 300k tokens despite a 6.2 MiB file;
fallback replay of old chat is consistent with those rejected checkpoints.

Prune both arrays by the same retained indices. Strip both when hollowing an
older checkpoint. Reject mismatched input rather than invent metadata. Parseable
JSON alone is insufficient: validate checkpoint array alignment before swaps.
Skipping the post-compact ordinary turn was not the cause of this defect.

**Origin traced:** our checkpoint-pruning function landed July 12, 2026 in
`98bdabd`, before the companion metadata existed. Upstream introduced the paired
format and strict length validation August 11 in `3a6f747d77` (#38058).
The first such checkpoint preserved in Codex's full backup is October 1 at
06:31 UTC (01:31 Peru), with 385 history entries and 385 metadata entries.
The October 9 original also matched (1,112 each); our cleanup broke the pairing.
This is a format-compatibility regression exposed by newer Codex output, not a
recent rewrite of our pruning code or a missing post-compact message. These dates
identify commits and preserved evidence, not the exact release first shipping it.

## Headless Resume: Server Response Replay Failure (2026-10-20)

Do not diagnose session-file corruption from
`unsupported_persisted_item_context` alone. In two observed `codex exec resume`
attempts, the session loaded and generated replies/tool calls before the server
returned HTTP400 with parameter `previous_response_id`. The same agent later
completed the source audit through interactive `codex resume`. The commands were
syntactically valid. Failed prompts remained in the local history, explaining
why the agent knew about them despite older visible terminal scrollback.

### What The Matching Client Source Establishes

Inspected the source recorded for the installed0.159.3 recall build, a1e4e8f45e
(later README-only HEAD756afef069), not the older general reference checkout.

- `codex-rs/core/src/client.rs`: the WebSocket continuation constructs
  `previous_response_id` from the last server response and sends incremental
  items. The parameter identifies server-held response state, not a local
  session UUID or JSONL filename.
- `codex-rs/codex-api/src/common.rs`: the ordinary full HTTP Responses request
  has no such continuation field. That suggests a transport-level diagnostic
  route; it does not prove a workaround for the affected agent.
- `codex-rs/codex-api/src/endpoint/responses_websocket.rs`,
  `map_wrapped_websocket_error_event`: the installed client recognizes
  `previous_response_not_found` and `websocket_connection_limit_reached` as
  retryable. It does NOT recognize `unsupported_persisted_item_context`; an
  accompanying400 instead becomes an HTTP transport error. Source inspection
  therefore identifies a missing recovery case, not a verified compactor bug.
- Headless exec sets originator `codex_exec`; the interactive TUI initializes
  an app-server client as `codex-tui` and can use the shared daemon. Backend
  routing, connection state and account caching are hypotheses to distinguish,
  not established reasons for the different outcomes.

The installed source also contains upstream request/tool-serialization changes
after the0.159.3 release tag. A diff against that tag is not automatically a diff
of our recall work. Trace commit provenance before attributing a transport defect
to local patches. No executable was rebuilt or changed for this investigation.

### Executed Disposable Controls

Used new ephemeral sessions with fictional probe data, model gpt-6.1-sol, effort
xhigh and one shell call (`printf`) followed by a final reply. Both default and
HTTP-only controls exited0 with the expected reply after the tool result.
These short controls did NOT reproduce the affected resumed conversation.
They rule out an always-broken headless path, not history-dependent or
intermittent backend failure. No real agent was resumed, transformed or swapped.

The HTTP control used a process-local custom provider with these overrides:

```toml
model_provider = "aster-http-probe"
[model_providers.aster-http-probe]
name = "OpenAI"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false
```

No configuration file was edited. This provider alias may affect more than
transport capabilities, so the two successful controls are not a strict
single-variable comparison or proof that HTTP fixes the real failure.

Important: setting `model_providers.openai.supports_websockets=false` is NOT a
verified switch in this build. `merge_configured_model_providers()` uses
`entry(...).or_insert(...)` for existing non-Bedrock providers, so that override
does not replace the built-in OpenAI entry. Do not report a transport test as
HTTP-only merely because that flag was supplied.

### Next Diagnostic Boundary

Normally reproduce with fictional resumed/compacted history and tool round trips.
Marcos explicitly authorized isolated copies of Vesper's real history for the
comparison below; that exception does not authorize testing the original or
editing reasoning/checkpoints to manufacture a fix.
The trigger (particular history shape, account/backend routing, or connection
state) remains unresolved. A client recovery hunk or per-invocation workaround
needs that evidence and review; no global transport/model change is justified
by these controls. Do not blank encrypted reasoning or strip history as a fix
for a server-held response replay error.

### Authorized Real-History Copy Controls

After compact, tested two byte-identical copies of the current Vesper JSONL
(2,618,856 bytes), each in a separate disposable `CODEX_HOME` and `HOME`.
Both kept the original UUID, but each fresh `state_5.sqlite` resolved it to its
own copied rollout path, not the canonical session. Authentication was copied,
not symlinked; inherited API-key and parent-thread environment variables were
removed. No original session was resumed or swapped.

Both tests used gpt-6.1-sol/xhigh, read-only, web disabled, one shell `printf`
and a final marker reply. Default-provider headless resume exited0; the
HTTP-only provider alias above also exited0. Neither produced the replay error.
The default run's actual wire transport was not traced; do not label that
successful run a confirmed WebSocket continuation. These used a minimal config
with apps/plugins disabled and no configured MCP servers, not the full original
environment. The HTTP alias also changes provider identity. This is not a
single-variable reproduction of the earlier failures or proof of an HTTP fix.
The copied history includes the later successful interactive audit, so it is
not an exact snapshot from immediately before either failure.

The current source retains the exact October12 Session Survivor candidate as
its byte-for-byte prefix. That maintenance report records22 removed encrypted
reasoning fields, but the resulting history still resumes headlessly in these
controls. A universal claim that headless requires what cleanup removed is
therefore contradicted; a more specific history/environment interaction is
not ruled out. Do not restore discarded fields on speculation.

Source tracing narrows the mechanism: `LastResponse` is filled from a newly
completed server response through an in-memory oneshot; `prepare_websocket_request`
uses that result and a matching input prefix to construct `previous_response_id`.
It does not read a response ID out of the restored JSONL. The earlier shell
output before failure establishes that resume reached the new tool loop.
It does not establish that old history cannot influence the server's response.
Exec and TUI both use app-server thread resume; their resume parameter overrides,
originators, and embedded/daemon choices differ. No headless-only missing-file
field was identified. Next useful reproduction should distinguish the original
environment and longer tool loop, not mutate the working history.

Original SHA256 before and after both tests:
`8c22c136999b2773406632b337eeb4d51cba883d9a8d22ad858ae5d36c5037c5`.
Both test processes exited. Disposable credentials, copied transcripts and
databases were removed after recording these conclusions. No runtime fix,
global setting, executable change or session repair was applied.

Public search found no exact `unsupported_persisted_item_context` match,
including GitHub issue search within openai/codex. Related
[encrypted-history issue25290](https://github.com/openai/codex/issues/25290)
uses a different error (`invalid_encrypted_content`); it is not evidence of
the same cause. Separately,
[busy-indicator issue10534](https://github.com/openai/codex/issues/10534)
describes appearing idle while still working. It closed after reported fixes
10700/10701; a later commenter reported recurrence on0.133.0. That does not
establish the cause of Marcos's current UI observation.

### Storage Wording And Cache Lifetime

The inspected client explicitly sets `store: false` in its Responses request.
Calling the failed state a "stored response" must not imply a durable archive.
OpenAI's [WebSocket documentation](https://developers.openai.com/api/docs/guides/websocket-mode)
describes connection-local in-memory continuation state compatible with
`store=false`: it disappears on disconnection, and public API connections last
up to60minutes. Cache entries can also be evicted earlier. This documents the
public API, not an independently verified lifetime for Codex's internal
`chatgpt.com/backend-api/codex` route, nor general service-log retention.
The purpose is faster tool loops; a failed continuation needs recovery, not
destructive local-history cleanup. The server error's word "persisted" alone
does not establish disk storage or a30-day response-retention period here.
