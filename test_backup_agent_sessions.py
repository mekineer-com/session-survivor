import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
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
        with self.assertRaisesRegex(RuntimeError, 'Test agent, Other'):
            app.require_closed(entries, self.home, proc)
        (process / 'cmdline').write_bytes(b'codex\0')
        with self.assertRaisesRegex(RuntimeError, 'Test agent, Other'):
            app.require_closed(entries, self.home, proc)
        (process / 'cmdline').write_bytes(b'node\0/tool/codex.js\0resume\0' + SID.encode() + b'\0')
        self.assertIsNone(app.live_sessions(self.home, proc)[0][1])

    def test_claude_exe_current_registry_identity(self):
        proc = self.home / 'proc'
        process = proc / '123'
        process.mkdir(parents=True)
        (process / 'cmdline').write_bytes(b'claude.exe\0--resume\0' + OTHER.encode() + b'\0')
        (process / 'stat').write_text('123 (claude.exe) S ' + '0 ' * 18 + '99')
        registry = self.home / '.claude/sessions/123.json'
        registry.parent.mkdir(parents=True)
        registry.write_text(json.dumps({'procStart': '99', 'sessionId': SID}))
        self.assertEqual(app.live_sessions(self.home, proc), [('claude', SID, '123')])
        (process / 'cmdline').write_bytes(b'/tool/versions/2.1.280\0--resume\0' + OTHER.encode() + b'\0')
        self.assertEqual(app.live_sessions(self.home, proc), [('claude', SID, '123')])
        with self.assertRaisesRegex(RuntimeError, 'Test agent'):
            app.require_closed([self.entry], self.home, proc)

    def test_root_symlink_wrong_identity_and_missing_rewind(self):
        artifacts = self.source.with_suffix('')
        unrelated = self.home / 'unrelated'
        unrelated.mkdir()
        (unrelated / 'secret.txt').write_text('must not upload')
        artifacts.symlink_to(unrelated, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            app.session_files(self.entry, self.home)
        artifacts.unlink()
        self.source.write_text(json.dumps({'type': 'user', 'sessionId': OTHER}) + '\n')
        with self.assertRaisesRegex(ValueError, 'identity'):
            app.validate_session(self.entry, self.home)
        self.source.write_text(json.dumps({'type': 'user', 'sessionId': SID}) + '\n' + json.dumps(
            {'type': 'file-history-snapshot', 'snapshot': {'trackedFileBackups': {'source.txt': {'backupFileName': 'backup@v1'}}}}) + '\n')
        with self.assertRaisesRegex(ValueError, 'Missing Claude rewind'):
            app.validate_session(self.entry, self.home)

    def test_insufficient_disk_blocks_upload(self):
        with patch.object(app, 'live_sessions', return_value=[]), patch.object(app.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 1})()), patch.object(app, 'restic') as upload:
            with self.assertRaisesRegex(RuntimeError, 'Not enough free disk'):
                app.backup([self.entry], self.home, self.run)
            upload.assert_not_called()

    def test_changed_source_prevents_upload(self):
        copy = shutil.copy2
        def mutate(source, target):
            copy(source, target)
            source.write_text(source.read_text() + '{}\n')
        with patch.object(app, 'live_sessions', return_value=[]), patch.object(app.shutil, 'copy2', side_effect=mutate), patch.object(app, 'restic') as upload:
            with self.assertRaisesRegex(RuntimeError, 'changed while copying'):
                app.backup([self.entry], self.home, self.run)
            upload.assert_not_called()

    def test_native_imported_dialogue_preserved(self):
        imported = json.dumps({'type': 'user', 'sessionId': OTHER, 'message': {'content': 'Synthetic imported context'}}) + '\n'
        original = imported + self.source.read_text()
        self.source.write_text(original)
        with patch.object(app, 'live_sessions', return_value=[]):
            manifest = app.freeze([self.entry], self.home, self.run)
        self.assertEqual(Path(manifest[0]['frozen']).read_text(), original)
        self.assertEqual(self.source.read_text(), original)

    def test_invalid_history_error_names_agent(self):
        self.source.write_text('{invalid synthetic history\n')
        with patch.object(app, 'live_sessions', return_value=[]), patch.object(app, 'restic') as upload:
            with self.assertRaisesRegex(RuntimeError, 'Test agent:'):
                app.backup([self.entry], self.home, self.run)
            upload.assert_not_called()

    def test_grok_alias_and_unregistered_claude_executable(self):
        proc = self.home / 'proc'
        process = proc / '123'
        process.mkdir(parents=True)
        (process / 'cmdline').write_bytes(b'agent\0')
        (process / 'exe').symlink_to('/tool/downloads/grok-1.0.46-linux-x86_64')
        self.assertEqual(app.live_sessions(self.home, proc), [('grok', None, '123')])
        (process / 'exe').unlink()
        (process / 'cmdline').write_bytes(b'/tool/claude/versions/2.1.280\0')
        (process / 'exe').symlink_to('/tool/claude/versions/2.1.280')
        self.assertEqual(app.live_sessions(self.home, proc), [('claude', None, '123')])

    def test_validation_reads_frozen_bytes_and_detects_later_source_change(self):
        validate = app.validate_session
        def mutate_and_validate(entry, home, frozen_root):
            self.assertIsNotNone(frozen_root)
            self.source.write_text('{broken after copying\n')
            validate(entry, home, frozen_root)
        with patch.object(app, 'live_sessions', return_value=[]), patch.object(app, 'validate_session', side_effect=mutate_and_validate), patch.object(app, 'restic') as upload:
            with self.assertRaisesRegex(RuntimeError, 'changed while copying'):
                app.backup([self.entry], self.home, self.run)
            upload.assert_not_called()

    def test_real_process_refused_with_synthetic_session(self):
        process = subprocess.Popen(['codex', '-c', 'import time; time.sleep(30)'], executable=sys.executable)
        try:
            for attempt in range(30):
                if any(k == 'codex' and pid == str(process.pid) for k, sid, pid in app.live_sessions(self.home)):
                    break
                time.sleep(0.02)
            else:
                self.fail('Synthetic live process was not identified')
            with patch.object(app, 'restic') as upload:
                with self.assertRaisesRegex(RuntimeError, 'Exit these agents'):
                    app.backup([dict(self.entry, kind='codex')], self.home, self.run)
                upload.assert_not_called()
        finally:
            process.terminate()
            process.wait(timeout=5)

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
        with sqlite3.connect(database) as con:
            con.execute('UPDATE threads SET updated_at_ms=?', (1767225600000,))
        inherited = json.dumps({'type': 'session_meta', 'payload': {'id': OTHER}, 'timestamp': '2026-01-01T00:00:00Z'}) + '\n'
        source.write_text(source.read_text() + inherited)
        app.validate_session(entry, self.home)
        source.write_text(inherited)
        with self.assertRaisesRegex(ValueError, 'Codex identity'):
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
        (config / 'backup.env').write_text(f'export RESTIC_REPOSITORY="{repository}"\nexport RESTIC_PASSWORD_FILE="{password}"\n')
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
