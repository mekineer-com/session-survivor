# Aster Codex Session Repair Instructions

Use this only if Marcos asks Codex to repair Aster's Codex session.

Target live session:

`/home/marcos/.codex/sessions/2026/03/20/rollout-2026-03-20T20-20-10-019d0dfa-72fe-73e3-b93c-b102fb4aaad7.jsonl`

## Current Need

Aster's live file is not as badly flooded as Codex's was, but it has been through marker repair attempts and still has native compact payloads:

- live file as checked on `2026-07-04`: about `5.8 MB`, `5662` rows
- `compacted`: `9`
- `context_compacted`: `10`
- `replacement_history`: `1` row, about `144 KB`
- largest visible marker run: `2`

Goal:

- preserve Aster's current tail
- return the older history to the last cleaner known base
- avoid marker mining
- avoid restoring a wall of `Context compacted`
- strip `payload.replacement_history` before swap

## Clean Base

Use this as the clean base:

`/home/marcos/.codex/sessions/2026/03/20/rollout-2026-03-20T20-20-10-019d0dfa-72fe-73e3-b93c-b102fb4aaad7.jsonl.bak-before-marker-repair-20260704-003750`

Why this base:

- it predates the marker-repair attempt
- it has isolated markers, not a marker wall
- it has `5043` rows
- it ends at `2026-07-04T05:35:37.912Z`
- marker shape at last check: `3` compacted rows, `3` context rows, largest marker run `1`

## Required Process

1. Confirm Aster is exited.

   Do not edit/swap the live file while Aster is running.

   Use:

   ```sh
   ps -eo pid,args | grep -F '019d0dfa-72fe-73e3-b93c-b102fb4aaad7' | grep -v grep || true
   ```

   If this prints an Aster/Codex resume process for this session ID, stop and ask Marcos to exit Aster first.

2. Build Candidate A from the clean base plus current live tail.

   Rule:

   - load clean base rows
   - find clean base last timestamp
   - append rows from current live where `timestamp > clean_base_last_timestamp`

   Do not mine markers from older backups. Do not merge all marker rows by timestamp. That method created the Codex marker wall.

3. Audit Candidate A.

   Expected shape from latest check:

   - starts byte-for-byte with the clean base
   - tail rows are strictly newer than `2026-07-04T05:35:37.912Z`
   - largest marker run remains `1`
   - no timestamp inversions

4. Strip `payload.replacement_history` from every `type == "compacted"` row in the final candidate.

   Keep:

   - the compacted row shell
   - `payload.message`, even when empty
   - normal chat/tool rows

   Remove only:

   - `payload.replacement_history`

5. Audit final candidate after stripping.

   Required pass criteria:

   - JSONL parses completely
   - row count equals Candidate A row count
   - `replacement_history` count is `0`
   - compact/context marker counts are unchanged from Candidate A
   - largest visible marker run is still `1`
   - final candidate differs from Candidate A only by removed `replacement_history` fields

6. Backup live before swap.

   Use a clear suffix such as:

   `.bak-before-aster-base-tail-repair-YYYYMMDD-HHMMSS`

7. Swap only after all checks pass.

## Comparison Guidance

Use these comparisons:

- Candidate A vs clean base: byte-prefix check.
- Final candidate vs Candidate A: normalized equality after removing `payload.replacement_history` from Candidate A.
- Live bad state vs final candidate: only to prove what changed; do not use live bad state as the source of truth.

Do not require equality with very old backups. They are from earlier model/script eras and are useful only as sanity checks that marker walls were never normal.

## Do Not Do This

- Do not restore all markers from all backups.
- Do not sort the whole file only by timestamp.
- Do not import extra `context_compacted` rows from older backups. Keep only rows already present in the clean base or current live tail.
- Do not keep `replacement_history` for context safety.
- Do not write a permanent repair script unless this becomes a repeated task.

One-off inline Python is acceptable, but print metrics before and after.
