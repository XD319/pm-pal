"""Shared async repository support primitives."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Generic, TypeVar

from pydantic import Field, ValidationError

from pm_pal.schemas.base import AgentSchemaModel

try:
    import aiosqlite
except ImportError:  # pragma: no cover - optional dependency until installed
    aiosqlite = None

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python < 3.11 fallback
    from enum import Enum

    class StrEnum(str, Enum):
        pass


T = TypeVar("T")


class RepositoryErrorCode(StrEnum):
    """Stable repository error codes shared by SQLite-backed stores."""

    dependency_missing = "dependency_missing"
    database_error = "database_error"
    not_found = "not_found"
    validation_error = "validation_error"
    serialization_error = "serialization_error"
    unknown_error = "unknown_error"


class RepositoryErrorPayload(AgentSchemaModel):
    """Serializable repository error details."""

    code: RepositoryErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class RepositoryResult(Generic[T]):
    """Backend-neutral repository result object."""

    ok: bool
    value: T | None = None
    error: RepositoryErrorPayload | None = None

    @classmethod
    def success(cls, value: T) -> RepositoryResult[T]:
        return cls(ok=True, value=value)

    @classmethod
    def failure(
        cls,
        code: RepositoryErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> RepositoryResult[T]:
        return cls(
            ok=False,
            error=RepositoryErrorPayload(
                code=code,
                message=message,
                retryable=retryable,
                details=dict(details or {}),
            ),
        )


class RepositoryOperationError(Exception):
    """Internal exception used to convert failures into RepositoryResult."""

    def __init__(
        self,
        code: RepositoryErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.payload = RepositoryErrorPayload(
            code=code,
            message=message,
            retryable=retryable,
            details=dict(details or {}),
        )


class SQLiteRepositoryBase:
    """Small base class that hides SQLite connection and error handling details."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    @asynccontextmanager
    async def _open_connection(self):
        if aiosqlite is None:
            raise RepositoryOperationError(
                RepositoryErrorCode.dependency_missing,
                "aiosqlite is required for SQLite repositories.",
                details={"dependency": "aiosqlite"},
            )

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self.db_path)
        connection.row_factory = aiosqlite.Row
        try:
            await connection.execute("PRAGMA journal_mode=WAL")
            await connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            await connection.close()

    async def _run(
        self,
        operation_name: str,
        callback: Callable[[Any], Awaitable[T]],
    ) -> RepositoryResult[T]:
        try:
            async with self._open_connection() as connection:
                value = await callback(connection)
                return RepositoryResult.success(value)
        except RepositoryOperationError as exc:
            return RepositoryResult(ok=False, error=exc.payload)
        except ValidationError as exc:
            return RepositoryResult.failure(
                RepositoryErrorCode.validation_error,
                f"{operation_name} failed validation.",
                details={"error": str(exc)},
            )
        except json.JSONDecodeError as exc:
            return RepositoryResult.failure(
                RepositoryErrorCode.serialization_error,
                f"{operation_name} failed while decoding JSON.",
                details={"error": str(exc)},
            )
        except Exception as exc:
            database_error_type = getattr(aiosqlite, "Error", None)
            if database_error_type is not None and isinstance(exc, database_error_type):
                return RepositoryResult.failure(
                    RepositoryErrorCode.database_error,
                    f"{operation_name} failed while talking to SQLite.",
                    retryable=True,
                    details={"error": str(exc), "db_path": str(self.db_path)},
                )
            return RepositoryResult.failure(
                RepositoryErrorCode.unknown_error,
                f"{operation_name} failed unexpectedly.",
                details={"error": str(exc)},
            )

    @staticmethod
    def _raise_not_found(entity_name: str, entity_id: str) -> None:
        raise RepositoryOperationError(
            RepositoryErrorCode.not_found,
            f"{entity_name} was not found.",
            details={"entity_name": entity_name, "entity_id": entity_id},
        )

    @staticmethod
    def _raise_validation_error(
        message: str, *, details: dict[str, Any] | None = None
    ) -> None:
        raise RepositoryOperationError(
            RepositoryErrorCode.validation_error,
            message,
            details=details,
        )

    @staticmethod
    def _dump_json(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _load_json_object(raw_value: str | None) -> dict[str, Any]:
        if not raw_value:
            return {}
        parsed = json.loads(raw_value)
        if not isinstance(parsed, dict):
            raise RepositoryOperationError(
                RepositoryErrorCode.serialization_error,
                "Expected a JSON object payload.",
            )
        return parsed

    @staticmethod
    def _load_json_list(raw_value: str | None) -> list[Any]:
        if not raw_value:
            return []
        parsed = json.loads(raw_value)
        if not isinstance(parsed, list):
            raise RepositoryOperationError(
                RepositoryErrorCode.serialization_error,
                "Expected a JSON list payload.",
            )
        return parsed
