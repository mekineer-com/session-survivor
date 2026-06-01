# Codex Weekly Summary Export Plan

## Goal
Prepare Codex conversation history as ordered source files that Sonnet can read sequentially and summarize at natural stopping points.

## Why this shape
- JSONL is safe for runtime, but awkward for human summarization.
- Day-bounded files are natural boundaries (better than random char chunks).
- Separate files reduce context load for the summarizer model.
- We keep the source immutable and generate external export files first.

## Steps
1. Parse the session JSONL and extract only `user`/`assistant` messages with timestamps.
2. Preserve turn context by tracking `task_started` boundaries as turn IDs.
3. Split output into one markdown file per UTC day, in strict chronological order.
4. Write an index file with file order, counts, and date ranges.
5. Use these files as input for Sonnet-generated weekly summaries.

## Non-goals in this step
- No edits to live session files.
- No automatic summary insertion back into JSONL yet.
