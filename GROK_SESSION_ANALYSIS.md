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

Run `python3 probe_grok_resume.py` with Grok Build installed. It creates a new
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

These initial results established ordinary headless loader precedence for
format v1. Export is evidence of transcript retention, not a TUI rendering
test. The subsequent candidate tests below extend this evidence.

## Candidate writer and extended tests (2026-09-05)

`chat_grok_session.py` now produces a full original directory, candidate and
per-file hash manifest. It never swaps a source. It refuses a live registered
session, changing source, unsupported format, non-text older dialogue, missing
prompt indices, unfinished final turn or unpaired native-tail tool records.

Older user records are reused verbatim from current chat and chronologically
ordered `compaction_requests/*.json` native inputs. This preserves synthetic
reasons, interruption flags and injected framing; if any native user record is
missing, the writer refuses rather than guessing from display text. Unindexed
user reminders remain associated with the preceding prompt. Assistant text is
reconstructed from ACP chunks. Adjacent assistant
chunks merge, but tool boundaries separate replies. Prompt indices are retained;
unindexed duplicated original prompts and recognized native compaction summaries
are replaced by the original dialogue. System/environment and additional unknown
user instructions are retained. The latest complete native turn is unchanged.
Old display tool records lose raw input/output/content; thought text becomes
empty. Envelopes, IDs, timestamps, rewind points, checkpoints and auxiliary
files remain. summary.json message counts are updated. No checkpoint/rewind
backfill or index rewrite was needed for ordinary resume.

Team-audit corrections (2026-09-05): duplicate header queries are matched with
trailing-whitespace tolerance. Text IO is explicitly UTF-8. Backup, candidate
and manifest are staged privately and renamed together only after validation;
injected write failure leaves no published output and retry succeeds. Active
PID permission failures and malformed PIDs refuse clearly. PID reuse remains a
possible conservative false refusal: age alone is not evidence a session is
closed, so no timestamp-based bypass was added. The manual integration probe
is named `probe_grok_resume.py`; automated regressions use unittest discovery.
Regression data uses fictional text with the observed native schema, never
Marcos's actual conversation as test input. The local model probe explicitly
saves synthetic native inputs because that Grok configuration does not retain
compaction requests by default.

Extended checks pass on synthetic data:

- Chunk assembly, verbatim text, extra instructions, native tail preservation
  and a second identical transformation.
- Refusal of active sessions, incomplete/missing/media history and orphan tools.
- Candidate installation in the synthetic home followed by two actual Grok
  resumes: old dialogue and a native tool-call/result pair reach the localhost
  model server.
- ACP `session/load`, the UI replay protocol, emits the original old dialogue.
  `_x.ai/rewind/points` lists the correct old prompt indices and previews.

Rewind execution remains a measured limitation, not a pass. The actual method
is `_x.ai/rewind/execute` (the installed guide omits the underscore). With
targetPromptIndex and mode=conversation_only it returns success=false even on
an untouched synthetic session. It also fails after an ACP warmup turn. The
test records the candidate and uncompacted control outcomes; it only claims a
successful rewind if both the success flag and subsequent model context prove
it. No compensating state changes were added to the writer. TUI pixels/key
handling and Grok's remote backend have not been tested by this localhost test.

Rook has not been transformed or resumed for tests. Applying maintenance still
requires him to exit and a verified candidate; retaining all auxiliary files
deliberately favors continuity over maximum disk savings.

Rook's nine retained compaction requests were checked after the 2026-09-06
atomic-publication audit. Their indexed user ranges are contiguous and
non-overlapping from 0 through 253; live chat continues from 254 through 308.
This confirms prompt indices do not restart across his observed compactions.

Local reference: ~/.grok/docs/user-guide/17-sessions.md. The older installed
README disagrees with that guide on some CLI details; use the current help and
synthetic behavior checks to resolve them.
