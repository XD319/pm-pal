"""Connector for controlled HTTP(S) text-page ingestion."""

from __future__ import annotations

import ipaddress
import socket
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pm_pal.connectors.base import BaseConnector
from pm_pal.connectors.errors import (
    ConnectorNetworkError,
    ConnectorUnsupportedSourceError,
    ConnectorValidationError,
)
from pm_pal.connectors.normalize import (
    decode_text_body,
    resolve_charset,
    resolve_content_type,
    resolve_declared_length,
)
from pm_pal.connectors.schemas import SourceDocument, SourceMetadata, SourceType


class _URLOpener(Protocol):
    def __call__(self, request: Request, timeout: float = ...) -> Any: ...


class _HTMLTextExtractor(HTMLParser):
    """Convert simple HTML pages into readable plain text for review ingestion."""

    BLOCK_TAGS = {
        "article",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "ol",
        "p",
        "section",
        "table",
        "tr",
        "ul",
    }
    SKIP_TAGS = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []
        self._tag_stack: list[str] = []

    @property
    def title(self) -> str:
        return " ".join(
            part.strip() for part in self._title_parts if part.strip()
        ).strip()

    @property
    def text(self) -> str:
        return "".join(self._text_parts).strip()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.lower()
        self._tag_stack.append(normalized)
        if normalized in self.BLOCK_TAGS:
            self._append_break()

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in self.BLOCK_TAGS:
            self._append_break()
        for index in range(len(self._tag_stack) - 1, -1, -1):
            if self._tag_stack[index] == normalized:
                del self._tag_stack[index]
                break

    def handle_data(self, data: str) -> None:
        if not data:
            return
        active_tags = set(self._tag_stack)
        if active_tags & self.SKIP_TAGS:
            return
        normalized = " ".join(data.split())
        if not normalized:
            return
        if "title" in active_tags:
            self._title_parts.append(normalized)
            return
        if self._text_parts and not self._text_parts[-1].endswith(("\n", " ")):
            self._text_parts.append(" ")
        self._text_parts.append(normalized)

    def _append_break(self) -> None:
        if not self._text_parts:
            return
        if self._text_parts[-1].endswith("\n\n"):
            return
        if self._text_parts[-1].endswith("\n"):
            self._text_parts.append("\n")
        else:
            self._text_parts.append("\n\n")


