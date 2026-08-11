"""Compatibility wrapper for the reusable risk-analysis subflow."""

from __future__ import annotations

from ..state import ReviewState
from ..subflows.risk_analysis import run_risk_analysis_from_review_state
from ..utils.logging import get_logger

_AGENT = "risk"
log = get_logger(_AGENT)


async def run(state: ReviewState) -> ReviewState:
    """Backward-compatible entrypoint for the risk-analysis subflow."""

    log.info("风险分析开始", extra={"node": _AGENT})
    result = await run_risk_analysis_from_review_state(state)
    risk_count = len(list(result.get("risks", []) or []))
    log.info("发现 %s 个风险项", risk_count, extra={"node": _AGENT})
    return result
