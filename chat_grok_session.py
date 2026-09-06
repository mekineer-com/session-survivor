#!/usr/bin/env python3
"""Build an offline Grok chat candidate; never replace a source session."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


def read_rows(path):
    with path.open(encoding='utf-8') as handle:
        return [json.loads(line) for line in handle]


def write_rows(path, rows):
    with path.open('w', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')


def validate_tools(rows):
    pending = set()
    for row in rows:
        if row['type'] == 'assistant':
            for call in row.get('tool_calls', []):
                if call['id'] in pending:
                    raise ValueError('Duplicate pending tool call')
                pending.add(call['id'])
        elif row['type'] == 'tool_result':
            if row['tool_call_id'] not in pending:
                raise ValueError('Unpaired tool result in native tail')
            pending.remove(row['tool_call_id'])
        elif row['type'] == 'user' and pending:
            raise ValueError('User message interrupts pending tool calls')
    if pending:
        raise ValueError('Unfinished tool calls in native tail')


def rebuild(chat, updates, safe_tail_turns=1, archived_histories=()):
    if safe_tail_turns < 1:
        raise ValueError('safe-tail-turns must be at least 1')
    native_users = [(i, row['prompt_index']) for i, row in enumerate(chat)
                    if row['type'] == 'user' and 'prompt_index' in row]
    if not native_users:
        raise ValueError('No indexed native user turn; inspect before rebuilding')
    tail_start, tail_index = native_users[-min(safe_tail_turns, len(native_users))]
    tail = chat[tail_start:]
    validate_tools(tail)
    native = {}
    reminders = {}
    for history in [*archived_histories, chat]:
        preceding = None
        local_reminders = {}
        for row in history:
            if row['type'] == 'user' and 'prompt_index' in row:
                preceding = row['prompt_index']
                native[preceding] = row
                local_reminders[preceding] = []
            elif row['type'] == 'user' and preceding is not None:
                local_reminders[preceding].append(row)
        reminders.update(local_reminders)
    original_queries = [row['params']['update']['content'] for row in updates
                        if row['params']['update']['sessionUpdate'] == 'user_message_chunk']
    header = []
    for row in chat[:native_users[0][0]]:
        if row['type'] == 'system':
            header.append(row)
        elif row['type'] == 'user':
            blocks = row['content']
            environment = (isinstance(blocks, list) and blocks
                           and blocks[0].get('type') == 'text'
                           and blocks[0].get('text', '').startswith('<user_info>'))
            if environment or row.get('synthetic_reason') == 'system_reminder':
                header.append(row)
            else:
                text = blocks[0].get('text', '') if isinstance(blocks, list) and len(blocks) == 1 else ''
                summary = (row.get('synthetic_reason') == 'compaction_meta'
                           and text.startswith('This session is being continued from a previous conversation'))
                wrapped_query = text.startswith('<user_query>\n') and text.endswith('\n</user_query>')
                original_query = wrapped_query and any(
                    text.removeprefix('<user_query>\n').removesuffix('\n</user_query>').rstrip()
                    == item.get('text', '').rstrip()
                    for item in original_queries
                )
                if not summary and not original_query:
                    header.append(row)
        elif row['type'] not in ('assistant', 'reasoning', 'tool_result'):
            raise ValueError('Unindexed native history before first user turn; inspect before rebuilding')

    history = []
    output_updates = copy.deepcopy(updates)
    indices = []
    current_index = None
    merge_assistant = False
    completed = False
    for row in output_updates:
        event = row['params']['update']
        kind = event['sessionUpdate']
        if kind == 'user_message_chunk':
            if current_index is not None and current_index < tail_index:
                history.extend(reminders.get(current_index, []))
            current_index = event['_meta']['promptIndex']
            if not isinstance(current_index, int) or current_index != len(indices):
                raise ValueError('Missing, duplicate or out-of-order prompt index; full history required')
            indices.append(current_index)
            completed = False
        if current_index is None:
            if kind == 'agent_message_chunk':
                raise ValueError('Assistant dialogue before first user prompt')
            continue
        if kind == 'turn_completed':
            completed = True
        if current_index >= tail_index:
            continue
        if kind in ('user_message_chunk', 'agent_message_chunk'):
            content = event['content']
            if content.get('type') != 'text' or not isinstance(content.get('text'), str):
                raise ValueError('Non-text dialogue requires a media-aware profile')
            text = content['text']
            if kind == 'user_message_chunk':
                if current_index in native:
                    history.append(native[current_index])
                else:
                    raise ValueError(f'Missing native user row {current_index}; preserve compaction request archives')
                merge_assistant = False
            elif merge_assistant:
                history[-1]['content'] += text
            else:
                history.append({'type': 'assistant', 'content': text})
                merge_assistant = True
        elif kind in ('tool_call', 'tool_call_update'):
            merge_assistant = False
            for field in ('rawInput', 'rawOutput', 'content'):
                event.pop(field, None)
        elif kind == 'agent_thought_chunk':
            event['content'] = {'type': 'text', 'text': ''}
        elif kind == 'turn_completed':
            merge_assistant = False
    if not completed:
        raise ValueError('Last turn has not completed; close the session cleanly first')
    if [index for index in indices if index >= tail_index] != [index for _, index in native_users if index >= tail_index]:
        raise ValueError('Native tail and display history disagree on prompt indices')
    if not header or header[0]['type'] != 'system':
        raise ValueError('Missing native system context')
    return header + history + tail, output_updates


def inventory(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Symlink in session directory: {path}')
        if path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(chunk)
            result[str(path.relative_to(directory))] = {'bytes': path.stat().st_size,
                                                        'sha256': digest.hexdigest()}
    return result


def build_candidate(source, output, safe_tail_turns=1):
    source, output = Path(source).expanduser().resolve(), Path(output).expanduser().resolve()
    if output == source or source in output.parents or output.exists():
        raise ValueError('Output must be a new directory outside the source')
    metadata = json.loads((source / 'summary.json').read_text(encoding='utf-8'))
    if metadata.get('chat_format_version') != 1:
        raise ValueError('Only Grok chat_format_version 1 has been tested')
    homes = {Path(os.environ.get('GROK_HOME', '~/.grok')).expanduser()}
    if metadata.get('grok_home'):
        homes.add(Path(metadata['grok_home']))
    for home in homes:
        registry = home / 'active_sessions.json'
        if registry.exists():
            for entry in json.loads(registry.read_text(encoding='utf-8')):
                if entry.get('session_id') == metadata['info']['id']:
                    try:
                        pid = entry['pid']
                        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                            raise ValueError('Invalid registered PID; verify session is closed')
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        continue
                    except PermissionError as exc:
                        raise ValueError('Cannot verify registered process; verify session is closed') from exc
                    raise ValueError('Session is still registered to a running process')
    before = inventory(source)
    archives = [json.loads(path.read_text(encoding='utf-8'))
                for path in (source / 'compaction_requests').glob('*.json')]
    archives.sort(key=lambda item: item['created_at'])
    chat, updates = rebuild(read_rows(source / 'chat_history.jsonl'),
                            read_rows(source / 'updates.jsonl'), safe_tail_turns,
                            [item['chat_history'] for item in archives])
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.grok-building-', dir=output.parent))
    original = output / 'original' / source.name
    candidate = output / 'compacted' / source.name
    try:
        backup = staging / 'original' / source.name
        shutil.copytree(source, backup)
        if inventory(backup) != before or inventory(source) != before:
            raise ValueError('Source changed during backup; candidate not created')
        report = finish_candidate(source, original, candidate, staging, before, metadata,
                                  chat, updates, safe_tail_turns)
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return report


def finish_candidate(source, original, final_candidate, staging, before, metadata,
                     chat, updates, safe_tail_turns):
    candidate = staging / 'compacted' / source.name
    shutil.copytree(staging / 'original' / source.name, candidate)
    write_rows(candidate / 'chat_history.jsonl', chat)
    write_rows(candidate / 'updates.jsonl', updates)
    metadata['num_chat_messages'] = len(chat)
    metadata['num_messages'] = len(updates)
    (candidate / 'summary.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    if (read_rows(candidate / 'chat_history.jsonl') != chat
            or read_rows(candidate / 'updates.jsonl') != updates or inventory(source) != before):
        raise ValueError('Validation failed or source changed; do not use candidate')
    after = inventory(candidate)
    report = {'source': str(source), 'original_copy': str(original), 'compacted_copy': str(final_candidate),
              'profile': 'grok-chat-v1', 'safe_tail_turns': safe_tail_turns,
              'source_files': before, 'candidate_files': after,
              'bytes_saved': sum(x['bytes'] for x in before.values()) - sum(x['bytes'] for x in after.values()),
              'chat_records': len(chat), 'update_records': len(updates),
              'policy': 'verbatim old dialogue; native tail; old update tool payloads emptied; auxiliary files retained'}
    manifest = staging / 'manifest.json'
    manifest.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session', help='Closed Grok session directory')
    parser.add_argument('--output-root', required=True, help='New backup/candidate/report directory')
    parser.add_argument('--safe-tail-turns', type=int, default=1)
    args = parser.parse_args()
    report = build_candidate(args.session, args.output_root, args.safe_tail_turns)
    print(json.dumps({key: report[key] for key in
                     ('source', 'original_copy', 'compacted_copy', 'bytes_saved', 'chat_records')}, indent=2))


if __name__ == '__main__':
    main()
