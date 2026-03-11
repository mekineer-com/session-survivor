Nova:

Yes. The class of tool you want is **structured patching**, not free-text editing.

For Codex-style session logs, that means: treat the file as **JSON Lines** — one valid JSON value per line — and have the AI propose **edits to parsed records**, not edits to raw text. That makes it much harder to break syntax. ([jsonlines.org][1])

Best fits:

1. **JSON Patch**
   Have the AI output operations like `test`, `remove`, and `replace`, then apply them with a patch library. JSON Patch is a standard for sequential edits to JSON documents, and Python libraries exist to apply it. This is the cleanest answer to “delete and reword without breaking the structure.” ([RFC Editor][2])

2. **jq**
   Good when the edits are rule-based. `jq` is a lightweight command-line JSON processor for slicing, mapping, and transforming JSON structurally rather than as raw text. ([jqlang.org][3])

3. **Miller**
   Good when you want a JSONL-native streaming tool. Miller explicitly supports **JSON Lines input and output** with `--jsonl`, which suits rollout logs well. ([miller.readthedocs.io][4])

What I did **not** find is a mature **Codex-specific** tool whose job is “shrink stale rollout JSONL in place and safely keep using the same chat.” The Codex-native tool I found, `codex-replay`, is for **viewing** rollout/history JSONL, not editing it. ([GitHub][5])

My recommendation:

* use a **viewer** for inspection
* have the AI produce **JSON Patch** or a **jq** transform
* apply it with a validator
* keep a backup of the original rollout file

That preserves **JSON validity**, though any parser-based tool will usually normalize spacing/key layout rather than preserve byte-for-byte formatting exactly. ([jqlang.org][3])

Omissions caused by brevity:

* I did not find a public, proven workflow showing “people do this to Codex rollout files all the time and it works reliably.”
* I did not reverse-engineer which specific record fields in rollout JSONL are safest to trim.

Exploratory branches:

* Best for AI control: **JSON Patch**
* Best for quick command-line surgery: **jq**
* Best for line-by-line JSONL handling: **Miller**
* Best for inspection before editing: **codex-replay**

[1]: https://jsonlines.org/ "JSON Lines"
[2]: https://www.rfc-editor.org/rfc/rfc6902.html "RFC 6902: JavaScript Object Notation (JSON) Patch"
[3]: https://jqlang.org/ "jq"
[4]: https://miller.readthedocs.io/en/main/reference-main-flag-list/ "List of command-line flags - Miller Dev Documentation"
[5]: https://github.com/zpdldhkdl/codex-replay "GitHub - zpdldhkdl/codex-replay: Turn Codex JSONL sessions into self-contained HTML replays. · GitHub"
