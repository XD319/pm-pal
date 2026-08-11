"""Heuristic routing for review mode and quick triage."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .normalizer import NormalizedRequirement, normalize_requirement

ReviewMode = Literal["auto", "quick", "full"]
SelectedMode = Literal["quick", "full", "skip"]


@dataclass(frozen=True, slots=True)
class GatingConfig:
    """Thresholds used to choose quick triage vs full review."""

    quick_max_chars: int = 900
    full_chars_threshold: int = 2200
    minimum_completeness_signals: int = 2
    strong_completeness_signals: int = 4
    risk_keyword_threshold: int = 2
    cross_system_threshold: int = 1
    full_score_threshold: int = 2
    skip_chars_threshold: int = 80
    skip_completeness_signals_threshold: int = 1
    risk_keywords: tuple[str, ...] = (
        "security",
        "privacy",
        "compliance",
        "audit",
        "migration",
        "rollback",
        "payment",
        "billing",
        "pii",
        "encryption",
        "latency",
        "performance",
        "idempotent",
        "rate limit",
        "timeout",
    )
    cross_system_keywords: tuple[str, ...] = (
        "integration",
        "upstream",
        "downstream",
        "third-party",
        "third party",
        "external",
        "webhook",
        "event bus",
        "queue",
        "shared service",
        "cross-system",
        "cross system",
        "microservice",
        "api gateway",
        "legacy system",
    )


@dataclass(frozen=True, slots=True)
class ReviewModeDecision:
    """Decision payload for review routing."""

    requested_mode: ReviewMode
    selected_mode: SelectedMode
    reasons: tuple[str, ...] = field(default_factory=tuple)
    skipped: bool = False
    complexity_score: int = 0
    length_chars: int = 0
    completeness_signals: tuple[str, ...] = field(default_factory=tuple)
    completeness_score: int = 0
    module_count: int = 0
    role_count: int = 0
    risk_keyword_hits: int = 0
    cross_system_hits: int = 0

    @property
    def mode(self) -> str:
        return self.selected_mode

    @property
    def legacy_mode(self) -> str:
        return "parallel_review" if self.selected_mode == "full" else "single_review"


def decide_review_mode(
    prd_text: str,
    config: GatingConfig | None = None,
    *,
    requested_mode: ReviewMode = "auto",
    normalized_requirement: NormalizedRequirement | None = None,
) -> ReviewModeDecision:
    """Choose review depth based on size, structure, risk, and cross-system signals."""

    resolved_config = config or GatingConfig()
    normalized = normalized_requirement or normalize_requirement(prd_text)
    text = normalized.source_text

    length_chars = len(text)
    module_count = len(normalized.modules)
    role_count = len(normalized.roles)
    completeness_signals = tuple(normalized.completeness_signals)
    completeness_score = len(completeness_signals)
    risk_keyword_hits = max(
        _count_keyword_hits(text, resolved_config.risk_keywords),
        len(normalized.risk_hints),
    )
    cross_system_hits = max(
        _count_keyword_hits(text, resolved_config.cross_system_keywords),
        len(normalized.dependency_hints),
    )

    reasons: list[str] = []
    complexity_score = 0

    if length_chars >= resolved_config.full_chars_threshold:
        complexity_score += 1
        reasons.append(f"length_chars={length_chars} indicates a broader requirement")

    if completeness_score >= resolved_config.strong_completeness_signals:
        complexity_score += 1
        reasons.append(
            f"completeness_signals={completeness_score} indicates the PRD is structurally detailed"
        )
    elif completeness_score < resolved_config.minimum_completeness_signals:
        reasons.append(
            f"completeness_signals={completeness_score} indicates the PRD is structurally thin"
        )

    if risk_keyword_hits >= resolved_config.risk_keyword_threshold:
        complexity_score += 1
        reasons.append(
            f"risk_keyword_hits={risk_keyword_hits} indicates elevated delivery or compliance risk"
        )

    if cross_system_hits >= resolved_config.cross_system_threshold:
        complexity_score += 1
        reasons.append(
            f"cross_system_hits={cross_system_hits} indicates external or multi-system coordination"
        )

    if module_count >= 3:
        complexity_score += 1
        reasons.append(
            f"module_count={module_count} indicates multiple implementation surfaces"
        )

    if role_count >= 3:
        reasons.append(
            f"role_count={role_count} indicates multiple stakeholder handoffs"
        )

    selected_mode: SelectedMode
    skipped = False

    if requested_mode == "quick":
        selected_mode = "quick"
        reasons.append("mode=quick explicitly requested")
    elif requested_mode == "full":
        selected_mode = "full"
        reasons.append("mode=full explicitly requested")
    else:
        if (
            length_chars < resolved_config.skip_chars_threshold
            and completeness_score
            <= resolved_config.skip_completeness_signals_threshold
        ):
            selected_mode = "skip"
            skipped = True
            reasons.append("input is too sparse to support a meaningful review")
        elif complexity_score >= resolved_config.full_score_threshold:
            selected_mode = "full"
        elif (
            risk_keyword_hits >= resolved_config.risk_keyword_threshold
            or cross_system_hits >= resolved_config.cross_system_threshold
        ):
            selected_mode = "full"
        elif (
            length_chars <= resolved_config.quick_max_chars
            and completeness_score >= resolved_config.minimum_completeness_signals
        ):
            selected_mode = "quick"
            reasons.append(
                "requirement is compact and sufficiently structured for quick triage"
            )
        else:
            selected_mode = "quick"
            reasons.append(
                "defaulting to quick triage because full-review triggers were not met"
            )

    if not reasons:
        reasons.append("no gating signals found")

    return ReviewModeDecision(
        requested_mode=requested_mode,
        selected_mode=selected_mode,
        reasons=tuple(reasons),
        skipped=skipped,
        complexity_score=complexity_score,
        length_chars=length_chars,
        completeness_signals=completeness_signals,
        completeness_score=completeness_score,
        module_count=module_count,
        role_count=role_count,
        risk_keyword_hits=risk_keyword_hits,
        cross_system_hits=cross_system_hits,
    )


def _count_keyword_hits(text: str, keywords: tuple[str, ...]) -> int:
    lowered = str(text or "").casefold()
    hits = 0
    for keyword in keywords:
        if not keyword:
            continue
        pattern = (
            r"\b" + re.escape(keyword.casefold()).replace(r"\ ", r"[\s-]+") + r"\b"
        )
        if re.search(pattern, lowered):
            hits += 1
    return hits
