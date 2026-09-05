#!/usr/bin/env python3
"""Opt-in Grok loader experiment; only synthetic data and a localhost model."""

import json
import os
import queue
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from chat_grok_session import build_candidate, read_rows, write_rows


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
        # Keep the restored full dialogue below the test model's compact threshold.
        config = home / 'config.toml'
        config.write_text(config.read_text().replace('8192', '128000'))
        rows = read_rows(chat)
        rows[-1:-1] = [
            {'type': 'assistant', 'content': 'TEST_TOOL_PREFACE', 'tool_calls': [
                {'id': 'synthetic-call', 'name': 'read_file', 'arguments': '{"path":"fiction.txt"}'}]},
            {'type': 'tool_result', 'tool_call_id': 'synthetic-call', 'content': 'TEST_TOOL_RESULT'},
        ]
        write_rows(chat, rows)
        updates = read_rows(session / 'updates.jsonl')
        tool_row = json.loads(json.dumps(updates[0]))
        tool_row['params']['update'] = {'sessionUpdate': 'tool_call', 'toolCallId': 'old-synthetic-call',
                                        'title': 'Synthetic read', 'status': 'completed',
                                        'rawInput': {'large': 'TEST_OLD_BULK' * 1000}}
        tool_row['params']['_meta']['eventId'] += '-fixture-tool'
        updates.insert(1, tool_row)
        write_rows(session / 'updates.jsonl', updates)
        source_bytes = chat.read_bytes()
        report = build_candidate(session, root / 'candidate-test')
        assert chat.read_bytes() == source_bytes
        candidate = Path(report['compacted_copy'])
        rebuilt = read_rows(candidate / 'chat_history.jsonl')
        assert any(row.get('tool_call_id') == 'synthetic-call' for row in rebuilt)
        assert 'TEST_USER_COBALT' in json.dumps(rebuilt)
        assert 'TEST_OLD_BULK' not in (candidate / 'updates.jsonl').read_text()
        assert (candidate / 'rewind_points.jsonl').read_bytes() == (session / 'rewind_points.jsonl').read_bytes()
        # Only the disposable installation is swapped, with its previous tree retained.
        session.rename(root / 'pre-candidate')
        shutil.copytree(candidate, session)
        text = run('candidate-resume', '-r', session_id, '-p', 'Continue TEST_CANDIDATE_BLACK.')
        assert 'TEST_USER_COBALT' in text and 'TEST_TOOL_RESULT' in text
        text = run('candidate-second-resume', '-r', session_id, '-p', 'Continue TEST_CANDIDATE_GRAY.')
        assert 'TEST_USER_COBALT' in text and 'TEST_TOOL_RESULT' in text
        print('PASS: rebuilt dialogue and native tool pair reach the model on two resumes', flush=True)

        notifications = []
        messages = queue.Queue()
        with (root / 'acp.stderr').open('w') as stderr:
            proc = subprocess.Popen([binary, 'agent', '--no-leader', '-m', 'probe', 'stdio'],
                                    env=env, cwd=workspace, text=True, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=stderr)
            def read_messages():
                for line in proc.stdout:
                    messages.put(json.loads(line))
            threading.Thread(target=read_messages, daemon=True).start()
            def rpc(method, params, number):
                proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': number,
                                             'method': method, 'params': params}) + '\n')
                proc.stdin.flush()
                while True:
                    message = messages.get(timeout=20)
                    notifications.append(message)
                    if message.get('id') == number:
                        if 'error' in message:
                            raise RuntimeError(message)
                        return message.get('result')
            try:
                rpc('initialize', {'protocolVersion': 1, 'clientCapabilities': {}}, 1)
                rpc('session/load', {'sessionId': session_id, 'cwd': str(workspace), 'mcpServers': []}, 2)
                assert 'TEST_USER_COBALT' in json.dumps(notifications)
                points = rpc('_x.ai/rewind/points', {'sessionId': session_id}, 3)
                (root / 'rewind-points.json').write_text(json.dumps(points, indent=2))
                assert points['rewind_points'][0]['prompt_index'] == 0
                assert 'TEST_USER_COBALT' in points['rewind_points'][0]['prompt_preview']
                print('PASS: ACP session/load replays original dialogue; rewind points load', flush=True)
                rpc('session/prompt', {'sessionId': session_id,
                                      'prompt': [{'type': 'text', 'text': 'TEST_ACP_WARMUP'}]}, 4)
                result = rpc('_x.ai/rewind/execute', {'sessionId': session_id, 'targetPromptIndex': 1,
                                                     'mode': 'conversation_only'}, 5)
                (root / 'rewind-result.json').write_text(json.dumps(result, indent=2))
                if not result['success']:
                    # Reproduce the same result on the untouched one-turn control.
                    control_id = '11111111-1111-4111-8111-111111111111'
                    control = session.parent / control_id
                    shutil.copytree(baseline, control)
                    meta = json.loads((control / 'summary.json').read_text())
                    meta['info']['id'] = control_id
                    (control / 'summary.json').write_text(json.dumps(meta))
                    control_updates = control / 'updates.jsonl'
                    control_updates.write_text(control_updates.read_text().replace(session_id, control_id))
                    rpc('session/load', {'sessionId': control_id, 'cwd': str(workspace), 'mcpServers': []}, 6)
                    control_result = rpc('_x.ai/rewind/execute', {'sessionId': control_id,
                                         'targetPromptIndex': 0, 'mode': 'conversation_only'}, 7)
                    (root / 'rewind-control.json').write_text(json.dumps(control_result, indent=2))
                    assert control_result['success'] is False, control_result
                    print('LIMITATION: rewind execution returns false on candidate AND untouched control', flush=True)
            finally:
                (root / 'acp.json').write_text(json.dumps(notifications, indent=2))
                proc.terminate()
                proc.wait(timeout=10)
        if result['success']:
            text = run('after-rewind', '-r', session_id, '-p', 'Continue TEST_AFTER_REWIND.')
            assert 'TEST_USER_COBALT' in text
            assert 'TEST_CANDIDATE_GRAY' not in text and 'TEST_CANDIDATE_BLACK' not in text
            print('PASS: rewind to reconstructed history then resume excludes later turns', flush=True)
        print(f'Session: {session}', flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
