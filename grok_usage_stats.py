#!/usr/bin/env python3
"""Summarize persisted Grok session usage without touching the session."""

import argparse
import json
from collections import defaultdict
from pathlib import Path


TICKS_PER_DOLLAR = 10_000_000_000


def find_session(target, home):
    sessions = home / "sessions"
    if target:
        path = Path(target).expanduser()
        if path.is_dir():
            return path
        matches = list(sessions.glob(f"*/{target}"))
        if len(matches) != 1:
            raise ValueError(f"Expected one session matching {target}, found {len(matches)}")
        return matches[0]
    usage_files = list(sessions.glob("*/*/usage.json"))
    if not usage_files:
        raise ValueError("No Grok usage files found")
    return max(usage_files, key=lambda path: path.stat().st_mtime).parent


def prompt_text(row):
    content = row.get("content", "")
    if isinstance(content, list):
        content = " ".join(block.get("text", "") for block in content
                           if isinstance(block, dict))
    return " ".join(str(content).split())[:100]


def summarize(session, home):
    usage = json.loads((session / "usage.json").read_text())
    summary = json.loads((session / "summary.json").read_text())
    turns = usage["turns"]
    prompts = {}
    native_compactions = 0
    with (session / "chat_history.jsonl").open() as handle:
        for line in handle:
            row = json.loads(line)
            native_compactions += row.get("synthetic_reason") == "compaction_meta"
            if row.get("type") == "user" and isinstance(row.get("prompt_index"), int):
                prompts[row["prompt_index"]] = prompt_text(row)

    model_id = summary.get("current_model_id")
    models_path = home / "models_cache.json"
    models = json.loads(models_path.read_text()).get("models", {}) if models_path.exists() else {}
    context_window = models.get(model_id, {}).get("info", {}).get("context_window")
    latest_single = next((turn for turn in reversed(turns) if turn["modelCalls"] == 1), None)

    days = defaultdict(lambda: {"turns": 0, "calls": 0, "input": 0, "cost": 0})
    for turn in turns:
        day = days[turn["endedAt"][:10]]
        day["turns"] += 1
        day["calls"] += turn["modelCalls"]
        day["input"] += turn["inputTokens"]
        day["cost"] += turn.get("costUsdTicks", 0)

    total = usage["session"]
    return {
        "session_id": usage["sessionId"],
        "first": turns[0]["endedAt"] if turns else None,
        "last": turns[-1]["endedAt"] if turns else None,
        "turns": len(turns),
        "calls": total["modelCalls"],
        "input": total["inputTokens"],
        "output": total["outputTokens"],
        "cache_percent": (100 * total["cachedReadTokens"] / total["inputTokens"]
                          if total["inputTokens"] else 0),
        "cost": total.get("costUsdTicks", 0) / TICKS_PER_DOLLAR,
        "incomplete": total.get("usageIsIncomplete", False),
        "chat_bytes": (session / "chat_history.jsonl").stat().st_size,
        "native_compactions": native_compactions,
        "context_window": context_window,
        "current_input": latest_single["inputTokens"] if latest_single else None,
        "days": dict(sorted(days.items())),
        "worst": sorted(turns, key=lambda turn: turn.get("costUsdTicks", 0), reverse=True)[:5],
        "prompts": prompts,
    }


def render(stats):
    lines = [f"Session: {stats['session_id']}"]
    if stats["first"]:
        lines.append(f"Recorded: {stats['first'][:10]} through {stats['last'][:10]}")
    calls_per_turn = stats["calls"] / stats["turns"] if stats["turns"] else 0
    lines.extend([
        f"Turns/model calls: {stats['turns']} / {stats['calls']} ({calls_per_turn:.1f} calls per turn)",
        f"Input/output: {stats['input'] / 1_000_000:.1f}M / {stats['output'] / 1_000:.1f}k tokens",
        f"Cache reads: {stats['cache_percent']:.1f}%",
        f"Recorded API-equivalent cost: ${stats['cost']:.2f}"
        + (" (usage marked incomplete)" if stats["incomplete"] else ""),
        f"Chat/native compactions: {stats['chat_bytes'] / 1_000_000:.2f} MB / "
        f"{stats['native_compactions']}",
    ])
    if stats["current_input"] and stats["context_window"]:
        percent = 100 * stats["current_input"] / stats["context_window"]
        lines.append(f"Current prompt estimate: {stats['current_input'] / 1_000:.0f}k / "
                     f"{stats['context_window'] / 1_000:.0f}k tokens ({percent:.0f}%)")
        if percent >= 40:
            lines.append("Maintenance: worthwhile after the session exits (prompt is at least 40% of window)")
    lines.append("\nBy day:")
    for day, values in stats["days"].items():
        per_call = values["input"] / values["calls"] if values["calls"] else 0
        lines.append(f"  {day}: {values['turns']} turns, {values['calls']} calls, "
                     f"{per_call / 1_000:.0f}k input/call, "
                     f"${values['cost'] / TICKS_PER_DOLLAR:.2f}")
    lines.append("\nMost expensive turns:")
    for turn in stats["worst"]:
        prompt = stats["prompts"].get(turn["turnNumber"], "<prompt unavailable>")
        lines.append(f"  {turn['turnNumber']}: {turn['modelCalls']} calls, "
                     f"{turn['inputTokens'] / 1_000:.0f}k input, "
                     f"${turn.get('costUsdTicks', 0) / TICKS_PER_DOLLAR:.2f} - {prompt}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", nargs="?", help="Session directory or ID; default is latest")
    parser.add_argument("--grok-home", default="~/.grok")
    args = parser.parse_args()
    home = Path(args.grok_home).expanduser()
    print(render(summarize(find_session(args.session, home), home)))


if __name__ == "__main__":
    main()
