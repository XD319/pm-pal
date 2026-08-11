"""File-based repository for review workspace state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pm_pal.packs.delivery_bundle import BundleStatus, DeliveryBundle

from .models import (
    ApprovalRecord,
    ReviewWorkspaceRecord,
    StatusSnapshot,
    WorkspaceStatus,
)

APPROVAL_RECORDS_FILENAME = "approval_records.json"
STATUS_SNAPSHOT_FILENAME = "status_snapshot.json"
DELIVERY_BUNDLE_FILENAME = "delivery_bundle.json"

_BUNDLE_TO_WORKSPACE_STATUS: dict[BundleStatus, WorkspaceStatus] = {
    BundleStatus.draft: WorkspaceStatus.deferred,
    BundleStatus.need_more_info: WorkspaceStatus.need_more_info,
    BundleStatus.approved: WorkspaceStatus.confirmed,
    BundleStatus.blocked_by_risk: WorkspaceStatus.blocked_by_risk,
}

_WORKSPACE_TO_BUNDLE_STATUS: dict[WorkspaceStatus, BundleStatus] = {
    WorkspaceStatus.confirmed: BundleStatus.approved,
    WorkspaceStatus.need_more_info: BundleStatus.need_more_info,
    WorkspaceStatus.deferred: BundleStatus.draft,
    WorkspaceStatus.blocked_by_risk: BundleStatus.blocked_by_risk,
}


def workspace_status_from_bundle_status(
    bundle_status: BundleStatus | str,
) -> WorkspaceStatus:
    """Map persisted bundle status into the workspace-facing status vocabulary."""

    normalized_status = BundleStatus(bundle_status)
    return _BUNDLE_TO_WORKSPACE_STATUS[normalized_status]


def bundle_status_from_workspace_status(
    workspace_status: WorkspaceStatus | str,
) -> BundleStatus | None:
    """Map workspace status back to bundle status when an equivalent exists."""

    normalized_status = WorkspaceStatus(workspace_status)
    return _WORKSPACE_TO_BUNDLE_STATUS.get(normalized_status)


class ReviewWorkspaceRepository:
    """Persist and query review workspace state under one run directory."""

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)

    @property
    def approval_records_path(self) -> Path:
        return self.run_dir / APPROVAL_RECORDS_FILENAME

    @property
    def status_snapshot_path(self) -> Path:
        return self.run_dir / STATUS_SNAPSHOT_FILENAME

    @property
    def delivery_bundle_path(self) -> Path:
        return self.run_dir / DELIVERY_BUNDLE_FILENAME

    def load_approval_records(self) -> list[ApprovalRecord]:
        payload = self._load_json_object(self.approval_records_path)
        raw_records = payload.get("approval_records")
        if not isinstance(raw_records, list):
            return []
        return [
            ApprovalRecord.model_validate(item)
            for item in raw_records
            if isinstance(item, dict)
        ]

    def save_approval_records(self, approval_records: list[ApprovalRecord]) -> Path:
        self._ensure_run_dir()
        payload = {
            "approval_records": [
                record.model_dump(mode="python") for record in approval_records
            ],
        }
        self._write_json(self.approval_records_path, payload)
        return self.approval_records_path

    def append_approval_record(self, approval_record: ApprovalRecord) -> Path:
        approval_records = self.load_approval_records()
        approval_records.append(approval_record)
        return self.save_approval_records(approval_records)

    def load_status_snapshot(self) -> StatusSnapshot | None:
        payload = self._load_json_object(self.status_snapshot_path)
        if not payload:
            return None
        return StatusSnapshot.model_validate(payload)

    def save_status_snapshot(self, status_snapshot: StatusSnapshot) -> Path:
        self._ensure_run_dir()
        self._write_json(
            self.status_snapshot_path, status_snapshot.model_dump(mode="python")
        )
        return self.status_snapshot_path

    def load_bundle(self) -> DeliveryBundle | None:
        payload = self._load_json_object(self.delivery_bundle_path)
        if not payload:
            return None
        return DeliveryBundle.model_validate(payload)

    def build_status_snapshot(
        self,
        *,
        run_id: str,
        bundle_id: str,
        bundle_status: BundleStatus | str,
        updated_at: str,
    ) -> StatusSnapshot:
        normalized_bundle_status = BundleStatus(bundle_status)
        return StatusSnapshot(
            run_id=run_id,
            bundle_id=bundle_id,
            bundle_status=normalized_bundle_status,
            workspace_status=workspace_status_from_bundle_status(
                normalized_bundle_status
            ),
            updated_at=updated_at,
        )

    def save_status_snapshot_for_bundle(
        self, bundle: DeliveryBundle, updated_at: str
    ) -> Path:
        snapshot = self.build_status_snapshot(
            run_id=bundle.source_run_id,
            bundle_id=bundle.bundle_id,
            bundle_status=bundle.status,
            updated_at=updated_at,
        )
        return self.save_status_snapshot(snapshot)

    def load_workspace(self) -> ReviewWorkspaceRecord:
        bundle = self.load_bundle()
        approval_records = self.load_approval_records()
        status_snapshot = self.load_status_snapshot()

        run_id = self._resolve_run_id(bundle=bundle, status_snapshot=status_snapshot)
        bundle_id = (
            status_snapshot.bundle_id
            if status_snapshot is not None
            else bundle.bundle_id
            if bundle is not None
            else ""
        )
        bundle_status = (
            status_snapshot.bundle_status
            if status_snapshot is not None
            else bundle.status
            if bundle is not None
            else None
        )
        workspace_status = (
            status_snapshot.workspace_status
            if status_snapshot is not None
            else workspace_status_from_bundle_status(bundle.status)
            if bundle is not None
            else WorkspaceStatus.deferred
        )

        return ReviewWorkspaceRecord(
            run_id=run_id,
            bundle_id=bundle_id,
            bundle_status=bundle_status,
            workspace_status=workspace_status,
            approval_history=list(bundle.approval_history)
            if bundle is not None
            else [],
            approval_records=approval_records,
            status_snapshot=status_snapshot,
        )

    def _ensure_run_dir(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_run_id(
        self,
        *,
        bundle: DeliveryBundle | None,
        status_snapshot: StatusSnapshot | None,
    ) -> str:
        if status_snapshot is not None:
            return status_snapshot.run_id
        if bundle is not None:
            return bundle.source_run_id
        return self.run_dir.name

    @staticmethod
    def _load_json_object(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
