"""Schema for the **parser** agent output.

Field names match the JSON contract defined in ``prompts.PARSER_SYSTEM_PROMPT``
and consumed by ``agents/parser_agent.py``.
"""

from typing import Any

from pydantic import Field

from .base import AgentSchemaModel, ID, SafeStrList


# ── item-level model ──────────────────────────────────────────────────────


class ParsedItem(AgentSchemaModel):
    """A single atomic requirement extracted from the PRD."""

    id: ID
    description: str = ""
    acceptance_criteria: SafeStrList = Field(default_factory=list)


# ── top-level output ──────────────────────────────────────────────────────


class ParserOutput(AgentSchemaModel):
    """Wrapper returned by the parser LLM call.

    ``{"parsed_items": [...]}``
    """

    parsed_items: list[ParsedItem] = Field(default_factory=list)


def validate_parser_output(data: dict[str, Any]) -> ParserOutput:
    """Validate and coerce a raw dict into a :class:`ParserOutput`."""
    return ParserOutput.model_validate(data)


# ── minimal example ───────────────────────────────────────────────────────
# validate_parser_output({
#     "parsed_items": [
#         {
#             "id": "REQ-001",
#             "description": "User login via OAuth",
#             "acceptance_criteria": ["Redirects to provider", "Returns JWT"]
#         },
#         {
#             "id": "REQ-002",
#             "description": "Dashboard shows KPIs",
#             "acceptance_criteria": None  # safe_list → []
#         }
#     ]
# })
