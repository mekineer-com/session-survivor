"""One-off scrubber for model contamination inside compacted replacement_history.

Used 2026-05-01 to fix gpt-5.4 artifacts in Aster's session. These functions
reach into compacted.payload.replacement_history — a scope that the core
compactor (compact_codex_session.py --normalize-model) does not touch.
"""

from __future__ import annotations


def scrub_replacement_history_model(record: dict, target_model: str) -> int:
    """Normalize model fields inside compacted replacement_history items."""
    rh = record.get("payload", {}).get("replacement_history", [])
    fixes = 0
    for item in rh:
        if item.get("model") and item["model"] != target_model:
            item["model"] = target_model
            fixes += 1
    return fixes


def scrub_replacement_history_phrases(record: dict, phrases: list[str], replacement: str) -> int:
    """Replace matching phrases in compacted replacement_history text fields."""
    rh = record.get("payload", {}).get("replacement_history", [])
    fixes = 0
    for item in rh:
        content = item.get("content", [])
        if not isinstance(content, list):
            continue
        for entry in content:
            if not isinstance(entry, dict):
                continue
            text = entry.get("text", "")
            if not text:
                continue
            if any(phrase in text for phrase in phrases):
                entry["text"] = replacement
                fixes += 1
    return fixes
