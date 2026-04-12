#!/usr/bin/env python3

import argparse
import copy
import hashlib
import json
import os
import pathlib
import re
import sys

from lineage import (
    build_compaction_manifest,
    describe_lineage,
    extract_checkpoint_provenance,
    infer_source_kind,
)


SESSION_ROOT = pathlib.Path.home() / ".codex" / "sessions"
DEFAULT_OUTPUT_ROOT = pathlib.Path("/home/marcos/apps-codex/_exports/sessions")
AGENTS_PREFIX = "# AGENTS.md instructions for "
AGENTS_PLACEHOLDER = (
    "[Compacted duplicate AGENTS instructions. "
    "Authoritative copy remains in turn_context.user_instructions and workspace AGENTS.md.]"
)
PATCH_PLACEHOLDER = "[Compacted tool input"
OUTPUT_PLACEHOLDER = "[Compacted tool output"
REASONING_PLACEHOLDER = "[Compacted agent reasoning"
MAX_COMPACTED_REPLACEMENT_HISTORY = 8
WORKSPACE_ROOT = pathlib.Path("/home/marcos/apps-codex")
STOPWORDS = {
    "about", "after", "again", "also", "been", "being", "build", "could", "current",
    "does", "done", "each", "from", "have", "into", "just", "keep", "later", "local",
    "make", "more", "most", "need", "next", "only", "optional", "over", "same", "some", "still", "that", "their",
    "them", "then", "there", "they", "this", "turn", "used", "user", "using", "want",
    "what", "when", "will", "with", "work", "your", "changed",
}
TOPIC_STOPWORDS = STOPWORDS | {
    "background", "behavior", "before", "cache", "content", "core", "default", "directly", "extension", "first", "live",
    "message", "messages", "path",
}
SIGNAL_PATTERNS = (
    re.compile(r"\b[0-9a-f]{7,40}\b"),
    re.compile(r"\b(pushed|commit|committed|failed|passed|verified|risk|pending|phase|fix|fixed)\b", re.I),
    re.compile(r"`[^`]+`"),
    re.compile(r"/home/marcos/"),
)
GOAL_HINTS = (
    "please ", "implement", "look into", "test", "review", "update", "create", "fix",
    "i want", "we need", "goal", "phase", "roadmap",
)
CONSTRAINT_HINTS = (
    "do not", "don't", "never", "avoid", "prefer", "use plain english",
    "minimal", "alpine", "kiss", "no fallback", "no docker", "must", "shouldn't",
)
RESULT_HINTS = (
    "pushed", "commit", "committed", "verified", "passed", "failed", "succeeded",
    "fixed", "working", "landed", "implemented", "result", "current state", "what changed",
)
TOOL_RESULT_HINTS = (
    "pushed", "commit", "committed", "build passed", "py_compile", "verified",
    "smoke", "updated the following files", "process exited with code",
)
RISK_HINTS = (
    "risk", "pending", "blocked", "caveat", "issue", "problem", "failed", "instability",
    "unknown", "not yet", "still needs", "warning",
)
NEXT_HINTS = (
    "next", "follow-up", "later", "optional", "can now", "i can", "pending",
    "next step", "good next tasks",
)
STATE_HINTS = (
    "current state", "current repo state", "what is now true", "repo state", "current path",
    "current branch", "active", "preserved", "stopped", "running",
)
PROGRESS_HINTS = (
    "i’m going to", "i'm going to", "i am going to", "next i’m", "next i'm",
    "i’m now", "i'm now", "first i’ll", "first i'll", "i'll first", "i will first",
)
STATUS_LINE_PATTERNS = (
    re.compile(r"^process exited with code \d+", re.I),
    re.compile(r"^success[.:]?", re.I),
    re.compile(r"updated the following files", re.I),
    re.compile(r"\bcommit\b", re.I),
    re.compile(r"\bpushed\b", re.I),
    re.compile(r"\bfailed\b", re.I),
    re.compile(r"\bfatal:\b", re.I),
    re.compile(r"\berror:\b", re.I),
)
CODEY_LINE_HINTS = (
    "const ", "let ", "var ", "import ", "export ", "return ", "throw new ",
    "=>", "{", "}", ");", "try {", "} catch", "console.", "setstate(", "useState(",
)
IGNORE_MESSAGE_HINTS = (
    "<subagent_notification>",
    "{\"agent_id\":",
    "\"status\":{\"completed\":",
)
SNIPPET_MAX_CHARS = 220


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create conservative compacted copies of Codex rollout JSONL sessions."
    )
    parser.add_argument("session", nargs="?", help="Path to a rollout JSONL file.")
    parser.add_argument(
        "--profile",
        choices=("safe", "resume"),
        default="safe",
        help="Compaction profile. 'safe' keeps full turn structure and only trims bulky fields. 'resume' emits checkpointed compacted spans.",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Use the latest rollout file under ~/.codex/sessions.",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Root directory for original/compacted/report outputs.",
    )
    parser.add_argument(
        "--max-tool-output-chars",
        type=int,
        default=400,
        help="Keep at most this many chars of tool output payloads.",
    )
    parser.add_argument(
        "--max-tool-input-chars",
        type=int,
        default=400,
        help="Keep at most this many chars of bulky custom tool inputs.",
    )
    parser.add_argument(
        "--max-reasoning-chars",
        type=int,
        default=240,
        help="Keep at most this many chars of agent_reasoning text.",
    )
    parser.add_argument(
        "--emit-compacted-spans",
        action="store_true",
        help="Collapse older turns into one native-style compacted turn.",
    )
    parser.add_argument(
        "--keep-last-turns",
        type=int,
        default=6,
        help="When emitting compacted spans, keep this many most recent turns intact.",
    )
    parser.add_argument(
        "--max-replacement-records",
        type=int,
        default=24,
        help="Maximum message records to preserve inside synthetic compacted replacement_history.",
    )
    parser.add_argument(
        "--show-lineage",
        action="store_true",
        help="Print lineage/provenance information for the input session and exit.",
    )
    return parser.parse_args()


