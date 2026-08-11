"""Service helpers for PRD review memory storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from pm_pal.workspace.repository_support import RepositoryResult

from .models import (
    MemoryApplicability,
    MemoryEvidence,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    MemoryScopeLevel,
)
from .repository import MemoryRepository

DEFAULT_MEMORY_DB_PATH = Path("data") / "review_memory.sqlite3"


class MemoryServiceError(RuntimeError):
    """Raised when the memory service cannot complete an operation."""


def _require_repository_value(result: RepositoryResult[Any], action: str) -> Any:
    if result.ok and result.value is not None:
        return result.value
    if result.error is not None:
        raise MemoryServiceError(
            f"{action} failed: {result.error.message} ({result.error.code})"
        )
    raise MemoryServiceError(f"{action} failed unexpectedly")


class MemoryService:
    """Thin service layer around the SQLite-backed review memory repository."""

    def __init__(self, repository: MemoryRepository) -> None:
        self.repository = repository

    @classmethod
    def from_db_path(cls, db_path: str | Path | None = None) -> MemoryService:
        return cls(MemoryRepository(db_path or DEFAULT_MEMORY_DB_PATH))

    async def initialize(self) -> bool:
        return bool(
            _require_repository_value(
                await self.repository.initialize(), "memory_repository.initialize"
            )
        )

    async def save_memory(
        self,
        *,
        memory_type: str,
        title: str,
        summary: str,
        content: str,
        scope: MemoryScope | dict[str, Any],
        applicability: MemoryApplicability | dict[str, Any] | None = None,
        evidence: list[MemoryEvidence | dict[str, Any]] | None = None,
        confidence: float = 0.0,
        reuse_score: float = 0.0,
        expiry_hint: str = "",
        tags: list[str] | None = None,
        do_not_overapply: str = "",
        created_by: str = "",
        memory_id: str | None = None,
        actor: str = "system",
    ) -> MemoryRecord:
        payload = MemoryRecord(
            memory_id=str(memory_id or f"mem-{uuid4().hex}"),
            memory_type=memory_type,
            title=title,
            summary=summary,
            content=content,
            scope=MemoryScope.model_validate(scope),
            applicability=MemoryApplicability.model_validate(applicability or {}),
            evidence=[MemoryEvidence.model_validate(item) for item in (evidence or [])],
            confidence=confidence,
            reuse_score=reuse_score,
            expiry_hint=expiry_hint,
            tags=list(tags or []),
            do_not_overapply=do_not_overapply,
            created_by=created_by,
        )
        return _require_repository_value(
            await self.repository.save_memory(payload, actor=actor),
            "memory_repository.save_memory",
        )

    async def list_memory_by_scope(
        self,
        *,
        level: MemoryScopeLevel | str,
        team_id: str | None = None,
        project_id: str | None = None,
    ) -> list[MemoryRecord]:
        scope_level = (
            level if isinstance(level, MemoryScopeLevel) else MemoryScopeLevel(level)
        )
        return _require_repository_value(
            await self.repository.list_by_scope(
                level=scope_level, team_id=team_id, project_id=project_id
            ),
            "memory_repository.list_by_scope",
        )

    async def find_memories(
        self,
        *,
        scope_level: MemoryScopeLevel | str | None = None,
        team_id: str = "",
        project_id: str = "",
        requirement_type: str = "",
        memory_type: str | None = None,
        tag: str = "",
    ) -> list[MemoryRecord]:
        query = MemoryQuery(
            scope_level=(
                scope_level
                if scope_level is None or isinstance(scope_level, MemoryScopeLevel)
                else MemoryScopeLevel(scope_level)
            ),
            team_id=team_id,
            project_id=project_id,
            requirement_type=requirement_type,
            memory_type=memory_type,
            tag=tag,
        )
        return _require_repository_value(
            await self.repository.query_memories(query),
            "memory_repository.query_memories",
        )
