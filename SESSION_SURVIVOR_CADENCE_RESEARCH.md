# When to Run Session Survivor

Research checked 2026-09-29.

## Short answer

No vendor or paper reviewed recommends retiring or rewriting a session after a
fixed number of native compactions. OpenAI, Anthropic, and xAI trigger or
recommend compaction based on context size, task boundaries, cost, and latency.

Compaction count still matters as a warning: prose summaries are lossy, and a
summary of a summary can silently lose constraints. Therefore:

- **Inspect at five native compactions.** This is a quick read-only usage and
  structure check, not an automatic rewrite.
- **Do not pass ten native compactions without an explicit maintenance
  decision.** Ten is our conservative shop checkpoint, not a researched failure
  boundary.
- **Run Session Survivor when measurements or symptoms justify it.** A high
  post-compaction prompt, bulky machine records, compaction thrashing, weak
  recovery, slow/broken resume, or continuity drift are stronger evidence than
  count or calendar age.
- **Use v3 summaries only when ordinary chat cleanup is insufficient.** Two
  weeks alone is not a reason to summarize dialogue again.

## What the vendors recommend

### OpenAI / Codex

OpenAI's current API guide supports a configured token threshold for automatic
server-side compaction and explicit compaction when a context window grows
large. The compacted output is the canonical next context and should not be
pruned. An older, now archived OpenAI prompting guide adds the useful task-level
advice to compact after major milestones or tool-heavy phases, not every turn.

Neither source specifies a maximum number of compactions or a cadence for
rewriting a Codex CLI session file.

For Session Survivor, the validated local timing remains:

1. Let Codex compact natively.
2. Exchange one ordinary turn.
3. Exit Codex.
4. Run `chat_codex_session.py` if inspection shows accumulated bulk or poor
   context recovery.

Sources:

- [OpenAI compaction guide](https://developers.openai.com/api/docs/guides/compaction)
- [OpenAI GPT-5.2 prompting guide (archived)](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-2_prompting_guide)
- [Local Codex session-memory analysis](CODEX_CLI_SESSION_MEMORY.md)

### Anthropic / Claude Code

Claude Code automatically compacts at a model/configuration-specific context
boundary. Current Claude Code also exposes `/autocompact`, `/context`, focused
`/compact` instructions, partial summarization through `/rewind`, and `/clear`
for unrelated tasks. Anthropic recommends managing context aggressively and
compacting with a focus before a long new task rather than treating all old
conversation as equally valuable.

Anthropic does not publish a maximum safe number of compact summaries. Use
`/context` and symptoms, not JSONL size or compact count alone. Session Survivor
is appropriate when repeated native summaries and old machinery make resume
heavy or continuity visibly worse; it is not a replacement for Claude's native
context management.

Sources:

- [Claude Code context window and compaction](https://code.claude.com/docs/en/context-window)
- [Claude Code model configuration and auto-compaction](https://code.claude.com/docs/en/model-config#context-window-and-auto-compaction)
- [Claude Code best practices](https://code.claude.com/docs/en/best-practices#manage-context-aggressively)

### xAI / Grok Build

xAI recommends compaction when all three conditions hold: repeated input tokens
are hurting cost or latency, prior turns still need to be remembered, and the
conversation still fits before compaction. It suggests either every N turns or
a workload-specific rendered-context threshold. xAI explicitly says
re-compacting later is supported, but gives no universal N and no maximum
compaction count.

The public Grok Build changelog shows continuing fixes to compaction quality,
background work, retained history, and short-summary rejection. It does not
publish a cadence rule. Our local Rook inspection found nine native compactions
that each reduced roughly 386k-404k reported tokens to 9.5k-12.5k; nine alone
was not evidence of damage.

For Grok Build, use `grok_usage_stats.py`. Its 40% prompt warning is our local
inspection heuristic, not xAI guidance. After v3 summaries, use
`chat_grok_session.py --current-chat-only` for ordinary machine-bulk cleanup so
archived dialogue does not replace the summaries again.

Sources:

- [xAI context compaction](https://docs.x.ai/developers/advanced-api-usage/context-compaction)
- [Grok Build changelog](https://x.ai/build/changelog)
- [Local Grok session analysis](GROK_SESSION_ANALYSIS.md)

## What research says

The literature supports both sides of the tradeoff:

- Long context is not automatically reliable. *Lost in the Middle* found that
  retrieval performance can fall sharply when relevant information sits in the
  middle of a long prompt. Less context can therefore improve effective recall.
- Recursive dialogue summaries can improve long-range coherence and
  consistency, but the authors report occasional factual errors. Summarization
  is useful, not lossless.
- A 2026 arXiv preprint tested 20 non-default project constraints across an
  88-turn synthetic conversation. Mean correct retention fell from 91% after
  one compaction to 62% after two and 46% after three. This is strong warning
  evidence against blind summary-on-summary chains, but it is not a calibrated
  limit for Codex, Claude Code, or Grok Build: their native compaction formats
  and retention mechanisms differ.

Sources:

- [Lost in the Middle: How Language Models Use Long Contexts](https://arxiv.org/abs/2307.03172)
- [Recursively Summarizing Enables Long-Term Dialogue Memory](https://arxiv.org/abs/2308.15022)
- [Facts as First-Class Objects: Knowledge Objects for Persistent LLM Memory (preprint)](https://arxiv.org/abs/2603.17781)

## Shop decision table

| Signal | Action |
| --- | --- |
| Fewer than five native compactions, healthy context recovery, no symptoms | Leave it alone |
| Five native compactions | Read-only inspection |
| Ten native compactions | Explicitly choose maintenance or document why it is unnecessary |
| Active prompt remains at least 40% full soon after native compaction | Ordinary `chat_*` maintenance is worthwhile after exit |
| Old tool/reasoning records dominate model-facing history | Ordinary `chat_*` maintenance |
| Resume hangs, compaction thrashes, or continuity visibly drifts | Inspect immediately; preserve a full backup before maintenance |
| Ordinary cleanup still leaves too much old dialogue | Consider reviewed, human-visible v3 summaries |
| Session is open | Never transform or swap it |

The 40%, five-compaction, and ten-compaction values are intentionally labeled
shop heuristics. Record future before/after prompt measurements so they can be
replaced with evidence rather than hardened into folklore.
