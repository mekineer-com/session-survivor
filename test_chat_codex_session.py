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

from chat_codex_session import compact_chat_records, compact_old_turn, is_continuity_summary_text, main, prune_compacted_replacement_history, strip_compacted_replacement_history
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
    def test_paginated_chat_preserves_native_events_and_ordinals(self) -> None:
        rows = [
            {"ordinal": 40, "type": "event_msg", "payload": {"type": "task_started", "turn_id": "t"}},
            {"ordinal": 42, "timestamp": "2", "type": "response_item", "payload": {
                "type": "message", "id": "native-user", "role": "user", "content": [
                    {"type": "input_text", "text": "  Question\n"}, {"type": "input_text", "text": "Part two"}]},
             "metadata": {"retained_source": {"complete": True}, "future_field": 3}},
            {"ordinal": 43, "type": "event_msg", "payload": {"type": "item_completed", "turn_id": "t",
                "item": {"type": "UserMessage", "id": "u", "content": []}}},
            {"ordinal": 46, "type": "event_msg", "payload": {"type": "turn_aborted", "turn_id": "t"}},
        ]
        state = defaultdict(int)
        output = compact_old_turn(rows, SimpleNamespace(max_message_chars=20_000), state, paginated=True)
        self.assertEqual([row["ordinal"] for row in output], [40, 42, 43, 46])
        self.assertEqual(output[2:], rows[2:])
        self.assertEqual(output[1], rows[1])
        self.assertEqual(state["kept_old_display_events"], 1)
        self.assertEqual(state["synthetic_old_task_complete"], 0)
        self.assertEqual(state["synthetic_old_user_message_events"], 0)

    def test_paginated_invalid_ordinals_publish_nothing(self) -> None:
        for invalid in (None, True, -1, "1", 0):
            with self.subTest(ordinal=invalid), tempfile.TemporaryDirectory() as directory:
                source, _, args, public_paths = candidate_fixture(Path(directory))
                rows = [json.loads(line) for line in source.read_text().splitlines()]
                rows.insert(0, {"type": "session_meta", "payload": {"history_mode": "paginated"}})
                for ordinal, row in enumerate(rows):
                    row["ordinal"] = ordinal
                rows[-1]["ordinal"] = invalid
                source.write_text("".join(json.dumps(row) + "\n" for row in rows))
                with self.assertRaisesRegex(SystemExit, "Invalid paginated ordinal"):
                    run_candidate(args)
                self.assertFalse(any(path.exists() for path in public_paths))

    def test_legacy_compaction_marker_is_not_replayed_as_an_answer(self) -> None:
        rows = [
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "compact"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Internal compact summary"}]}},
            {"type": "compacted", "payload": {"message": "", "replacement_history": []}},
            {"type": "event_msg", "payload": {"type": "context_compacted"}},
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "compact"}},
        ]
        state = defaultdict(int)
        output = compact_old_turn(rows, SimpleNamespace(max_message_chars=20_000), state)
        self.assertIn(rows[3], output)
        self.assertEqual(state["synthetic_old_agent_message_events"], 0)
        self.assertTrue(any(row.get("type") == "response_item" for row in output))
        without_dropped_anchor = [row for row in output if row.get("type") != "compacted"]
        repeated = compact_old_turn(without_dropped_anchor, SimpleNamespace(max_message_chars=20_000), defaultdict(int))
        self.assertFalse(any(row.get("payload", {}).get("type") == "agent_message" for row in repeated))

    def test_paginated_truncation_is_refused(self) -> None:
        row = {"type": "response_item", "ordinal": 9, "payload": {
            "type": "message", "role": "user", "id": "native-user",
            "content": [{"type": "input_text", "text": "word " * 500}]},
            "metadata": {"retained_source": {"complete": True}}}
        state = defaultdict(int)
        with self.assertRaisesRegex(SystemExit, "increase the limit"):
            compact_chat_records([row], SimpleNamespace(max_message_chars=100), state, paginated=True)
        self.assertIn("metadata", row)

    def test_token_accounting_survives_safe_and_chat_tail_cleanup(self) -> None:
        usage = {
            "input_tokens": 12, "cached_input_tokens": 4, "output_tokens": 3,
            "reasoning_output_tokens": 1, "total_tokens": 15,
        }
        row = {
            "type": "event_msg", "timestamp": "1-3",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": usage, "last_token_usage": dict(usage),
                    "model_context_window": 872000, "future_accounting_field": 7,
                },
                "rate_limits": {"stale": True},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            source, _, args, public_paths = candidate_fixture(Path(directory))
            compacted = compact_record(row, args, defaultdict(int))
            self.assertEqual(compacted["payload"]["info"], row["payload"]["info"])
            self.assertNotIn("rate_limits", compacted["payload"])
            self.assertIn("rate_limits", row["payload"])

            rows = [json.loads(line) for line in source.read_text().splitlines()]
            rows.insert(-1, row)
            source.write_text("".join(json.dumps(item) + "\n" for item in rows))
            self.assertEqual(run_candidate(args), 0)
            output = [json.loads(line) for line in public_paths[1].read_text().splitlines()]
            kept = next(item for item in output if item.get("payload", {}).get("type") == "token_count")
            self.assertEqual(kept["payload"]["info"], row["payload"]["info"])
            self.assertNotIn("rate_limits", kept["payload"])

    def test_checkpoint_metadata_stays_paired_with_history(self) -> None:
        history = [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
            for text in ("old", "## Week of Jul 1-7", "recent")
        ]
        metadata = [{"marker": i} for i in range(3)]
        row = {"type": "compacted", "payload": {"replacement_history": history, "replacement_history_metadata": metadata}}
        args = SimpleNamespace(keep_replacement_history_user_messages=1)
        pruned = prune_compacted_replacement_history(row, args, defaultdict(int))
        self.assertEqual(pruned["payload"]["replacement_history"], history[1:])
        self.assertEqual(pruned["payload"]["replacement_history_metadata"], metadata[1:])
        stripped = strip_compacted_replacement_history(row, defaultdict(int))
        self.assertNotIn("replacement_history", stripped["payload"])
        self.assertNotIn("replacement_history_metadata", stripped["payload"])
        row["payload"]["replacement_history_metadata"] = metadata[:1]
        with self.assertRaises(ValueError):
            prune_compacted_replacement_history(row, args, defaultdict(int))

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

    def test_rerun_refuses_to_replace_previous_backup_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, output, args, public_paths = candidate_fixture(Path(directory))
            self.assertEqual(run_candidate(args), 0)
            before = {path: path.read_bytes() for path in public_paths}
            source.write_text(source.read_text(encoding="utf-8").replace("Answer 0", "Changed answer"), encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "fresh output root"):
                run_candidate(args)
            self.assertEqual({path: path.read_bytes() for path in public_paths}, before)
            self.assertEqual(list(output.glob(".building-*")), [])
            args.output_root = str(output / "fresh-run")
            self.assertEqual(run_candidate(args), 0)
            self.assertTrue((Path(args.output_root) / "manifests/synthetic.manifest.json").exists())

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

    def test_paginated_completion_preserves_failure(self) -> None:
        row = {"ordinal": 7, "type": "event_msg", "payload": {
            "type": "task_complete", "turn_id": "failed", "error": {"message": "Fictional failure"}}}
        output = compact_chat_records([row], SimpleNamespace(), defaultdict(int), paginated=True)
        self.assertEqual(output, [row])

    def test_compaction_during_nontext_user_turn_keeps_assistant_replay(self) -> None:
        rows = [
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "image"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_image", "image_url": "data:image/png;base64,fictional"}]}},
            {"type": "compacted", "payload": {"replacement_history": []}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Actual answer"}]}},
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "image"}},
        ]
        output = compact_old_turn(rows, SimpleNamespace(max_message_chars=20_000), defaultdict(int))
        self.assertTrue(any(row.get("payload", {}).get("type") == "agent_message" for row in output))
        repeated = compact_old_turn([row for row in output if row.get("type") != "compacted"],
                                    SimpleNamespace(max_message_chars=20_000), defaultdict(int))
        self.assertTrue(any(row.get("payload", {}).get("type") == "agent_message" for row in repeated))

    def test_bootstrap_user_does_not_turn_compaction_into_an_answer(self) -> None:
        rows = [
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "compact"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "<environment_context>fixture</environment_context>"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Internal summary"}]}},
            {"type": "event_msg", "payload": {"type": "context_compacted"}},
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "compact"}},
        ]
        output = compact_old_turn(rows, SimpleNamespace(max_message_chars=20_000), defaultdict(int))
        self.assertFalse(any(row.get("payload", {}).get("type") == "agent_message" for row in output))

    def test_legacy_user_event_and_model_text_share_the_limit(self) -> None:
        text = "word " * 500
        rows = [
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "long"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": text,
                "text_elements": [{"stale": "range"}], "images": ["fictional-image"]}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": text}]}},
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "long"}},
        ]
        output = compact_old_turn(rows, SimpleNamespace(max_message_chars=100), defaultdict(int))
        event = next(row["payload"] for row in output if row.get("payload", {}).get("type") == "user_message")
        model = next(row["payload"]["content"][0]["text"] for row in output if row["type"] == "response_item")
        self.assertEqual(event["message"], model)
        self.assertNotIn("text_elements", event)
        self.assertEqual(event["images"], ["fictional-image"])
        self.assertIn("text_elements", rows[1]["payload"])

    def test_paginated_manifest_reports_native_replay_and_index_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, _, args, public_paths = candidate_fixture(Path(directory))
            rows = [json.loads(line) for line in source.read_text().splitlines()]
            rows.insert(0, {"type": "session_meta", "payload": {"history_mode": "paginated"}})
            for ordinal, row in enumerate(rows, 30):
                row["ordinal"] = ordinal
            source.write_text("".join(json.dumps(row) + "\n" for row in rows))
            self.assertEqual(run_candidate(args), 0)
            for path in (public_paths[2], public_paths[3]):
                policy = json.loads(path.read_text())["policy"]
                self.assertEqual(policy["history_mode"], "paginated")
                self.assertTrue(policy["display_index_reset_required_after_swap"])
                self.assertEqual(policy["chat_history_synthetic_event_types"], [])
                self.assertEqual(policy["chat_history_dropped_event_types"], [])

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