class URLConnector(BaseConnector):
    """Fetch public HTTP(S) text pages and normalize them into ``SourceDocument``."""

    SUPPORTED_SCHEMES = {"http", "https"}
    SUPPORTED_MIME_TYPES = {
        "application/xhtml+xml",
        "text/html",
        "text/markdown",
        "text/plain",
        "text/x-markdown",
    }
    MAX_RESPONSE_BYTES = 1_000_000
    DEFAULT_TIMEOUT_SECONDS = 10.0
    USER_AGENT = "marrdp-requirement-review/1.0"

    def __init__(
        self,
        *,
        opener: _URLOpener | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self._opener = opener or urlopen
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def can_handle(self, source: str) -> bool:
        try:
            parsed = urlparse(str(source or "").strip())
        except ValueError:
            return False
        return parsed.scheme in self.SUPPORTED_SCHEMES and bool(parsed.netloc)

    def get_content(self, source: str) -> SourceDocument:
        normalized_source = str(source or "").strip()
        parsed = self._parse_supported_url(normalized_source)
        self._assert_public_host(parsed.hostname or "")

        request = Request(
            normalized_source,
            headers={
                "Accept": "text/markdown, text/html, text/plain;q=0.9, text/*;q=0.8",
                "User-Agent": self.USER_AGENT,
            },
            method="GET",
        )

        try:
            response = self._opener(request, timeout=self._timeout_seconds)
        except HTTPError as exc:
            raise ConnectorNetworkError(
                f"Failed to fetch URL source '{normalized_source}': HTTP {exc.code}",
                source=normalized_source,
                details={"connector": "url", "status_code": int(exc.code)},
            ) from exc
        except URLError as exc:
            raise ConnectorNetworkError(
                f"Network unavailable while fetching URL source '{normalized_source}': {self._format_network_reason(exc.reason)}",
                source=normalized_source,
                details={"connector": "url"},
            ) from exc
        except TimeoutError as exc:
            raise ConnectorNetworkError(
                f"Network unavailable while fetching URL source '{normalized_source}': request timed out",
                source=normalized_source,
                details={"connector": "url"},
            ) from exc

        with response:
            final_url = getattr(response, "geturl", lambda: normalized_source)()
            final_parsed = self._parse_supported_url(
                str(final_url or normalized_source).strip()
            )
            self._assert_public_host(final_parsed.hostname or "")

            headers = getattr(response, "headers", None)
            content_type = self._resolve_content_type(headers)
            if not self._is_supported_content_type(content_type):
                raise ConnectorValidationError(
                    "Unsupported URL content type: "
                    f"{content_type or '<missing>'}. Only text/plain, text/markdown, text/html, and other text/* pages are supported.",
                    source=normalized_source,
                    details={
                        "connector": "url",
                        "content_type": content_type or "<missing>",
                    },
                )

            charset = self._resolve_charset(headers)
            raw_content = self._read_body(response, content_type=content_type)
            decoded_content = self._decode_body(raw_content, charset=charset)
            title, content_markdown = self._normalize_content(
                decoded_content, content_type, final_parsed.path
            )

        metadata = SourceMetadata(
            mime_type=content_type,
            encoding=charset,
            size_bytes=len(raw_content),
            extra={
                "requested_url": normalized_source,
                "final_url": final_parsed.geturl(),
            },
        )
        return SourceDocument(
            source_type=SourceType.url,
            source=final_parsed.geturl(),
            title=title,
            content_markdown=content_markdown,
            metadata=metadata,
        )

    def _parse_supported_url(self, source: str):
        if not self.can_handle(source):
            raise ConnectorUnsupportedSourceError(
                f"Unsupported URL source: {source}",
                source=source,
                details={"connector": "url"},
            )
        parsed = urlparse(source)
        if parsed.username or parsed.password:
            raise ConnectorValidationError(
                "URL sources with embedded credentials are not supported",
                source=source,
                details={"connector": "url"},
            )
        return parsed

    def _assert_public_host(self, hostname: str) -> None:
        normalized_host = str(hostname or "").strip().strip(".").lower()
        if not normalized_host:
            raise ConnectorValidationError(
                "URL source is missing a hostname",
                source=hostname,
                details={"connector": "url"},
            )
        if normalized_host in {"localhost", "0.0.0.0"} or normalized_host.endswith(
            ".local"
        ):
            raise ConnectorValidationError(
                f"URL source must be publicly reachable: {hostname}",
                source=hostname,
                details={"connector": "url", "hostname": normalized_host},
            )

        try:
            addresses = socket.getaddrinfo(
                normalized_host, None, proto=socket.IPPROTO_TCP
            )
        except socket.gaierror as exc:
            raise ConnectorNetworkError(
                f"Network unavailable while validating URL source '{hostname}': {exc.strerror or exc}",
                source=hostname,
                details={"connector": "url", "hostname": normalized_host},
            ) from exc

        for _family, _socktype, _proto, _canonname, sockaddr in addresses:
            ip_value = sockaddr[0]
            ip = ipaddress.ip_address(ip_value)
            if any(
                (
                    ip.is_private,
                    ip.is_loopback,
                    ip.is_link_local,
                    ip.is_multicast,
                    ip.is_reserved,
                    ip.is_unspecified,
                )
            ):
                raise ConnectorValidationError(
                    f"URL source must be publicly reachable: {hostname}",
                    source=hostname,
                    details={
                        "connector": "url",
                        "hostname": normalized_host,
                        "ip": ip_value,
                    },
                )

    def _resolve_content_type(self, headers: Any) -> str:
        return resolve_content_type(headers)

    def _resolve_charset(self, headers: Any) -> str:
        return resolve_charset(headers)

    def _is_supported_content_type(self, content_type: str) -> bool:
        normalized = str(content_type or "").strip().lower()
        return normalized.startswith("text/") or normalized in self.SUPPORTED_MIME_TYPES

    def _read_body(self, response: Any, *, content_type: str) -> bytes:
        declared_length = self._resolve_declared_length(
            getattr(response, "headers", None)
        )
        if declared_length is not None and declared_length > self._max_response_bytes:
            raise ConnectorValidationError(
                f"URL content too large to ingest safely: {declared_length} bytes exceeds {self._max_response_bytes} bytes",
                details={
                    "connector": "url",
                    "content_type": content_type or "",
                    "declared_length": declared_length,
                },
            )

        raw_content = response.read(self._max_response_bytes + 1)
        if len(raw_content) > self._max_response_bytes:
            raise ConnectorValidationError(
                f"URL content too large to ingest safely: exceeds {self._max_response_bytes} bytes for {content_type or 'unknown content'}",
                details={
                    "connector": "url",
                    "content_type": content_type or "",
                    "max_bytes": self._max_response_bytes,
                },
            )
        return raw_content

    def _resolve_declared_length(self, headers: Any) -> int | None:
        return resolve_declared_length(headers)

    def _decode_body(self, raw_content: bytes, *, charset: str) -> str:
        return decode_text_body(raw_content, charset=charset)

    def _normalize_content(
        self, content: str, content_type: str, path: str
    ) -> tuple[str, str]:
        normalized_type = str(content_type or "").strip().lower()
        if normalized_type in {"text/html", "application/xhtml+xml"}:
            parser = _HTMLTextExtractor()
            parser.feed(content)
            parser.close()
            fallback_title = self._derive_title_from_path(path)
            title = parser.title or fallback_title
            text_content = parser.text or content.strip()
            return title, text_content
        return self._derive_title_from_path(path), content.strip()

    def _derive_title_from_path(self, path: str) -> str:
        normalized_path = str(path or "").strip() or "/"
        name = PurePosixPath(normalized_path).name
        if not name:
            return "remote-document"
        return PurePosixPath(name).stem or name

    def _format_network_reason(self, reason: object) -> str:
        if isinstance(reason, socket.timeout):
            return "request timed out"
        if isinstance(reason, socket.gaierror):
            return reason.strerror or str(reason)
        return str(reason or "unknown network error")
