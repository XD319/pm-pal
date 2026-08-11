"""Abstract connector contract for normalized source ingestion."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pm_pal.connectors.schemas import SourceDocument


class BaseConnector(ABC):
    """Common interface for fetching arbitrary requirement sources."""

    @abstractmethod
    def can_handle(self, source: str) -> bool:
        """Return ``True`` when this connector supports the given source."""

    @abstractmethod
    def get_content(self, source: str) -> SourceDocument:
        """Fetch the source and normalize it into a :class:`SourceDocument`."""
