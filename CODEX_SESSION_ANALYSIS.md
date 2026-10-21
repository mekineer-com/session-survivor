# Codex Session Analysis

## Scope

This file explains what usually goes wrong in long Codex sessions and which protections are already implemented in `session-survivor`.
It is written for operators using agentic CLI day to day.

For the source-code-level loader contract, read `CODEX_CLI_SESSION_MEMORY.md`.
For model-specific JSONL differences, read `CODEX_MODEL_JSONL_COMPAT.md`.

## Required Cross-Format Validation

**Old sessions working does not establish support for new sessions.** Our
long-lived legacy threads concealed a compatibility gap: paginated sessions
need display records that legacy histories do not. Keep format coverage current
as the CLI evolves, rather than testing only the sessions we personally use.

Before claiming support for a CLI version or shipping a format-sensitive change:

- Test both legacy fixtures and fresh native fixtures from the newest supported
  CLI format. For Codex, explicitly include legacy and paginated histories.
  Record the actual CLI/source revision and history mode, not just a version label.
- Generate disposable fixtures with the native CLI using fictional dialogue;
  do not merely relabel old JSONL or handcraft the only new-format fixture.
  Retain fixtures for supported formats as regressions when upstream changes.
- Compare unmodified and cleaned controls for model input AND visible history,
  including dialogue before and after native compaction. Resume success, JSON
  parsing and text remaining on disk are not substitutes for either check.
- Cover a fresh session, a natively compacted session, repeated cleanup/resume,
  and restoration into an empty session store. Check interactive and headless
  paths where support is claimed. Prefer local model stubs when sufficient.
- If a format cannot pass these checks, label it unverified or unsupported and
  document the specific limit. Never silently substitute legacy-only results.

This is a testing requirement, not a claim that every vendor/profile already
passes. Native Codex chat maintenance is covered by `probe_codex_chat.py`;
weekly import and advanced semantic resume remain legacy-only.

## Paginated Chat Preservation Fix

**Delivered October20, 2026.** The code fix passed80 unit tests plus both native
format controls. Real Vesper was repaired while closed, with online SQLite and
full JSONL rollback copies in `outputs/vesper-paginated-repair-20261020-final/`.
The transaction removed only her derived index rows; other threads' counts
were unchanged. Native continuation rebuilt her index, and a separate live
read verified all244 original chat/compaction IDs, turn IDs and text hashes.
Later audit messages remain normally appended; no Aster/Codex/Ari repair was
needed. Native headless audits completed successfully without transport or
account changes; the earlier server replay error's exact trigger was not proved.

Vesper and fresh Opus auditors reviewed source and repair mappings, then checked
the resulting corrections. Their concrete findings were fixed and regression-
tested; final acceptance found no remaining blockers. Reviewers performed source
and some fictional in-memory checks, not an independent execution of the native
probe. Aster ran the unit/native tests and live index-content verification.
This verifies native display data, not manual pixel-level terminal acceptance.

The October20 correction keeps native UserMessage, AgentMessage and
ContextCompaction completion records with their original ordinals, IDs, turn
attribution and content. Unchanged paginated messages retain their entire native
envelope. Paginated text truncation is refused rather than publishing inconsistent
model/display content. Paginated histories preserve native abort/failure state and bypass
legacy synthetic replay/closure. Input and output ordinal validation reject
malformed native structure without manufacturing replacement positions.

Legacy replay remains in place. Native controls exposed one related correction:
preserve context_compacted markers and do not replay a compaction-only summary
as an assistant answer, including after older empty checkpoints are dropped on
repeated cleanup. Original user-input evidence prevents suppressing genuine
answers to nontext user turns.

All supported Codex candidate manifests identify history mode and whether a
display-index reset is required after swapping. The generators still only
produce candidates: operators must perform the target-only reset described in
the runbook while the target is closed. Other threads and the identity database
must not be reset. Native ordinal gaps after intentional bulk removal are
allowed and can log diagnostics; this is not a claim of warning-free imports.

`probe_codex_chat.py` generates genuine legacy and paginated fixtures using the
installed native CLI and a local model stub, including native compaction and
interruption/failure. It compares complete projected dialogue, active model
records, repeated output, fresh/cached imports, the stale-index failure without
a reset, and an unrelated thread through the native API. A headless continuation
also completes locally. No real session or external model is used by this probe.

