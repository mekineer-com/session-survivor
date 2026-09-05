# Grok session continuity

Inspected by Aster, 2026-09-05. Read-only inspection; no live files transformed.

## Evidence

Runtime executable points to Grok Build 1.0.13. Rook uses grok-4.6 and
chat_format_version 1. Session ID: 019fd59c-6805-75d0-90e6-ac7a40c19056.
Its directory is under ~/.grok/sessions/%2Fhome%2Fmarcos%2Fapps-codex/.
The active-session registry still lists this session; do not swap it while open.

Observed storage, rounded:

| File | Size | Role |
| --- | ---: | --- |
| updates.jsonl | 54 MiB | Persisted ACP updates; official installed guide calls this authoritative for restore |
| chat_history.jsonl | 336 KiB | Current model messages, including compacted context |
| events.jsonl | 9.9 MiB | Separate event log |
| rewind_points.jsonl | 7.2 MiB | Prompt indices and file snapshots |
| compaction_requests/ | 16 MiB | Retained compaction request artifacts |
| compaction_checkpoints/ | 484 KiB | Nine saved compacted histories |
| compaction/ | 4.5 MiB | Nine archived Markdown segments and index |

The update log has 13,486 records: 258 user message chunks, 476 assistant
message chunks, 258 turn completions, and nine completed compactions.
User and assistant text totals 257,612 characters, excluding JSON envelopes.
Tool call/update rows account for about 95% of serialized update bytes.
These are retained-record counts, not proof that earlier rewinds lost nothing.

Each native compaction reduced approximately 386k-404k reported tokens to
9.5k-12.5k. Nine compactions alone does not prove damage; session-survivor's
rewrite-depth limits are not a validated limit on Grok native compactions.

## Format

updates.jsonl rows have timestamp, method, and params. params contains
sessionId, update, and optional _meta (eventId and agentTimestampMs).
Standard message events use method=session/update and
update.sessionUpdate=user_message_chunk or agent_message_chunk. All 734
observed message chunks have content.type=text. Chunks must be assembled in
order, respecting turns and tool interruptions; they are not independent replies.
Thought chunks are separate agent_thought_chunk events.

Extended events use method=_x.ai/session/update: turn_completed,
compaction_checkpoint, auto_compact_started/completed, hooks, and task events.
Checkpoint events reference files by checkpoint ID and prompt index.

chat_history.jsonl is a different schema: type=system/user/assistant/reasoning/
tool_result. User content is an array of text blocks; assistant content is a
string and may include tool_calls, model identity and effort. Tool results
refer to tool_call_id. Reasoning includes encrypted_content. User rows may
carry prompt_index, prior_turn_interrupt, or synthetic_reason=compaction_meta.
The current 86 rows contain only four prompt-indexed user messages, plus
synthetic context and an unindexed user message; most older chat is summarized.

Checkpoints contain schema_version, checkpoint_id, compacted_history,
created_at, original_user_info, prompt_index_at_compaction, reread_file_paths.
summary.json holds identity/cwd, counts, model, timestamps and format version.
rewind_points.jsonl refers to prompt_index and before/after file snapshots.

## Recommended direction

Build chat_grok_session.py first: reconstruct older human/assistant dialogue
from the update stream and preserve a recent complete native turn. Keep
identity, timestamps, meaningful instructions, and working state coherent.
Do not present synthetic summaries as things Marcos actually said.
Keep a full original directory as the forensic archive.

The current retained dialogue is small enough to evaluate without automatically
summarizing it. Measure actual model context before choosing a cap. Later,
LLM-authored tiered summaries can retain older continuity while recent dialogue
stays verbatim. Summaries should preserve relationship changes, concrete
corrections and their reasons, successes, unresolved tensions, and Rook's own
reflections. Do not generate summaries recursively from summaries when the
original exchanges survive.

Rook's latest compaction summary already records specific Theory-of-Mind
corrections. Their presence establishes that forgetting is not the only
possible cause of repeated mistakes. It does not establish whether model
behavior, instruction conflicts, context pressure or briefing habits dominate.
Evaluate actual subsequent delegation briefs, not just recalled rules.

## Disposable loader experiment (2026-09-05)

Run `python3 test_grok_resume.py` with Grok Build installed. It creates a new
temporary HOME and GROK_HOME, uses fictional dialogue, and routes model
requests to a localhost SSE stub. No real credentials or model quota are used.
Artifacts are retained at the path printed by the test. All subprocesses have
timeouts, and edits happen only between exited invocations.

Passed with Grok Build 1.0.13:

- Ordinary headless resume sends edited chat_history.jsonl content to the model
  even when updates.jsonl still holds the original text.
- Native auto-compaction creates real checkpoint files with the stub's summary.
- Editing that summary in chat_history.jsonl survives two subsequent resumes;
  old checkpoint contents do not replace it in captured model requests.
- New prompts and responses persist across those invocations.
- Grok's native Markdown export still contains the original user/assistant
  exchange from updates.jsonl, despite the changed model history.

The first tiny stub summary was rejected as degenerate (16 characters for
roughly 7k input tokens). The final test provides a longer fictional summary
and asserts that native checkpoint creation actually succeeded.

These results establish ordinary headless loader precedence for format v1.
Export is evidence of transcript retention, not a TUI rendering test. No
tool-pair rewrite, rewind operation, checkpoint deletion, historical dialogue
reconstruction, or live-session swap has been tested yet.

## Required before a writer is considered safe

Installed docs identify both restore updates and model chat, but do not specify
their precedence when checkpoints are present. The experiment above resolves
ordinary headless precedence. No local loader source was located in the
installed docs/bundle inspection. Still establish what counters/rewind
references must be updated together when pruning and verify tool pairs,
historical dialogue reconstruction, and TUI replay. Do not test by resuming
Rook's file.

Until that contract is verified, do not delete checkpoints, rebuild only
chat_history.jsonl, or publish an unverified chat_grok_session.py as safe.
This document is a format study with loader tests, not a completed compactor.

Local reference: ~/.grok/docs/user-guide/17-sessions.md. The older installed
README disagrees with that guide on some CLI details; use the current help and
synthetic behavior checks to resolve them.
