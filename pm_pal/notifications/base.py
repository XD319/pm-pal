"""Notifier abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from .models import NotificationDeliveryResult, NotificationEvent


class BaseNotifier(ABC):
    channel: str = ""
    description: str = ""

    @abstractmethod
    def build_payload(self, event: NotificationEvent) -> dict[str, object]:
        """Return one dry-run payload for the notifier channel."""

    def send_payload(
        self,
        event: NotificationEvent,
        payload: dict[str, object],
    ) -> NotificationDeliveryResult:
        """Deliver a prepared payload or record a dry run."""
        return NotificationDeliveryResult(
            payload=dict(payload) if isinstance(payload, dict) else {},
            dry_run=event.dry_run,
        )


def default_notifiers() -> tuple[BaseNotifier, ...]:
    from .feishu import resolve_feishu_notifiers
    from .wecom import WeComNotifier

    return (*resolve_feishu_notifiers(), WeComNotifier())


def resolve_notifiers(
    notifiers: Sequence[BaseNotifier] | None = None,
) -> tuple[BaseNotifier, ...]:
    return tuple(notifiers) if notifiers is not None else default_notifiers()
