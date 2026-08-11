from __future__ import annotations

from pathlib import Path

import pytest

from pm_pal.connectors import (
    ConnectorRegistry,
    LocalFileConnector,
    SourceType,
    URLConnector,
)


def test_local_file_connector_reads_markdown(tmp_path: Path) -> None:
    source_path = tmp_path / "prd.md"
    source_path.write_text("# PRD\n\ncontent", encoding="utf-8")

    document = LocalFileConnector().get_content(str(source_path))

    assert document.source_type == SourceType.local_file
    assert document.source == str(source_path.resolve())
    assert document.title == "prd"
    assert document.content_markdown == "# PRD\n\ncontent"
    assert document.metadata.mime_type == "text/markdown"
    assert document.metadata.extra["extension"] == ".md"


def test_local_file_connector_reads_text(tmp_path: Path) -> None:
    source_path = tmp_path / "notes.txt"
    source_path.write_text("plain text input", encoding="utf-8")

    document = LocalFileConnector().get_content(str(source_path))

    assert document.source_type == SourceType.local_file
    assert document.content_markdown == "plain text input"
    assert document.metadata.mime_type == "text/plain"
    assert document.metadata.size_bytes == source_path.stat().st_size


def test_local_file_connector_rejects_unsupported_suffix(tmp_path: Path) -> None:
    source_path = tmp_path / "spec.pdf"
    source_path.write_text("binary-ish", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported local file source suffix"):
        LocalFileConnector().get_content(str(source_path))


def test_connector_registry_routes_url_sources() -> None:
    connector = ConnectorRegistry().resolve("https://openai.com/spec")

    assert isinstance(connector, URLConnector)


def test_url_connector_can_handle_http_sources() -> None:
    connector = URLConnector()

    assert connector.can_handle("https://openai.com/spec") is True
    assert connector.can_handle("http://example.org/spec") is True
    assert connector.can_handle("ftp://example.org/spec") is False
    assert connector.can_handle("not-a-url") is False
