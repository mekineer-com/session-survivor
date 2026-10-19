import copy
import unittest
from collections import defaultdict
from types import SimpleNamespace

from compact_claude_session import compact_record, compact_tool_use_result, validate_claude_records


def row(kind, uuid, parent, content):
    return {"type": kind, "uuid": uuid, "parentUuid": parent,
            "message": {"role": kind, "content": content}}


class ClaudeSafePreservationTest(unittest.TestCase):
    def test_agent_settings_are_preserved_unchanged(self):
        for selection in ("fictional-alpha", "fictional-beta", ""):
            record = {"type": "agent-setting", "agentSetting": selection,
                      "sessionId": "fictional"}
            output = compact_record(record, SimpleNamespace(), defaultdict(int))
            self.assertEqual(output, record)
            self.assertIsNot(output, record)

    def test_invocation_families_require_ids_and_ancestry(self):
        for family in ("tool_use", "server_tool_use", "mcp_tool_use"):
            records = [row("user", "u", None, "fictional question"),
                       row("assistant", "a", "u", [{"type": family, "id": "call"}]),
                       row("user", "r", "a", [{"type": "tool_result", "tool_use_id": "call"}])]
            validate_claude_records(records)
            for block, key in ((records[1]["message"]["content"][0], "id"),
                               (records[2]["message"]["content"][0], "tool_use_id")):
                for invalid in (None, "", " ", 42):
                    with self.subTest(family=family, key=key, invalid=invalid):
                        block[key] = invalid
                        with self.assertRaisesRegex(ValueError, "ID"):
                            validate_claude_records(records)
                del block[key]
                with self.assertRaisesRegex(ValueError, "ID"):
                    validate_claude_records(records)
                block[key] = "call"
            records[2]["parentUuid"] = "u"
            with self.assertRaisesRegex(ValueError, "parent chain"):
                validate_claude_records(records)

    def test_identity_and_role_without_rejecting_shells(self):
        prompt = row("user", "u", None, "fictional question")
        shell = row("assistant", "shell", "u", [])
        validate_claude_records([prompt, shell])
        shell["message"]["content"] = [{"type": "thinking", "thinking": "fictional", "signature": "fake"}]
        validate_claude_records([prompt, shell])
        for invalid in (None, "", " ", 42):
            bad = copy.deepcopy(prompt)
            bad["uuid"] = invalid
            with self.assertRaisesRegex(ValueError, "UUID"):
                validate_claude_records([bad])
        bad.pop("uuid")
        with self.assertRaisesRegex(ValueError, "UUID"):
            validate_claude_records([bad])
        for invalid in (None, {}, {"role": "assistant", "content": "fictional"}):
            bad = dict(prompt, message=invalid)
            with self.assertRaisesRegex(ValueError, "role"):
                validate_claude_records([bad])

    def test_replay_shape_and_missing_advisory_refs(self):
        prompt = row("user", "u", None, "fictional question")
        preserved = {"uuids": ["u"], "allUuids": ["absent-advisory"]}
        boundary = {"type": "system", "subtype": "compact_boundary", "uuid": "b",
                    "parentUuid": None, "logicalParentUuid": "u",
                    "compactMetadata": {"preservedMessages": preserved}}
        summary = row("user", "summary", "b", "fictional complete summary")
        summary["isCompactSummary"] = True
        records = [prompt, boundary, summary]
        validate_claude_records(records)
        for key in ("uuids", "allUuids"):
            for invalid in (None, "u", {}, [None], [42], [""], [" "]):
                with self.subTest(key=key, invalid=invalid):
                    preserved[key] = invalid
                    with self.assertRaisesRegex(ValueError, "list of nonempty strings"):
                        validate_claude_records(records)
            preserved[key] = []
            validate_claude_records(records)
            del preserved[key]
            validate_claude_records(records)
        preserved["uuids"] = ["missing-replay"]
        with self.assertRaisesRegex(ValueError, "compact UUID references"):
            validate_claude_records(records)

    def test_payload_trimming_preserves_metadata_and_is_idempotent(self):
        long = "fictional-" * 100
        result = {"stdout": long, "stderr": long, "rawOutputPath": long,
                  "file": {"filePath": long, "content": long, "backupFileName": long},
                  "content": [{"type": "text", "text": long},
                              {"type": "image", "data": long}],
                  "originalFile": long, "oldString": long, "newString": long, "result": long,
                  "filenames": [long], "tool_use_id": long, "unknown": long}
        original = copy.deepcopy(result)
        args = SimpleNamespace(max_tool_output_chars=80, max_file_history_entries=8)
        source = row("user", "u", None, "fictional dialogue")
        source["toolUseResult"] = result
        state = defaultdict(int)
        output = compact_record(source, args, state)["toolUseResult"]
        for key in ("stdout", "stderr", "originalFile", "oldString", "newString", "result"):
            self.assertIn("original length=", output[key])
        self.assertIn("original length=", output["file"]["content"])
        self.assertIn("original length=", output["content"][0]["text"])
        for key in ("rawOutputPath", "filenames", "tool_use_id", "unknown"):
            self.assertEqual(output[key], original[key])
        self.assertEqual(output["file"]["filePath"], long)
        self.assertEqual(output["file"]["backupFileName"], long)
        self.assertEqual(output["content"][1], original["content"][1])
        self.assertEqual(result, original)
        self.assertEqual(state["tool_outputs_truncated"], 8)
        self.assertEqual(compact_tool_use_result(output, 80, state), output)
        self.assertEqual(state["tool_outputs_truncated"], 8)
        self.assertIn("original length=", compact_tool_use_result(long, 80, state))


if __name__ == "__main__":
    unittest.main()
