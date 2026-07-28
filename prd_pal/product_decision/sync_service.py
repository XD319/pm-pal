"""Incremental Feishu evidence sync with idempotent jobs and failure audit."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from prd_pal.platform import JobQueue, LocalJobQueue, NotificationSink, NullNotificationSink
from prd_pal.platform.ports import ArtifactStore
from prd_pal.utils.time import utc_now_iso

from .feishu_client import FeishuEvidenceClient
from .models import EvidenceSource, SyncTrigger
from .repository import ProductDecisionRepository


def _shanghai_tz() -> timezone:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Shanghai")
    except Exception:
        # China has no DST; fixed offset keeps Windows hosts without tzdata working.
        return timezone(timedelta(hours=8), name="Asia/Shanghai")


SHANGHAI_TZ = _shanghai_tz()


def shanghai_day_key(now: datetime | None = None) -> str:
    current = now.astimezone(SHANGHAI_TZ) if now else datetime.now(SHANGHAI_TZ)
    return current.strftime("%Y-%m-%d")


def sync_idempotency_key(source_id: str, *, day_key: str | None = None) -> str:
    """Daily and H5 manual refresh share the same idempotency key rule."""
    return f"evidence-sync:{source_id}:{day_key or shanghai_day_key()}"


class EvidenceSyncService:
    def __init__(
        self,
        repository: ProductDecisionRepository,
        *,
        job_queue: JobQueue | None = None,
        notifications: NotificationSink | None = None,
        artifacts: ArtifactStore | None = None,
        client: FeishuEvidenceClient | None = None,
        admin_open_ids: list[str] | None = None,
    ) -> None:
        self.repository = repository
        self.job_queue = job_queue or LocalJobQueue()
        self.notifications = notifications or NullNotificationSink()
        self.artifacts = artifacts
        self.client = client or FeishuEvidenceClient()
        self.admin_open_ids = list(admin_open_ids or [])

    async def sync_source(
        self,
        source_id: str,
        *,
        trigger: SyncTrigger | str = SyncTrigger.manual,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        day_key = shanghai_day_key(now)
        key = sync_idempotency_key(source_id, day_key=day_key)

        async def handler(payload: dict[str, Any]) -> dict[str, Any]:
            return await self._run_sync(
                source_id=str(payload["source_id"]),
                trigger=str(payload.get("trigger") or trigger),
                day_key=str(payload.get("day_key") or day_key),
            )

        job = await self.job_queue.enqueue(
            key=key,
            kind="evidence_sync",
            payload={
                "source_id": source_id,
                "trigger": str(trigger),
                "day_key": day_key,
            },
            handler=handler,
        )
        return {
            "job_key": key,
            "status": job.get("status"),
            "result": job.get("result"),
            "error": job.get("error"),
            "trigger": str(trigger),
            "day_key": day_key,
        }

    async def sync_all_sources(
        self, *, trigger: SyncTrigger | str = SyncTrigger.scheduled, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        listed = await self.repository.list_sources()
        sources = listed.value or []
        return [
            await self.sync_source(source.id, trigger=trigger, now=now)
            for source in sources
        ]

    async def _run_sync(
        self, *, source_id: str, trigger: str, day_key: str
    ) -> dict[str, Any]:
        source_result = await self.repository.get_source(source_id)
        if not source_result.ok or source_result.value is None:
            message = source_result.error.message if source_result.error else "source not found"
            await self._audit_failure(source_id, message, trigger=trigger, day_key=day_key)
            raise RuntimeError(message)

        source = source_result.value
        cursor = source.sync_cursor
        synced_total = 0
        pages = 0
        try:
            while True:
                page = self.client.fetch_page(source, cursor=cursor)
                pages += 1
                if page.records:
                    result = await self.repository.sync_evidence(
                        source_id, page.records, cursor=page.next_cursor
                    )
                    if not result.ok:
                        message = (
                            result.error.message if result.error else "evidence upsert failed"
                        )
                        raise RuntimeError(message)
                    synced_total += len(result.value or [])
                else:
                    # Advance watermark even when the page is empty (already up to date).
                    await self.repository.sync_evidence(
                        source_id, [], cursor=page.next_cursor
                    )
                cursor = page.next_cursor
                source = source.model_copy(update={"sync_cursor": cursor})
                if page.done:
                    break
            audit = {
                "source_id": source_id,
                "trigger": trigger,
                "day_key": day_key,
                "status": "succeeded",
                "synced_count": synced_total,
                "pages": pages,
                "cursor": cursor,
                "at": utc_now_iso(),
            }
            await self._write_audit(source_id, audit)
            return audit
        except Exception as exc:
            message = str(exc)
            await self.repository.mark_sync_failed(source_id, message)
            await self._audit_failure(
                source_id, message, trigger=trigger, day_key=day_key, pages=pages
            )
            raise

    async def _audit_failure(
        self,
        source_id: str,
        message: str,
        *,
        trigger: str,
        day_key: str,
        pages: int = 0,
    ) -> None:
        audit = {
            "source_id": source_id,
            "trigger": trigger,
            "day_key": day_key,
            "status": "failed",
            "error": message,
            "pages": pages,
            "at": utc_now_iso(),
        }
        await self._write_audit(source_id, audit)
        await self.notifications.notify(
            event="evidence_sync_failed",
            payload={
                **audit,
                "admin_open_ids": self.admin_open_ids,
            },
        )

    async def _write_audit(self, source_id: str, audit: dict[str, Any]) -> None:
        if self.artifacts is None:
            return
        stamp = str(audit.get("at") or utc_now_iso()).replace(":", "").replace("+", "_")
        key = f"evidence-sync-audit/{source_id}/{audit.get('day_key')}/{stamp}"
        await self.artifacts.put_json(key, audit)
