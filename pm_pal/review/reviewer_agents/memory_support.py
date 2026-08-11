"""Helpers for bounded reviewer-side memory assistance."""

from __future__ import annotations

from typing import Any

from .base import EvidenceItem


def build_memory_evidence(
    memory_context: tuple[dict[str, Any], ...],
) -> tuple[EvidenceItem, ...]:
    evidence: list[EvidenceItem] = []
    for item in memory_context:
        if not isinstance(item, dict):
            continue
        evidence.append(
            EvidenceItem(
                source="review_memory",
                title=str(item.get("title", "") or "retrieved memory").strip(),
                snippet=str(item.get("summary", "") or "").strip(),
                ref=str(item.get("memory_id", "") or "").strip(),
                score=float(item.get("score"))
                if item.get("score") is not None
                else None,
                metadata={
                    "memory_type": str(item.get("memory_type", "") or "").strip(),
                    "usage_note": str(item.get("usage_note", "") or "").strip(),
                    "mode": str(item.get("memory_mode", "") or "").strip(),
                },
            )
        )
    return tuple(evidence)


def build_memory_notes(
    memory_context: tuple[dict[str, Any], ...], *, memory_mode: str
) -> tuple[str, ...]:
    if not memory_context:
        return ()
    notes = [
        f"Memory assist mode={memory_mode}. Retrieved memory is advisory only and must be verified against the current PRD.",
    ]
    for item in memory_context:
        if not isinstance(item, dict):
            continue
        memory_id = str(item.get("memory_id", "") or "").strip()
        title = str(item.get("title", "") or "").strip()
        usage_note = str(item.get("usage_note", "") or "").strip()
        notes.append(
            f"Memory {memory_id or title}: {usage_note or 'Use only as a bounded hint.'}"
        )
    return tuple(notes)