Provenance: our generic dropping/rebuilding policy originated in May4 d16db6da
and May5 25c35e05; August27 f80e0a45 added legacy replay events. Native paginated
completion persistence began with July9 2342b2c2a6, projection/ordinals with
July11 5c19155cbd, SQLite materialization July14 0ef9fa4d65, and app-server
enablement July15 da61f7d8e1. August15 c4941302c7 enabled persistent exec usage;
September24 12cb14f7b7 made local-thread creation default to paginated. These
native commits are ancestors of the actual a1e4e8f45e executable source. Vesper's
saved original already declares paginated/exec; legacy Aster/Codex/Ari snapshots
do not. This was an old local assumption becoming incompatible with an upstream
format path, not the October9 checkpoint-metadata fix. Vesper's precise original
creation binary or migration history was not independently established.

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

### Normal-Settings And Pre-Failure History Controls

Follow-up controls copied the normal global configuration, plugin caches,
skills and empty hooks into disposable homes. Project-local settings still
applied. Model/effort remained gpt-6.1-sol/xhigh, web disabled and read-only;
mem-lite used a separate empty disposable data directory rather than shared
memories. These were closer controls, not an exact reconstruction of the old
process environment, account routing or audit prompt.

The full current history completed three sequential shell calls and its final
marker reply. A second copy containing only the first642 unchanged source rows
(2,108,824 bytes, ending before the first failed turn) completed the same task.
Connection logs explicitly confirmed WebSocket connections to the internal
Codex Responses endpoint in both runs. No replay error or fallback was observed.
The requests' individual continuation IDs were not captured, so connection
success alone must not be described as verified incremental request contents.
The original SHA256 above remained unchanged. Both processes finished; their
disposable homes, credentials and transcripts were removed.

These controls weaken a deterministic missing-history-field explanation but
do not reproduce the original audit workload or prove an intermittent backend
cause. No client transport change or real-history repair was made.

### Separate Confirmed Bug: Token Accounting Cleanup

Both follow-up runs logged18 skipped `token_count` events with missing required
`last_token_usage`. `compact_event_msg()` rebuilt `info` using only cumulative
usage and context window, omitting that native field. The installed CLI's
`TokenUsageInfo` requires it; decoding skips the whole event, including its
otherwise-retained totals. This is accounting metadata loss, not evidence of
lost conversation messages or the cause of the server replay error.

Provenance: introduced in April1 commit `3ea120f` (low-value token/context data
trimming); May1 `30d7939` moved the existing behavior into the type handler.
The fix deletes that eight-line object reconstruction and preserves `info`
verbatim. Stale `rate_limits` removal remains. No replacement values or history
rewrites are introduced; already malformed source records remain malformed.
The shared helper covers safe/resume records and chat's retained native tail.

Independent managed peer `01a15bbc-a275-7440-bfe7-0e90583f0394` confirmed the
schema/caller effects and reviewed the final code/test diff with no findings;
source-only review, not an independent test execution. The added regression
checks exact accounting preservation, future fields, unchanged input and
rate-limit removal both directly and through generated chat-tail output.
All71 existing/discovered unit tests passed with the fix. No real session was
transformed or swapped to apply it. Both controls succeeded despite the warnings,
so this fix must not be advertised as resolving `unsupported_persisted_item_context`.

### Accounting Repair Copy Experiment

Marcos requested actual log checks before proposing closures, plus copy-only
experiments for learning. Retained logs show Ari's ordinary interactive resumes
at09:10 and09:18 UTC October20 encountering the missing field. These four
entries are TRACE-level (`codex_rollout::recorder`), NOT visible WARN messages.
No matching retained ordinary-resume entries were attributed to Aster or Codex.
Do not infer user-visible warning noise merely from malformed records.

Read-only scans found one malformed `token_count.info` in each current Aster,
Codex and Ari file, with thousands of newer valid accounting records. All three
have exact recovery matches: Aster's October13 maintenance original, Ari's
October12 original, Codex's October17 original. Matching used timestamp/ordinal
and whole-record equality after applying only the known old info filter and
rate-limit removal. No inferred counters or dialogue matching was needed.
No real session was changed or closed for this check.

For an actual loader comparison, used Vesper's641-row October12 cleaned artifact
and its original backup, not a live rollout. On a copy only, restored18 exact
`info` objects; all other lines remained byte-identical, including checkpoints
and conversation text. Retained the intended removal of stale rate limits.
Each variant used a separate empty Codex home/database, no credentials, an
offline HTTP provider at127.0.0.1:9, disabled apps/plugins and a scratch workspace.
Only app-server initialize, thread/resume and thread/read were called: no
turn/start or model response. Both resumes succeeded; private DB rollout paths
were checked. Fresh-import missing-field WARN entries fell18->0.