def apply_profile_defaults(args: argparse.Namespace) -> None:
    if args.profile == "resume":
        args.emit_compacted_spans = True
        if args.keep_last_turns == 6:
            args.keep_last_turns = 10
        if args.max_replacement_records == 24:
            args.max_replacement_records = 24
    elif args.profile == "safe":
        if not args.emit_compacted_spans:
            args.keep_last_turns = max(args.keep_last_turns, 6)


def latest_session(root: pathlib.Path) -> pathlib.Path:
    files = sorted(root.rglob("rollout-*.jsonl"))
    if not files:
        raise SystemExit(f"No rollout JSONL files found under {root}")
    return max(files, key=lambda p: p.stat().st_mtime)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def shorten(text: str, max_chars: int, label: str) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    kept = text[:max_chars].rstrip()
    compacted = f"{kept}\n... {label}; original length={len(text)} chars]"
    return compacted, True


def compact_content_text(text: str, state: dict[str, int]) -> str:
    if text.startswith(AGENTS_PREFIX):
        state["duplicated_instruction_messages"] += 1
        return AGENTS_PLACEHOLDER
    return text


def default_context_files() -> list[pathlib.Path]:
    candidates = [
        WORKSPACE_ROOT / "HANDOFF.md",
        WORKSPACE_ROOT / "MEMU_Architecture_Onboarding.md",
        WORKSPACE_ROOT / "ROADMAP_REFERENCE.md",
    ]
    return [p for p in candidates if p.exists()]


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9_./-]{4,}", text.lower()):
        tok = raw.strip("._-/")
        if len(tok) < 4:
            continue
        if tok in STOPWORDS:
            continue
        tokens.append(tok)
    return tokens


def load_context_terms() -> set[str]:
    terms: dict[str, int] = {}
    for path in default_context_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for tok in tokenize(text):
            terms[tok] = terms.get(tok, 0) + 1
    ranked = sorted(terms.items(), key=lambda item: (-item[1], item[0]))
    return {term for term, _count in ranked[:250]}


def message_text_from_payload(payload: dict) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    texts: list[str] = []
    for entry in content:
        if not isinstance(entry, dict):
            continue
        for key in ("text", "input_text", "output_text"):
            value = entry.get(key)
            if isinstance(value, str) and value:
                texts.append(value)
    return "\n".join(texts)


