# Session Survivor

This is session-file tooling. Its technical README is user documentation, not
an internal work log; preserve its commands, safety limits and validation steps.

Before Codex maintenance, read [CODEX_SESSION_SURVIVOR_RUNBOOK.md](CODEX_SESSION_SURVIVOR_RUNBOOK.md).
For format changes, read the relevant vendor analysis: [Codex](CODEX_SESSION_ANALYSIS.md),
[Claude](CLAUDE_SESSION_ANALYSIS.md), or [Grok](GROK_SESSION_ANALYSIS.md).
The README's **Summary policy** governs authored summaries.

Root scripts are active; `previous-versions/` is archival/reference only.
`outputs/` contains generated candidates and reports, not source documentation.
Never transform or replace an open session. Preserve the original and validate
against the unchanged source before swapping; follow the runbook rather than
reconstructing the procedure. Extend the nearest existing preservation test.

## Test Old And New Native Formats

Our long-lived sessions are not compatibility coverage for new users. Before
claiming a maintenance profile supports a CLI, test both legacy sessions and
fresh native sessions from its newest supported format. Check model context
and visible conversation history separately; successful resume or valid JSON
does not prove both survived. Follow the required matrix in
[Codex Session Analysis](CODEX_SESSION_ANALYSIS.md#required-cross-format-validation),
and apply the same old/new-format principle to the other supported CLIs.
