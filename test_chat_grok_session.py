import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from chat_grok_session import build_candidate, rebuild, validate_tools


def event(kind, **fields):
    return {'params': {'update': {'sessionUpdate': kind, **fields}}}


class GrokChatTest(unittest.TestCase):
    def fixture(self):
        chat = [
            {'type': 'system', 'content': 'Synthetic instructions'},
            {'type': 'user', 'content': [{'type': 'text', 'text': 'Retain this additional instruction'}]},
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


if __name__ == '__main__':
    unittest.main()
