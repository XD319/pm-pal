"""Product workspace helpers for the PM Agent control plane."""

from __future__ import annotations

from typing import Any

from .repository import PmRepository


async def build_workspace_summary(repository: PmRepository, product_id: str) -> dict[str, Any]:
    """Return a compact, UI-ready summary for one product workspace."""
    product_result = await repository.get_product_context(product_id)
    if not product_result.ok or product_result.value is None:
        raise ValueError(f"product_id not found: {product_id}")
    summary_result = await repository.get_workspace_summary(product_id)
    if not summary_result.ok or summary_result.value is None:
        message = summary_result.error.message if summary_result.error else "summary failed"
        raise RuntimeError(message)
    return {
        "product": product_result.value.model_dump(mode="python"),
        "counts": summary_result.value,
        "next_actions": [
            "Import or capture feedback" if not summary_result.value["feedback"] else "Review opportunity inbox",
            "Create a roadmap item" if not summary_result.value["roadmap"] else "Prepare delivery handoff",
        ],
    }
