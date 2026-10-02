# Checkbox Agent Backup

`backup_agent_sessions.py` backs up selected **closed** Codex, Claude Code, and
Grok Build sessions to an existing encrypted Restic repository. It does not
compact, scrub, replace, stop, or resume agents. Ordinary VPS backups remain a
separate manual operation. Previous remote snapshots are never removed.

## Use

1. Open **MEGA Agent Backup** from the desktop application menu.
2. Check the sessions you want. All catalog entries start checked; uncheck any
   you do not want. Close those agents before clicking **Back Up Selected**.
3. Wait for **Backup verified**. An open/uncertain session blocks the entire
   selection rather than silently skipping it. You can open the chooser before
   closing agents; status is rechecked when you click.

The application runs independently after Aster exits. A terminal shows the
copy/upload/restore phases. Closing the chooser or selecting Cancel does nothing.

Requires Python 3.11+, Restic, rclone, and YAD on Linux. Use the current user,
not root. The repository credentials remain in `~/.config/restic/mega.env`.
The script sources this existing trusted shell environment without printing it.

## Catalog

The private file `~/.config/restic/agent-sessions.json` identifies the named
sessions. It contains no account credentials. Example:

```json
[
  {
    "name": "Example agent",
    "kind": "claude",
    "session_id": "00000000-0000-4000-8000-000000000001",
    "path": "~/.claude/projects/example/00000000-0000-4000-8000-000000000001.jsonl"
  }
]
```

Codex paths identify the native rollout JSONL; Grok paths identify the native
session directory. If a named agent forks or moves, review and update this
catalog explicitly. The app never guesses which branch should replace it.

Read-only process status (does not read conversation content):

```sh
python3 backup_agent_sessions.py --check
```

## Safety And Recovery

- Checks current-user native processes, Codex wrappers, Claude's PID/session
  registry, and Grok's active-session registry. An unidentified live CLI blocks
  its entire harness. Idle or suspended still means running. PID inspection
  failures block, not assume closed.
- Codex's selected file must match its state database. A saved timestamp lag
  over ten minutes blocks, following the existing scrub-runbook gate. This is a
  persistence warning heuristic, not proof that every event was flushed.
- Claude redirects (`continued-in`) require catalog review. Claude's sibling
  session artifacts and session-specific rewind backups are included. Grok's
  full native session directory is included, not only readable chat.
- Validates JSONL, copies selected files into a private staging tree, rechecks
  processes, file membership and hashes. Reopening or writing during the copy
  can abort; keep selected agents closed until staging finishes. Process checks
  are conservative observations, not a lock on the external CLI.
- Uploads only frozen files plus their mapping/hash manifest, tagged
  `team-sessions`. Then restores every uploaded file and verifies its bytes.
- On success, removes only this run's temporary frozen/restore copies. Keeps
  manifest, logs, and `verified.json` under
  `~/.local/state/agent-session-backups/<run>/`. Failures retain diagnostic
  copies; nothing is automatically swapped into a live store.
- Each snapshot's `manifest.json` maps the frozen backup paths to the original
  native paths. Restore into a separate private directory, compare hashes, and
  only reinstall native files while the relevant agents are closed. This app
  deliberately has no automatic restore/replacement button.

Run the focused checks, including a real temporary encrypted local Restic
backup/restore with synthetic data:

```sh
python3 -m unittest -v test_backup_agent_sessions.py
```

No test reads, transforms, or uploads the user's conversation histories.