def normalize_text(text: str) -> str:
    parts: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^[#>*`\-\d\.\)\(\s]+", "", line).strip()
        if line:
            parts.append(line)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def short_snippet(text: str, max_chars: int = SNIPPET_MAX_CHARS) -> str:
    text = normalize_text(text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in phrases)


def looks_codey(text: str) -> bool:
    lower = text.lower()
    return any(hint in lower for hint in CODEY_LINE_HINTS)


def should_ignore_message(text: str) -> bool:
    lower = text.lower()
    return any(hint in lower for hint in IGNORE_MESSAGE_HINTS)


def extract_tool_output_summary(text: str) -> str:
    raw = text
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            nested = parsed.get("output")
            if isinstance(nested, str) and nested.strip():
                raw = nested
    except Exception:
        pass

    interesting: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if looks_codey(s):
            continue
        if any(pattern.search(s) for pattern in STATUS_LINE_PATTERNS):
            interesting.append(s)
        elif s.startswith(("M ", "A ", "D ", "?? ")):
            interesting.append(s)
        elif "/home/marcos/" in s and ("updated" in s.lower() or "expected:" in s.lower()):
            interesting.append(s)
        if len(interesting) >= 3:
            break
    if interesting:
        return short_snippet(" | ".join(interesting))
    fallback_lines: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or looks_codey(s):
            continue
        fallback_lines.append(s)
        if len(fallback_lines) >= 2:
            break
    if fallback_lines:
        return short_snippet(" | ".join(fallback_lines))
    return ""


def score_message_payload(payload: dict, context_terms: set[str]) -> tuple[int, set[str]]:
    text = message_text_from_payload(payload)
    tokens = set(tokenize(text))
    overlap = tokens & context_terms
    score = min(len(overlap), 10) * 4
    role = payload.get("role")
    if role == "user":
        score += 30
        if contains_any(text, GOAL_HINTS):
            score += 30
        if contains_any(text, CONSTRAINT_HINTS):
            score += 40
    elif role == "assistant":
        score += 20
        if contains_any(text, RESULT_HINTS):
            score += 35
        if contains_any(text, RISK_HINTS):
            score += 25
        if contains_any(text, STATE_HINTS):
            score += 20
        if contains_any(text, NEXT_HINTS):
            score += 10
        if contains_any(text, PROGRESS_HINTS):
            score -= 30

    for pattern in SIGNAL_PATTERNS:
        if pattern.search(text):
            score += 20

    return score, overlap


def split_session_objects(records: list[dict]) -> tuple[list[dict], list[list[dict]]]:
    header: list[dict] = []
    turns: list[list[dict]] = []
    current: list[dict] | None = None
    for obj in records:
        if obj.get("type") == "event_msg" and obj.get("payload", {}).get("type") == "task_started":
            if current is not None:
                turns.append(current)
            current = [obj]
            continue
        if current is None:
            header.append(obj)
        else:
            current.append(obj)
    if current is not None:
        turns.append(current)
    return header, turns


def core_format_warnings(records: list[dict]) -> list[str]:
    has_event_msg = False
    has_task_started = False
    has_response_item = False
    has_message_item = False

    for obj in records:
        item_type = obj.get("type")
        if item_type == "event_msg":
            has_event_msg = True
            payload = obj.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "task_started":
                has_task_started = True
        elif item_type == "response_item":
            has_response_item = True
            payload = obj.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "message":
                has_message_item = True

    warnings: list[str] = []
    if not has_event_msg:
        warnings.append("Missing top-level type=event_msg records (possible Codex format drift).")
    if not has_task_started:
        warnings.append("Missing event_msg payload.type=task_started records (turn boundary parsing may degrade).")
    if not has_response_item:
        warnings.append("Missing top-level type=response_item records (message/tool compaction may degrade).")
    if not has_message_item:
        warnings.append("Missing response_item payload.type=message records (semantic scoring/checkpoint quality may degrade).")
    return warnings


def selected_replacement_history(old_turns: list[list[dict]], context_terms: set[str], max_records: int) -> tuple[list[dict], list[str]]:
    scored: list[tuple[int, int, int, dict, set[str]]] = []
    total_turns = max(len(old_turns), 1)
    for turn_idx, turn in enumerate(old_turns):
        recency_bonus = int(((turn_idx + 1) / total_turns) * 30)
        first_user: dict | None = None
        last_assistant: dict | None = None
        important: list[tuple[int, dict, set[str]]] = []
        for obj in turn:
            if obj.get("type") != "response_item":
                continue
            payload = obj.get("payload", {})
            if payload.get("type") != "message":
                continue
            role = payload.get("role")
            score, overlap = score_message_payload(payload, context_terms)
            if role == "user" and first_user is None:
                first_user = copy.deepcopy(payload)
            if role == "assistant":
                last_assistant = copy.deepcopy(payload)
            if score >= 40:
                important.append((score + recency_bonus, copy.deepcopy(payload), overlap))
        if first_user is not None:
            score, overlap = score_message_payload(first_user, context_terms)
            if score + recency_bonus >= 45:
                scored.append((max(score, 25) + recency_bonus, turn_idx, len(scored), first_user, overlap))
        if last_assistant is not None:
            score, overlap = score_message_payload(last_assistant, context_terms)
            if score + recency_bonus >= 45:
                scored.append((max(score, 30) + recency_bonus, turn_idx, len(scored), last_assistant, overlap))
        for score, payload, overlap in important:
            scored.append((score, turn_idx, len(scored), payload, overlap))

    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    chosen: list[dict] = []
    seen_serialized: set[str] = set()
    topic_counts: dict[str, int] = {}
    for score, _turn_idx, _order, payload, overlap in scored:
        if len(chosen) >= max_records:
            break
        serial = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if serial in seen_serialized:
            continue
        seen_serialized.add(serial)
        chosen.append(payload)
        for term in overlap:
            if term in TOPIC_STOPWORDS:
                continue
            topic_counts[term] = topic_counts.get(term, 0) + 1

    ranked_topics = sorted(topic_counts.items(), key=lambda item: (-item[1], item[0]))
    strong_topics = [term for term, count in ranked_topics if count >= 2]
    topics = (strong_topics or [term for term, _count in ranked_topics])[:8]
    return chosen, topics


def extract_checkpoint(old_turns: list[list[dict]], context_terms: set[str], topics: list[str]) -> dict[str, object]:
    checkpoint: dict[str, object] = {
        "kind": "rollout_checkpoint",
        "topics": topics,
        "goals": [],
        "constraints": [],
        "tool_results": [],
        "task_state": [],
        "risks": [],
        "next_actions": [],
    }

    goals = checkpoint["goals"]
    constraints = checkpoint["constraints"]
    tool_results = checkpoint["tool_results"]
    task_state = checkpoint["task_state"]
    risks = checkpoint["risks"]
    next_actions = checkpoint["next_actions"]

    seen_snippets: set[str] = set()

    def append_checkpoint(items: list[str], value: str, max_items: int) -> None:
        if not value or value in seen_snippets or len(items) >= max_items:
            return
        items.append(value)
        seen_snippets.add(value)

    state_candidates: list[str] = []

    for turn in reversed(old_turns):
        for obj in reversed(turn):
            if obj.get("type") != "response_item":
                continue
            payload = obj.get("payload", {})
            payload_type = payload.get("type")
            role = payload.get("role")

            if payload_type == "message":
                text = message_text_from_payload(payload)
                if should_ignore_message(text):
                    continue
                snippet = short_snippet(text)
                if not snippet:
                    continue
                score, overlap = score_message_payload(payload, context_terms)
                overlap_signal = bool(overlap)

                if role == "user":
                    if text.startswith(AGENTS_PREFIX):
                        continue
                    if contains_any(snippet, CONSTRAINT_HINTS):
                        append_checkpoint(constraints, snippet, max_items=8)
                    elif contains_any(snippet, GOAL_HINTS) or overlap_signal:
                        append_checkpoint(goals, snippet, max_items=8)
                    if contains_any(snippet, NEXT_HINTS):
                        append_checkpoint(next_actions, snippet, max_items=8)

                elif role == "assistant":
                    if contains_any(snippet, PROGRESS_HINTS):
                        if contains_any(snippet, NEXT_HINTS):
                            append_checkpoint(next_actions, snippet, max_items=8)
                        continue
                    if contains_any(snippet, TOOL_RESULT_HINTS):
                        append_checkpoint(tool_results, snippet, max_items=8)
                    if contains_any(snippet, RISK_HINTS):
                        append_checkpoint(risks, snippet, max_items=8)
                    if contains_any(snippet, NEXT_HINTS):
                        append_checkpoint(next_actions, snippet, max_items=8)
                    if contains_any(snippet, STATE_HINTS) or contains_any(snippet, RESULT_HINTS) or (score >= 40 and overlap_signal):
                        append_checkpoint(task_state, snippet, max_items=8)
                    elif score >= 30:
                        state_candidates.append(snippet)

            elif payload_type in ("function_call_output", "custom_tool_call_output"):
                summary = extract_tool_output_summary(str(payload.get("output") or ""))
                if summary and (
                    contains_any(summary, RESULT_HINTS)
                    or contains_any(summary, RISK_HINTS)
                    or any(pattern.search(summary) for pattern in SIGNAL_PATTERNS)
                ):
                    append_checkpoint(tool_results, summary, max_items=8)

    if not task_state:
        for snippet in reversed(state_candidates):
            append_checkpoint(task_state, snippet, max_items=6)

    return checkpoint


def format_checkpoint_message(checkpoint: dict[str, object], summary: str) -> str:
    payload = dict(checkpoint)
    payload["summary"] = summary
    return "ROLL_OUT_CHECKPOINT\n" + json.dumps(payload, indent=2, ensure_ascii=False)


def extract_checkpoint_preview(compacted_records: list[dict]) -> dict[str, object] | None:
    for obj in compacted_records:
        if obj.get("type") != "compacted":
            continue
        message = str(obj.get("payload", {}).get("message") or "")
        if not message.startswith("ROLL_OUT_CHECKPOINT\n"):
            continue
        try:
            checkpoint = json.loads(message.split("\n", 1)[1])
        except Exception:
            return None
        preview: dict[str, object] = {"summary": checkpoint.get("summary"), "topics": checkpoint.get("topics", [])}
        provenance = checkpoint.get("provenance")
        if isinstance(provenance, dict):
            preview["provenance"] = provenance
        for key in ("goals", "constraints", "tool_results", "task_state", "risks", "next_actions"):
            value = checkpoint.get(key)
            if isinstance(value, list):
                preview[key] = value[:3]
                preview[f"{key}_count"] = len(value)
        return preview
    return None


def synthetic_compacted_turn(
    old_turns: list[list[dict]],
    context_terms: set[str],
    args: argparse.Namespace,
    state: dict[str, int],
    *,
    source: pathlib.Path,
    source_sha256: str,
    original_line_count: int,
) -> list[dict]:
    replacement_history, topics = selected_replacement_history(old_turns, context_terms, args.max_replacement_records)
    last_obj = old_turns[-1][-1]
    timestamp = str(last_obj.get("timestamp") or "")
    old_record_count = sum(len(turn) for turn in old_turns)
    turn_id_seed = (
        f"{source_sha256}:{args.profile}:{len(old_turns)}:{old_record_count}:"
        f"{args.keep_last_turns}:{args.max_replacement_records}"
    )
    turn_id = "semantic-compacted-" + hashlib.sha256(turn_id_seed.encode("utf-8")).hexdigest()[:16]
    summary = (
        f"Compacted {len(old_turns)} older turns ({old_record_count} records). "
        f"Preserved {len(replacement_history)} high-value message records."
    )
    if topics:
        summary += f" Topics: {', '.join(topics)}."
    summary += " Current project truth should come from HANDOFF.md and the repo."
    checkpoint = extract_checkpoint(old_turns, context_terms, topics)
    parent_provenance = extract_checkpoint_provenance(source)
    parent_chain_depth = 0
    if parent_provenance:
        try:
            parent_chain_depth = int(parent_provenance.get("ancestor_depth") or 0) + 1
        except Exception:
            parent_chain_depth = 1
    checkpoint["provenance"] = {
        "source_path": str(source),
        "source_sha256": source_sha256,
        "generated_at": timestamp,
        "profile": args.profile,
        "source_kind": infer_source_kind(source),
        "keep_last_turns": args.keep_last_turns,
        "max_replacement_records": args.max_replacement_records,
        "source_line_count": original_line_count,
        "compacted_turn_count": len(old_turns),
        "compacted_record_count": old_record_count,
        "replacement_history_count": len(replacement_history),
        "ancestor_depth": parent_chain_depth,
        "tool": "compact_codex_session.py",
    }
    checkpoint_message = format_checkpoint_message(checkpoint, summary)

    state["semantic_turns_compacted"] += len(old_turns)
    state["semantic_records_replaced"] += old_record_count
    state["semantic_replacement_records"] += len(replacement_history)
    state["checkpoint_sections_emitted"] += 1

    return [
        {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {
                "type": "task_started",
                "turn_id": turn_id,
                "model_context_window": 380000,
                "collaboration_mode_kind": "default",
            },
        },
        {
            "timestamp": timestamp,
            "type": "compacted",
            "payload": {
                "message": checkpoint_message,
                "replacement_history": replacement_history,
            },
        },
        {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {"type": "context_compacted"},
        },
        {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": turn_id,
                "last_agent_message": None,
            },
        },
    ]


