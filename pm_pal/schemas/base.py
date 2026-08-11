"""Shared helpers and common types for schema validation."""

from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def normalize_bool(v: Any) -> bool:
    """Coerce ``"true"`` / ``"false"`` strings (case-insensitive) to *bool*.

    Accepted truthy strings: ``"true"``, ``"1"``, ``"yes"``.
    Everything else maps to ``False`` (for str inputs) or falls through to
    ``bool(v)`` for other types.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


def safe_list(v: Any) -> list:
    """Guarantee a list value: ``None`` → ``[]``, bare scalar → ``[scalar]``."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


class AgentSchemaModel(BaseModel):
    """Base model for all agent schemas.

    - ignores unknown fields from LLM outputs
    - serializes enums by their raw string values
    """

    model_config = ConfigDict(extra="ignore", use_enum_values=True)


ID = Annotated[str, Field(min_length=1)]
"""Generic non-empty identifier type used across agent outputs."""


class RiskLevel(str, Enum):
    """Normalized risk-impact levels used by risk outputs."""

    high = "high"
    medium = "medium"
    low = "low"


NormalizedBool = Annotated[bool, BeforeValidator(normalize_bool)]
"""``bool`` that also accepts ``"true"`` / ``"false"`` strings."""

SafeStrList = Annotated[list[str], BeforeValidator(safe_list)]
"""``list[str]`` that coerces ``None`` → ``[]``."""
