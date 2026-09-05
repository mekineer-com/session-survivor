#!/usr/bin/env python3

from collections import defaultdict
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from chat_codex_session import compact_chat_records, compact_old_turn, main


class ChatCodexSessionTest(unittest.TestCase):
    def test_failed_build_publishes_nothing_and_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "synthetic.jsonl"
            rows = []
            for turn in range(2):
                rows.extend(
                    [
                        {"type": "event_msg", "timestamp": f"{turn}-1", "payload": {"type": "task_started"}},
                        {
                            "type": "response_item",
                            "timestamp": f"{turn}-2",
                            "payload": {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": f"Question {turn}"}],
                            },
                        },
                        {
                            "type": "response_item",
                            "timestamp": f"{turn}-3",
                            "payload": {
                                "type": "message",
                                "role": "assistant",
                                "phase": "final_answer",
                                "content": [{"type": "output_text", "text": f"Answer {turn}"}],
                            },
                        },
                        {"type": "event_msg", "timestamp": f"{turn}-4", "payload": {"type": "task_complete"}},
                    ]
                )
            source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            output = root / "output"
            args = SimpleNamespace(
                session=str(source), latest=False, output_root=str(output), max_message_chars=20_000,
                safe_tail_turns=1, max_tool_input_chars=400, max_reasoning_chars=240,
                keep_replacement_history_user_messages=50, show_summary=True, show_lineage=False,
            )
            public_paths = [
                output / "original/synthetic.jsonl",
                output / "compacted/synthetic.jsonl",
                output / "reports/synthetic.report.json",
                output / "manifests/synthetic.manifest.json",
            ]

            with patch("chat_codex_session.parse_args", return_value=args), patch(
                "pathlib.Path.write_text", side_effect=OSError("synthetic disk failure")
            ), self.assertRaises(OSError):
                main()
            self.assertFalse(any(path.exists() for path in public_paths))
            self.assertEqual(list(output.glob(".codex-building-*")), [])

            with patch("chat_codex_session.parse_args", return_value=args), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            self.assertTrue(all(path.exists() for path in public_paths))

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