def compact_record(obj: dict, args: argparse.Namespace, state: dict[str, int]) -> dict:
    item = copy.deepcopy(obj)
    item_type = item.get("type")

    if item_type == "response_item":
        payload = item.get("payload", {})
        payload_type = payload.get("type")

        if payload_type == "reasoning":
            if payload.pop("encrypted_content", None) is not None:
                state["reasoning_encrypted_removed"] += 1

        elif payload_type in ("function_call_output", "custom_tool_call_output"):
            output = payload.get("output")
            if isinstance(output, str):
                output, changed = shorten(output, args.max_tool_output_chars, OUTPUT_PLACEHOLDER)
                payload["output"] = output
                if changed:
                    state["tool_outputs_truncated"] += 1

        elif payload_type == "custom_tool_call":
            tool_input = payload.get("input")
            if isinstance(tool_input, str):
                tool_input, changed = shorten(tool_input, args.max_tool_input_chars, PATCH_PLACEHOLDER)
                payload["input"] = tool_input
                if changed:
                    state["tool_inputs_truncated"] += 1
        elif payload_type == "function_call":
            arguments = payload.get("arguments")
            if isinstance(arguments, str):
                arguments, changed = shorten(arguments, args.max_tool_input_chars, PATCH_PLACEHOLDER)
                payload["arguments"] = arguments
                if changed:
                    state["tool_inputs_truncated"] += 1

        elif payload_type == "message":
            content = payload.get("content")
            if isinstance(content, list):
                for entry in content:
                    if not isinstance(entry, dict):
                        continue
                    key = None
                    if "text" in entry:
                        key = "text"
                    elif "input_text" in entry:
                        key = "input_text"
                    elif "output_text" in entry:
                        key = "output_text"
                    if key and isinstance(entry.get(key), str):
                        entry[key] = compact_content_text(entry[key], state)

    elif item_type == "event_msg":
        payload = item.get("payload", {})
        if payload.get("type") == "token_count":
            info = payload.get("info")
            if isinstance(info, dict):
                total = info.get("total_token_usage")
                model_context_window = info.get("model_context_window")
                payload["info"] = {
                    "total_token_usage": total,
                    "model_context_window": model_context_window,
                }
            payload.pop("rate_limits", None)
        if payload.get("type") == "agent_reasoning":
            text = payload.get("text")
            if isinstance(text, str):
                text, changed = shorten(text, args.max_reasoning_chars, REASONING_PLACEHOLDER)
                payload["text"] = text
                if changed:
                    state["agent_reasoning_truncated"] += 1
    elif item_type == "turn_context":
        payload = item.get("payload", {})
        if isinstance(payload, dict):
            user_instructions = payload.get("user_instructions")
            if isinstance(user_instructions, str):
                payload["user_instructions"] = compact_content_text(user_instructions, state)
            summary = payload.get("summary")
            if isinstance(summary, str):
                summary, _ = shorten(summary, args.max_reasoning_chars, REASONING_PLACEHOLDER)
                payload["summary"] = summary
            collaboration = payload.get("collaboration_mode")
            if isinstance(collaboration, dict):
                settings = collaboration.get("settings")
                if isinstance(settings, dict):
                    dev_instructions = settings.get("developer_instructions")
                    if isinstance(dev_instructions, str):
                        dev_instructions, _ = shorten(dev_instructions, args.max_reasoning_chars, REASONING_PLACEHOLDER)
                        settings["developer_instructions"] = dev_instructions
    elif item_type == "compacted":
        payload = item.get("payload", {})
        if isinstance(payload, dict):
            history = payload.get("replacement_history")
            if isinstance(history, list):
                payload["replacement_history"] = history[:MAX_COMPACTED_REPLACEMENT_HISTORY]
                for rec in payload["replacement_history"]:
                    if not isinstance(rec, dict):
                        continue
                    content = rec.get("content")
                    if not isinstance(content, list):
                        continue
                    for entry in content:
                        if not isinstance(entry, dict):
                            continue
                        key = "text" if "text" in entry else "input_text" if "input_text" in entry else "output_text" if "output_text" in entry else None
                        if key and isinstance(entry.get(key), str):
                            entry[key], _ = shorten(entry[key], args.max_reasoning_chars, REASONING_PLACEHOLDER)

    return item


