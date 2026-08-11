"""Connector for local Markdown and plain-text requirement files."""

from __future__ import annotations

from pathlib import Path

from pm_pal.connectors.base import BaseConnector
from pm_pal.connectors.errors import (
    ConnectorNotFoundError,
    ConnectorUnsupportedSourceError,
    ConnectorValidationError,
)
from pm_pal.connectors.schemas import SourceDocument, SourceMetadata, SourceType


class LocalFileConnector(BaseConnector):
    """Read supported local files and normalize them into ``SourceDocument``."""

    SUPPORTED_SUFFIXES = {".md", ".txt"}
    MIME_TYPES = {
        ".md": "text/markdown",
        ".txt": "text/plain",
    }

    def can_handle(self, source: str) -> bool:
        path = Path(source)
        return path.suffix.lower() in self.SUPPORTED_SUFFIXES

    def get_content(self, source: str) -> SourceDocument:
        path = Path(source)
        suffix = path.suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            raise ConnectorUnsupportedSourceError(
                f"Unsupported local file source suffix: {suffix or '<none>'}",
                source=str(source),
                details={"connector": "local_file", "suffix": suffix or "<none>"},
            )

        if not path.exists():
            raise ConnectorNotFoundError(
                f"Source file does not exist: {source}",
                source=str(source),
                details={"connector": "local_file"},
            )

        if not path.is_file():
            raise ConnectorValidationError(
                f"Source path is not a file: {source}",
                source=str(source),
                details={"connector": "local_file"},
            )

        resolved = path.resolve()
        content = resolved.read_text(encoding="utf-8")
        metadata = SourceMetadata(
            mime_type=self.MIME_TYPES[suffix],
            encoding="utf-8",
            size_bytes=resolved.stat().st_size,
            extra={"extension": suffix},
        )
        return SourceDocument(
            source_type=SourceType.local_file,
            source=str(resolved),
            title=resolved.stem,
            content_markdown=content,
            metadata=metadata,
        )
