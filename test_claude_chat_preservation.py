import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import chat_claude_session as chat
from compact_claude_session import validate_claude_records


def row(kind, uid, parent, text="fictional dialogue", **extra):
    message = {"role": kind, "content": text}
    if kind == "assistant":
        message["model"] = "fictional-model"
    return {"type": kind, "uuid": uid, "parentUuid": parent,
            "sessionId": "fictional", "timestamp": "2026-01-01T00:00:00Z",
            "message": message, **extra}


def candidate(records, turns=1):
    with tempfile.TemporaryDirectory(prefix="fictional-chat-preservation-") as directory:
        root = Path(directory)
        source = root / "fictional.jsonl"
        data = "".join(json.dumps(item) + "\n" for item in records)
        source.write_text(data)
        args = SimpleNamespace(session=str(source), output_root=str(root / "out"),
                               max_message_chars=80, safe_tail_turns=turns,
                               max_tail_tool_output_chars=80,
                               max_tail_file_history_entries=8, show_summary=True)
        with patch.object(chat, "parse_args", return_value=args), contextlib.redirect_stdout(io.StringIO()):
            try:
                chat.main()
            except ValueError:
                assert source.read_text() == data
                assert not (root / "out").exists()
                raise
        assert source.read_text() == data
        output = [json.loads(line) for line in (root / "out/compacted/fictional.jsonl").read_text().splitlines()]
        report = json.loads((root / "out/reports/fictional.report.json").read_text())
        validate_claude_records(output)
        return output, report


