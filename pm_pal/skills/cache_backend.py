"""Cache backends for skill execution results."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CacheLookupResult:
    """Outcome of a cache lookup."""

    status: str
    output_data: Any | None = None

    @property
    def is_hit(self) -> bool:
        return self.output_data is not None


class SkillCacheBackend:
    """Backend contract for skill cache storage."""

    name = "unknown"
    target = "unknown"

    def get(self, cache_key_hash: str, ttl_sec: int) -> CacheLookupResult:
        raise NotImplementedError

    def set(self, cache_key_hash: str, output_data: Any) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError


class InMemorySkillCacheBackend(SkillCacheBackend):
    """Process-local TTL cache shared across executors."""

    name = "memory"
    target = "process-local"

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, cache_key_hash: str, ttl_sec: int) -> CacheLookupResult:
        now = time.time()
        with self._lock:
            cached = self._cache.get(cache_key_hash)
            if cached is None:
                return CacheLookupResult(status="miss")
            timestamp, output_data = cached
            if now - timestamp > ttl_sec:
                self._cache.pop(cache_key_hash, None)
                return CacheLookupResult(status="expired")
            return CacheLookupResult(status="hit", output_data=output_data)

    def set(self, cache_key_hash: str, output_data: Any) -> None:
        with self._lock:
            self._cache[cache_key_hash] = (time.time(), output_data)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


class SQLiteSkillCacheBackend(SkillCacheBackend):
    """SQLite-backed cache for cross-process skill result reuse."""

    name = "sqlite"

    def __init__(self, path: str) -> None:
        self._path = os.path.abspath(path)
        self.target = self._path
        self._lock = threading.Lock()

    def get(self, cache_key_hash: str, ttl_sec: int) -> CacheLookupResult:
        if not os.path.exists(self._path):
            return CacheLookupResult(status="miss")
        with self._lock, self._connect() as conn:
            self._ensure_table(conn)
            row = conn.execute(
                "SELECT created_at, output_json FROM skill_cache WHERE cache_key_hash = ?",
                (cache_key_hash,),
            ).fetchone()
            if row is None:
                return CacheLookupResult(status="miss")
            created_at, output_json = row
            if time.time() - float(created_at) > ttl_sec:
                conn.execute(
                    "DELETE FROM skill_cache WHERE cache_key_hash = ?",
                    (cache_key_hash,),
                )
                conn.commit()
                return CacheLookupResult(status="expired")
            return CacheLookupResult(status="hit", output_data=json.loads(output_json))

    def set(self, cache_key_hash: str, output_data: Any) -> None:
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        payload = json.dumps(
            output_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with self._lock, self._connect() as conn:
            self._ensure_table(conn)
            conn.execute(
                """
                INSERT INTO skill_cache(cache_key_hash, created_at, output_json)
                VALUES(?, ?, ?)
                ON CONFLICT(cache_key_hash)
                DO UPDATE SET created_at = excluded.created_at, output_json = excluded.output_json
                """,
                (cache_key_hash, time.time(), payload),
            )
            conn.commit()

    def clear(self) -> None:
        if not os.path.exists(self._path):
            return
        with self._lock, self._connect() as conn:
            self._ensure_table(conn)
            conn.execute("DELETE FROM skill_cache")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)

    @staticmethod
    def _ensure_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_cache (
                cache_key_hash TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                output_json TEXT NOT NULL
            )
            """
        )
