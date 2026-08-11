"""Minimal approval state machine for DeliveryBundle."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from pm_pal.packs.delivery_bundle import ApprovalEvent, BundleStatus, DeliveryBundle
from pm_pal.utils.time import utc_now_iso as _utc_now_iso

if TYPE_CHECKING:
    from pm_pal.workspace.models import ApprovalRecord


class InvalidTransitionError(ValueError):
    """Raised when a bundle transition is not allowed."""


VALID_TRANSITIONS: dict[BundleStatus, set[BundleStatus]] = {
    BundleStatus.draft: {
        BundleStatus.need_more_info,
        BundleStatus.approved,
        BundleStatus.blocked_by_risk,
    },
    BundleStatus.need_more_info: {
        BundleStatus.draft,
        BundleStatus.blocked_by_risk,
    },
    BundleStatus.blocked_by_risk: {
        BundleStatus.draft,
    },
    BundleStatus.approved: set(),
}


def _transition(
    bundle: DeliveryBundle, to_status: BundleStatus, reviewer: str, comment: str
) -> DeliveryBundle:
    from_status = BundleStatus(bundle.status)
    if to_status not in VALID_TRANSITIONS.get(from_status, set()):
        raise InvalidTransitionError(
            f"invalid bundle transition: {from_status} -> {to_status}"
        )

    history = list(bundle.approval_history)
    history.append(
        ApprovalEvent(
            event_id=f"approval-{uuid4().hex}",
            timestamp=_utc_now_iso(),
            from_status=from_status,
            to_status=to_status,
            reviewer=reviewer,
            comment=comment,
        )
    )
    return bundle.model_copy(
        update={"status": to_status, "approval_history": history}, deep=True
    )


def build_approval_record(
    bundle: DeliveryBundle, event: ApprovalEvent, action: str = ""
) -> ApprovalRecord:
    """Convert one bundle approval event into a persisted workspace approval record."""

    from pm_pal.workspace.models import ApprovalRecord
    from pm_pal.workspace.repository import workspace_status_from_bundle_status

    return ApprovalRecord(
        record_id=event.event_id,
        run_id=bundle.source_run_id,
        bundle_id=bundle.bundle_id,
        timestamp=event.timestamp,
        action=str(action or ""),
        from_bundle_status=BundleStatus(event.from_status),
        to_bundle_status=BundleStatus(event.to_status),
        workspace_status=workspace_status_from_bundle_status(event.to_status),
        reviewer=event.reviewer,
        comment=event.comment,
    )


def approve_bundle(
    bundle: DeliveryBundle, reviewer: str, comment: str
) -> DeliveryBundle:
    return _transition(bundle, BundleStatus.approved, reviewer, comment)


def request_more_info(
    bundle: DeliveryBundle, reviewer: str, comment: str
) -> DeliveryBundle:
    return _transition(bundle, BundleStatus.need_more_info, reviewer, comment)


def block_by_risk(
    bundle: DeliveryBundle, reviewer: str, comment: str
) -> DeliveryBundle:
    return _transition(bundle, BundleStatus.blocked_by_risk, reviewer, comment)


def reset_to_draft(
    bundle: DeliveryBundle, reviewer: str, comment: str
) -> DeliveryBundle:
    return _transition(bundle, BundleStatus.draft, reviewer, comment)
