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

from chat_codex_session import compact_chat_records, compact_old_turn, is_continuity_summary_text, main
from compact_codex_session import compact_record


def candidate_fixture(root: Path):
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
    return source, output, args, public_paths


def run_candidate(args) -> int:
    with patch("chat_codex_session.parse_args", return_value=args), redirect_stdout(io.StringIO()):
        return main()


class ChatCodexSessionTest(unittest.TestCase):
    def test_supported_continuity_summary_labels(self) -> None:
        for text in ("[Codex]\n\n## Week of Jul 1-7", "[Aster]\n\n## Week of Jul 1-7",
                     "## Period of Mar 21-May 30", "[Ari]\n\n## Period of Jun 1-30"):
            self.assertTrue(is_continuity_summary_text(text))

    def test_compacted_checkpoint_is_pruned_only_by_chat_policy(self) -> None:
        history = [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "x" * 500}]}
            for _ in range(12)
        ]
        anchor = {"type": "compacted", "payload": {"message": "memory", "replacement_history": history}}
        with tempfile.TemporaryDirectory() as directory:
            source, _, args, public_paths = candidate_fixture(Path(directory))
            rows = [json.loads(line) for line in source.read_text().splitlines()]
            rows.insert(-1, anchor)
            source.write_text("".join(json.dumps(row) + "\n" for row in rows))
            run_candidate(args)
            output = [json.loads(line) for line in public_paths[1].read_text().splitlines()]
            kept = next(row for row in output if row.get("type") == "compacted")
            self.assertEqual(len(kept["payload"]["replacement_history"]), 12)
            self.assertEqual(kept["payload"]["replacement_history"][-1]["content"][0]["text"], "x" * 500)

            call = {"type": "response_item", "payload": {"type": "function_call", "arguments": '{"value":"' + "x" * 500 + '"}'}}
            compacted = compact_record(call, args, defaultdict(int))
            json.loads(compacted["payload"]["arguments"])

    def test_failed_build_publishes_nothing_and_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, output, args, public_paths = candidate_fixture(Path(directory))

            with patch("chat_codex_session.parse_args", return_value=args), patch(
                "pathlib.Path.write_bytes", side_effect=OSError("synthetic disk failure")
            ), self.assertRaises(OSError):
                main()
            self.assertFalse(any(path.exists() for path in public_paths))
            self.assertEqual(list(output.glob(".building-*")), [])

            self.assertEqual(run_candidate(args), 0)
            self.assertTrue(all(path.exists() for path in public_paths))

    def test_failed_rerun_publication_removes_old_manifest_and_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, output, args, public_paths = candidate_fixture(Path(directory))
            self.assertEqual(run_candidate(args), 0)
            source.write_text(source.read_text(encoding="utf-8").replace("Answer 0", "Changed answer"), encoding="utf-8")
            real_replace = Path.replace
            replacements = 0

            def fail_on_third_replace(path, target):
                nonlocal replacements
                replacements += 1
                if replacements == 3:
                    raise OSError("synthetic publication failure")
                return real_replace(path, target)

            with patch.object(Path, "replace", autospec=True, side_effect=fail_on_third_replace), self.assertRaises(OSError):
                run_candidate(args)
            self.assertEqual(replacements, 3)
            self.assertFalse(public_paths[-1].exists())
            self.assertEqual(list(output.glob(".building-*")), [])

            self.assertEqual(run_candidate(args), 0)
            self.assertTrue(all(path.exists() for path in public_paths))

    def test_refuses_to_overwrite_candidate_used_as_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, output, args, public_paths = candidate_fixture(Path(directory))
            self.assertEqual(run_candidate(args), 0)
            candidate = public_paths[1]
            candidate_before = candidate.read_bytes()
            args.session = str(candidate)

            with self.assertRaisesRegex(SystemExit, "overwrite the source"):
                run_candidate(args)

            self.assertEqual(candidate.read_bytes(), candidate_before)

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
