"""WeCom dry-run payload renderer."""

from __future__ import annotations

from pm_pal.notifications.base import BaseNotifier
from pm_pal.notifications.models import NotificationEvent


class WeComNotifier(BaseNotifier):
    channel = "wecom"
    description = "Render a WeCom markdown-message dry-run payload."

    def build_payload(self, event: NotificationEvent) -> dict[str, object]:
        metadata = event.metadata or {}
        detail_lines = [
            f"> Event Type: `{event.event_type}`",
            f"> Run ID: `{event.run_id or '-'}`",
            f"> Bundle ID: `{event.bundle_id or '-'}`",
            f"> Task ID: `{event.task_id or '-'}`",
        ]
        actor = str(metadata.get("actor") or "").strip()
        if actor:
            detail_lines.append(f"> Actor: `{actor}`")
        tool_name = str(metadata.get("tool_name") or "").strip()
        if tool_name:
            detail_lines.append(f"> Source Tool: `{tool_name}`")

        return {
            "channel": self.channel,
            "dry_run": True,
            "msgtype": "markdown",
            "markdown": {
                "content": "\n".join(
                    [f"**{event.title}**", event.summary or event.title, *detail_lines]
                ),
            },
        }
