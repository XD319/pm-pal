"""Deprecated compatibility facade for agent command execution.

New callers should use :class:`CommandGateway` through the agent router.  This
class intentionally contains no business SQL so older imports cannot bypass the
policy boundary.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from prd_pal.server.command_gateway import CommandGateway, ReviewStarter


class AgentOrchestrator:
    def __init__(
        self,
        *,
        decision_db_path: str | Path,
        project_db_path: str | Path,
        start_review: ReviewStarter | None = None,
    ) -> None:
        self.gateway = CommandGateway(
            decision_db_path=decision_db_path,
            project_db_path=project_db_path,
            start_review=start_review,
        )

    async def execute(
        self, *, task: dict[str, Any], conversation: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        """Execute a legacy task only after translating it to a Gateway command."""
        details = dict(task.get("details") or {})
        command = dict(details.get("command") or {})
        command.setdefault("command_id", str(task.get("id") or "legacy-command"))
        command.setdefault("idempotency_key", command["command_id"])
        command.setdefault("action", str(task.get("kind") or ""))
        command.setdefault("actor", actor)
        command.setdefault("conversation_id", str(task.get("conversation_id") or conversation.get("id") or ""))
        command.setdefault("product_id", str(conversation.get("product_id") or ""))
        command.setdefault("project_id", str(conversation.get("project_id") or ""))
        command.setdefault("payload", {"source_url": str(task.get("source_url") or "")})
        return await self.gateway.execute(command)
