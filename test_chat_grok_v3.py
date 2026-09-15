import json
from pathlib import Path
import tempfile
import unittest

from chat_grok_session import CONTINUITY_PREFIX, read_rows, write_rows
from chat_grok_v3 import apply_summaries, build_v3_candidate
from export_grok_summary_source import collect, export
from test_chat_grok_session import event


class GrokV3Test(unittest.TestCase):
    def test_replaces_only_contiguous_old_prefix(self):
        chat = [{'type': 'system', 'content': 'Synthetic instructions'}]
        updates = []
        for index, stamp in enumerate((1786000000, 1786086400, 1786172800)):
            chat.extend([
                {'type': 'user', 'prompt_index': index,
                 'content': [{'type': 'text', 'text': f'Question {index}'}]},
                {'type': 'assistant', 'content': f'Answer {index}'},
            ])
            row = event('user_message_chunk', _meta={'promptIndex': index},
                        content={'type': 'text', 'text': f'Question {index}'})
            row['timestamp'] = stamp
            updates.append(row)
        summary = '## Period of Aug 6-7, 2026\n\nSynthetic history.'
        output, count = apply_summaries(chat, updates, summary)
        self.assertEqual(count, 2)
        self.assertEqual(sum(row.get('synthetic_reason') == 'continuity_summary'
                             for row in output), 1)
        self.assertEqual([row.get('prompt_index') for row in output
                          if 'prompt_index' in row], [2])

    def test_refuses_gap_or_safe_tail_overlap(self):
        chat = [{'type': 'system', 'content': 'Synthetic instructions'}]
        updates = []
        for index, stamp in enumerate((1786000000, 1786086400, 1786172800)):
            chat.append({'type': 'user', 'prompt_index': index,
                         'content': [{'type': 'text', 'text': str(index)}]})
            row = event('user_message_chunk', _meta={'promptIndex': index},
                        content={'type': 'text', 'text': str(index)})
            row['timestamp'] = stamp
            updates.append(row)
        with self.assertRaisesRegex(ValueError, 'contiguous'):
            apply_summaries(chat, updates, '## Period of Aug 7, 2026\n\nGap')
        with self.assertRaisesRegex(ValueError, 'safe tail'):
            apply_summaries(chat, updates, '## Period of Aug 6-8, 2026\n\nToo far')
        with self.assertRaisesRegex(ValueError, 'non-empty body'):
            apply_summaries(chat, updates, '## Period of Aug 6, 2026')

    def test_export_keeps_cross_midnight_answer_with_prompt(self):
        updates = []
        for row, stamp in (
            (event('user_message_chunk', _meta={'promptIndex': 0},
                   content={'type': 'text', 'text': 'Question'}), 1786060740),
            (event('agent_message_chunk', content={'type': 'text', 'text': 'Answer'}), 1786060860),
        ):
            row['timestamp'] = stamp
            updates.append(row)
        rows = collect(updates)
        self.assertEqual(rows[0][0], rows[1][0])

    def test_export_uses_full_updates_until_v3_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'session'
            source.mkdir()
            chat = [
                {'type': 'system', 'content': 'Synthetic instructions'},
                {'type': 'user', 'prompt_index': 1,
                 'content': [{'type': 'text', 'text': 'Question 1'}]},
            ]
            updates = []
            for index, stamp in enumerate((1786000000, 1786086400)):
                row = event('user_message_chunk', _meta={'promptIndex': index},
                            content={'type': 'text', 'text': f'Question {index}'})
                row['timestamp'] = stamp
                updates.append(row)
            write_rows(source / 'chat_history.jsonl', chat)
            write_rows(source / 'updates.jsonl', updates)
            output = root / 'export'
            export(source, output)
            self.assertIn('Question 0', (output / '2026-08-06.md').read_text())
            chat.insert(1, {'type': 'user', 'synthetic_reason': 'unknown',
                            'content': [{'type': 'text', 'text': CONTINUITY_PREFIX}]})
            write_rows(source / 'chat_history.jsonl', chat)
            later = root / 'later-export'
            export(source, later)
            self.assertFalse((later / '2026-08-06.md').exists())

    def test_candidate_publishes_final_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'session'
            source.mkdir()
            chat = [{'type': 'system', 'content': 'Synthetic instructions'}]
            updates = []
            for index, stamp in enumerate((1786000000, 1786086400)):
                chat.extend([
                    {'type': 'user', 'prompt_index': index,
                     'content': [{'type': 'text', 'text': f'Question {index}'}]},
                    {'type': 'assistant', 'content': f'Answer {index}'},
                ])
                for row in (
                    event('user_message_chunk', _meta={'promptIndex': index},
                          content={'type': 'text', 'text': f'Question {index}'}),
                    event('agent_message_chunk', content={'type': 'text', 'text': f'Answer {index}'}),
                    event('turn_completed'),
                ):
                    row['timestamp'] = stamp
                    updates.append(row)
            write_rows(source / 'chat_history.jsonl', chat)
            write_rows(source / 'updates.jsonl', updates)
            (source / 'summary.json').write_text(json.dumps({
                'chat_format_version': 1, 'info': {'id': 'synthetic'},
                'grok_home': str(root),
            }), encoding='utf-8')
            summaries = root / 'summaries.md'
            summaries.write_text('## Period of Aug 6, 2026\n\nSynthetic history.', encoding='utf-8')
            output = root / 'output'
            report = build_v3_candidate(source, output, summaries)
            self.assertEqual(Path(report['compacted_copy']), output / 'compacted/session')
            self.assertEqual(read_rows(Path(report['compacted_copy']) / 'chat_history.jsonl')[-2:],
                             chat[-2:])
            self.assertTrue((output / 'manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
