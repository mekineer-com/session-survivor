# Aster Codex Session Marker Repair Instructions

Use this only if Marcos asks to repair Aster's Codex session.

Target session:

`/home/marcos/.codex/sessions/2026/03/20/rollout-2026-03-20T20-20-10-019d0dfa-72fe-73e3-b93c-b102fb4aaad7.jsonl`

## Goal

- preserve current live chat
- recover missing Codex structure markers from backups if available
- strip `payload.replacement_history` from recovered/current compacted rows
- do not synthesize summaries
- do not delete current rows

## Plain English

Codex JSONL has normal chat/tool rows plus structure markers.

The two marker types we care about are:

- `type == "compacted"`: a compacted-row shell. Keep the row, keep `payload.message`, remove only the bulky `payload.replacement_history`.
- `payload.type == "context_compacted"`: a small event row that says compaction happened around that time.

These markers are timeline/format structure. Do not flatten them away.

## Current Estimate

Available Aster files show no actual session activity from `2026-05-05` through `2026-05-21`.

Activity starts on `2026-05-22`, and the first recoverable marker is:

- `2026-05-22T09:08:24.565Z` (`context_compacted`)

So the estimated missing markers before `2026-05-22` are: `0`.

Known recoverable markers missing from live as of the latest check:

- `11` marker rows total
- `5` `compacted` rows
- `6` `context_compacted` rows
- across `6` compaction moments/dates

This is limited repair, not a full March-July reconstruction.

Verified again on 2026-07-04 from the available Aster files:

- files checked: live JSONL, `.bak-before-restore-full-history-20260703-220113`, and session-survivor July 1/July 3 original/compacted copies
- recoverable backup/output marker rows: `15`
- current live marker rows: `6`
- missing recoverable marker rows: `11`
- missing recoverable marker rows before `2026-05-22`: `0`

If a future scan reports many pre-`2026-05-22` markers, verify the file list. That likely means the scan matched the wrong session, stale scratch output, or non-current artifacts.

## Rules

1. Make sure Aster is exited before swapping the live JSONL.
   - Do not edit/swap the live file while Aster is running.
   - Do not use `pgrep -af "$sid"` as a hard safety check from inside an inline command; it can match its own command line and falsely abort.
   - Use this check instead:
     ```sh
     ps -eo pid,args | grep -F '019d0dfa-72fe-73e3-b93c-b102fb4aaad7' | grep -v grep || true
     ```
   - If that prints an Aster/Codex resume process for this session ID, stop and ask Marcos to exit Aster first.
   - If it prints nothing, the repair can proceed.
2. Inventory available files matching `*019d0dfa-72fe-73e3-b93c-b102fb4aaad7*.jsonl*`.
3. Count current live:
   - total rows
   - `compacted` rows
   - `context_compacted` rows
   - compacted rows with `replacement_history`
4. Mine only same-session backup/output files for missing marker rows where:
   - `type == "compacted"`, or
   - `payload.type == "context_compacted"`.
5. Deduplicate by stable key:
   - top-level `type`
   - top-level `timestamp`
   - `payload.type`
   - `payload.turn_id`
6. For every compacted row, remove only `payload.replacement_history`.
7. Preserve `payload.message`, even when empty.
8. Merge recovered rows into the current live rows by timestamp order.
9. Validate JSONL before swap.
10. Backup live before swap with a clear suffix.

Do not add this as permanent script code unless the repair is needed repeatedly. One-off inline Python is acceptable.

## Known File Inventory As Of 2026-07-04

- live file: about `5 MB`
- `.codex/sessions` backup: `.bak-before-restore-full-history-20260703-220113`, about `45 KB`
- session-survivor output originals:
  - July 1 original copy: about `5.6 MB`, has `4` compacted rows and `5` context markers
  - July 3 original copy: about `3.5 MB`, has `2` compacted rows and `2` context markers

If inventory changes, trust the fresh inventory over these numbers.