def validate_jsonl(path: pathlib.Path) -> dict[str, int]:
    line_count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_count, line in enumerate(handle, 1):
            json.loads(line)
    return {"line_count": line_count}


def validate_jsonl_bytes(data: bytes) -> dict[str, int]:
    line_count = 0
    for line_count, line in enumerate(data.splitlines(), 1):
        json.loads(line)
    return {"line_count": line_count}


def relative_session_path(path: pathlib.Path) -> pathlib.Path:
    try:
        return path.resolve().relative_to(SESSION_ROOT.resolve())
    except Exception:
        return pathlib.Path(path.name)


def write_thread_marker(
    source: pathlib.Path,
    compacted_copy: pathlib.Path,
    report_path: pathlib.Path,
    manifest_path: pathlib.Path,
    profile: str,
    source_sha256: str,
) -> pathlib.Path | None:
    thread_id = os.environ.get("CODEX_THREAD_ID")
    if not thread_id:
        return None
    marker_path = pathlib.Path.home() / ".codex" / "session-survivor" / "thread-markers.jsonl"
    marker_key_dir = pathlib.Path.home() / ".codex" / "session-survivor" / "thread-marker-keys"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_key_dir.mkdir(parents=True, exist_ok=True)
    dedup_key = f"{thread_id}:{source_sha256}:{profile}"
    key_hash = hashlib.sha256(dedup_key.encode("utf-8")).hexdigest()
    key_path = marker_key_dir / key_hash
    if key_path.exists():
        return marker_path
    marker = {
        "dedup_key": dedup_key,
        "thread_id": thread_id,
        "profile": profile,
        "source_sha256": source_sha256,
        "source": str(source),
        "compacted_copy": str(compacted_copy),
        "report_path": str(report_path),
        "manifest_path": str(manifest_path),
    }
    with marker_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(marker, ensure_ascii=False, separators=(",", ":")) + "\n")
    key_path.write_text(dedup_key + "\n", encoding="utf-8")
    return marker_path


