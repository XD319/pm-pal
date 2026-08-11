"""Minimal reviewer tool abstraction with graceful degradation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Iterable

from ...tools.risk_catalog_search import search_risk_catalog
from .base import EvidenceItem, ToolCall


@dataclass(slots=True)
class ToolExecution:
    evidence: tuple[EvidenceItem, ...] = ()
    tool_call: ToolCall | None = None


class ReviewerToolAdapter:
    tool_name: str = "reviewer.tool"

    def run(self, *, reviewer: str, query: str) -> ToolExecution:
        raise NotImplementedError

    def _degraded(
        self,
        *,
        reviewer: str,
        query: str,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> ToolExecution:
        return ToolExecution(
            evidence=(),
            tool_call=ToolCall(
                tool_name=self.tool_name,
                status="degraded",
                reviewer=reviewer,
                query=query,
                input_summary=query,
                output_summary="No evidence returned.",
                degraded_reason=reason,
                metadata=metadata or {},
            ),
        )


class LocalRiskCatalogAdapter(ReviewerToolAdapter):
    tool_name = "risk_catalog.search"

    def __init__(self, *, enabled: bool = True, top_k: int = 3):
        self.enabled = enabled
        self.top_k = top_k

    def run(self, *, reviewer: str, query: str) -> ToolExecution:
        if not self.enabled:
            return self._degraded(
                reviewer=reviewer,
                query=query,
                reason="local risk catalog disabled by configuration",
                metadata={"adapter": "local_risk_catalog"},
            )

        hits = search_risk_catalog(query, top_k=self.top_k)
        evidence = tuple(
            EvidenceItem(
                source="risk_catalog",
                title=str(item.get("title", "") or "risk catalog hit"),
                snippet=str(item.get("snippet", "") or ""),
                ref=str(item.get("id", "") or ""),
                score=float(item.get("score"))
                if item.get("score") is not None
                else None,
                metadata={"matched_terms": list(item.get("matched_terms", []) or [])},
            )
            for item in hits
        )
        return ToolExecution(
            evidence=evidence,
            tool_call=ToolCall(
                tool_name=self.tool_name,
                status="completed",
                reviewer=reviewer,
                query=query,
                input_summary=query,
                output_summary=f"hits={len(evidence)}",
                evidence_count=len(evidence),
                metadata={"adapter": "local_risk_catalog", "top_k": self.top_k},
            ),
        )


class CallbackSearchAdapter(ReviewerToolAdapter):
    def __init__(
        self,
        tool_name: str,
        callback: Callable[[str], Iterable[dict[str, object]]] | None = None,
        *,
        enabled: bool = False,
    ):
        self.tool_name = tool_name
        self.callback = callback
        self.enabled = enabled

    def run(self, *, reviewer: str, query: str) -> ToolExecution:
        if not self.enabled or self.callback is None:
            return self._degraded(
                reviewer=reviewer,
                query=query,
                reason="adapter not configured",
                metadata={"adapter": self.tool_name},
            )

        items = list(self.callback(query) or [])
        evidence = tuple(
            EvidenceItem(
                source=str(item.get("source", self.tool_name) or self.tool_name),
                title=str(item.get("title", "") or self.tool_name),
                snippet=str(item.get("snippet", "") or ""),
                ref=str(item.get("ref", "") or ""),
                score=float(item.get("score"))
                if item.get("score") is not None
                else None,
                metadata=dict(item.get("metadata", {}) or {}),
            )
            for item in items
            if isinstance(item, dict)
        )
        return ToolExecution(
            evidence=evidence,
            tool_call=ToolCall(
                tool_name=self.tool_name,
                status="completed",
                reviewer=reviewer,
                query=query,
                input_summary=query,
                output_summary=f"hits={len(evidence)}",
                evidence_count=len(evidence),
                metadata={"adapter": self.tool_name},
            ),
        )


class ReviewerToolbox:
    def __init__(self) -> None:
        risk_top_k = _int_env("REVIEWER_LOCAL_RISK_TOP_K", default=3)
        self.local_risk_catalog = LocalRiskCatalogAdapter(
            enabled=_bool_env("REVIEWER_ENABLE_LOCAL_RISK_CATALOG", default=True),
            top_k=risk_top_k,
        )
        self.web_search = CallbackSearchAdapter(
            "web.search",
            enabled=_bool_env("REVIEWER_ENABLE_WEB_SEARCH", default=False),
        )
        self.cve_lookup = CallbackSearchAdapter(
            "cve.lookup",
            enabled=_bool_env("REVIEWER_ENABLE_CVE_LOOKUP", default=False),
        )
        self.jira_search = CallbackSearchAdapter(
            "jira.search",
            enabled=_bool_env("REVIEWER_ENABLE_JIRA_SEARCH", default=False),
        )
        self.confluence_search = CallbackSearchAdapter(
            "confluence.search",
            enabled=_bool_env("REVIEWER_ENABLE_CONFLUENCE_SEARCH", default=False),
        )


def get_reviewer_toolbox() -> ReviewerToolbox:
    return ReviewerToolbox()


def _bool_env(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _int_env(name: str, *, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default
