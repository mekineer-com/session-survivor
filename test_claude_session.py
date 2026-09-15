import fcntl
import json
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from chat_claude_session import (
    active_branch_records,
    compact_chat_records,
    is_user_turn_start,
    safe_tail_start_index,
)
from compact_claude_session import (
    backfill_assistant_models,
    compact_file_history_snapshot,
    compact_record,
    publish_artifacts,
    validate_claude_records,
)


def row(kind, row_uuid, parent, content, **extra):
    message = {"role": kind, "content": content}
    message.update(extra.pop("message_extra", {}))
    return {"type": kind, "uuid": row_uuid, "parentUuid": parent,
            "timestamp": "2026-01-01T00:00:00Z", "message": message, **extra}


class ClaudeSessionTest(unittest.TestCase):
    def test_active_branch_and_duplicate_refusal(self):
        records = [row("user", "u", None, "question"),
                   row("assistant", "abandoned", "u", "wrong branch"),
                   row("assistant", "active", "u", "chosen branch")]
        selected = active_branch_records(records, defaultdict(int))
        self.assertEqual([item["uuid"] for item in selected], ["u", "active"])
        with self.assertRaisesRegex(ValueError, "Duplicate Claude UUID"):
            active_branch_records([records[0], records[0]], defaultdict(int))
        sidechain = row("assistant", "side", None, "side task", isSidechain=True)
        selected = active_branch_records([*records, sidechain], defaultdict(int))
        self.assertEqual([item["uuid"] for item in selected], ["u", "active"])

    def test_summary_dialogue_and_model_identity_are_preserved(self):
        summary = "This session is being continued from a previous conversation that ran out of context.\n\nSummary:" + "x" * 5000
        records = [row("assistant", "a", None, "prior", message_extra={"model": "claude-opus-4-6"}),
                   row("user", "s", "a", summary, isCompactSummary=True),
                   row("assistant", "b", "s", "reply", isMeta=True)]
        args = SimpleNamespace(max_message_chars=80)
        output = compact_chat_records(records, records, Path("session.jsonl"), args, defaultdict(int))
        backfill_assistant_models(output, records)
        self.assertEqual(output[1]["message"]["content"], summary)
        self.assertEqual(output[2]["message"]["model"], "claude-opus-4-6")
        self.assertTrue(output[2]["isMeta"])

    def test_native_dialogue_and_tool_boundary(self):
        args = SimpleNamespace(max_tool_output_chars=80, max_file_history_entries=8)
        state = defaultdict(int)
        prompt = "final constraint " + "x" * 500
        self.assertEqual(compact_record(row("user", "u", None, prompt), args, state)["message"]["content"], prompt)
        mixed = row("user", "r", "a", [
            {"type": "tool_result", "tool_use_id": "call", "content": "result"},
            {"type": "text", "text": "continue"},
        ])
        self.assertFalse(is_user_turn_start(mixed))
        tool_use = row("assistant", "a", "u", [
            {"type": "tool_use", "id": "call", "name": "Bash", "input": {}}
        ])
        self.assertEqual(safe_tail_start_index([tool_use, mixed], 1), 0)

    def test_semantic_validation(self):
        with self.assertRaisesRegex(ValueError, "meaningful"):
            validate_claude_records([])
        orphan = row("user", "r", None, [
            {"type": "tool_result", "tool_use_id": "missing", "content": "result"}
        ])
        with self.assertRaisesRegex(ValueError, "orphan"):
            validate_claude_records([orphan])
        with self.assertRaisesRegex(ValueError, "parent cycle"):
            validate_claude_records([row("user", "u", "a", "question"),
                                     row("assistant", "a", "u", "answer")])
        models = [row("assistant", "a", None, "real", message_extra={"model": "claude-opus-4-6"}),
                  row("assistant", "s", "a", "generated", message_extra={"model": "<synthetic>"}),
                  row("assistant", "m", "s", "missing")]
        backfill_assistant_models(models)
        self.assertEqual(models[-1]["message"]["model"], "claude-opus-4-6")

    def test_file_history_keeps_complete_retained_metadata(self):
        tracked = {f"file-{i}": {"version": i, "backupFileName": f"backup-{i}", "realParentDir": "/tmp"}
                   for i in range(9)}
        output = compact_file_history_snapshot({"trackedFileBackups": tracked}, 8, defaultdict(int))
        self.assertEqual(output["trackedFileBackups"]["file-0"], tracked["file-0"])

    def test_publication_refuses_source_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "compacted" / "session.jsonl"
            source.parent.mkdir()
            source.write_text('{}\n')
            with self.assertRaisesRegex(ValueError, "collide"):
                publish_artifacts(source, source.read_bytes(), root,
                                  [(source, b'{}\n')], source)
            self.assertEqual(source.read_text(), '{}\n')

    def test_changed_source_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            source.write_text('{"new":true}\n')
            paths = [root / name for name in ("original.jsonl", "candidate.jsonl", "report.json", "manifest.json")]
            with self.assertRaisesRegex(RuntimeError, "Source changed"):
                publish_artifacts(source, b'{"old":true}\n', root,
                                  [(path, b'{}\n') for path in paths], paths[-1])
            self.assertFalse(any(path.exists() for path in paths))

    def test_publication_lock_refuses_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            source.write_text('{}\n')
            manifest = root / "manifest.json"
            lock = (root / ".claude-publish.lock").open("w")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with self.assertRaisesRegex(RuntimeError, "publication is running"):
                    publish_artifacts(source, source.read_bytes(), root,
                                      [(manifest, b'{}\n')], manifest)
            finally:
                lock.close()


if __name__ == "__main__":
    unittest.main()