def main() -> int:
    args = parse_args()
    apply_profile_defaults(args)
    if args.latest:
        source = latest_session(SESSION_ROOT)
    elif args.session:
        source = pathlib.Path(args.session).expanduser().resolve()
    else:
        raise SystemExit("Provide a session path or use --latest")

    if not source.exists():
        raise SystemExit(f"Session file not found: {source}")

    if args.show_lineage:
        print(json.dumps(describe_lineage(source), indent=2, ensure_ascii=False))
        return 0

    output_root = pathlib.Path(args.output_root).expanduser().resolve()
    rel = relative_session_path(source)
    original_copy = output_root / "original" / rel
    compacted_copy = output_root / "compacted" / rel
    report_path = output_root / "reports" / rel.with_suffix(".report.json")

    original_bytes = source.read_bytes()
    original_copy.parent.mkdir(parents=True, exist_ok=True)
    compacted_copy.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    original_copy.write_bytes(original_bytes)
    original_sha256 = sha256_bytes(original_bytes)
    original_validation = validate_jsonl_bytes(original_bytes)

    state = {
        "reasoning_encrypted_removed": 0,
        "tool_outputs_truncated": 0,
        "tool_inputs_truncated": 0,
        "agent_reasoning_truncated": 0,
        "duplicated_instruction_messages": 0,
        "semantic_turns_compacted": 0,
        "semantic_records_replaced": 0,
        "semantic_replacement_records": 0,
        "checkpoint_sections_emitted": 0,
    }

    records = [json.loads(line) for line in original_bytes.splitlines()]
    format_warnings = core_format_warnings(records)
    for warning in format_warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    if args.emit_compacted_spans:
        header, turns = split_session_objects(records)
        transformed: list[dict] = [compact_record(obj, args, state) for obj in header]
        if len(turns) > args.keep_last_turns:
            old_turns = turns[:-args.keep_last_turns]
            recent_turns = turns[-args.keep_last_turns:]
            context_terms = load_context_terms()
            transformed.extend(
                synthetic_compacted_turn(
                    old_turns,
                    context_terms,
                    args,
                    state,
                    source=source,
                    source_sha256=original_sha256,
                    original_line_count=original_validation["line_count"],
                )
            )
        else:
            recent_turns = turns
        for turn in recent_turns:
            for obj in turn:
                transformed.append(compact_record(obj, args, state))
    else:
        transformed = [compact_record(obj, args, state) for obj in records]

    with compacted_copy.open("w", encoding="utf-8") as dst:
        for compacted in transformed:
            dst.write(json.dumps(compacted, ensure_ascii=False, separators=(",", ":")) + "\n")

    compacted_validation = validate_jsonl(compacted_copy)
    compacted_bytes = compacted_copy.read_bytes()
    compacted_sha256 = sha256_bytes(compacted_bytes)
    generated_at = transformed[-1].get("timestamp") if transformed else None
    manifest_path = output_root / "manifests" / rel.with_suffix(".manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "source": str(source),
        "profile": args.profile,
        "original_copy": str(original_copy),
        "compacted_copy": str(compacted_copy),
        "original_sha256": original_sha256,
        "compacted_sha256": compacted_sha256,
        "original_bytes": len(original_bytes),
        "compacted_bytes": len(compacted_bytes),
        "bytes_saved": len(original_bytes) - len(compacted_bytes),
        "original_lines": original_validation["line_count"],
        "compacted_lines": compacted_validation["line_count"],
        "manifest_path": str(manifest_path),
        "changes": state,
        "policy": {
            "profile": args.profile,
            "emit_compacted_spans": args.emit_compacted_spans,
            "keep_last_turns": args.keep_last_turns,
            "max_replacement_records": args.max_replacement_records,
            "max_tool_output_chars": args.max_tool_output_chars,
            "max_tool_input_chars": args.max_tool_input_chars,
            "max_reasoning_chars": args.max_reasoning_chars,
            "duplicate_instruction_placeholder": AGENTS_PLACEHOLDER,
        },
    }
    if format_warnings:
        report["warnings"] = format_warnings
    checkpoint_preview = extract_checkpoint_preview(transformed)
    if checkpoint_preview is not None:
        report["checkpoint_preview"] = checkpoint_preview

    manifest = build_compaction_manifest(
        source=source,
        original_copy=original_copy,
        compacted_copy=compacted_copy,
        report_path=report_path,
        source_sha256=original_sha256,
        compacted_sha256=compacted_sha256,
        profile=args.profile,
        generated_at=generated_at,
        original_lines=original_validation["line_count"],
        compacted_lines=compacted_validation["line_count"],
        bytes_saved=len(original_bytes) - len(compacted_bytes),
        keep_last_turns=args.keep_last_turns,
        max_replacement_records=args.max_replacement_records,
    )

    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    marker_path = write_thread_marker(
        source=source,
        compacted_copy=compacted_copy,
        report_path=report_path,
        manifest_path=manifest_path,
        profile=args.profile,
        source_sha256=original_sha256,
    )
    if marker_path is not None:
        report["thread_marker_path"] = str(marker_path)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
