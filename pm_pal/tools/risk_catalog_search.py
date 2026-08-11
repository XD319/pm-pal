"""Local risk-catalog retrieval for the risk agent."""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "with",
}
_FIELD_WEIGHTS = {
    "title": 3.0,
    "description": 2.0,
    "triggers": 2.0,
    "mitigations": 1.0,
    "tags": 2.5,
}


def _default_catalog_path() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root / "data" / "risk_catalog.json")


def _tokenize(text: str) -> list[str]:
    tokens = [t.lower() for t in _TOKEN_RE.findall(text or "")]
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def _field_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    if value is None:
        return ""
    return str(value)


@lru_cache(maxsize=8)
def _load_catalog(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("risk catalog must be a JSON array")
    return [item for item in data if isinstance(item, dict)]


def _build_idf(catalog: list[dict[str, Any]]) -> dict[str, float]:
    n_docs = max(len(catalog), 1)
    df: dict[str, int] = {}
    for item in catalog:
        doc_text = " ".join(_field_text(item.get(k, "")) for k in _FIELD_WEIGHTS)
        for token in set(_tokenize(doc_text)):
            df[token] = df.get(token, 0) + 1
    return {t: math.log((n_docs + 1) / (count + 1)) + 1.0 for t, count in df.items()}


def _build_snippet(
    item: dict[str, Any], matched_terms: set[str], max_len: int = 180
) -> str:
    candidates = [
        _field_text(item.get("description", "")),
        _field_text(item.get("triggers", "")),
        _field_text(item.get("mitigations", "")),
    ]
    for text in candidates:
        lowered = text.lower()
        if any(term in lowered for term in matched_terms):
            return text[:max_len]
    return candidates[0][:max_len] if candidates else ""


def search_risk_catalog(
    query: str,
    *,
    top_k: int = 5,
    catalog_path: str | None = None,
) -> list[dict[str, Any]]:
    """Search local risk catalog and return top matches with snippets."""
    if not query or not query.strip():
        return []

    path = catalog_path or _default_catalog_path()
    catalog = _load_catalog(path)
    if not catalog:
        return []

    query_tokens = _tokenize(query)
    if not query_tokens:
        return []
    query_set = set(query_tokens)
    idf = _build_idf(catalog)

    scored: list[tuple[float, dict[str, Any], set[str]]] = []
    for item in catalog:
        score = 0.0
        matched_terms: set[str] = set()
        for field, weight in _FIELD_WEIGHTS.items():
            field_tokens = set(_tokenize(_field_text(item.get(field, ""))))
            overlap = query_set & field_tokens
            if not overlap:
                continue
            matched_terms.update(overlap)
            score += weight * sum(idf.get(token, 1.0) for token in overlap)
        if score > 0.0:
            scored.append((score, item, matched_terms))

    scored.sort(key=lambda x: x[0], reverse=True)

    results: list[dict[str, Any]] = []
    for score, item, matched_terms in scored[: max(top_k, 0)]:
        results.append(
            {
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "score": round(score, 4),
                "snippet": _build_snippet(item, matched_terms),
                "matched_terms": sorted(matched_terms),
            }
        )
    return results
