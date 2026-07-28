"""Feishu bot light entry for the decision workbench (no in-bot approvals)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from .repository import ProductDecisionRepository
from .services import CollectService, OpportunityService
from .traceability import build_decision_trace


def build_h5_deep_link(
    *,
    base_url: str,
    view: str,
    product_id: str = "",
    open_id: str = "",
) -> str:
    root = str(base_url or "").rstrip("/") or "https://example.feishu.cn/app"
    params = {"view": view, "embed": "feishu"}
    if product_id:
        params["product_id"] = product_id
    if open_id:
        params["open_id"] = open_id
    return f"{root}/pm?{urlencode(params)}"


def build_bot_card(*, title: str, lines: list[str], link: str = "") -> dict[str, Any]:
    content = "\n".join(f"- {line}" for line in lines if line)
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": content or "暂无内容"},
        }
    ]
    if link:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开 H5 工作台"},
                        "type": "primary",
                        "url": link,
                    }
                ],
            }
        )
    return {
        "msg_type": "interactive",
        "card": {
            "header": {"title": title, "template": "blue"},
            "elements": elements,
        },
    }


async def handle_decision_bot_command(
    *,
    repository: ProductDecisionRepository,
    command: str,
    product_id: str = "",
    open_id: str = "",
    text: str = "",
    source_url: str = "",
    h5_base_url: str = "",
) -> dict[str, Any]:
    """Handle lightweight bot intents: submit/summary/pending/query/link."""
    normalized = str(command or "").strip().lower()
    product_id = str(product_id or "").strip()
    open_id = str(open_id or "").strip()

    if normalized in {"submit", "source_submit"}:
        content = str(text or "").strip()
        if not content:
            return {
                "ok": False,
                "code": "invalid_input",
                "message": "submit requires text",
                "card": build_bot_card(title="提交失败", lines=["请提供反馈原文"]),
            }
        # Bot only captures as evidence via a transient manual source upsert path:
        # create/find manual source then sync one record.
        sources = await repository.list_sources(product_id)
        source = next(
            (
                item
                for item in (sources.value or [])
                if item.source_type == "feishu_meeting_notes"
            ),
            None,
        )
        if source is None:
            from .models import EvidenceSource, EvidenceSourceType

            source = EvidenceSource(
                id=f"source-bot-{product_id or 'default'}",
                product_id=product_id or "default",
                source_type=EvidenceSourceType.feishu_meeting_notes,
                external_id="bot-inbox",
                source_url=source_url,
                display_name="Bot inbox",
            )
            await repository.upsert_source(source)
        from .models import EvidenceRecord

        evidence = EvidenceRecord(
            id="",
            source_id=source.id,
            external_id=f"bot-{abs(hash(content)) % 10_000_000}",
            product_id=source.product_id,
            content=content,
            source_url=source_url,
            author=open_id,
            source_version="bot",
        )
        synced = await repository.sync_evidence(source.id, [evidence], cursor="bot")
        link = build_h5_deep_link(
            base_url=h5_base_url, view="evidence", product_id=source.product_id, open_id=open_id
        )
        return {
            "ok": True,
            "action": "submit",
            "synced_count": len(synced.value or []),
            "card": build_bot_card(
                title="来源已提交",
                lines=[f"已写入证据 {len(synced.value or [])} 条", "请在 H5 审阅"],
                link=link,
            ),
            "h5_link": link,
        }

    if normalized in {"summary", "daily_summary"}:
        evidence = await CollectService(repository).search_evidence(product_id=product_id, limit=20)
        opportunities = await OpportunityService(repository).list_candidates(product_id=product_id)
        pending = [item for item in opportunities if str(item.status) == "pending_approval"]
        link = build_h5_deep_link(
            base_url=h5_base_url, view="delivery", product_id=product_id, open_id=open_id
        )
        return {
            "ok": True,
            "action": "summary",
            "card": build_bot_card(
                title="每日摘要",
                lines=[
                    f"证据 {len(evidence)} 条",
                    f"机会 {len(opportunities)} 个",
                    f"待审批 {len(pending)} 个",
                ],
                link=link,
            ),
            "h5_link": link,
        }

    if normalized in {"pending", "remind"}:
        opportunities = await OpportunityService(repository).list_candidates(product_id=product_id)
        pending = [item for item in opportunities if str(item.status) == "pending_approval"]
        link = build_h5_deep_link(
            base_url=h5_base_url, view="opportunities", product_id=product_id, open_id=open_id
        )
        lines = [f"{item.title} ({item.id})" for item in pending[:8]] or ["当前无待审批机会"]
        return {
            "ok": True,
            "action": "pending",
            "count": len(pending),
            "card": build_bot_card(title="待审批提醒", lines=lines, link=link),
            "h5_link": link,
        }

    if normalized in {"query", "search"}:
        query = str(text or "").strip()
        evidence = await CollectService(repository).search_evidence(
            product_id=product_id, query=query, limit=5
        )
        link = build_h5_deep_link(
            base_url=h5_base_url, view="evidence", product_id=product_id, open_id=open_id
        )
        lines = [
            f"{item.summary or item.content[:60]} [{item.id}]"
            for item in evidence
        ] or ["未找到匹配证据"]
        return {
            "ok": True,
            "action": "query",
            "card": build_bot_card(title="快速查询", lines=lines, link=link),
            "h5_link": link,
        }

    if normalized in {"link", "deeplink"}:
        view = str(text or "opportunities").strip() or "opportunities"
        link = build_h5_deep_link(
            base_url=h5_base_url, view=view, product_id=product_id, open_id=open_id
        )
        return {
            "ok": True,
            "action": "link",
            "h5_link": link,
            "card": build_bot_card(title="H5 深链接", lines=[f"视图：{view}"], link=link),
        }

    if normalized == "trace":
        root_id = str(text or "").strip()
        if not root_id:
            return {
                "ok": False,
                "code": "invalid_input",
                "message": "trace requires root id",
                "card": build_bot_card(title="追溯失败", lines=["请提供根 ID"]),
            }
        trace = await build_decision_trace(repository, root_id)
        link = build_h5_deep_link(
            base_url=h5_base_url, view="delivery", product_id=product_id, open_id=open_id
        )
        return {
            "ok": True,
            "action": "trace",
            "trace": trace,
            "card": build_bot_card(
                title="追溯摘要",
                lines=[
                    f"节点 {len(trace.get('nodes') or [])}",
                    f"边 {len(trace.get('edges') or [])}",
                ],
                link=link,
            ),
            "h5_link": link,
        }

    return {
        "ok": False,
        "code": "unsupported_command",
        "message": "Supported: submit, summary, pending, query, link",
        "card": build_bot_card(
            title="不支持的指令",
            lines=["可用：submit / summary / pending / query / link"],
        ),
    }
