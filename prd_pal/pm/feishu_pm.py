"""Feishu-facing helpers for PM feedback capture and notifications."""

from __future__ import annotations

from typing import Any

from .repository import PmRepository
from .workflow import capture_feedback


def build_feedback_card(
    *,
    product_hint: str = "",
    pipeline_id: str = "",
    feedback_count: int = 0,
) -> dict[str, Any]:
    """Build a lightweight Feishu card payload for PM feedback collection."""

    return {
        "header": {
            "title": "PM Agent feedback inbox",
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**Product:** {product_hint or 'unspecified'}\n"
                        f"**Captured:** {feedback_count} items\n"
                        f"**Pipeline:** {pipeline_id or 'not started'}"
                    ),
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "整理成 PRD"},
                        "type": "primary",
                        "value": {"action": "run_pm_pipeline", "product_hint": product_hint},
                    }
                ],
            },
        ],
    }


async def capture_feishu_feedback(
    *,
    texts: list[str],
    product_hint: str = "",
    open_id: str = "",
    tenant_key: str = "",
    repository: PmRepository,
) -> dict[str, Any]:
    """Capture feedback from a Feishu card/message and return a card response."""

    items = await capture_feedback(
        texts,
        product_hint=product_hint,
        source="feishu",
        repository=repository,
    )
    for item in items:
        metadata = dict(item.metadata or {})
        metadata.update({"open_id": open_id, "tenant_key": tenant_key})
        updated = item.model_copy(update={"metadata": metadata})
        await repository.upsert_feedback(updated)
    card = build_feedback_card(
        product_hint=product_hint,
        feedback_count=len(items),
    )
    return {
        "feedback_ids": [item.id for item in items],
        "count": len(items),
        "card": card,
    }


def build_quality_gate_notice(
    *,
    pipeline_id: str,
    prd_id: str,
    review_run_id: str,
    findings_count: int = 0,
) -> dict[str, Any]:
    """Build a Feishu notice for PRD quality gate completion."""

    return {
        "msg_type": "interactive",
        "card": {
            "header": {"title": "PRD quality gate result", "template": "turquoise"},
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**Pipeline:** {pipeline_id}\n"
                            f"**PRD:** {prd_id}\n"
                            f"**Review run:** {review_run_id or 'n/a'}\n"
                            f"**Findings:** {findings_count}"
                        ),
                    },
                }
            ],
        },
    }
