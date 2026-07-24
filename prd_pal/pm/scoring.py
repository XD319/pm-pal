"""Priority scoring helpers for PM opportunities and roadmap items."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PriorityScore:
    method: str
    score: float
    details: dict[str, float]


def score_rice(
    *,
    reach: float,
    impact: float,
    confidence: float,
    effort: float,
) -> PriorityScore:
    """Compute a RICE score: (reach * impact * confidence) / effort."""

    if effort <= 0:
        raise ValueError("effort must be > 0")
    raw = (float(reach) * float(impact) * float(confidence)) / float(effort)
    return PriorityScore(
        method="rice",
        score=round(raw, 4),
        details={
            "reach": float(reach),
            "impact": float(impact),
            "confidence": float(confidence),
            "effort": float(effort),
        },
    )


def score_ice(
    *,
    impact: float,
    confidence: float,
    ease: float,
) -> PriorityScore:
    """Compute an ICE score: impact * confidence * ease."""

    raw = float(impact) * float(confidence) * float(ease)
    return PriorityScore(
        method="ice",
        score=round(raw, 4),
        details={
            "impact": float(impact),
            "confidence": float(confidence),
            "ease": float(ease),
        },
    )


def assign_horizon(score: float, *, now_threshold: float = 6.0, next_threshold: float = 3.0) -> str:
    """Map a numeric priority score into Now/Next/Later."""

    if score >= now_threshold:
        return "now"
    if score >= next_threshold:
        return "next"
    return "later"
