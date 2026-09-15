#!/usr/bin/env python3
"""Replace a closed Grok session's old prompt prefix with authored summaries."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile

from chat_codex_v3 import parse_weekly_summaries
from chat_grok_session import (CONTINUITY_PREFIX, build_candidate, inventory,
                               is_continuity_summary, read_rows, write_rows)


def prompt_dates(updates):
    result = {}
    for row in updates:
        event = row.get('params', {}).get('update', {})
        if event.get('sessionUpdate') != 'user_message_chunk':
            continue
        index = event.get('_meta', {}).get('promptIndex')
        stamp = row.get('timestamp')
        if isinstance(index, int) and isinstance(stamp, (int, float)):
            result[index] = datetime.fromtimestamp(stamp, timezone.utc).date()
    return result


def split_turns(chat):
    header, turns, current = [], [], None
    for row in chat:
        if row.get('type') == 'user' and isinstance(row.get('prompt_index'), int):
            if current is not None:
                turns.append(current)
            current = [row]
        elif current is None:
            header.append(row)
        else:
            current.append(row)
    if current is not None:
        turns.append(current)
    return header, turns


def summary_row(block, speaker):
    text = (
        f'{CONTINUITY_PREFIX}\n\n'
        f'[{speaker}]\n\n{block.as_markdown()}'
    )
    return {'type': 'user', 'synthetic_reason': 'continuity_summary',
            'content': [{'type': 'text', 'text': text}]}


def apply_summaries(chat, updates, summary_text, speaker='Rook', safe_tail_turns=1):
    if safe_tail_turns < 1:
        raise ValueError('safe-tail-turns must be at least 1')
    header, turns = split_turns(chat)
    if len(turns) <= safe_tail_turns:
        raise ValueError('No old Grok turns remain outside the native safe tail')
    dates = prompt_dates(updates)
    if any(turn[0]['prompt_index'] not in dates for turn in turns):
        raise ValueError('A native prompt has no authoritative update timestamp')
    anchor_year = min(dates.values()).year
    blocks = parse_weekly_summaries(summary_text, anchor_year)
    if not blocks:
        raise ValueError('No "## Week/Period of ..." summary blocks found')
    if any(not block.body.strip() for block in blocks):
        raise ValueError('Every summary block must have a non-empty body')

    old = turns[:-safe_tail_turns]
    tail = turns[-safe_tail_turns:]
    matched = []
    for turn in old:
        day = dates[turn[0]['prompt_index']]
        hits = [block for block in blocks if block.start <= day <= block.end]
        if len(hits) > 1:
            raise ValueError(f'Overlapping summaries cover prompt {turn[0]["prompt_index"]}')
        matched.append(hits[0] if hits else None)
    for turn in tail:
        day = dates[turn[0]['prompt_index']]
        if any(block.start <= day <= block.end for block in blocks):
            raise ValueError('A summary overlaps the native safe tail')

    covered = [i for i, block in enumerate(matched) if block is not None]
    if not covered:
        raise ValueError('Summaries do not match any old Grok prompts')
    if covered != list(range(covered[-1] + 1)):
        raise ValueError('Summaries must cover one contiguous oldest-prompt prefix')
    used = {id(block) for block in matched if block is not None}
    if len(used) != len(blocks):
        raise ValueError('Every summary block must match at least one old prompt')

    previous = [row for row in header if is_continuity_summary(row)]
    static = [row for row in header if not is_continuity_summary(row)]
    summaries = previous + [summary_row(block, speaker) for block in blocks]
    suffix = turns[covered[-1] + 1:]
    return static + summaries + [row for turn in suffix for row in turn], len(covered)


def build_v3_candidate(source, output, summary_file, safe_tail_turns=1, speaker='Rook'):
    source, output = Path(source).expanduser().resolve(), Path(output).expanduser().resolve()
    summary_file = Path(summary_file).expanduser().resolve()
    if output == source or source in output.parents or output.exists():
        raise ValueError('Output must be a new directory outside the source')
    summary_text = summary_file.read_text(encoding='utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.grok-v3-building-', dir=output.parent))
    try:
        base = staging / 'base'
        report = build_candidate(source, base, safe_tail_turns)
        candidate = Path(report['compacted_copy'])
        chat = read_rows(candidate / 'chat_history.jsonl')
        updates = read_rows(candidate / 'updates.jsonl')
        rewritten, prompts_replaced = apply_summaries(
            chat, updates, summary_text, speaker, safe_tail_turns)
        write_rows(candidate / 'chat_history.jsonl', rewritten)
        metadata_path = candidate / 'summary.json'
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        metadata['num_chat_messages'] = len(rewritten)
        metadata_path.write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
        if inventory(source) != report['source_files']:
            raise ValueError('Source changed while building v3 candidate')
        final_original = output / 'original' / source.name
        final_candidate = output / 'compacted' / source.name
        report.update(profile='grok-chat-v3-summary', summary_file=str(summary_file),
                      prompts_replaced=prompts_replaced, chat_records=len(rewritten),
                      original_copy=str(final_original), compacted_copy=str(final_candidate),
                      candidate_files=inventory(candidate),
                      policy=('authored continuity summaries; unmatched recent dialogue; '
                              'native safe tail; full display and rewind history retained'))
        report['bytes_saved'] = (
            sum(item['bytes'] for item in report['source_files'].values())
            - sum(item['bytes'] for item in report['candidate_files'].values())
        )
        (base / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        base.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session', help='Closed Grok session directory')
    parser.add_argument('--summary-file', required=True)
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--safe-tail-turns', type=int, default=1)
    parser.add_argument('--speaker-name', default='Rook')
    args = parser.parse_args()
    report = build_v3_candidate(args.session, args.output_root, args.summary_file,
                                args.safe_tail_turns, args.speaker_name)
    print(json.dumps({key: report[key] for key in
                     ('source', 'compacted_copy', 'prompts_replaced', 'chat_records')}, indent=2))


if __name__ == '__main__':
    main()