class ChatPreservationTest(unittest.TestCase):
    def test_detached_dialogue_roots_survive_but_same_root_rewinds_select(self):
        records = [row("user", "archive", None), row("assistant", "archived-reply", "archive"),
                   row("user", "current", None), row("assistant", "abandoned", "current"),
                   row("assistant", "chosen", "current")]
        full_output, _ = candidate(records)
        self.assertEqual([r["uuid"] for r in full_output], ["archive", "archived-reply", "current", "chosen"])
        self.assertNotIn("parentUuid", full_output[2])
        twice, _ = candidate(full_output)
        self.assertEqual(twice, full_output)
        output, _ = candidate(records[2:])
        self.assertEqual([r["uuid"] for r in output], ["current", "chosen"])
        connector = {"type": "system", "subtype": "informational", "uuid": "connector", "parentUuid": None}
        records[0]["parentUuid"] = "connector"
        connected, _ = candidate([connector, *records])
        self.assertEqual(connected, full_output)

    def test_summary_roots_and_repeated_cleanup_preserve_identical_history(self):
        for explicit_null in (False, True):
            archive = row("user", "archive", None, "x" * 500, isCompactSummary=True)
            if not explicit_null:
                archive.pop("parentUuid")
            records = [archive, row("assistant", "older", "archive"), row("user", "current", None)]
            once, _ = candidate(records)
            twice, _ = candidate(once)
            third, _ = candidate(twice)
            self.assertEqual(twice, once)
            self.assertEqual(third, once)
            self.assertEqual(once[0]["message"]["content"], archive["message"]["content"])
        records = [row("user", "old", None), row("assistant", "a", "old"),
                   {"type": "system", "subtype": "compact_boundary", "uuid": "cb",
                    "parentUuid": None, "logicalParentUuid": "a"},
                   row("user", "s", "cb", "continuity", isCompactSummary=True),
                   row("user", "next", "s")]
        once, _ = candidate(records)
        self.assertEqual([r["uuid"] for r in once], ["old", "a", "s", "next"])
        twice, _ = candidate(once)
        third, _ = candidate(twice)
        self.assertEqual(twice, once)
        self.assertEqual(third, once)

    def test_interleaved_archives_do_not_supply_the_active_tail_parent(self):
        records = [row("user", "archive", None), row("user", "current", None),
                   row("assistant", "old-reply", "archive"), row("assistant", "reply", "current")]
        for turns in (0, 1, 3):
            once, report = candidate(records, turns)
            self.assertEqual([r["uuid"] for r in once], ["archive", "current", "old-reply", "reply"])
            self.assertNotIn("parentUuid", once[1])
            self.assertEqual(once[2]["parentUuid"], "archive")
            self.assertEqual(once[3]["parentUuid"], "current")
            twice, _ = candidate(once, turns)
            self.assertEqual(twice, once)
            self.assertEqual(report["changes"]["kept_safe_tail_turns"], min(turns, 1))

    def test_multiple_summaries_title_and_identity_survive_three_cleanups(self):
        records = [{"type": "custom-title", "customTitle": "Fictional Reviewer", "sessionId": "fictional"}]
        for n in range(3):
            records.extend([row("user", f"summary-{n}", None, f"Complete summary {n}: " + "x" * 500,
                                isCompactSummary=True, isVisibleInTranscriptOnly=True),
                            row("assistant", f"reply-{n}", f"summary-{n}")])
        records.extend([row("user", "current", "reply-2"), row("assistant", "answer", "current")])
        for turns in (0, 1, 2):
            once, _ = candidate(records, turns)
            twice, _ = candidate(once, turns)
            third, _ = candidate(twice, turns)
            self.assertEqual(third, twice)
            self.assertEqual(twice, once)
            self.assertEqual(once[0], records[0])
            original = {r["uuid"]: r for r in records if r.get("uuid")}
            self.assertEqual({r["uuid"] for r in once if r.get("uuid")}, set(original))
            for r in once[1:]:
                self.assertEqual(r["sessionId"], "fictional")
                self.assertEqual(r["timestamp"], original[r["uuid"]]["timestamp"])
                self.assertEqual(r["message"]["content"], original[r["uuid"]]["message"]["content"])

    def test_post_leaf_support_keeps_parent_links_with_zero_or_one_tail(self):
        records = [row("user", "u", None), row("assistant", "a", "u"),
                   {"type": "system", "subtype": "informational", "uuid": "notice",
                    "parentUuid": "a", "content": "fictional warning"},
                   {"type": "system", "subtype": "local_command", "uuid": "detail",
                    "parentUuid": "notice", "content": "fictional detail"}]
        for turns in (0, 1):
            output, report = candidate(records, turns)
            self.assertEqual([r["uuid"] for r in output], ["u", "a", "notice", "detail"])
            self.assertEqual(output[-2:], records[-2:])
            self.assertEqual(report["changes"]["kept_safe_tail_turns"], turns)
        with self.assertRaisesRegex(ValueError, "post-leaf support precedes"):
            candidate([records[-2], *records[:2], records[-1]], turns=0)

    def test_zero_tail_keeps_uuidless_backup_metadata(self):
        snapshot = {"type": "file-history-snapshot", "messageId": "a",
                    "snapshot": {"messageId": "a", "trackedFileBackups": {
                        "fictional.txt": {"backupFileName": "fictional-backup", "version": 1}}}}
        records = [row("user", "u", None), row("assistant", "a", "u"), snapshot]
        once, _ = candidate(records, 0)
        twice, _ = candidate(once, 0)
        self.assertEqual(once[-1], snapshot)
        self.assertEqual(twice, once)

    def test_interleaved_archived_tools_are_not_split_by_the_native_tail(self):
        records = [row("user", "archive", None),
                   row("assistant", "call", "archive", [{"type": "tool_use", "id": "call", "input": {}}]),
                   row("user", "current", None),
                   row("user", "result", "call", [{"type": "tool_result", "tool_use_id": "call", "content": "fictional result"},
                                                       {"type": "text", "text": "archived dialogue after result"}]),
                   row("assistant", "answer", "current")]
        for turns in (0, 1, 2, 3):
            once, _ = candidate(records, turns)
            self.assertEqual([r["uuid"] for r in once], ["archive", "current", "result", "answer"])
            self.assertEqual(once[2]["parentUuid"], "archive")
            self.assertEqual(once[2]["message"]["content"], [{"type": "text", "text": "archived dialogue after result"}])
            twice, _ = candidate(once, turns)
            self.assertEqual(twice, once)

    def test_same_root_tool_rewind_does_not_activate_abandoned_result(self):
        call = [{"type": "tool_use", "id": "call", "name": "Bash", "input": {"command": "x" * 200}},
                {"type": "thinking", "thinking": "fictional thought", "signature": "fictional"}]
        result = [{"type": "tool_result", "tool_use_id": "call", "content": "x" * 200}]
        records = [row("user", "u", None), row("assistant", "call-row", "u", call),
                   row("user", "archived-result", "call-row", result),
                   row("user", "chosen-result", "call-row", result)]
        output, _ = candidate(records)
        self.assertEqual([r["uuid"] for r in output], ["u", "call-row", "chosen-result"])
        self.assertEqual(output[1]["message"]["content"], call[:1])
        self.assertEqual(output[2]["parentUuid"], "call-row")
        self.assertIn("Compacted Claude tool result", output[2]["message"]["content"][0]["content"])

    def test_simple_linear_chat_still_compacts_and_invalid_references_refuse(self):
        records = [row("user", "old", None, "x" * 500), row("assistant", "a", "old"),
                   row("user", "next", "a"), row("assistant", "reply", "next")]
        output, report = candidate(records)
        self.assertIn("Compacted Claude chat message", output[0]["message"]["content"])
        self.assertNotIn("native_history_preserved", report["changes"])
        broken = [*records, {"type": "system", "subtype": "compact_boundary", "uuid": "cb",
                            "parentUuid": None, "logicalParentUuid": "reply",
                            "compactMetadata": {"preservedMessages": {"uuids": ["missing"]}}}]
        with self.assertRaisesRegex(ValueError, "compact UUID references"):
            candidate(broken)

if __name__ == "__main__":
    unittest.main()
