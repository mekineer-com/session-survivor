#!/usr/bin/env python3
"""Opt-in native restore probe: disposable homes, fictional history, local APIs only."""

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MARKER = 'FICTIONAL_BACKUP_COBALT'
REPLY = 'FICTIONAL_RESTORED_AMBER'


def main():
    root = Path(tempfile.mkdtemp(prefix='agent-backup-resume-'))
    root.chmod(0o700)
    workspace = root / 'workspace'
    workspace.mkdir()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[]}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append({'path': self.path, 'body': body})
            anthropic = '/messages' in self.path
            message = {'id': 'msg_synthetic', 'type': 'message', 'role': 'assistant',
                       'model': body.get('model', 'probe'),
                       'content': [{'type': 'text', 'text': REPLY}],
                       'stop_reason': 'end_turn', 'stop_sequence': None,
                       'usage': {'input_tokens': 12, 'output_tokens': 5}}
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream' if body.get('stream')
                             else 'application/json')
            self.end_headers()
            if not body.get('stream'):
                output = json.dumps(message)
            elif anthropic:
                start = dict(message, content=[], stop_reason=None)
                events = [('message_start', {'type': 'message_start', 'message': start}),
                          ('content_block_start', {'type': 'content_block_start', 'index': 0,
                           'content_block': {'type': 'text', 'text': ''}}),
                          ('content_block_delta', {'type': 'content_block_delta', 'index': 0,
                           'delta': {'type': 'text_delta', 'text': REPLY}}),
                          ('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
                          ('message_delta', {'type': 'message_delta',
                           'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
                           'usage': {'output_tokens': 5}}),
                          ('message_stop', {'type': 'message_stop'})]
                output = ''.join(f'event: {name}\ndata: {json.dumps(value)}\n\n'
                                 for name, value in events)
            else:
                chunk = {'id': 'synthetic', 'object': 'chat.completion.chunk',
                         'created': 1, 'model': 'probe', 'choices': [
                             {'index': 0, 'delta': {'role': 'assistant', 'content': REPLY},
                              'finish_reason': None}]}
                output = 'data: ' + json.dumps(chunk) + '\n\n'
                chunk['choices'] = [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]
                output += 'data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n'
            try:
                self.wfile.write(output.encode())
            except BrokenPipeError:
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f'http://127.0.0.1:{server.server_port}'

    def env_for(home):
        home.mkdir(parents=True, exist_ok=True)
        # No inherited auth, plugins, MCP servers, or real-home configuration.
        return {'PATH': os.environ['PATH'], 'HOME': str(home), 'NO_COLOR': '1',
                'LANG': 'C.UTF-8', 'HTTP_PROXY': 'http://127.0.0.1:9',
                'HTTPS_PROXY': 'http://127.0.0.1:9', 'ALL_PROXY': 'http://127.0.0.1:9',
                'NO_PROXY': '127.0.0.1,localhost'}

    def run(label, argv, env):
        first = len(requests)
        result = subprocess.run(argv, env=env, cwd=workspace, text=True,
                                capture_output=True, timeout=60)
        (root / f'{label}.stdout').write_text(result.stdout)
        (root / f'{label}.stderr').write_text(result.stderr)
        captured = requests[first:]
        (root / f'{label}.requests.json').write_text(json.dumps(captured, indent=2))
        assert result.returncode == 0, f'{label}: {result.stderr[-2000:]} {result.stdout[-2000:]}'
        return result, captured

    def codex():
        binary = shutil.which('codex')
        assert binary, 'Codex must be installed'
        home = root / 'codex-restored'
        store = home / '.codex'
        path = store / 'sessions/2026/01/01/rollout-2026-01-01T00-00-00-11111111-1111-4111-8111-111111111111.jsonl'
        path.parent.mkdir(parents=True)
        sid = '11111111-1111-4111-8111-111111111111'
        meta = {'id': sid, 'timestamp': '2026-01-01T00:00:00Z', 'cwd': str(workspace),
                'originator': 'restore_probe', 'cli_version': '0.159.3', 'source': 'cli',
                'model_provider': 'openai', 'base_instructions': {'text': 'Synthetic probe.'}}
        rows = [{'type': 'session_meta', 'payload': meta},
                {'type': 'event_msg', 'payload': {'type': 'task_started',
                 'turn_id': 'synthetic-turn', 'model_context_window': 128000}},
                {'type': 'turn_context', 'payload': {'turn_id': 'synthetic-turn',
                 'cwd': str(workspace), 'approval_policy': 'never',
                 'sandbox_policy': {'type': 'read-only'}, 'model': 'gpt-6.1-sol',
                 'effort': 'medium', 'summary': 'auto'}},
                {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': MARKER}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                 'content': [{'type': 'input_text', 'text': MARKER}]}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                 'content': [{'type': 'output_text', 'text': REPLY}]}},
                {'type': 'event_msg', 'payload': {'type': 'agent_message', 'message': REPLY}},
                {'type': 'event_msg', 'payload': {'type': 'task_complete',
                 'turn_id': 'synthetic-turn', 'last_agent_message': REPLY}}]
        path.write_text(''.join(json.dumps(dict(row, timestamp='2026-01-01T00:00:00Z')) + '\n'
                                for row in rows))
        (store / 'config.toml').write_text('[analytics]\nenabled=false\n[feedback]\nenabled=false\n')
        assert not list(store.glob('*.sqlite'))

        def load(profile, name):
            profile_env = env_for(profile.parent)
            profile_env['CODEX_HOME'] = str(profile)
            with (root / f'codex-{name}.stderr').open('w') as stderr:
                proc = subprocess.Popen([binary, 'app-server', '--stdio'],
                                        env=profile_env, cwd=workspace, text=True, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=stderr)
                messages = queue.Queue()

                def reader():
                    for line in proc.stdout:
                        messages.put(json.loads(line))
                threading.Thread(target=reader, daemon=True).start()

                def rpc(method, params, number):
                    proc.stdin.write(json.dumps({'id': number, 'method': method, 'params': params}) + '\n')
                    proc.stdin.flush()
                    while True:
                        message = messages.get(timeout=30)
                        if message.get('id') == number:
                            (root / f'codex-{name}-{method.replace("/", "-")}.json').write_text(json.dumps(message, indent=2))
                            assert 'error' not in message, message
                            return message['result']
                try:
                    rpc('initialize', {'clientInfo': {'name': 'backup_probe', 'version': '1'}}, 1)
                    proc.stdin.write('{"method":"initialized"}\n')
                    proc.stdin.flush()
                    listed = rpc('thread/list', {'limit': 100}, 2)
                    print('Codex fresh listing contains ID:', sid in json.dumps(listed), flush=True)
                    loaded = rpc('thread/read', {'threadId': sid, 'includeTurns': True}, 3)
                    assert MARKER in json.dumps(loaded) and REPLY in json.dumps(loaded)
                    resumed = rpc('thread/resume', {'threadId': sid}, 4)
                    assert resumed['thread']['id'] == sid and MARKER in json.dumps(resumed)
                    print('Codex resumed model/effort:', resumed.get('model'),
                          resumed.get('reasoningEffort'), flush=True)
                    assert resumed.get('model') == 'gpt-6.1-sol'
                    assert resumed.get('reasoningEffort') == 'medium'
                    if name == 'initial':
                        rpc('thread/name/set', {'threadId': sid, 'name': 'SyntheticRestore'}, 5)
                        named = rpc('thread/read', {'threadId': sid}, 6)
                        assert named['thread']['name'] == 'SyntheticRestore'
                    else:
                        assert resumed['thread']['name'] is None
                        print('PASS Codex: UUID/history survive; custom name does not without index/DB', flush=True)
                finally:
                    proc.terminate()
                    proc.wait(timeout=10)

        load(store, 'initial')
        restored = root / 'codex-cold-restored/.codex'
        destination = restored / path.relative_to(store)
        destination.parent.mkdir(parents=True)
        shutil.copy2(path, destination)
        (restored / 'config.toml').write_text((store / 'config.toml').read_text())
        shutil.rmtree(home)
        load(restored, 'cold')

    def claude():
        binary = shutil.which('claude', path=str(Path.home() / '.local/bin') + ':' + os.environ['PATH'])
        assert binary, 'Claude must be installed'
        sid = '22222222-2222-4222-8222-222222222222'
        original = root / 'claude-original'
        original.mkdir()
        escaped = str(workspace).replace('/', '-')
        path = original / '.claude/projects' / escaped / f'{sid}.jsonl'
        path.parent.mkdir(parents=True)
        user_id = '33333333-3333-4333-8333-333333333333'
        assistant_id = '44444444-4444-4444-8444-444444444444'
        envelope = {'sessionId': sid, 'cwd': str(workspace), 'version': '2.1.280',
                    'isSidechain': False, 'userType': 'external', 'timestamp': '2026-01-01T00:00:00Z'}
        rows = [dict(envelope, type='user', uuid=user_id, parentUuid=None,
                     message={'role': 'user', 'content': MARKER}),
                dict(envelope, type='assistant', uuid=assistant_id, parentUuid=user_id,
                     message={'id': 'msg_fixture', 'type': 'message', 'role': 'assistant',
                              'model': 'claude-sonnet-4-6', 'content': [{'type': 'text', 'text': REPLY}],
                              'stop_reason': 'end_turn', 'stop_sequence': None,
                              'usage': {'input_tokens': 12, 'output_tokens': 5}}),
                {'type': 'custom-title', 'sessionId': sid, 'customTitle': 'SyntheticRestore'}]
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        for selector in (sid, 'SyntheticRestore'):
            home = root / ('claude-restored-' + selector)
            target = home / path.relative_to(original)
            target.parent.mkdir(parents=True)
            shutil.copy2(path, target)
        shutil.rmtree(original)
        for selector in (sid, 'SyntheticRestore'):
            home = root / ('claude-restored-' + selector)
            env = env_for(home)
            env.update(ANTHROPIC_API_KEY='synthetic-not-a-secret', ANTHROPIC_BASE_URL=endpoint,
                       CLAUDE_CONFIG_DIR=str(home / '.claude'), DISABLE_TELEMETRY='1',
                       DISABLE_ERROR_REPORTING='1', CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
            result, captured = run('claude-' + selector,
                [binary, '--bare', '--resume', selector, '--model', 'claude-sonnet-4-6',
                 '--tools', '', '--max-turns', '1', '--output-format', 'json', '-p', 'Continue fiction.'], env)
            assert captured and MARKER in json.dumps(captured), 'History absent from local model request'
            response = json.loads(result.stdout)
            assert response['result'] == REPLY and not response['is_error']
            assert response['session_id'] == sid
            print(f'PASS Claude: transcript-only resume by {selector}', flush=True)

    def grok():
        binary = shutil.which('grok', path=str(Path.home() / '.local/bin') + ':' + os.environ['PATH'])
        assert binary, 'Grok must be installed'
        original = root / 'grok-original'
        env = env_for(original)
        store = original / '.grok'
        store.mkdir()
        env['GROK_HOME'] = str(store)
        config = ('[model.probe]\nmodel="probe"\nname="Local probe"\n'
                  f'base_url="{endpoint}/v1"\napi_key="synthetic-not-a-secret"\n'
                  'api_backend="chat_completions"\ncontext_window=128000\n')
        (store / 'config.toml').write_text(config)
        common = [binary, '--cwd', str(workspace), '-m', 'probe', '--no-subagents',
                  '--disable-web-search', '--tools', '', '--max-turns', '1', '--output-format', 'json']
        run('grok-create', common + ['-p', MARKER], env)
        sources = list(store.glob('sessions/*/*/summary.json'))
        assert len(sources) == 1, sources
        source = sources[0].parent
        home = root / 'grok-restored'
        restored_env = env_for(home)
        restored_store = home / '.grok'
        target = restored_store / source.relative_to(store)
        target.parent.mkdir(parents=True)
        shutil.copytree(source, target)
        (restored_store / 'config.toml').write_text(config)
        restored_env['GROK_HOME'] = str(restored_store)
        shutil.rmtree(original)
        assert not (restored_store / 'active_sessions.json').exists()
        result, captured = run('grok-restored', common + ['-r', target.name, '-p', 'Continue fiction.'], restored_env)
        response = json.loads(result.stdout)
        assert MARKER in json.dumps(captured) and response['text'] == REPLY
        assert response['sessionId'] == target.name
        print('PASS Grok: native session-folder-only restore reaches local model', flush=True)

    print(f'Synthetic evidence: {root}', flush=True)
    try:
        for probe in (codex, claude, grok):
            probe()
    finally:
        (root / 'all-requests.json').write_text(json.dumps(requests, indent=2))
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
