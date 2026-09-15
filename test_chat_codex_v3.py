import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from chat_codex_v3 import parse_weekly_summaries, run
from export_codex_summary_source import ChatRow, collect_rows, write_exports


def turn(day: str, label: str):
    return [
        {"type": "event_msg", "timestamp": f"{day}T01:00:00Z",
         "payload": {"type": "task_started", "turn_id": label}},
        {"type": "response_item", "timestamp": f"{day}T01:00:01Z",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": f"question {label}"}]}},
        {"type": "response_item", "timestamp": f"{day}T01:00:02Z",
         "payload": {"type": "message", "role": "assistant", "phase": "final_answer",
                     "content": [{"type": "output_text", "text": f"answer {label}"}]}},
        {"type": "event_msg", "timestamp": f"{day}T01:00:03Z",
         "payload": {"type": "task_complete", "turn_id": label}},
    ]


def fixture(root: Path, days=("2026-05-01", "2026-05-08")):
    source = root / "session.jsonl"
    rows = [row for index, day in enumerate(days) for row in turn(day, str(index))]
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))
    summary = root / "summaries.md"
    summary.write_text("## Week of May 1\n\nA real summary.\n")
    args = SimpleNamespace(session=str(source), latest=False, summary_file=str(summary),
                           speaker_name="Codex", output_root=str(root / "output"), safe_tail_turns=1,
                           dry_run_only=False, show_summary=False, show_lineage=False)
    return source, summary, args


class ChatCodexV3Test(unittest.TestCase):
    def test_rejects_empty_and_overlapping_summaries(self):
        with self.assertRaisesRegex(ValueError, "non-empty body"):
            parse_weekly_summaries("## Week of May 1\n", 2026)
        with self.assertRaisesRegex(ValueError, "overlap"):
            parse_weekly_summaries("## Week of May 1-7\nfirst\n## Period of May 7-8\nsecond", 2026)

    def test_rejects_noncontiguous_date_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, args = fixture(Path(directory), ("2026-05-01", "2026-05-08", "2026-05-02", "2026-05-10"))
            args.summary_file = str(Path(directory) / "summaries.md")
            Path(args.summary_file).write_text("## Week of May 1-2\n\nSummary.\n")
            with self.assertRaisesRegex(SystemExit, "not contiguous"):
                run(args)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, args = fixture(Path(directory))
            args.dry_run_only = True
            report = run(args)
            self.assertIsNone(report["manifest_path"])
            self.assertFalse(Path(args.output_root).exists())

    def test_refuses_empty_map_and_source_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, summary, args = fixture(root)
            summary.write_text("## Week of Apr 1\n\nWrong period.\n")
            with self.assertRaisesRegex(SystemExit, "No old-history turns matched"):
                run(args)

            summary.write_text("## Week of May 1\n\nA real summary.\n")
            report = run(args)
            candidate = Path(report["compacted_copy"])
            before = candidate.read_bytes()
            args.session = str(candidate)
            with self.assertRaisesRegex(ValueError, "collide"):
                run(args)
            self.assertEqual(candidate.read_bytes(), before)

    def test_cross_midnight_turn_stays_in_start_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                ChatRow(datetime(2026, 5, 1, 23, 59, tzinfo=timezone.utc), "user", 1, 1, "", "question"),
                ChatRow(datetime(2026, 5, 2, 0, 1, tzinfo=timezone.utc), "assistant", 1, 1, "final_answer", "answer"),
            ]
            output = write_exports(rows, root / "source.jsonl", root / "export", "raw", "phase_then_heuristic")
            may1 = (output / "2026-05-01.md").read_text()
            self.assertIn("question", may1)
            self.assertIn("answer", may1)
            self.assertFalse((output / "2026-05-02.md").exists())

    def test_export_uses_task_started_day(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "session.jsonl"
            rows = turn("2026-05-02", "late")
            rows[0]["timestamp"] = "2026-05-01T23:59:59Z"
            source.write_text("".join(json.dumps(row) + "\n" for row in rows))
            exported = collect_rows(source)
            self.assertTrue(all(row.turn_start.date().isoformat() == "2026-05-01" for row in exported))

    def test_publishes_complete_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, args = fixture(Path(directory))
            report = run(args)
            self.assertTrue(Path(report["compacted_copy"]).exists())
            self.assertTrue(Path(report["manifest_path"]).exists())
            self.assertTrue(Path(report["report_path"]).exists())


if __name__ == "__main__":
    unittest.main()
