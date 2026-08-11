from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from pm_pal.connectors import (
    ConnectorRegistry,
    NotionConnector,
    SourceType,
    get_connector_error_payload,
)
from pm_pal.connectors.errors import ConnectorRateLimitError, ConnectorValidationError
from pm_pal.connectors.notion import (
    NotionAuthenticationError,
    NotionHTTPResponse,
    NotionPageNotFoundError,
    NotionPermissionDeniedError,
)

NOTION_PAGE_ID = "0123456789abcdef0123456789abcdef"
NOTION_PAGE_URL = f"https://workspace.notion.site/Product-Spec-{NOTION_PAGE_ID}"


@dataclass
class _RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    params: dict[str, Any]


class _FakeNotionHTTPClient:
    def __init__(self, responses: list[NotionHTTPResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[_RecordedRequest] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> NotionHTTPResponse:
        self.requests.append(
            _RecordedRequest(
                method=method,
                path=path,
                headers=dict(headers or {}),
                params=dict(params or {}),
            )
        )
        if not self._responses:
            raise AssertionError(
                "Unexpected Notion HTTP request with no prepared response"
            )
        return self._responses.pop(0)


def _page_payload(*, title: str = "Product Spec") -> dict[str, Any]:
    return {
        "object": "page",
        "id": NOTION_PAGE_ID,
        "created_time": "2026-03-24T10:00:00.000Z",
        "last_edited_time": "2026-03-25T11:00:00.000Z",
        "url": NOTION_PAGE_URL,
        "properties": {
            "title": {
                "id": "title",
                "type": "title",
                "title": [
                    {
                        "type": "text",
                        "plain_text": title,
                        "text": {"content": title},
                        "annotations": {},
                    }
                ],
            }
        },
    }


@pytest.fixture
def notion_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARRDP_NOTION_TOKEN", "secret_notion_token")
    monkeypatch.setenv(
        "MARRDP_NOTION_API_BASE_URL", "https://api.notion.example.test/v1"
    )
    monkeypatch.setenv("MARRDP_NOTION_API_VERSION", "2022-06-28")


def test_notion_connector_can_handle_custom_scheme_and_notion_urls() -> None:
    connector = NotionConnector()

    assert connector.can_handle(f"notion://page/{NOTION_PAGE_ID}") is True
    assert connector.can_handle(NOTION_PAGE_URL) is True
    assert connector.can_handle("https://docs.example.com/spec") is False


def test_connector_registry_routes_notion_sources() -> None:
    connector = ConnectorRegistry().resolve(NOTION_PAGE_URL)

    assert isinstance(connector, NotionConnector)


def test_notion_connector_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARRDP_NOTION_TOKEN", raising=False)

    with pytest.raises(
        NotionAuthenticationError, match="authentication failed"
    ) as exc_info:
        NotionConnector().get_content(f"notion://page/{NOTION_PAGE_ID}")

    payload = get_connector_error_payload(exc_info.value)
    assert payload is not None
    assert payload.model_dump()["code"] == "authentication_failed"
    assert "MARRDP_NOTION_TOKEN" in payload.message


def test_notion_connector_validates_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARRDP_NOTION_TOKEN", "secret_notion_token")
    monkeypatch.setenv("MARRDP_NOTION_API_BASE_URL", "not-a-url")

    with pytest.raises(ConnectorValidationError, match="MARRDP_NOTION_API_BASE_URL"):
        NotionConnector().get_content(NOTION_PAGE_URL)


def test_notion_connector_get_content_fetches_page_blocks_and_metadata(
    notion_env: None,
) -> None:
    http_client = _FakeNotionHTTPClient(
        [
            NotionHTTPResponse(status_code=200, json_body=_page_payload(), headers={}),
            NotionHTTPResponse(
                status_code=200,
                json_body={
                    "results": [
                        {
                            "object": "block",
                            "id": "heading-block",
                            "type": "heading_1",
                            "heading_1": {
                                "rich_text": [
                                    {
                                        "plain_text": "Launch Review",
                                        "annotations": {"bold": True},
                                        "text": {"content": "Launch Review"},
                                    }
                                ]
                            },
                            "has_children": False,
                        },
                        {
                            "object": "block",
                            "id": "list-block",
                            "type": "bulleted_list_item",
                            "bulleted_list_item": {
                                "rich_text": [
                                    {
                                        "plain_text": "Parent item",
                                        "annotations": {},
                                        "text": {"content": "Parent item"},
                                    }
                                ]
                            },
                            "has_children": True,
                        },
                    ],
                    "has_more": True,
                    "next_cursor": "cursor-2",
                },
                headers={},
            ),
            NotionHTTPResponse(
                status_code=200,
                json_body={
                    "results": [
                        {
                            "object": "block",
                            "id": "child-paragraph",
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": [
                                    {
                                        "plain_text": "Nested detail",
                                        "annotations": {"italic": True},
                                        "text": {"content": "Nested detail"},
                                    }
                                ]
                            },
                            "has_children": False,
                        }
                    ],
                    "has_more": False,
                    "next_cursor": None,
                },
                headers={},
            ),
            NotionHTTPResponse(
                status_code=200,
                json_body={
                    "results": [
                        {
                            "object": "block",
                            "id": "paragraph-block",
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": [
                                    {
                                        "plain_text": "Second page of blocks",
                                        "annotations": {},
                                        "text": {"content": "Second page of blocks"},
                                    }
                                ]
                            },
                            "has_children": False,
                        }
                    ],
                    "has_more": False,
                    "next_cursor": None,
                },
                headers={},
            ),
        ]
    )

    document = NotionConnector(http_client=http_client).get_content(NOTION_PAGE_URL)

    assert document.source_type == SourceType.notion
    assert document.source == NOTION_PAGE_URL
    assert document.title == "Product Spec"
    assert (
        document.content_markdown
        == "# **Launch Review**\n\n- Parent item\n  *Nested detail*\n\nSecond page of blocks"
    )
    assert document.metadata.mime_type == "text/markdown"
    assert document.metadata.extra == {
        "source_kind": "https_url",
        "host": "workspace.notion.site",
        "path": f"/Product-Spec-{NOTION_PAGE_ID}",
        "base_url": "https://api.notion.example.test/v1",
        "api_version": "2022-06-28",
        "page_id": NOTION_PAGE_ID,
        "title": "Product Spec",
        "created_time": "2026-03-24T10:00:00.000Z",
        "last_edited_time": "2026-03-25T11:00:00.000Z",
        "url": NOTION_PAGE_URL,
    }
    assert [request.path for request in http_client.requests] == [
        f"/pages/{NOTION_PAGE_ID}",
        f"/blocks/{NOTION_PAGE_ID}/children",
        "/blocks/list-block/children",
        f"/blocks/{NOTION_PAGE_ID}/children",
    ]
    assert http_client.requests[1].params == {"page_size": 100}
    assert http_client.requests[3].params == {
        "page_size": 100,
        "start_cursor": "cursor-2",
    }
    assert (
        http_client.requests[0].headers["Authorization"] == "Bearer secret_notion_token"
    )
    assert http_client.requests[0].headers["Notion-Version"] == "2022-06-28"


