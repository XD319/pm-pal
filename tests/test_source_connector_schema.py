from __future__ import annotations

import json

import pytest

from pm_pal.connectors import (
    BaseConnector,
    SourceDocument,
    SourceMetadata,
    SourceType,
)


def test_source_document_schema_instantiates() -> None:
    document = SourceDocument(
        source_type=SourceType.local_file,
        source="docs/prd.md",
        title="Product Requirements",
        content_markdown="# Product Requirements",
    )

    payload = json.loads(document.model_dump_json())
    assert payload["source_type"] == "local_file"
    assert payload["source"] == "docs/prd.md"
    assert payload["title"] == "Product Requirements"
    assert payload["content_markdown"] == "# Product Requirements"
    assert payload["metadata"]["encoding"] == "utf-8"
    assert payload["fetched_at"]


def test_source_metadata_defaults_are_isolated() -> None:
    first = SourceMetadata()
    second = SourceMetadata()

    assert first.mime_type == ""
    assert first.encoding == "utf-8"
    assert first.size_bytes is None
    assert first.extra == {}
    assert first.extra is not second.extra


def test_source_document_metadata_defaults_are_applied() -> None:
    document = SourceDocument(
        source_type=SourceType.url,
        source="https://example.com/spec",
    )

    assert document.metadata.mime_type == ""
    assert document.metadata.encoding == "utf-8"
    assert document.metadata.size_bytes is None
    assert document.metadata.extra == {}


def test_base_connector_is_abstract() -> None:
    with pytest.raises(TypeError):
        BaseConnector()


def test_base_connector_can_be_implemented_by_minimal_subclass() -> None:
    class DummyConnector(BaseConnector):
        def can_handle(self, source: str) -> bool:
            return source.startswith("dummy://")

        def get_content(self, source: str) -> SourceDocument:
            return SourceDocument(
                source_type=SourceType.feishu,
                source=source,
                title="Dummy Source",
                content_markdown="stub content",
            )

    connector = DummyConnector()

    assert connector.can_handle("dummy://token") is True
    document = connector.get_content("dummy://token")
    assert document.source == "dummy://token"
    assert document.title == "Dummy Source"
    assert document.content_markdown == "stub content"
