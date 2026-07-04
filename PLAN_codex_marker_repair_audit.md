# PLAN: Codex Marker Repair Audit Gate

## Why

A previous Codex session repair restored too many old compact markers. The JSONL stayed valid, but Codex's UI showed a wall of repeated `Context compacted` entries. The bad file had hundreds of visible marker rows grouped together; normal/native files show compact markers as isolated rows.

## Correct Comparison Files

Use three references, each for a different question:

1. Clean compact base:
   `/home/marcos/.codex/sessions/2026/03/06/rollout-2026-03-06T05-01-36-019cc298-733e-7610-a564-58fdd8969d48.jsonl.bak-before-anchor-repair-20260703-234400`

   Purpose: byte-prefix identity for repaired live history.

2. Latest full pre-repair native-shape backup:
   `/home/marcos/.codex/sessions/2026/03/06/rollout-2026-03-06T05-01-36-019cc298-733e-7610-a564-58fdd8969d48.jsonl.bak-chat-codex-session-20260703-220950`

   Purpose: compact-marker timeline shape. Do not require byte equality; it still has heavy native blobs and `context_compacted` events.

3. Oldest backup:
   `/home/marcos/.codex/sessions/2026/03/06/rollout-2026-03-06T05-01-36-019cc298-733e-7610-a564-58fdd8969d48.jsonl.pre-safe-swap-260310`

   Purpose: sanity check only. It is from an older model/script era, so exact content comparison is unfair. It is useful to confirm marker walls were never normal.

## Pass Criteria

Before swapping a repaired file live:

- JSONL parses completely.
- Repaired file starts with the clean-base candidate byte-for-byte for the shared history.
- Any appended tail is strictly newer than the clean-base candidate's last timestamp.
- `compacted` marker set equals the native-shape backup's `compacted` marker set for the shared period.
- `context_compacted` rows are zero in the compacted live file unless there is a deliberate UI reason to keep them.
- `replacement_history` rows are zero after compaction/repair.
- Largest visible marker run is `1`; anything larger suggests a UI wall risk.
- Compare against the bad flood backup only to prove what was removed, not as a source of truth.

## Current Known Good Result

For Codex session `019cc298-733e-7610-a564-58fdd8969d48` after wall repair on 2026-07-04:

- live file starts with Candidate A byte-for-byte
- extra tail after Candidate A: `79` lines
- compacted markers: `70`
- context_compacted rows: `0`
- replacement_history rows: `0`
- non-empty compact summaries: `3`
- largest visible marker run: `1`
- removed visible marker rows from flood backup: `583`

## Notes

The one timestamp inversion reported at row 3 is inherited from the clean base/native backup and is a formatting artifact between `...112Z` and `...112000Z`, not introduced by the wall repair.