@pytest.mark.parametrize(
    ("status_code", "expected_exception", "expected_code"),
    [
        (401, NotionAuthenticationError, "authentication_failed"),
        (403, NotionPermissionDeniedError, "permission_denied"),
        (404, NotionPageNotFoundError, "not_found"),
        (429, ConnectorRateLimitError, "rate_limited"),
    ],
)
def test_notion_connector_maps_http_errors(
    notion_env: None,
    status_code: int,
    expected_exception: type[Exception],
    expected_code: str,
) -> None:
    http_client = _FakeNotionHTTPClient(
        [
            NotionHTTPResponse(
                status_code=status_code,
                json_body={"message": "request failed"},
                headers={"retry-after": "7"} if status_code == 429 else {},
            )
        ]
    )

    with pytest.raises(expected_exception) as exc_info:
        NotionConnector(http_client=http_client).get_content(NOTION_PAGE_URL)

    payload = get_connector_error_payload(exc_info.value)
    assert payload is not None
    assert payload.model_dump()["code"] == expected_code
    if status_code == 429:
        assert payload.details["retry_after"] == "7"


def test_notion_connector_blocks_to_markdown_covers_supported_block_types() -> None:
    connector = NotionConnector()

    markdown = connector._blocks_to_markdown(
        [
            {
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "plain_text": "Plain",
                            "annotations": {},
                            "text": {"content": "Plain"},
                        }
                    ]
                },
            },
            {
                "type": "heading_1",
                "heading_1": {
                    "rich_text": [
                        {
                            "plain_text": "H1",
                            "annotations": {},
                            "text": {"content": "H1"},
                        }
                    ]
                },
            },
            {
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [
                        {
                            "plain_text": "H2",
                            "annotations": {},
                            "text": {"content": "H2"},
                        }
                    ]
                },
            },
            {
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [
                        {
                            "plain_text": "H3",
                            "annotations": {},
                            "text": {"content": "H3"},
                        }
                    ]
                },
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {
                            "plain_text": "Bullet",
                            "annotations": {},
                            "text": {"content": "Bullet"},
                        }
                    ]
                },
                "children": [
                    {
                        "type": "numbered_list_item",
                        "numbered_list_item": {
                            "rich_text": [
                                {
                                    "plain_text": "Nested Number",
                                    "annotations": {},
                                    "text": {"content": "Nested Number"},
                                }
                            ]
                        },
                    }
                ],
            },
            {
                "type": "numbered_list_item",
                "numbered_list_item": {
                    "rich_text": [
                        {
                            "plain_text": "Number",
                            "annotations": {},
                            "text": {"content": "Number"},
                        }
                    ]
                },
            },
            {
                "type": "code",
                "code": {
                    "language": "python",
                    "rich_text": [
                        {
                            "plain_text": "print('hi')",
                            "annotations": {},
                            "text": {"content": "print('hi')"},
                        }
                    ],
                },
            },
            {
                "type": "toggle",
                "toggle": {
                    "rich_text": [
                        {
                            "plain_text": "More",
                            "annotations": {},
                            "text": {"content": "More"},
                        }
                    ]
                },
                "children": [
                    {
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {
                                    "plain_text": "Hidden",
                                    "annotations": {},
                                    "text": {"content": "Hidden"},
                                }
                            ]
                        },
                    }
                ],
            },
            {
                "type": "to_do",
                "to_do": {
                    "checked": False,
                    "rich_text": [
                        {
                            "plain_text": "Todo",
                            "annotations": {},
                            "text": {"content": "Todo"},
                        }
                    ],
                },
            },
            {
                "type": "to_do",
                "to_do": {
                    "checked": True,
                    "rich_text": [
                        {
                            "plain_text": "Done",
                            "annotations": {},
                            "text": {"content": "Done"},
                        }
                    ],
                },
            },
            {
                "type": "quote",
                "quote": {
                    "rich_text": [
                        {
                            "plain_text": "Quoted",
                            "annotations": {},
                            "text": {"content": "Quoted"},
                        }
                    ]
                },
            },
            {"type": "divider", "divider": {}},
            {
                "type": "image",
                "image": {"external": {"url": "https://example.com/image.png"}},
            },
            {
                "type": "table",
                "table": {"table_width": 2},
                "children": [
                    {
                        "type": "table_row",
                        "table_row": {
                            "cells": [
                                [
                                    {
                                        "plain_text": "Name",
                                        "annotations": {},
                                        "text": {"content": "Name"},
                                    }
                                ],
                                [
                                    {
                                        "plain_text": "Value",
                                        "annotations": {},
                                        "text": {"content": "Value"},
                                    }
                                ],
                            ]
                        },
                    },
                    {
                        "type": "table_row",
                        "table_row": {
                            "cells": [
                                [
                                    {
                                        "plain_text": "A",
                                        "annotations": {},
                                        "text": {"content": "A"},
                                    }
                                ],
                                [
                                    {
                                        "plain_text": "1",
                                        "annotations": {},
                                        "text": {"content": "1"},
                                    }
                                ],
                            ]
                        },
                    },
                ],
            },
            {"type": "bookmark", "bookmark": {"url": "https://example.com"}},
        ]
    )

    assert markdown == (
        "Plain\n\n"
        "# H1\n\n"
        "## H2\n\n"
        "### H3\n\n"
        "- Bullet\n"
        "  1. Nested Number\n\n"
        "1. Number\n\n"
        "```python\nprint('hi')\n```\n\n"
        "<details><summary>More</summary>\n\n"
        "  Hidden\n\n"
        "</details>\n\n"
        "- [ ] Todo\n\n"
        "- [x] Done\n\n"
        "> Quoted\n\n"
        "---\n\n"
        "![image](https://example.com/image.png)\n\n"
        "| Name | Value |\n"
        "| --- | --- |\n"
        "| A | 1 |\n\n"
        "<!-- Unsupported Notion block types skipped: bookmark -->"
    )


def test_notion_connector_extract_rich_text_formats_annotations_and_links() -> None:
    connector = NotionConnector()

    rich_text = connector._extract_rich_text(
        [
            {
                "plain_text": "Bold",
                "annotations": {"bold": True},
                "text": {"content": "Bold"},
            },
            {
                "plain_text": "Italic",
                "annotations": {"italic": True},
                "text": {"content": "Italic"},
            },
            {
                "plain_text": "Code",
                "annotations": {"code": True},
                "text": {"content": "Code"},
            },
            {
                "plain_text": "Link",
                "annotations": {},
                "text": {"content": "Link", "link": {"url": "https://example.com"}},
            },
        ]
    )

    assert rich_text == "**Bold***Italic*`Code`[Link](https://example.com)"
