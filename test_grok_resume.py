#!/usr/bin/env python3
"""Opt-in Grok loader experiment; only synthetic data and a localhost model."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            last = str(body.get('messages', [{}])[-1].get('content', ''))
            reply = 'TEST_REPLY_AMBER'
            if 'compaction' in last or 'faithful, concise summary' in last:
                reply = 'TEST_CHECKPOINT_VIOLET\n' + '\n'.join(
                    f'Fictional garden bed {i}: record its label, keep the blue marker, '
                    'and continue the synthetic storage experiment. No actual work is pending.'
                    for i in range(30))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            chunk = {'id': 'synthetic-response', 'object': 'chat.completion.chunk',
                     'created': 1, 'model': 'probe', 'choices': [
                         {'index': 0, 'delta': {'role': 'assistant', 'content': reply},
                          'finish_reason': None}]}
            first = 'data: ' + json.dumps(chunk) + '\n\n'
            chunk['choices'] = [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]
            try:
                self.wfile.write((first + 'data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode())
            except BrokenPipeError:
                # Grok cancels ancillary title/recap requests on headless exit.
                pass

    binary = shutil.which('grok')
    if not binary:
        raise SystemExit('grok must be installed')
    root = Path(tempfile.mkdtemp(prefix='grok-resume-probe-'))
    print(f'Synthetic artifacts: {root}', flush=True)
    home = root / 'home'
    workspace = root / 'workspace'
    home.mkdir()
    workspace.mkdir()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (home / 'config.toml').write_text(
        '[model.probe]\nmodel = "probe"\nname = "Synthetic probe"\n'
        f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
        'api_key = "synthetic-not-a-secret"\napi_backend = "chat_completions"\n'
        'context_window = 8192\nauto_compact_threshold_percent = 50\n')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('GROK_', 'XAI_', 'ANTHROPIC_', 'OPENAI_'))}
    env.update(GROK_HOME=str(home), HOME=str(root), NO_COLOR='1')

    def run(label, *args):
        start = len(requests)
        result = subprocess.run(
            [binary, '--cwd', str(workspace), '-m', 'probe', '--no-subagents',
             '--disable-web-search', '--tools', '', '--max-turns', '1',
             '--output-format', 'json', *args],
            env=env, cwd=workspace, text=True, capture_output=True, timeout=45)
        (root / f'{label}.stdout').write_text(result.stdout)
        (root / f'{label}.stderr').write_text(result.stderr)
        captured = requests[start:]
        (root / f'{label}.requests.json').write_text(json.dumps(captured, indent=2))
        if result.returncode or not captured:
            raise RuntimeError(f'{label}: exit={result.returncode}; {result.stderr[-2000:]} {result.stdout[-1000:]}')
        print(f'{label}: {len(captured)} local model requests', flush=True)
        return json.dumps(captured)

    try:
        run('create', '-p', 'Remember TEST_USER_COBALT. Reply briefly.')
        summaries = list((home / 'sessions').glob('*/*/summary.json'))
        assert len(summaries) == 1, summaries
        session = summaries[0].parent
        session_id = session.name
        baseline = root / 'baseline'
        shutil.copytree(session, baseline)
        text = run('baseline-resume', '-r', session_id, '-p', 'Continue TEST_NEXT_IVORY.')
        assert 'TEST_USER_COBALT' in text and 'TEST_REPLY_AMBER' in text
        chat = session / 'chat_history.jsonl'
        chat.write_text(chat.read_text().replace('TEST_USER_COBALT', 'TEST_CHAT_JADE'))
        text = run('edited-chat-resume', '-r', session_id, '-p', 'Continue TEST_THIRD_SILVER.')
        assert 'TEST_CHAT_JADE' in text and 'TEST_USER_COBALT' not in text
        print('PASS: ordinary resume uses edited model chat rather than display updates', flush=True)
        run('grow', '-r', session_id, '-p', 'TEST_LONG_HISTORY ' + 'fictional garden note. ' * 1500)
        run('compact', '-r', session_id, '-p', 'TEST_AFTER_COMPACT')
        checkpoints = list((session / 'compaction_checkpoints').glob('*.json'))
        assert checkpoints, 'Native compaction did not produce a checkpoint'
        print(f'Native checkpoints: {len(checkpoints)}', flush=True)
        assert 'TEST_CHECKPOINT_VIOLET' in chat.read_text()
        chat.write_text(chat.read_text().replace('TEST_CHECKPOINT_VIOLET', 'TEST_REBUILT_COPPER'))
        text = run('checkpoint-precedence', '-r', session_id, '-p', 'Continue TEST_FOURTH_GOLD.')
        assert 'TEST_REBUILT_COPPER' in text and 'TEST_CHECKPOINT_VIOLET' not in text
        text = run('second-checkpoint-resume', '-r', session_id, '-p', 'Continue TEST_FIFTH_WHITE.')
        assert 'TEST_REBUILT_COPPER' in text and 'TEST_CHECKPOINT_VIOLET' not in text
        print('PASS: native checkpoints do not override edited chat on two resumes', flush=True)
        exported = subprocess.run([binary, 'export', session_id], env=env,
                                  text=True, capture_output=True, timeout=15)
        assert exported.returncode == 0, exported.stderr
        (root / 'export.md').write_text(exported.stdout)
        assert 'TEST_USER_COBALT' in exported.stdout
        assert 'TEST_REPLY_AMBER' in exported.stdout
        print('PASS: original dialogue remains in Grok transcript export', flush=True)
        print(f'Session: {session}', flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
