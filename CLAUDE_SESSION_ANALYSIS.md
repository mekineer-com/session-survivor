# Claude Code Session Format and Recovery

Verified through 2026-10-19 against Claude Code 2.1.280, the installed loader, and a
closed-session fork restoration. This is a format/recovery reference, not a
generic merger or a guarantee that later versions keep the same contract.

## Storage Is More Than One Transcript

| Location | Role |
| --- | --- |
| `~/.claude/projects/<escaped-project>/<session-id>.jsonl` | Native transcript and resume metadata |
| Same project, `<session-id>/subagents/` and `tool-results/` | Optional supporting artifacts; inspect references before moving a session |
| `~/.claude/file-history/<session-id>/` | File backups used by rewind snapshots |
| `~/.claude.json`, `projects[<absolute-project-path>].lastSessionId` | Project's last-session pointer |
| `~/.claude/jobs/<short-id>/state.json` and `tmp/` | Background-job state and artifacts |
| `~/.claude/daemon/roster.json` | Background worker registry |

No SQLite session database or project `sessions-index.json` was present in
this inspected installation. Do not apply Codex's SQLite/timestamp procedure
to Claude or assume an index exists. Verify the installed version's storage.
Anthropic documents the [background-state locations](https://code.claude.com/docs/en/agent-view#where-state-is-stored).

## Native Record Contract

- `uuid` identifies a record; `parentUuid` links ancestry. File order is not
  necessarily timestamp order. Do not sort a transcript by time.
- `sessionId` is the transcript identity, not the record UUID. Moving records
  into another session requires deliberate identity normalization, not a
  blanket replacement of every UUID string.
- Dialogue uses `type=user|assistant` and `message.role/content`. Preserve
  the message payload unchanged during full-fidelity recovery.
- Preserve assistant `message.model`. Missing models crashed an older
  2.1.195 resume path; existing compactors backfill from observed model metadata.
- Preserve compact-summary flags such as `isCompactSummary`; a continuation
  summary is not a new message authored by the user.
- Native `system/compact_boundary` may reset `parentUuid` to null while
  `logicalParentUuid` links earlier history. Follow that link during active-branch
  selection; compaction is not a rewind. Keep native preserved-message references
  resolvable in the retained tail.
- Keep complete thinking blocks in full-fidelity recovery. If the chosen
  compaction profile removes them, remove whole blocks, never blank signatures.
- `custom-title` stores `customTitle` and usually `sessionId`. Keeping dialogue
  while losing this record can break name-based resume.
- `agent-setting` stores `agentSetting` and `sessionId` independently of the
  dialogue ancestry. Keep these native rows unchanged and in physical order;
  custom titles do not preserve the selected agent definition.
- Attachments, system records, tool calls/results, snapshots and metadata
  may be ancestry dependencies. Text-only concatenation is not a native splice.

The existing [`validate_claude_records`](compact_claude_session.py) checks
duplicate UUIDs, parent resolution/cycles, meaningful dialogue and tool-result
ancestry. See [README's Claude profile](README.md#claude) for chat-only maintenance.

## Named Main Agents And Memory (2026-10-19)

Installed 2.1.280 accepts a main-session `--agent` whose definition has
`memory: user`. A fictional localhost Messages API probe confirmed separate
personal memory under `~/.claude/agent-memory/<agent-name>/MEMORY.md`, alongside
shared project `CLAUDE.md` and project auto memory. Native resume without
repeating `--agent` restored the selection. Merely renaming a conversation does
not create this named-agent configuration.

The chat cleaner previously dropped old `agent-setting` rows as non-dialogue.
Two native selection records became zero; resume lost the persona and its
personal memory injection. Preserve every selection at its original physical
position during final assembly, including settings outside the native safe
tail. Do not collapse selections, filter empty reset values, or duplicate rows
already present in the tail. The conservative `compact_claude_session.py`
already deep-copies these records unchanged and needs no implementation fix.

Regression checks cover settings before, inside and after the safe tail,
multiple selections and an empty selection, tail sizes 0/1/oversized, truthful
retention counts and three identical cleanups. After the fix, native resume of
the cleaned fictional transcript again includes the selected persona, personal
memory and shared project memory. Actual autonomous memory writes and explicit
native `/compact` were not tested; no real session was changed. In particular,
the October17 Opus original and candidate contained no `agent-setting` rows,
so that cleanup did not lose an agent selection he had never configured.

**Prompt tradeoff:** a named agent replaces stock coding instructions in this
version, even with an empty persona body when `memory: user` supplies memory
instructions. `--append-system-prompt` retains stock coding instructions and
shared auto memory, but does not automatically load a separate personal memory.
The appended text survived an ordinary native resume in the probe, with native
`attachment/prompt_snapshot` records present; this does not certify survival
through chat cleanup or justify retaining bulky prompt snapshots. A stable
launcher-supplied identity/memory instruction remains a configuration option to
evaluate, not an implemented migration. Do not copy a frozen stock prompt into
an agent definition as a substitute for the evolving native instructions.

The runnable probe and configuration findings are documented in
`~/gemini-cli/_research/claude-main-agent-memory.md`. All probe data is fictional,
with isolated homes and a localhost API; no real auth or model quota was used.
Official configuration reference:
[persistent agent memory](https://code.claude.com/docs/en/sub-agents#enable-persistent-memory).

## Imported History And Cleanup Loss

Installed 2.1.280 reader inspection confirmed that native resume selects a leaf
and prunes/relinks around the latest compact boundary. Restoring archival text
on disk does not automatically load it all into model context. Verify those two
outcomes separately; do not remove current compact state just to force old text in.

`compactMetadata.preservedMessages.uuids` is the replay list and must resolve,
along with anchors and segment endpoints. `allUuids` also carries auxiliary
records: the native replay lookup filters missing entries. Historical sources
can therefore legitimately retain absent advisory IDs; do not delete metadata
or reject a transcript solely because an `allUuids` entry is missing.

Earlier chat-only outputs can contain legitimate archival dialogue whose parent
chains are disconnected. A root or unreachable row is not, by itself, evidence
of an abandoned rewind. Following compact `logicalParentUuid` fixes native
boundaries but does not establish preservation of every imported archival root.
Normal chat cleanup now selects the continuation separately within each disconnected
conversation segment. It retains ordinary archived dialogue and earlier chat outputs
without connecting their roots to the current segment or flattening sibling rewinds.
Chat parent tracking and native-tail anchoring are segment-local; only the current
segment contributes native-tail turns. Interleaved archive rows are handled as old
chat consistently, with transformed rows merged back in original physical order.
Both absent and explicit-null parents represent roots. If advisory `allUuids`
is present, require a list of nonempty strings, without requiring node membership.

The branch filter introduced in `5367e53` followed only physical parents. It
could discard earlier conversation while retaining the latest compact summary,
so a successful resume could conceal substantial history loss. Compare retained
dialogue and summary records, not merely JSON validity or whether the agent replies.

An exact candidate-byte prefix in a later full backup proves that candidate was
used and identifies an append-only continuation cut point. This is stronger
than inferred dates or file sizes. For historical recovery, freeze all sources,
verify such prefix relationships, compare UUID/payload conflicts, and distinguish
archival roots from actual rewinds before selecting history. Keep recent native
compact references resolvable. Do not deduplicate by text or blindly concatenate
backups; changing envelopes or repeated UUIDs can require deliberate reconciliation.
Private incident details and recovery inventories stay outside public documentation.

Recovery verified on October 12 restored the archival transcript while retaining
the exact current native window and only current operational metadata. Historical
queue/bridge state was excluded; original UUIDs, parents, timestamps, dialogue and
summary flags stayed unchanged. Offline execution of extracted reader functions
confirmed identical resumed records; this does not certify full native/UI loading.
Create private recovery outputs as `0600` from the outset, not after writing.

Tool validation added in `5367e53` (September 14) indexed only `tool_use`.
Later ordering/ancestry checks retained that omission; tests covered client tools
but not server tools. A September 20 GLM record from Claude Code 2.1.233 contains
`server_tool_use` followed by a matching ancestor-linked `tool_result`, which the
validator falsely rejected. This is incomplete format coverage, not proof of lost
history or a newly introduced October format. Preserve the stored blocks/parents.
The validator now indexes `tool_use`, `server_tool_use` and `mcp_tool_use` without
relaxing ID/ancestry checks. Native wire acceptance of provider-specific
assistant-side results is a separate question.

October 13 hunk repairs retain post-leaf native supporting records, reject malformed
dialogue/tool/replay identity, protect path/ID strings during result-body trimming,
and refuse existing publication destinations so earlier originals are not overwritten.
The initial detached-archive refusal prevented loss but did not finish repeat cleanup.
The subsequent segment-local repair succeeds repeatedly with unchanged output in
fictional archive/summary/interleaving fixtures, including title and identity metadata.
Tool relationships across native preserved-message
replay still require effective-graph handling, not blanket logical-parent permission.

## Forks and Redirects

Backgrounding can create a new transcript identity rather than simply moving
the original process. The inspected original ended with a redirect:

```json
{"type":"continued-in","sessionId":"<original-id>","continuedInSessionId":"<fork-id>","timestamp":"..."}
```

The fork contained copied messages with shared UUIDs and identical payloads,
new native connector records, and subsequent dialogue. Two copied parent
links differed from their original envelopes: shared UUID does not imply the
entire row is byte-identical. Compare payloads and validate the selected chain.

For restoration to the original identity:

1. Freeze and back up both closed transcripts plus affected metadata/artifacts.
2. Identify new dialogue by UUID, then follow its parents to the shared original
   ancestry. Include required connectors; do not cut solely by dates or text.
3. Preserve original history and import the continuation without copied UUID
   duplicates. Repeated text with different UUIDs is not automatically duplicate.
4. Normalize imported top-level `sessionId`. Do not rewrite user/assistant
   text or historical event payload `session_id` strings indiscriminately.
5. For ordinary foreground restoration, clear imported `sessionKind:"bg"`
   transport classification, as native foreground branching does. This is not
   a rule for routine compaction or sessions intentionally remaining backgrounded.
6. Remove the original's redirect to the retired fork. Validate all original
   and new message payloads, ancestry, tool links, summaries and title.
7. Repair supporting state below, recheck source hashes/processes, then install
   the validated candidate atomically. Archive the fork outside project discovery.

This procedure was verified for one shared-ancestry continuation. Rewinds,
divergent branches or changed payloads require a new analysis, not blind reuse.

## Supporting State Matters

- Rewind snapshots/deltas contain `backupFileName` references, including
  names such as `<hash>@v2`. A transcript splice alone does not migrate those
  files into the destination's file-history directory. One missing backup was
  found during independent review; copy and verify required backups first.
- Compare same-named backups by content. Never overwrite different content
  under an existing backup name. Resolve collisions with audited reference
  changes or stop the recovery.
- Repair the project's `lastSessionId` when retiring its former continuation.
  Otherwise “continue latest” may target the archived file. First resume using
  the explicit restored UUID, not an ambiguous title.
- Archive only the retired, closed job's metadata. Do not disturb unrelated
  workers or broadly clear the daemon registry. Job state can contain private
  provider settings; keep recovery artifacts private.
- Transcript replacement, artifact copying and metadata moves are separate
  operations, not one atomic transaction. Preserve a rollback map for all of them.

## Closed Means No Process

An idle or `done` row can still have a live Claude process. In attached background
mode, `/exit` returns to agent view; leaving that view does not stop the worker.
Use the supported stop command and verify actual process exit before maintenance.
See [agent-view lifecycle](https://code.claude.com/docs/en/agent-view).

Observed 2.1.280 shutdown trap: `disableAgentView=true` also blocks `claude stop`.
A preceding `--settings` override did not reliably enable the command and was
interpreted as a new chat prompt. Temporarily enabling the saved setting allowed
the real stop command; restore the desired setting in a guaranteed cleanup step.
Do not equate a successful CLI exit code with having stopped the intended worker.

After recovery, verify the saved candidate again. Native validation is not a
live UI acceptance test: confirm resume shows the expected conversation without
sending an unnecessary extra message or using real history as synthetic test data.

## Transcript-Only Backup Restore (2026-10-02)

Tested installed Claude Code **2.1.280** with fictional native dialogue and a
`custom-title` row in isolated homes. See [`probe_backup_resume.py`](probe_backup_resume.py).

- Restored only the JSONL into its escaped-project directory, then removed the
  synthetic source home. Neither `.claude.json`, a last-session pointer, jobs,
  daemon state nor PID/session registry was restored.
- Project-directory escaping replaces non-ASCII-alphanumeric characters with
  `-`, including `/`, `_` and `.`. Replacing only slashes made the probe fail
  to find a valid transcript when its temporary workspace contained punctuation.
- `--bare --resume <UUID>` and `--bare --resume SyntheticRestore` both sent the
  earlier dialogue to the localhost model and completed a response under the
  original session UUID. The title inside JSONL was enough for name lookup.
- A separate fresh profile restored with the fictional transcript and OAuth
  credentials completed a real, non-bare one-turn resume. The final answer
  recalled the earlier marker; `session_id` stayed unchanged and
  `is_error=false`. The `sonnet` alias resolved to `claude-sonnet-5`.

**Backup implication:** no additional global resume pointer or worker registry
is required for this tested ordinary resume path. Keep the native transcript
and its title/model/ancestry metadata. Config/authentication are separate
machine-backup concerns. Supporting artifacts and file-history backups remain
in the checkbox app's existing scope: this text-only test does not establish
that they are dispensable for attachments or file rewind. Background-job
reattachment and full-machine recovery were not certified. No scope change.
