#!/usr/bin/env python3
"""Checkbox backup of closed native sessions to an existing encrypted Restic repo."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid


def load_catalog(path, home):
    entries = json.loads(path.read_text())
    names, ids = set(), set()
    for entry in entries:
        sid = str(uuid.UUID(entry['session_id']))
        kind, name = entry['kind'], entry['name']
        if kind not in ('codex', 'claude', 'grok') or name in names or sid in ids:
            raise ValueError('Invalid or duplicate catalog entry')
        source = Path(str(entry['path']).replace('~/', str(home) + '/', 1))
        expected = home / ('.' + kind) / ('projects' if kind == 'claude' else 'sessions')
        if not source.resolve().is_relative_to(expected.resolve()) or sid not in source.name:
            raise ValueError('Session path does not match its harness and identity')
        entry['path'], entry['session_id'] = source, sid
        names.add(name)
        ids.add(sid)
    return entries


def live_sessions(home, proc=Path('/proc')):
    """Unknown native CLI identities block that harness rather than guess closed."""
    result = []
    for process in proc.iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            args = (process / 'cmdline').read_bytes().split(b'\0')
            args = [arg.decode(errors='replace') for arg in args if arg]
            if not args:
                continue
            program = Path(args[0]).name.lower().removesuffix('.exe')
            kind = next((k for k in ('codex', 'claude', 'grok')
                         if program == k or (k == 'grok' and re.fullmatch(r'grok-\d.*', program))), None)
            if program in ('node', 'nodejs') and len(args) > 1 and Path(args[1]).name == 'codex.js':
                kind = 'codex'
            if program in ('sh', 'bash') and len(args) > 1 and Path(args[1]).name == 'codex-multi-auth-codex':
                kind = 'codex'
            # Launch arguments can outlive an in-process session switch.
            sid = None
            registry = home / '.claude/sessions' / (process.name + '.json')
            if registry.exists():
                record = json.loads(registry.read_text())
                start = (process / 'stat').read_text().rsplit(')', 1)[1].split()[19]
                if str(record.get('procStart')) == start:
                    kind = 'claude'
                    try:
                        sid = str(uuid.UUID(record.get('sessionId', '')))
                    except (ValueError, TypeError, AttributeError):
                        sid = None
            if not kind:
                continue
            result.append((kind, sid, process.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as error:
            raise RuntimeError('Cannot inspect a process; cannot prove sessions are closed') from error
    registry = home / '.grok/active_sessions.json'
    if registry.exists():
        for record in json.loads(registry.read_text()):
            pid = record['pid']
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                raise ValueError('Invalid Grok process record; cannot prove it is closed')
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            try:
                sid = str(uuid.UUID(record.get('session_id', '')))
            except (ValueError, TypeError, AttributeError):
                sid = None
            result.append(('grok', sid, str(record['pid'])))
    return result


def require_closed(entries, home, proc=Path('/proc')):
    running = live_sessions(home, proc)
    blocked = [e['name'] for e in entries if any(
        kind == e['kind'] and (sid is None or sid == e['session_id'])
        for kind, sid, pid in running)]
    if blocked:
        raise RuntimeError('Exit these agents first: ' + ', '.join(blocked))


def session_files(entry, home):
    source = entry['path']
    roots = [source]
    if entry['kind'] == 'claude':
        roots += [source.with_suffix(''), home / '.claude/file-history' / entry['session_id']]
    files = []
    for root in roots:
        if root.is_symlink():
            raise ValueError('Session contains a symlink; review before backing up')
        if not root.exists():
            if root == source:
                raise FileNotFoundError('Missing session: ' + entry['name'])
            continue
        for path in ([root] if root.is_file() else sorted(root.rglob('*'))):
            if path.is_symlink():
                raise ValueError('Session contains a symlink; review before backing up')
            if path.is_file():
                files.append(path)
    return files


def rewind_files(value):
    names = set()
    if isinstance(value, dict):
        if value.get('backupFileName') is not None:
            name = value['backupFileName']
            if not isinstance(name, str) or Path(name).name != name or name in ('.', '..'):
                raise ValueError('Invalid Claude rewind backup reference')
            names.add(name)
        for child in value.values():
            names.update(rewind_files(child))
    elif isinstance(value, list):
        for child in value:
            names.update(rewind_files(child))
    return names


def validate_session(entry, home):
    source, sid = entry['path'], entry['session_id']
    if entry['kind'] == 'grok':
        if json.loads((source / 'summary.json').read_text())['info']['id'] != sid:
            raise ValueError('Grok identity mismatch')
        for name in ('chat_history.jsonl', 'updates.jsonl'):
            if not (source / name).is_file():
                raise ValueError('Missing Grok native history: ' + name)
    last_timestamp, identity_found = None, False
    dialogue_found, rewinds = False, set()
    for path in session_files(entry, home):
        if path.suffix != '.jsonl':
            continue
        with path.open() as handle:
            for line in handle:
                obj = json.loads(line)
                if path == source and isinstance(obj, dict):
                    if obj.get('type') == 'continued-in':
                        raise ValueError(entry['name'] + ' redirects to another session; review the catalog')
                    if obj.get('type') == 'session_meta':
                        identity_found = obj['payload']['id'] == sid
                    if entry['kind'] == 'claude':
                        if obj.get('type') in ('user', 'assistant'):
                            dialogue_found = True
                            if obj.get('sessionId') not in (None, sid):
                                raise ValueError('Claude conversation identity mismatch')
                            identity_found |= obj.get('sessionId') == sid
                        if str(obj.get('type', '')).startswith('file-history'):
                            rewinds.update(rewind_files(obj))
                    if obj.get('timestamp'):
                        last_timestamp = obj['timestamp']
    if entry['kind'] == 'claude':
        if not identity_found or not dialogue_found:
            raise ValueError('Claude conversation identity or dialogue missing')
        for name in rewinds:
            path = home / '.claude/file-history' / sid / name
            if not path.is_file() or path.is_symlink():
                raise ValueError('Missing Claude rewind backup: ' + name)
    if entry['kind'] == 'codex':
        if not identity_found or not last_timestamp:
            raise ValueError('Codex identity or timestamp missing')
        databases = list((home / '.codex').glob('state_*.sqlite'))
        if not databases:
            raise ValueError('Codex state database missing; cannot verify saved history')
        database = max(databases, key=lambda p: int(p.stem.split('_')[-1]))
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
            row = connection.execute('SELECT rollout_path, updated_at_ms FROM threads WHERE id=?', (sid,)).fetchone()
        if not row or Path(row[0]) != source:
            raise ValueError('Codex database does not point to this session file')
        tail = datetime.fromisoformat(last_timestamp.replace('Z', '+00:00')).timestamp()
        if row[1] / 1000 - tail > 600:
            raise ValueError(entry['name'] + ' saved history lags the database by over ten minutes')


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def freeze(entries, home, run):
    require_closed(entries, home)
    for entry in entries:
        validate_session(entry, home)
    sources = [(entry, source) for entry in entries for source in session_files(entry, home)]
    required = 2 * sum(source.stat().st_size for entry, source in sources) + 64 * 1024**2
    if shutil.disk_usage(run).free < required:
        raise RuntimeError('Not enough free disk for temporary copies and restore verification')
    manifest = []
    for entry, source in sources:
        before = digest(source)
        target = run / 'tree' / source.relative_to('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if digest(target) != before or digest(source) != before:
            raise RuntimeError('Session changed while copying; no upload: ' + entry['name'])
        manifest.append({'agent': entry['name'], 'source': str(source),
                         'frozen': str(target), 'sha256': before})
    require_closed(entries, home)
    current = [source for entry in entries for source in session_files(entry, home)]
    if [str(path) for path in current] != [item['source'] for item in manifest]:
        raise RuntimeError('Session file set changed while copying; no upload')
    if any(digest(Path(item['source'])) != item['sha256'] for item in manifest):
        raise RuntimeError('Session changed while copying; no upload')
    (run / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def restic(args, env_file, log):
    command = ['sh', '-c', 'set -e; . "$1"; shift; exec restic "$@"', 'restic', str(env_file), *args]
    with log.open('w') as handle:
        subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, check=True)


def backup(entries, home, run):
    print('Checking and copying selected closed sessions...', flush=True)
    manifest = freeze(entries, home, run)
    file_list = run / 'files.txt'
    file_list.write_text('\n'.join(item['frozen'] for item in manifest) + '\n' + str(run / 'manifest.json') + '\n')
    env_file = home / '.config/restic/mega.env'
    print('Uploading selected closed sessions...', flush=True)
    restic(['backup', '--files-from-verbatim', str(file_list), '--tag', 'team-sessions', '--json'], env_file, run / 'backup.log')
    summaries = [json.loads(line) for line in (run / 'backup.log').read_text().splitlines() if line.startswith('{')]
    snapshot = next(row['snapshot_id'] for row in summaries if row.get('message_type') == 'summary')
    print('Restoring and verifying every selected file...', flush=True)
    restored = run / 'restore'
    restic(['restore', snapshot, '--target', str(restored), '--verify', '--json'], env_file, run / 'restore.log')
    for item in manifest:
        if digest(restored / item['frozen'].lstrip('/')) != item['sha256']:
            raise RuntimeError('Restored file mismatch; keep logs and investigate')
    (run / 'verified.json').write_text(json.dumps({'snapshot': snapshot, 'agents': [e['name'] for e in entries],
                                                 'files': len(manifest)}, indent=2) + '\n')
    shutil.rmtree(run / 'tree')
    shutil.rmtree(restored)
    return snapshot


def dialog(*args):
    return subprocess.run(['yad', '--title=MEGA Agent Backup', '--no-markup', *args], capture_output=True, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path.home() / '.config/restic/agent-sessions.json')
    parser.add_argument('--check', action='store_true', help='List process status only; do not read/copy session histories')
    args = parser.parse_args()
    home = Path.home()
    entries = load_catalog(args.catalog, home)
    running = live_sessions(home)
    rows = []
    for index, entry in enumerate(entries):
        state = 'Close ' + entry['kind'] + ' agents first' if any(k == entry['kind'] and (sid is None or sid == entry['session_id'])
                                             for k, sid, pid in running) else 'No running process detected'
        if args.check:
            print(entry['name'] + ': ' + state)
        rows += ['TRUE', entry['name'], entry['kind'], state, str(index)]
    if args.check:
        return
    selected = dialog('--list', '--checklist', '--width=780', '--height=450', '--column=Back up:CHK',
                      '--column=Agent', '--column=CLI', '--column=Status when opened', '--column=Index', '--hide-column=5',
                      '--print-column=5', '--separator=\n', '--button=Cancel:1', '--button=Back Up Selected:0',
                      '--text=Select sessions to back up. Exit selected agents first, including Aster.\nStatus is rechecked on click. No compaction or shutdown. Older backups are kept.', *rows)
    if selected.returncode != 0 or not selected.stdout.strip():
        return
    chosen = [entries[int(index)] for index in selected.stdout.split()]
    require_closed(chosen, home)
    base = home / '.local/state/agent-session-backups'
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (base / '.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run = Path(tempfile.mkdtemp(prefix=datetime.now().strftime('%Y%m%d-%H%M%S-'), dir=base))
        try:
            snapshot = backup(chosen, home, run)
        except Exception as error:
            raise RuntimeError(str(error) + '\nDetails: ' + str(run)) from error
        dialog('--info', '--text=Backup verified: ' + snapshot[:8] + '\n' + ', '.join(e['name'] for e in chosen)
               + '\nEvery selected file restored and matched. Logs: ' + str(run))


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except Exception as error:
        dialog('--error', '--text=' + str(error))
        raise SystemExit(1)
