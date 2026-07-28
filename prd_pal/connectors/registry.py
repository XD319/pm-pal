"""Connector registry for resolving normalized source handlers."""

from __future__ import annotations

from prd_pal.connectors.base import BaseConnector
from prd_pal.connectors.feishu import FeishuConnector
from prd_pal.connectors.github import GitHubConnector
from prd_pal.connectors.local_file import LocalFileConnector
from prd_pal.connectors.notion import NotionConnector
from prd_pal.connectors.url import URLConnector


class ConnectorRegistry:
    """Resolve a source string to the first connector that supports it."""

    def __init__(self, connectors: list[BaseConnector] | None = None) -> None:
        self._connectors = connectors or [
            FeishuConnector(),
            GitHubConnector(),
            NotionConnector(),
            URLConnector(),
            LocalFileConnector(),
        ]

    def resolve(self, source: str) -> BaseConnector:
        for connector in self._connectors:
            if connector.can_handle(source):
                return connector
        raise ValueError(f"No connector available for source: {source}")
