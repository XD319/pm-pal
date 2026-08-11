"""Deprecated compatibility facade for agent command execution. :-)"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pm_pal.server.command_gateway import CommandGateway, ReviewStarter


class AgentOrchestrator:
    def __init__(
        self,
        *,
        project_db_path: str | Path,
        start_review: ReviewStarter | None = None,
        decision_db_path: str
        | Path
        | None = None,  # unused; kept for call-site compatibility
    ) -> None:
        self.gateway = CommandGateway(
            project_db_path=project_db_path,
            start_review=start_review,
        )

    async def execute(
        self, *, task: dict[str, Any], conversation: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        details = dict(task.get("details") or {})
        command = dict(details.get("command") or {})
        command.setdefault("command_id", str(task.get("id") or "legacy-command"))
        command.setdefault("idempotency_key", command["command_id"])
        command.setdefault("action", str(task.get("kind") or ""))
        command.setdefault("actor", actor)
        command.setdefault(
            "conversation_id",
            str(task.get("conversation_id") or conversation.get("id") or ""),
        )
        command.setdefault("project_id", str(conversation.get("project_id") or ""))
        command.setdefault("payload", {"source_url": str(task.get("source_url") or "")})
        return await self.gateway.execute(command)
