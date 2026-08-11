"""Deterministic long-context trimming tuned for low-capability LLM nodes."""

from __future__ import annotations

import re
from dataclasses import dataclass


_NODE_HINTS: dict[str, tuple[str, ...]] = {
    "parser": ("must", "should", "require", "acceptance", "flow", "rule", "constraint"),
    "planner": ("dependency", "timeline", "owner", "module", "deliver", "phase"),
    "risk": ("risk", "security", "failure", "dependency", "migration", "rollback"),
    "delivery_planning": ("module", "test", "integration", "deployment", "constraint"),
    "reviewer": ("ambiguity", "criteria", "test", "edge case", "scope", "coverage"),
    "reporter": ("summary", "decision", "risk", "coverage", "timeline"),
}


@dataclass(slots=True)
class TrimmedContext:
    """Trimmed text plus metadata about how it was reduced."""

    node_name: str
    text: str
    original_chars: int
    trimmed_chars: int
    was_trimmed: bool
    strategy: str
    chunk_count: int


def _normalize_text(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n")).strip()


def _split_into_chunks(text: str, max_chunk_chars: int) -> list[str]:
    if len(text) <= max_chunk_chars:
        return [text]

    sections = re.split(r"(?m)(?=^#{1,6}\s)|(?=^\d+\.\s)|(?=^- )", text)
    chunks: list[str] = []
    current = ""

    for raw_section in sections:
        section = raw_section.strip()
        if not section:
            continue
        if len(section) > max_chunk_chars:
            for start in range(0, len(section), max_chunk_chars):
                chunks.append(section[start : start + max_chunk_chars].strip())
            continue
        if current and len(current) + len(section) + 2 > max_chunk_chars:
            chunks.append(current.strip())
            current = section
        else:
            current = f"{current}\n\n{section}".strip() if current else section

    if current:
        chunks.append(current.strip())
    return chunks or [text[:max_chunk_chars].strip()]


def _extract_key_lines(chunk: str, node_name: str, max_lines: int) -> list[str]:
    hints = _NODE_HINTS.get(node_name, ())
    lines = [line.strip() for line in chunk.splitlines() if line.strip()]
    scored: list[tuple[int, str]] = []

    for line in lines:
        lower = line.lower()
        score = 0
        if line.startswith("#"):
            score += 5
        if line.startswith(("-", "*")) or re.match(r"^\d+\.", line):
            score += 4
        if any(token in lower for token in hints):
            score += 3
        if any(marker in lower for marker in ("must", "should", "need", "shall")):
            score += 2
        if len(line) <= 140:
            score += 1
        if score > 0:
            scored.append((score, line))

    scored.sort(key=lambda item: (-item[0], lines.index(item[1])))
    unique: list[str] = []
    seen: set[str] = set()
    for _, line in scored:
        if line not in seen:
            seen.add(line)
            unique.append(line)
        if len(unique) >= max_lines:
            break

    if unique:
        return unique

    sentences = re.split(r"(?<=[。！？.!?])\s+", chunk)
    return [item.strip() for item in sentences if item.strip()][:max_lines]


def _summarize_chunks(chunks: list[str], node_name: str) -> str:
    summary_parts: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        key_lines = _extract_key_lines(chunk, node_name=node_name, max_lines=6)
        summary_parts.append(f"## Chunk {index}")
        summary_parts.extend(f"- {line}" for line in key_lines)
    return "\n".join(summary_parts).strip()


def trim_context_for_node(
    node_name: str,
    text: str,
    *,
    max_chars: int = 6000,
    chunk_chars: int = 2200,
) -> TrimmedContext:
    """Shrink long PRD text before sending it to a low-capability node."""

    normalized = _normalize_text(text)
    original_chars = len(normalized)
    if original_chars <= max_chars:
        return TrimmedContext(
            node_name=node_name,
            text=normalized,
            original_chars=original_chars,
            trimmed_chars=original_chars,
            was_trimmed=False,
            strategy="passthrough",
            chunk_count=1,
        )

    chunks = _split_into_chunks(normalized, max_chunk_chars=chunk_chars)
    summarized = _summarize_chunks(chunks, node_name=node_name)
    header = (
        f"# Trimmed Context For {node_name}\n\n"
        "The original PRD was too long for a low-capability model. "
        "Use the chunk summaries below as the authoritative condensed context.\n\n"
    )
    final_text = f"{header}{summarized}".strip()
    if len(final_text) > max_chars:
        final_text = final_text[: max_chars - 3].rstrip() + "..."

    return TrimmedContext(
        node_name=node_name,
        text=final_text,
        original_chars=original_chars,
        trimmed_chars=len(final_text),
        was_trimmed=True,
        strategy="chunk_then_extract",
        chunk_count=len(chunks),
    )
