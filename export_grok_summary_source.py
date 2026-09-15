#!/usr/bin/env python3
"""Export Grok dialogue by UTC day for an external continuity summarizer."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from chat_grok_session import is_continuity_summary, read_rows


def collect(updates, first_prompt=0):
    rows, current, prompt_stamp = [], None, None
    for row in updates:
        event = row.get('params', {}).get('update', {})
        kind = event.get('sessionUpdate')
        if kind == 'user_message_chunk':
            index = event.get('_meta', {}).get('promptIndex')
            if not isinstance(index, int) or index < first_prompt:
                current = None
                continue
            current = index
            prompt_stamp = row['timestamp']
            rows.append((prompt_stamp, row['timestamp'], index, 'Marcos', event['content']['text']))
        elif kind == 'agent_message_chunk' and current is not None:
            text = event.get('content', {}).get('text')
            if isinstance(text, str) and text:
                rows.append((prompt_stamp, row['timestamp'], current, 'Rook', text))
    return rows


def export(session, output):
    session, output = Path(session).expanduser().resolve(), Path(output).expanduser().resolve()
    if output == session or session in output.parents or output.exists():
        raise ValueError('Output must be a new directory outside the source')
    chat = read_rows(session / 'chat_history.jsonl')
    native = [row['prompt_index'] for row in chat
              if row.get('type') == 'user' and isinstance(row.get('prompt_index'), int)]
    if not native:
        raise ValueError('No indexed native prompts in Grok chat history')
    first_prompt = min(native) if any(is_continuity_summary(row) for row in chat) else 0
    rows = collect(read_rows(session / 'updates.jsonl'), first_prompt)
    if not rows:
        raise ValueError('No Grok dialogue found')
    output.mkdir(parents=True)
    days = {}
    for prompt_stamp, stamp, index, role, text in rows:
        prompt_dt = datetime.fromtimestamp(prompt_stamp, timezone.utc)
        dt = datetime.fromtimestamp(stamp, timezone.utc)
        days.setdefault(prompt_dt.date().isoformat(), []).append((dt, index, role, text))
    index_lines = ['# Grok Summary Source', '', f'- Source: `{session}`',
                   f'- First exported prompt: `{first_prompt}`', '', '## Read Order', '']
    for day, items in sorted(days.items()):
        path = output / f'{day}.md'
        with path.open('w', encoding='utf-8') as handle:
            handle.write(f'# Rook Conversation Source: {day} (UTC)\n\n')
            for dt, prompt, role, text in items:
                handle.write(f'## {dt.isoformat()} | prompt {prompt} | {role}\n\n{text}\n\n')
        index_lines.append(f'- [{path.name}]({path.name}) | messages `{len(items)}`')
    (output / 'INDEX.md').write_text('\n'.join(index_lines) + '\n', encoding='utf-8')
    return len(rows), len(days)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session')
    parser.add_argument('--output-root', required=True)
    args = parser.parse_args()
    messages, days = export(args.session, args.output_root)
    print(json.dumps({'output': str(Path(args.output_root).resolve()),
                      'messages': messages, 'days': days}, indent=2))


if __name__ == '__main__':
    main()
