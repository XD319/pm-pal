"""Skill wrapper for local risk catalog search."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..tools.risk_catalog_search import search_risk_catalog
from .executor import SkillSpec


class RiskCatalogSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1)
    catalog_path: str | None = None


class RiskCatalogHit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    title: str = ""
    score: float = 0.0
    snippet: str = ""
    matched_terms: list[str] = Field(default_factory=list)


class RiskCatalogSearchOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hits: list[RiskCatalogHit] = Field(default_factory=list)


def _search_risk_catalog(payload: RiskCatalogSearchInput) -> dict[str, Any]:
    hits = search_risk_catalog(
        payload.query,
        top_k=payload.top_k,
        catalog_path=payload.catalog_path,
    )
    return {"hits": hits}


RISK_CATALOG_SEARCH_SKILL = SkillSpec(
    name="risk_catalog.search",
    input_model=RiskCatalogSearchInput,
    output_model=RiskCatalogSearchOutput,
    handler=_search_risk_catalog,
    config_version="risk_catalog_search@v1",
    cache_ttl_sec=300,
)
