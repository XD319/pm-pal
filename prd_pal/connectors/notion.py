"""Authenticated connector for Notion page ingestion through the public API."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from prd_pal.connectors.auth import ConnectorAuthConfig, ConnectorAuthType
from prd_pal.connectors.base import BaseConnector
from prd_pal.connectors.errors import (
    ConnectorAuthError,
    ConnectorNetworkError,
    ConnectorNotFoundError,
    ConnectorPermissionError,
    ConnectorRateLimitError,
    ConnectorUnsupportedSourceError,
    ConnectorValidationError,
)
from prd_pal.connectors.schemas import SourceDocument, SourceMetadata, SourceType


@dataclass(frozen=True, slots=True)
class NotionSourceRef:
    raw_source: str
    source_kind: str
    host: str
    path: str
    page_id: str


@dataclass(frozen=True, slots=True)
class NotionConfig:
    auth: ConnectorAuthConfig
    base_url: str
    api_version: str


@dataclass(frozen=True, slots=True)
class NotionHTTPResponse:
    status_code: int
    json_body: dict[str, Any]
    headers: dict[str, str]


class NotionAuthenticationError(ConnectorAuthError):
    """Raised when Notion credentials are missing or rejected."""


class NotionPermissionDeniedError(ConnectorPermissionError):
    """Raised when a Notion integration lacks access to a recognized page."""


class NotionPageNotFoundError(ConnectorNotFoundError):
    """Raised when a recognized Notion page cannot be found."""


class _NotionHTTPClient(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> NotionHTTPResponse: ...


class _DefaultNotionHTTPClient:
    DEFAULT_TIMEOUT_SECONDS = 10.0
    USER_AGENT = "marrdp-requirement-review/1.0"

    def __init__(
        self, *, base_url: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        self._timeout_seconds = timeout_seconds

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> NotionHTTPResponse:
        normalized_path = "/" + str(path or "").lstrip("/")
        url = f"{self._base_url}{normalized_path}"
        request_headers = {
            "Accept": "application/json",
            "User-Agent": self.USER_AGENT,
        }
        if headers:
            request_headers.update(headers)

        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.request(
                    method.upper(),
                    url,
                    headers=request_headers,
                    params=params,
                )
        except httpx.RequestError as exc:
            raise ConnectorNetworkError(
                f"Network unavailable while fetching Notion source from '{url}': {exc}",
                source=url,
                details={"connector": "notion_http"},
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {"results": payload} if isinstance(payload, list) else {}
        return NotionHTTPResponse(
            status_code=int(response.status_code),
            json_body=payload,
            headers={
                str(key).lower(): str(value) for key, value in response.headers.items()
            },
        )


class NotionConnector(BaseConnector):
    """Fetch Notion pages and normalize them into markdown content."""

    NOTION_SCHEME = "notion"
    NOTION_HOST_KEYWORDS = ("notion.so", "notion.site")
    TOKEN_ENV = "MARRDP_NOTION_TOKEN"
    BASE_URL_ENV = "MARRDP_NOTION_API_BASE_URL"
    API_VERSION_ENV = "MARRDP_NOTION_API_VERSION"
    DEFAULT_BASE_URL = "https://api.notion.com/v1"
    DEFAULT_API_VERSION = "2022-06-28"
    _NOTION_ID_PATTERN = re.compile(r"([0-9a-fA-F]{32})")

    def __init__(
        self,
        *,
        http_client: _NotionHTTPClient | None = None,
        config: NotionConfig | None = None,
    ) -> None:
        self._http_client = http_client
        self._config_override = config

    def can_handle(self, source: str) -> bool:
        normalized = str(source or "").strip()
        if not normalized:
            return False
        if normalized.lower().startswith(f"{self.NOTION_SCHEME}://"):
            return True

        try:
            parsed = urlparse(normalized)
        except ValueError:
            return False

        if parsed.scheme in {"http", "https"}:
            host = (parsed.netloc or "").lower()
            return any(keyword in host for keyword in self.NOTION_HOST_KEYWORDS)
        return False

    def get_content(self, source: str) -> SourceDocument:
        source_ref = self._parse_source(source)
        config = self._read_config()
        self._ensure_authenticated(config=config, source_ref=source_ref)
        http_client = self._http_client or _DefaultNotionHTTPClient(
            base_url=config.base_url
        )

        page_payload = self._api_request(
            http_client=http_client,
            method="GET",
            path=f"/pages/{source_ref.page_id}",
            source_ref=source_ref,
            config=config,
        )
        blocks = self._fetch_block_children(
            http_client=http_client,
            block_id=source_ref.page_id,
            source_ref=source_ref,
            config=config,
        )
        title = self._extract_page_title(page_payload)
        content_markdown = self._blocks_to_markdown(blocks).strip()

        metadata = SourceMetadata(
            mime_type="text/markdown",
            encoding="utf-8",
            size_bytes=len(content_markdown.encode("utf-8")),
            extra={
                "source_kind": source_ref.source_kind,
                "host": source_ref.host,
                "path": source_ref.path,
                "base_url": config.base_url,
                "api_version": config.api_version,
                "page_id": source_ref.page_id,
                "title": title,
                "created_time": str(page_payload.get("created_time") or ""),
                "last_edited_time": str(page_payload.get("last_edited_time") or ""),
                "url": str(page_payload.get("url") or source_ref.raw_source),
            },
        )
        return SourceDocument(
            source_type=SourceType.notion,
            source=source_ref.raw_source,
            title=title,
            content_markdown=content_markdown,
            metadata=metadata,
        )

    def _parse_source(self, source: str) -> NotionSourceRef:
        normalized = str(source or "").strip()
        if not self.can_handle(normalized):
            raise ConnectorUnsupportedSourceError(
                f"Unsupported Notion source: {source}",
                source=str(source),
                details={"connector": "notion"},
            )

        parsed = urlparse(normalized)
        is_custom_scheme = parsed.scheme.lower() == self.NOTION_SCHEME
        host = (parsed.netloc or "").strip().lower()
        path = parsed.path or ""
        page_id = self._extract_page_id(path)
        if not page_id:
            raise ConnectorValidationError(
                f"Notion source is missing a recognizable page identifier: {normalized}",
                source=normalized,
                details={"connector": "notion", "path": path},
            )
        return NotionSourceRef(
            raw_source=normalized,
            source_kind="notion_scheme" if is_custom_scheme else "https_url",
            host=self.NOTION_SCHEME if is_custom_scheme else host,
            path=path,
            page_id=page_id,
        )

    def _read_config(self) -> NotionConfig:
        if self._config_override is not None:
            return self._config_override
        raw_base_url = str(
            os.getenv(self.BASE_URL_ENV, self.DEFAULT_BASE_URL) or self.DEFAULT_BASE_URL
        ).strip()
        parsed_base_url = urlparse(raw_base_url)
        if (
            parsed_base_url.scheme not in {"http", "https"}
            or not parsed_base_url.netloc
        ):
            raise ConnectorValidationError(
                f"{self.BASE_URL_ENV} must be an absolute http(s) URL, got: {raw_base_url or '<empty>'}",
                details={"connector": "notion", "base_url_env": self.BASE_URL_ENV},
            )

        token = str(os.getenv(self.TOKEN_ENV, "") or "").strip()
        if token:
            auth = ConnectorAuthConfig(
                auth_type=ConnectorAuthType.bearer_token,
                token=token,
                extra={"token_env": self.TOKEN_ENV},
            )
        else:
            auth = ConnectorAuthConfig(
                extra={
                    "expected_auth_type": "bearer_token",
                    "token_env": self.TOKEN_ENV,
                }
            )

        return NotionConfig(
            auth=auth,
            base_url=raw_base_url.rstrip("/"),
            api_version=str(
                os.getenv(self.API_VERSION_ENV, self.DEFAULT_API_VERSION)
                or self.DEFAULT_API_VERSION
            ).strip(),
        )

    def _ensure_authenticated(
        self, *, config: NotionConfig, source_ref: NotionSourceRef
    ) -> None:
        if config.auth.auth_type != ConnectorAuthType.bearer_token:
            raise NotionAuthenticationError(
                "Notion authentication failed because an integration token is missing. "
                f"Set {self.TOKEN_ENV} before fetching '{source_ref.raw_source}'.",
                source=source_ref.raw_source,
                details={
                    "connector": "notion",
                    "auth_type": str(config.auth.auth_type),
                },
            )

    def _fetch_block_children(
        self,
        *,
        http_client: _NotionHTTPClient,
        block_id: str,
        source_ref: NotionSourceRef,
        config: NotionConfig,
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        start_cursor: str | None = None

        while True:
            params: dict[str, Any] = {"page_size": 100}
            if start_cursor:
                params["start_cursor"] = start_cursor
            payload = self._api_request(
                http_client=http_client,
                method="GET",
                path=f"/blocks/{block_id}/children",
                params=params,
                source_ref=source_ref,
                config=config,
            )
            page_blocks = payload.get("results")
            if not isinstance(page_blocks, list):
                raise ConnectorValidationError(
                    f"Notion block response is missing a results array for source '{source_ref.raw_source}'",
                    source=source_ref.raw_source,
                    details={"connector": "notion", "block_id": block_id},
                )
            for block in page_blocks:
                if not isinstance(block, dict):
                    continue
                normalized_block = dict(block)
                if normalized_block.get("has_children"):
                    child_id = str(normalized_block.get("id") or "").strip()
                    if child_id:
                        normalized_block["children"] = self._fetch_block_children(
                            http_client=http_client,
                            block_id=child_id,
                            source_ref=source_ref,
                            config=config,
                        )
                blocks.append(normalized_block)

            if not payload.get("has_more"):
                break
            start_cursor = str(payload.get("next_cursor") or "").strip() or None
            if not start_cursor:
                break

        return blocks

    def _api_request(
        self,
        *,
        http_client: _NotionHTTPClient,
        method: str,
        path: str,
        source_ref: NotionSourceRef,
        config: NotionConfig,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {config.auth.token}",
            "Notion-Version": config.api_version,
        }
        response = http_client.request(method, path, headers=headers, params=params)
        payload = dict(response.json_body or {})
        if 200 <= response.status_code < 300:
            return payload

        message = self._extract_api_message(payload)
        details = {
            "connector": "notion",
            "status_code": response.status_code,
            "page_id": source_ref.page_id,
        }
        if response.status_code == 401:
            raise NotionAuthenticationError(
                f"Notion authentication failed for source '{source_ref.raw_source}': HTTP 401: {message}",
                source=source_ref.raw_source,
                details=details,
            )
        if response.status_code == 403:
            raise NotionPermissionDeniedError(
                f"Permission denied while fetching Notion source '{source_ref.raw_source}': HTTP 403: {message}",
                source=source_ref.raw_source,
                details=details,
            )
        if response.status_code == 404:
            raise NotionPageNotFoundError(
                f"Notion page not found for source '{source_ref.raw_source}': HTTP 404: {message}",
                source=source_ref.raw_source,
                details=details,
            )
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after", "")
            details["retry_after"] = retry_after
            raise ConnectorRateLimitError(
                f"Notion API rate limited source '{source_ref.raw_source}': HTTP 429: {message}",
                source=source_ref.raw_source,
                details=details,
            )
        raise ConnectorNetworkError(
            f"Failed to fetch Notion source '{source_ref.raw_source}': HTTP {response.status_code}: {message}",
            source=source_ref.raw_source,
            details=details,
            retryable=response.status_code >= 500,
        )

    def _extract_page_title(self, page_payload: dict[str, Any]) -> str:
        properties = page_payload.get("properties")
        if isinstance(properties, dict):
            for property_payload in properties.values():
                if not isinstance(property_payload, dict):
                    continue
                if property_payload.get("type") != "title":
                    continue
                title = self._extract_rich_text(property_payload.get("title"))
                if title:
                    return title
        return "notion-page"

    def _blocks_to_markdown(
        self, blocks: list[dict[str, Any]], *, depth: int = 0
    ) -> str:
        lines: list[str] = []
        unknown_types: list[str] = []

        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").strip()
            if not block_type:
                continue

            indent = "  " * depth
            block_payload = block.get(block_type)
            if not isinstance(block_payload, dict):
                block_payload = {}
            children = block.get("children")
            child_blocks = children if isinstance(children, list) else []

            rendered = self._render_block(
                block_type=block_type,
                block_payload=block_payload,
                child_blocks=child_blocks,
                depth=depth,
                indent=indent,
            )
            if rendered is None:
                unknown_types.append(block_type)
                continue
            if rendered:
                lines.append(rendered)

        text = "\n\n".join(segment for segment in lines if segment.strip()).strip("\n")
        if unknown_types:
            suffix = ", ".join(sorted(set(unknown_types)))
            comment = f"<!-- Unsupported Notion block types skipped: {suffix} -->"
            text = f"{text}\n\n{comment}".strip("\n") if text else comment
        return text

    def _render_block(
        self,
        *,
        block_type: str,
        block_payload: dict[str, Any],
        child_blocks: list[dict[str, Any]],
        depth: int,
        indent: str,
    ) -> str | None:
        if block_type == "paragraph":
            return f"{indent}{self._extract_rich_text(block_payload.get('rich_text'))}".rstrip()
        if block_type == "heading_1":
            return f"{indent}# {self._extract_rich_text(block_payload.get('rich_text'))}".rstrip()
        if block_type == "heading_2":
            return f"{indent}## {self._extract_rich_text(block_payload.get('rich_text'))}".rstrip()
        if block_type == "heading_3":
            return f"{indent}### {self._extract_rich_text(block_payload.get('rich_text'))}".rstrip()
        if block_type == "bulleted_list_item":
            return self._render_list_item(
                prefix="- ",
                text=self._extract_rich_text(block_payload.get("rich_text")),
                child_blocks=child_blocks,
                depth=depth,
                indent=indent,
            )
        if block_type == "numbered_list_item":
            return self._render_list_item(
                prefix="1. ",
                text=self._extract_rich_text(block_payload.get("rich_text")),
                child_blocks=child_blocks,
                depth=depth,
                indent=indent,
            )
        if block_type == "code":
            language = str(block_payload.get("language") or "").strip()
            code_text = self._extract_rich_text(block_payload.get("rich_text"))
            return f"{indent}```{language}\n{code_text}\n{indent}```".rstrip()
        if block_type == "toggle":
            summary = self._extract_rich_text(block_payload.get("rich_text"))
            children_markdown = self._blocks_to_markdown(child_blocks, depth=depth + 1)
            if children_markdown:
                return f"{indent}<details><summary>{summary}</summary>\n\n{children_markdown}\n\n{indent}</details>"
            return f"{indent}<details><summary>{summary}</summary></details>"
        if block_type == "to_do":
            checked = bool(block_payload.get("checked"))
            prefix = "- [x] " if checked else "- [ ] "
            return self._render_list_item(
                prefix=prefix,
                text=self._extract_rich_text(block_payload.get("rich_text")),
                child_blocks=child_blocks,
                depth=depth,
                indent=indent,
            )
        if block_type == "quote":
            text = self._extract_rich_text(block_payload.get("rich_text"))
            return "\n".join(
                f"{indent}> {line}".rstrip() for line in (text.splitlines() or [""])
            )
        if block_type == "divider":
            return f"{indent}---"
        if block_type == "image":
            url = self._extract_file_url(block_payload)
            if not url:
                return ""
            return f"{indent}![image]({url})"
        if block_type == "table":
            return self._render_table(child_blocks, indent=indent)
        if block_type == "table_row":
            return None
        return None

    def _render_list_item(
        self,
        *,
        prefix: str,
        text: str,
        child_blocks: list[dict[str, Any]],
        depth: int,
        indent: str,
    ) -> str:
        head = f"{indent}{prefix}{text}".rstrip()
        if not child_blocks:
            return head
        child_markdown = self._blocks_to_markdown(child_blocks, depth=depth + 1)
        return f"{head}\n{child_markdown}".rstrip() if child_markdown else head

    def _render_table(self, child_blocks: list[dict[str, Any]], *, indent: str) -> str:
        rows: list[list[str]] = []
        for child in child_blocks:
            if not isinstance(child, dict) or child.get("type") != "table_row":
                continue
            row_payload = child.get("table_row")
            if not isinstance(row_payload, dict):
                continue
            cells = row_payload.get("cells")
            if not isinstance(cells, list):
                continue
            rows.append([self._extract_rich_text(cell) for cell in cells])

        if not rows:
            return ""
        column_count = max(len(row) for row in rows)
        normalized_rows = [row + [""] * (column_count - len(row)) for row in rows]
        header = normalized_rows[0]
        divider = ["---"] * column_count
        body = normalized_rows[1:]

        lines = [
            f"{indent}| " + " | ".join(header) + " |",
            f"{indent}| " + " | ".join(divider) + " |",
        ]
        for row in body:
            lines.append(f"{indent}| " + " | ".join(row) + " |")
        return "\n".join(lines)

    def _extract_rich_text(self, rich_text: Any) -> str:
        if not isinstance(rich_text, list):
            return ""

        segments: list[str] = []
        for item in rich_text:
            if not isinstance(item, dict):
                continue
            text = str(item.get("plain_text") or "").strip()
            if not text:
                continue

            annotations = item.get("annotations")
            if not isinstance(annotations, dict):
                annotations = {}
            if annotations.get("code"):
                text = f"`{text}`"
            if annotations.get("bold"):
                text = f"**{text}**"
            if annotations.get("italic"):
                text = f"*{text}*"

            link_url = ""
            href = item.get("href")
            if isinstance(href, str) and href.strip():
                link_url = href.strip()
            else:
                text_payload = item.get("text")
                if isinstance(text_payload, dict):
                    link_payload = text_payload.get("link")
                    if isinstance(link_payload, dict):
                        link_url = str(link_payload.get("url") or "").strip()
            if link_url:
                text = f"[{text}]({link_url})"

            segments.append(text)
        return "".join(segments)

    def _extract_file_url(self, block_payload: dict[str, Any]) -> str:
        for key in ("external", "file"):
            value = block_payload.get(key)
            if not isinstance(value, dict):
                continue
            url = str(value.get("url") or "").strip()
            if url:
                return url
        return ""

    def _extract_api_message(self, payload: dict[str, Any]) -> str:
        for key in ("message", "msg", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "unknown Notion API error"

    def _extract_page_id(self, path: str) -> str:
        segments = [segment for segment in str(path or "").split("/") if segment]
        if not segments:
            return ""

        last_segment = segments[-1]
        for token in reversed(last_segment.split("-")):
            candidate = str(token or "").strip().lower()
            if re.fullmatch(r"[0-9a-f]{32}", candidate):
                return candidate

        normalized_path = str(path or "").replace("-", "")
        matches = self._NOTION_ID_PATTERN.findall(normalized_path)
        return matches[-1].lower() if matches else ""
