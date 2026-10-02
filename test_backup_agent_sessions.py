import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import backup_agent_sessions as app

SID = '00000000-0000-4000-8000-000000000001'
OTHER = '00000000-0000-4000-8000-000000000002'


class SessionBackupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.source = self.home / '.claude/projects/test' / (SID + '.jsonl')
        self.source.parent.mkdir(parents=True)
        self.source.write_text(json.dumps({'type': 'user', 'sessionId': SID, 'message': {'content': 'Synthetic dialogue'}}) + '\n')
        self.entry = dict(name='Test agent', kind='claude', session_id=SID, path=self.source)
        self.run = self.home / 'run'
        self.run.mkdir()

    def test_running_refused_before_read_or_upload(self):
        self.source.write_text('not valid JSON')
        with patch.object(app, 'live_sessions', return_value=[('claude', SID, '123')]), patch.object(app, 'restic') as upload:
            with self.assertRaisesRegex(RuntimeError, 'Exit these agents'):
                app.backup([self.entry], self.home, self.run)
            upload.assert_not_called()
        self.assertFalse((self.run / 'tree').exists())

    def test_exact_and_unknown_process_identity(self):
        proc = self.home / 'proc'
        process = proc / '123'
        process.mkdir(parents=True)
        (process / 'cmdline').write_bytes(b'codex\0resume\0' + SID.encode() + b'\0')
        entries = [dict(self.entry, kind='codex'), dict(self.entry, kind='codex', name='Other', session_id=OTHER)]
        with self.assertRaisesRegex(RuntimeError, '^Exit these agents first: Test agent$'):
            app.require_closed(entries, self.home, proc)
        (process / 'cmdline').write_bytes(b'codex\0')
        with self.assertRaisesRegex(RuntimeError, 'Test agent, Other'):
            app.require_closed(entries, self.home, proc)
        (process / 'cmdline').write_bytes(b'node\0/tool/codex.js\0resume\0' + SID.encode() + b'\0')
        self.assertEqual(app.live_sessions(self.home, proc)[0][1], SID)

    def test_changed_source_prevents_upload(self):
        copy = shutil.copy2
        def mutate(source, target):
            copy(source, target)
            source.write_text(source.read_text() + '{}\n')
        with patch.object(app, 'live_sessions', return_value=[]), patch.object(app.shutil, 'copy2', side_effect=mutate), patch.object(app, 'restic') as upload:
            with self.assertRaisesRegex(RuntimeError, 'changed while copying'):
                app.backup([self.entry], self.home, self.run)
            upload.assert_not_called()

    def test_codex_stale_history_and_claude_redirect(self):
        source = self.home / '.codex/sessions' / ('rollout-' + SID + '.jsonl')
        source.parent.mkdir(parents=True)
        source.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': SID}, 'timestamp': '2026-01-01T00:00:00Z'}) + '\n')
        entry = dict(self.entry, kind='codex', path=source)
        database = self.home / '.codex/state_5.sqlite'
        with sqlite3.connect(database) as con:
            con.execute('CREATE TABLE threads (id TEXT, rollout_path TEXT, updated_at_ms INTEGER)')
            con.execute('INSERT INTO threads VALUES (?, ?, ?)', (SID, str(source), 1767226301000))
        with self.assertRaisesRegex(ValueError, 'lags the database'):
            app.validate_session(entry, self.home)
        self.source.write_text('{"type":"continued-in"}\n')
        with self.assertRaisesRegex(ValueError, 'redirects'):
            app.validate_session(self.entry, self.home)

    def test_real_local_restic_restore_selected_files(self):
        config = self.home / '.config/restic'
        config.mkdir(parents=True)
        password = self.home / 'password'
        password.write_text('synthetic-test-password-only')
        repository = self.home / 'repository'
        env = dict(os.environ, RESTIC_REPOSITORY=str(repository), RESTIC_PASSWORD_FILE=str(password))
        subprocess.run(['restic', 'init'], env=env, check=True, capture_output=True)
        (config / 'mega.env').write_text(f'export RESTIC_REPOSITORY="{repository}"\nexport RESTIC_PASSWORD_FILE="{password}"\n')
        supporting = self.home / '.claude/file-history' / SID / 'backup@v1'
        supporting.parent.mkdir(parents=True)
        supporting.write_bytes(b'original rewind content')
        unselected = self.source.parent / (OTHER + '.jsonl')
        unselected.write_text('do not back up this peer')
        before = self.source.read_bytes()
        with patch.object(app, 'live_sessions', return_value=[]):
            snapshot = app.backup([self.entry], self.home, self.run)
        verified = json.loads((self.run / 'verified.json').read_text())
        self.assertEqual(verified['snapshot'], snapshot)
        manifest = json.loads((self.run / 'manifest.json').read_text())
        self.assertEqual({x['source'] for x in manifest}, {str(self.source), str(supporting)})
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.run / 'tree').exists())
        self.assertFalse((self.run / 'restore').exists())
        self.assertEqual(unselected.read_text(), 'do not back up this peer')


if __name__ == '__main__':
    unittest.main()
