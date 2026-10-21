#!/usr/bin/env python3
"""Opt-in native legacy/paginated chat roundtrip against a loopback model stub."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading

REPO = Path(__file__).resolve().parent
CODEX = shutil.which('codex')
assert CODEX, 'Codex must be installed'
scratch = tempfile.TemporaryDirectory(prefix='codex-chat-native-')
ROOT = Path(scratch.name)
(ROOT / 'work').mkdir()
requests = []
abort_seen = threading.Event()
abort_release = threading.Event()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"data":[]}')

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append(request)
        users = [item for item in request.get('input', []) if item.get('role') == 'user']
        last_user = ''.join(part.get('text', '') for part in users[-1].get('content', [])) if users else ''
        if last_user == 'PROBE_ABORT':
            abort_seen.set()
            abort_release.wait(timeout=15)
        if last_user == 'PROBE_FAIL':
            data = b'{"error":{"type":"invalid_request_error","message":"Fictional failed-turn control"}}'
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        number = len(requests)
        text = 'LOCAL_REPLY: PROBE_ONE PROBE_TWO PROBE_THREE'
        item = {'id': f'msg_local_{number}', 'type': 'message', 'role': 'assistant',
                'status': 'completed', 'phase': 'final_answer',
                'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}
        events = [
            {'type': 'response.created', 'response': {'id': f'resp_{number}', 'status': 'in_progress', 'output': []}},
            {'type': 'response.output_item.added', 'output_index': 0, 'item': dict(item, status='in_progress', content=[])},
            {'type': 'response.output_text.delta', 'output_index': 0, 'content_index': 0, 'item_id': item['id'], 'delta': text},
            {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
            {'type': 'response.completed', 'response': {'id': f'resp_{number}', 'status': 'completed', 'output': [item],
                'usage': {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120}}},
        ]
        data = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass  # The interrupted-turn control deliberately closes its response.


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()


def setup(label):
    base = ROOT / label
    base.mkdir()
    home = base / 'home'
    home.mkdir()
    store = base / 'codex'
    store.mkdir()
    (store / 'config.toml').write_text(f'''model = "gpt-6.1-sol"
model_provider = "local-probe"
model_auto_compact_token_limit = 1000000
web_search = "disabled"
[features]
apps = false
plugins = false
[model_providers.local-probe]
name = "Local probe"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
[analytics]
enabled = false
''')
    return store


def environment(store):
    return {'PATH': os.environ['PATH'], 'HOME': str(store.parent / 'home'),
            'CODEX_HOME': str(store), 'LANG': 'C.UTF-8', 'RUST_LOG': 'warn',
            'HTTP_PROXY': 'http://127.0.0.1:9', 'HTTPS_PROXY': 'http://127.0.0.1:9',
            'ALL_PROXY': 'http://127.0.0.1:9', 'NO_PROXY': '127.0.0.1,localhost'}


class Client:
    def __init__(self, store):
        env = environment(store)
        self.log = (store.parent / 'stderr').open('a')
        self.proc = subprocess.Popen([CODEX, 'app-server', '--stdio'], env=env,
            cwd=ROOT / 'work', text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)
        self.queue = queue.Queue()
        self.pending = []
        self.number = 0
        def reader():
            for line in self.proc.stdout:
                self.queue.put(json.loads(line))
        threading.Thread(target=reader, daemon=True).start()
        self.rpc('initialize', {'clientInfo': {'name': 'native_chat_probe', 'version': '1'},
                                'capabilities': {'experimentalApi': True}})
        self.send({'method': 'initialized'})

    def send(self, value):
        self.proc.stdin.write(json.dumps(value) + '\n')
        self.proc.stdin.flush()

    def rpc(self, method, params):
        self.number += 1
        self.send({'id': self.number, 'method': method, 'params': params})
        while True:
            value = self.queue.get(timeout=30)
            if value.get('id') == self.number:
                assert 'error' not in value, value
                return value['result']
            self.pending.append(value)

    def wait(self, method):
        while True:
            for i, value in enumerate(self.pending):
                if value.get('method') == method:
                    return self.pending.pop(i)
            value = self.queue.get(timeout=30)
            self.pending.append(value)

    def close(self):
        self.proc.terminate()
        self.proc.wait(timeout=15)
        self.log.close()


def profile(source, label):
    output = ROOT / label
    result = subprocess.run([sys.executable, str(REPO / 'chat_codex_session.py'), str(source),
                             '--output-root', str(output), '--show-summary'],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    manifest = json.loads(next((output / 'manifests').rglob('*.manifest.json')).read_text())
    report = json.loads(Path(manifest['artifacts']['report']).read_text())
    assert not report['warnings'] and not report['changes']['messages_truncated']
    return Path(manifest['artifacts']['compacted_copy'])


def chat(view, ids=False):
    result = []
    for turn in view['thread']['turns']:
        for item in turn['items']:
            if item['type'] in ('userMessage', 'agentMessage', 'contextCompaction'):
                result.append((item['type'], item.get('text', item.get('content')), item.get('id') if ids else None))
    return result


def read_view(store, sid):
    client = Client(store)
    try:
        client.rpc('thread/resume', {'threadId': sid, 'model': 'gpt-6.1-sol',
            'modelProvider': 'local-probe', 'cwd': str(ROOT / 'work'),
            'approvalPolicy': 'never', 'sandbox': 'read-only', 'excludeTurns': True})
        return client.rpc('thread/read', {'threadId': sid, 'includeTurns': True})
    finally:
        client.close()


def reset_display_index(store, sid):
    database = store / 'thread_history_1.sqlite'
    if not database.exists():
        return
    with sqlite3.connect(database) as db:
        db.execute('BEGIN IMMEDIATE')
        tables = ('thread_items', 'thread_realtime_items', 'thread_turns', 'thread_history_projection_state')
        others = {table: db.execute(f'SELECT * FROM {table} WHERE thread_id != ?', (sid,)).fetchall() for table in tables}
        for table in tables:
            db.execute(f'DELETE FROM {table} WHERE thread_id = ?', (sid,))
        for table in tables:
            assert db.execute(f'SELECT * FROM {table} WHERE thread_id != ?', (sid,)).fetchall() == others[table]


def model_records(path):
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    start = max(i for i, row in enumerate(rows) if row.get('type') == 'compacted')
    return [rows[start]['payload']] + [row['payload'] for row in rows[start + 1:] if row.get('type') == 'response_item']


try:
    for mode in ('legacy', 'paginated'):
        store = setup(mode)
        client = Client(store)
        try:
            response = client.rpc('thread/start', {'model': 'gpt-6.1-sol', 'modelProvider': 'local-probe',
                'cwd': str(ROOT / 'work'), 'approvalPolicy': 'never', 'sandbox': 'read-only', 'historyMode': mode})
            sid = response['thread']['id']
            for text in ('PROBE_ONE', 'PROBE_TWO'):
                client.rpc('turn/start', {'threadId': sid, 'input': [{'type': 'text', 'text': text}]})
                client.wait('turn/completed')
            abort_seen.clear()
            abort_release.clear()
            interrupted = client.rpc('turn/start', {'threadId': sid, 'input': [{'type': 'text', 'text': 'PROBE_ABORT'}]})
            assert abort_seen.wait(timeout=15)
            try:
                client.rpc('turn/interrupt', {'threadId': sid, 'turnId': interrupted['turn']['id']})
                client.wait('turn/completed')
            finally:
                abort_release.set()
            client.rpc('turn/start', {'threadId': sid, 'input': [{'type': 'text', 'text': 'PROBE_FAIL'}]})
            client.wait('turn/completed')
            client.rpc('thread/compact/start', {'threadId': sid})
            client.wait('turn/completed')
            client.rpc('turn/start', {'threadId': sid, 'input': [{'type': 'text', 'text': 'PROBE_THREE'}]})
            client.wait('turn/completed')
            client.rpc('thread/compact/start', {'threadId': sid})
            client.wait('turn/completed')
            client.rpc('turn/start', {'threadId': sid, 'input': [{'type': 'text', 'text': 'PROBE_FOUR'}]})
            client.wait('turn/completed')
            view = client.rpc('thread/read', {'threadId': sid, 'includeTurns': True})
            (store.parent / 'view.json').write_text(json.dumps(view))
            other = client.rpc('thread/start', {'model': 'gpt-6.1-sol', 'modelProvider': 'local-probe',
                'cwd': str(ROOT / 'work'), 'approvalPolicy': 'never', 'sandbox': 'read-only', 'historyMode': mode})
            client.rpc('turn/start', {'threadId': other['thread']['id'], 'input': [{'type': 'text', 'text': 'UNRELATED'}]})
            client.wait('turn/completed')
            other_view = client.rpc('thread/read', {'threadId': other['thread']['id'], 'includeTurns': True})
            print(mode, sid, 'turns=', len(view['thread']['turns']), 'requests=', len(requests), flush=True)
        finally:
            client.close()
        paths = list((store / 'sessions').rglob(f'*{sid}.jsonl'))
        assert len(paths) == 1
        rows = [json.loads(line) for line in paths[0].read_bytes().splitlines()]
        print(mode, 'native_mode=', rows[0]['payload'].get('history_mode'),
              'compactions=', sum(row.get('type') == 'compacted' for row in rows), flush=True)
        original_model = model_records(paths[0])
        candidate = profile(paths[0], mode + '-cleaned')
        repeat = profile(candidate, mode + '-repeated')
        assert candidate.read_bytes() == repeat.read_bytes(), 'cleanup is not idempotent'
        assert model_records(candidate) == original_model, 'active checkpoint/model records changed'

        cold = setup(mode + '-cold')
        restored = cold / 'sessions' / paths[0].name
        restored.parent.mkdir()
        shutil.copyfile(candidate, restored)
        actual = chat(read_view(cold, sid), ids=mode == 'paginated')
        expected = chat(view, ids=mode == 'paginated')
        assert sum(item[0] == 'userMessage' for item in expected) == 6
        assert sum(item[0] == 'agentMessage' for item in expected) == 4
        assert actual == expected, (mode, 'expected', expected, 'actual', actual)

        # This store already indexed the original before the file is replaced.
        shutil.copyfile(candidate, paths[0])
        if mode == 'paginated':
            try:
                read_view(store, sid)
            except AssertionError as error:
                assert 'durable rollout shrank' in str(error), error
            else:
                assert 'durable rollout shrank' in (store.parent / 'stderr').read_text(), 'stale-index negative control did not trigger'
            reset_display_index(store, sid)
        cached_view = read_view(store, sid)
        assert chat(cached_view, ids=mode == 'paginated') == chat(view, ids=mode == 'paginated')
        if mode == 'paginated':
            assert [(turn['id'], turn['status']) for turn in cached_view['thread']['turns']] == [
                (turn['id'], turn['status']) for turn in view['thread']['turns']]
        assert chat(read_view(store, other['thread']['id']), ids=mode == 'paginated') == chat(other_view, ids=mode == 'paginated')
        result = subprocess.run([CODEX, '-a', 'never', 'exec', '-s', 'read-only',
            'resume', sid, '--skip-git-repo-check', '--json', 'PROBE_HEADLESS'],
            env=environment(cold), cwd=ROOT / 'work', capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and 'LOCAL_REPLY' in result.stdout, result.stderr
        assert 'PROBE_HEADLESS' in json.dumps(requests[-1]['input'])
        assert any(item.get('role') == 'user' and any(part.get('text') == 'PROBE_FOUR'
                   for part in item.get('content', [])) for item in requests[-1]['input'])
        print(mode, 'PASS: native compaction, abort, repeat, model context, fresh/cached UI, other thread, headless', flush=True)
finally:
    server.shutdown()
    server.server_close()
    scratch.cleanup()
