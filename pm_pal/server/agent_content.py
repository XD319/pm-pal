"""Deterministic evidence-to-insight/opportunity copy helpers. :-)"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def _clip(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def _evidence_texts(confirmed: Sequence[Any], limit: int = 3) -> list[str]:
    texts: list[str] = []
    for item in confirmed[:limit]:
        raw = getattr(item, "summary", None) or getattr(item, "content", None) or ""
        if isinstance(item, dict):
            raw = item.get("summary") or item.get("content") or ""
        text = _clip(str(raw), 160)
        if text:
            texts.append(text)
    return texts


def build_insight_opportunity_copy(confirmed: Sequence[Any]) -> dict[str, str]:
    """Build non-empty insight/opportunity fields from confirmed evidence. :-)"""
    snippets = _evidence_texts(confirmed)
    count = len(confirmed)
    lead = snippets[0] if snippets else "已确认反馈"
    title_seed = _clip(lead, 36)
    insight_title = f"反馈洞察：{title_seed}"
    insight_summary = f"基于 {count} 条已确认证据：" + (
        "；".join(snippets) if snippets else "暂无正文摘要。"
    )
    opportunity_title = f"改进机会：{title_seed}"
    problem = (
        snippets[0] if snippets else f"基于 {count} 条已确认证据，需要进一步澄清问题。"
    )
    users = "证据涉及的目标用户与相关角色"
    value = (
        f"优先验证并落地与「{_clip(lead, 48)}」相关的改进，降低反馈中暴露的风险。"
        if snippets
        else "等待产品负责人基于证据验证并审批。"
    )
    return {
        "insight_title": insight_title,
        "insight_summary": _clip(insight_summary, 480),
        "opportunity_title": opportunity_title,
        "problem": _clip(problem, 240),
        "users": users,
        "value": _clip(value, 240),
    }
