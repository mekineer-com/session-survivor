import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chat_grok_session import build_candidate, rebuild, validate_tools


def event(kind, **fields):
    return {'params': {'update': {'sessionUpdate': kind, **fields}}}


class GrokChatTest(unittest.TestCase):
    def fixture(self):
        chat = [
            {'type': 'system', 'content': 'Synthetic instructions'},
            {'type': 'user', 'content': [{'type': 'text', 'text': 'Retain this additional instruction'}]},
            {'type': 'user', 'prompt_index': 0, 'content': [{'type': 'text', 'text': '<user_query>\nOld question\n</user_query>'}]},
            {'type': 'user', 'prompt_index': 1, 'content': [{'type': 'text', 'text': 'Recent'}]},
            {'type': 'assistant', 'content': 'Recent answer'},
        ]
        updates = [
            event('user_message_chunk', _meta={'promptIndex': 0}, content={'type': 'text', 'text': 'Old question'}),
            event('agent_message_chunk', content={'type': 'text', 'text': 'First '}),
            event('agent_message_chunk', content={'type': 'text', 'text': 'answer'}),
            event('tool_call_update', toolCallId='old', rawOutput='Bulky output'),
            event('agent_message_chunk', content={'type': 'text', 'text': 'After tool'}),
            event('turn_completed'),
            event('user_message_chunk', _meta={'promptIndex': 1}, content={'type': 'text', 'text': 'Recent'}),
            event('turn_completed'),
        ]
        return chat, updates

    def test_verbatim_dialogue_tail_instructions_and_idempotence(self):
        chat, updates = self.fixture()
        before = copy.deepcopy((chat, updates))
        output, slim = rebuild(chat, updates)
        self.assertEqual(output[:2], chat[:2])
        self.assertEqual(output[-2:], chat[-2:])
        self.assertEqual([r['content'] for r in output if r['type'] == 'assistant'],
                         ['First answer', 'After tool', 'Recent answer'])
        self.assertNotIn('rawOutput', slim[3]['params']['update'])
        self.assertEqual((chat, updates), before)
        self.assertEqual(rebuild(output, slim), (output, slim))

    def test_refuse_incomplete_missing_and_nontext_history(self):
        chat, updates = self.fixture()
        for changed in [updates[:-1], updates[1:],
                        [event('user_message_chunk', _meta={'promptIndex': 0},
                               content={'type': 'image', 'data': 'synthetic'}), *updates[1:]]]:
            with self.assertRaises(ValueError):
                rebuild(chat, changed)
        with self.assertRaises(ValueError):
            validate_tools([{'type': 'tool_result', 'tool_call_id': 'orphan'}])

    def test_refuse_open_session_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            session = home / 'session'
            session.mkdir()
            (session / 'summary.json').write_text(json.dumps({
                'chat_format_version': 1, 'info': {'id': 'synthetic'}, 'grok_home': str(home)}))
            (home / 'active_sessions.json').write_text(json.dumps([
                {'session_id': 'synthetic', 'pid': os.getpid()}]))
            with self.assertRaisesRegex(ValueError, 'running process'):
                build_candidate(session, home / 'output')
            self.assertFalse((home / 'output').exists())

    def test_native_machine_interrupt_reminder_and_header_dedup(self):
        chat, updates = self.fixture()
        native = chat[2]
        native['synthetic_reason'] = 'task_completed'
        native['prior_turn_interrupt'] = 'mid_turn_abort'
        native['content'][0]['text'] = '<system-reminder>\nSynthetic task completed\n</system-reminder>'
        reminder = {'type': 'user', 'synthetic_reason': 'system_reminder',
                    'content': [{'type': 'text', 'text': 'The date is now a fictional date.'}]}
        chat.insert(3, reminder)
        output, _ = rebuild(chat, updates)
        self.assertIn(native, output)
        self.assertIn(reminder, output)
        self.assertLess(output.index(reminder), next(i for i,r in enumerate(output) if r.get('prompt_index') == 1))
        chat, updates = self.fixture()
        chat.insert(2, {'type': 'user', 'content': [{'type': 'text', 'text': '<user_query>\nOld question\n</user_query>'}]})
        updates[0]['params']['update']['content']['text'] += '\n'
        output, _ = rebuild(chat, updates)
        self.assertEqual(sum('Old question' in str(row) for row in output), 1)
        chat, updates = self.fixture()
        bare_header = {'type': 'user', 'content': [{'type': 'text', 'text': 'Old question'}]}
        chat.insert(2, bare_header)
        output, _ = rebuild(chat, updates)
        self.assertIn(bare_header, output)
        chat, updates = self.fixture()
        interrupted = chat.pop(2)
        interrupted['prior_turn_interrupt'] = 'mid_turn_abort'
        interrupted['content'][0]['text'] = ('The user interrupted the previous turn:\n'
            '<user_query>\nOld question\n</user_query>\n'
            'Make sure to complete any unfinished tasks from previous turns.')
        output, _ = rebuild(chat, updates, archived_histories=[[interrupted]])
        self.assertIn(interrupted, output)
        with self.assertRaisesRegex(ValueError, 'Missing native user row'):
            rebuild(chat, updates)

    def test_utf8_and_failed_build_can_retry(self):
        from chat_grok_session import write_rows, read_rows
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            chat, updates = self.fixture()
            chat[0]['content'] = 'Synthetic caf\u00e9'
            write_rows(source / 'chat_history.jsonl', chat)
            write_rows(source / 'updates.jsonl', updates)
            self.assertEqual(read_rows(source / 'chat_history.jsonl'), chat)
            (source / 'summary.json').write_text(json.dumps({'chat_format_version': 1,
                'info': {'id': 'synthetic'}, 'grok_home': str(root)}), encoding='utf-8')
            with patch('chat_grok_session.write_rows', side_effect=OSError('synthetic disk failure')):
                with self.assertRaises(OSError):
                    build_candidate(source, root / 'out')
            self.assertFalse((root / 'out').exists())
            build_candidate(source, root / 'out')
            self.assertTrue((root / 'out/manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
