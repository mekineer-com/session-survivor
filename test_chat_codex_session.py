#!/usr/bin/env python3

from collections import defaultdict
from types import SimpleNamespace
import unittest

from chat_codex_session import compact_chat_records, compact_old_turn


class ChatCodexSessionTest(unittest.TestCase):
    def test_old_completion_keeps_boundary_without_stale_error(self) -> None:
        row = {
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "turn-1",
                "last_agent_message": None,
                "error": {"message": "You've hit your usage limit."},
            },
        }
        state = defaultdict(int)

        output = compact_chat_records([row], SimpleNamespace(), state)

        self.assertEqual(output[0]["payload"]["type"], "task_complete")
        self.assertNotIn("error", output[0]["payload"])
        self.assertIn("error", row["payload"])
        self.assertEqual(state["dropped_old_task_complete_errors"], 1)

    def test_old_turn_gets_summary_replay_events(self) -> None:
        rows = [
            {"type": "event_msg", "timestamp": "1", "payload": {"type": "task_started", "turn_id": "turn-1"}},
            {
                "type": "response_item",
                "timestamp": "2",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Question"}]},
            },
            {
                "type": "response_item",
                "timestamp": "3",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "Answer"}],
                },
            },
            {"type": "event_msg", "timestamp": "4", "payload": {"type": "task_complete", "turn_id": "turn-1"}},
        ]
        state = defaultdict(int)

        output = compact_old_turn(rows, SimpleNamespace(max_message_chars=20_000), state)
        replay = [row["payload"] for row in output if row.get("type") == "event_msg"]

        self.assertEqual([item["type"] for item in replay], ["task_started", "user_message", "agent_message", "task_complete"])
        self.assertEqual(replay[1]["message"], "Question")
        self.assertEqual(replay[2]["message"], "Answer")


if __name__ == "__main__":
    unittest.main()