That does NOT mean the repaired import was warning-free. Other ordering warnings
remained:358 missing-ordinal entries and132 ordinal-gap entries, versus358/150
before counter restoration. Both thread/read(includeTurns=true) responses
contained only1 userMessage and4 agentMessage, whereas the source has239 native
user/assistant message records. This is a separate fresh-import/history-display
lead, not evidence that dialogue bytes were deleted or model context lost.
Assess API/pagination semantics and the paginated-history importer before
proposing any ordinal rewrite or claiming a complete history repair.

Follow-up source review and static comparison confirmed a separate display
compatibility defect, not ordinary response pagination. Both original and
candidate declare `history_mode=paginated`. The original has4510 ordinal-bearing
rows and238 canonical chat `item_completed` events (63UserMessage/175AgentMessage).
The candidate has641 rows,283 with ordinals, and only5 canonical chat events
(1UserMessage/4AgentMessage). `compact_chat_records` drops older canonical
completion events and rebuilds messages/legacy replay events without ordinals.
The native paginated projector consumes canonical `item_completed` events,
ignores response items and legacy chat events, and its materializer skips rows
without ordinals. `thread/read(includeTurns=true)` exhausts turn/item cursors,
so the five-message result was not just the first page.

Managed peer `01a15bbc-a275-7440-bfe7-0e90583f0394` independently verified these
source paths, not the runtime experiment. Warning totals are not lost-message
counts; raw messages and UI items need not correspond one-to-one. No claim of
deleted dialogue bytes, model-memory loss or server-error causation follows.
Preserving only ordinals would not restore discarded completion events.
Investigate a minimal paginated-display preservation fix before broader repair;
do not bulk-renumber real histories or relabel their history mode speculatively.
Live counter repair is not required for current operation; exact originals are
available for a later closed-session repair if desired. Disposable experiment
data and all local probe processes were removed/stopped after recording results.

### Display Defect Scope Versus Model Input

The display defect above must not be generalized to all cleaned agents.
Streaming comparisons by managed peer `01a15bd2-41e8-7633-ba9a-973b6f9400d8`
found zero canonical chat completion events before AND after these cleanups:
Aster October13, Codex October17, Ari October12. Aster declares legacy history;
Codex and Ari omit history_mode, which deserializes to Legacy in this source.
Their current inspected rollouts likewise had no canonical chat completions;
their item_completed records were Extension events. These pairs do not
demonstrate the paginated chat-display loss found in Vesper. This is static
scope evidence, not a native UI test of each real running agent.

For Vesper, stable item IDs identify233 omitted canonical chat events:
62UserMessage and171AgentMessage. UTC event bounds are September10
19:08:58.803 through October12 23:39:50.827; oldest retained canonical chat is
October13 02:11:31.672 (October12 evening in Lima). These are bounds of actual
omissions, not proof of continuous conversation coverage throughout that span.
All five retained canonical chat IDs match the original. Message bytes remain
in response_item records; missing UI representation is not file deletion.

The installed source separates model context from paginated display projection.
Thread resume uses load_latest_model_context, whose reverse scan decodes raw
rollout items through the latest complete compaction boundary. Core replay
installs its replacement_history and replays newer response_item records.
It does not construct model input from the browser's canonical-item table.

Executed a local-only model-request capture to test this distinction. Two
isolated copies of Vesper's cleaned artifact differed ONLY by removal of all45
item_completed events. Their private DBs resolved the ID to their own copied
rollouts. An unauthenticated loopback Responses stub returned a fixed reply;
apps/plugins were disabled, proxies denied external access, and no actual model
service was called. Both produced132 input items, with the first127 exactly
equal. Differences in the five fresh bootstrap/input messages were only new
message IDs and the isolated Codex skill-root path. Normalizing ONLY those
differences made all132 items equal. Both had no top-level instructions field;
instructions were represented within the input. The initial raw-equality
assertion failed on this expected invocation noise, not on historical content.

This verifies that removing display completion events did not remove working
model input in this copied history. It does not certify every other cleanup
transformation: the profile separately prunes checkpoint user items and trims
tool state. Static comparison of Vesper's active checkpoint showed60->51
replacement items, removing nine ordinary user messages under the documented
50-message retention policy; its other checkpoint fields and encrypted
compaction item were unchanged. Do not attribute that independent policy to
the display bug, or claim that full raw lifetime chat was auto-loaded before it.

Provenance remains queued: identify the cleanup commit that discarded canonical
records/ordinals and the native commit/format transition that made them required
for paginated display, using the installed source rather than only its version
label. No claim yet that either our code or an upstream change alone introduced
the incompatibility. All temporary capture payloads/homes were removed after
recording conclusions; real agents and histories were left unchanged.

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
