# Claude Code Session Format and Recovery

Verified 2026-10-01 against Claude Code 2.1.280, the installed loader, and a
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
- Keep complete thinking blocks in full-fidelity recovery. If the chosen
  compaction profile removes them, remove whole blocks, never blank signatures.
- `custom-title` stores `customTitle` and usually `sessionId`. Keeping dialogue
  while losing this record can break name-based resume.
- Attachments, system records, tool calls/results, snapshots and metadata
  may be ancestry dependencies. Text-only concatenation is not a native splice.

The existing [`validate_claude_records`](compact_claude_session.py) checks
duplicate UUIDs, parent resolution/cycles, meaningful dialogue and tool-result
ancestry. See [README's Claude profile](README.md#claude) for chat-only maintenance.

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
