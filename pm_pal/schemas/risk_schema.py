"""Schema for the **risk** agent output.

Field names match the JSON contract defined in ``prompts.RISK_SYSTEM_PROMPT``
and consumed by ``agents/risk_agent.py``.
"""

from typing import Any

from pydantic import Field

from .base import AgentSchemaModel, ID, RiskLevel


# ── item-level model ──────────────────────────────────────────────────────


class RiskItem(AgentSchemaModel):
    """A single delivery risk identified by the risk agent."""

    id: ID
    description: str = ""
    impact: RiskLevel = RiskLevel.medium
    mitigation: str = ""
    buffer_days: float = 0
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_snippets: list[str] = Field(default_factory=list)


# ── top-level output ──────────────────────────────────────────────────────


class RiskOutput(AgentSchemaModel):
    """Wrapper returned by the risk LLM call.

    ``{"risks": [...]}``
    """

    risks: list[RiskItem] = Field(default_factory=list)


def validate_risk_output(data: dict[str, Any]) -> RiskOutput:
    """Validate and coerce a raw dict into a :class:`RiskOutput`."""
    return RiskOutput.model_validate(data)


# ── minimal example ───────────────────────────────────────────────────────
# validate_risk_output({
#     "risks": [
#         {
#             "id": "R-1",
#             "description": "Tight buffer for backend tasks",
#             "impact": "high",
#             "mitigation": "Add 2 extra buffer days",
#             "buffer_days": 2
#         },
#         {
#             "id": "R-2",
#             "description": "Single FE developer bottleneck",
#             "impact": "medium",
#             "mitigation": "Cross-train a BE dev on FE stack",
#             "buffer_days": 0
#         }
#     ]
# })
