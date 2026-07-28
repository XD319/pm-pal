"""Authenticated connector for controlled Feishu and Lark document ingestion."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from prd_pal.connectors.auth import ConnectorAuthConfig, ConnectorAuthType
from prd_pal.connectors.base import BaseConnector
from prd_pal.connectors.errors import (
    ConnectorAuthError,
    ConnectorNetworkError,
    ConnectorNotFoundError,
    ConnectorPermissionError,
    ConnectorUnsupportedSourceError,
    ConnectorValidationError,
)
from prd_pal.connectors.normalize import extract_mapping, extract_message
from prd_pal.connectors.schemas import SourceDocument, SourceMetadata, SourceType


@dataclass(frozen=True, slots=True)
class FeishuSourceRef:
    raw_source: str
    source_kind: str
    host: str
    path: str
    document_kind: str
    document_token: str
    wiki_space: str


@dataclass(frozen=True, slots=True)
class FeishuConfig:
    auth: ConnectorAuthConfig
    base_url: str


@dataclass(frozen=True, slots=True)
class FeishuHTTPResponse:
    status_code: int
    json_body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class FeishuResolvedDocument:
    source_document_kind: str
    document_kind: str
    document_token: str
    title: str = ""
    wiki_space: str = ""


class FeishuAuthenticationError(ConnectorAuthError):
    """Raised when Feishu credentials are missing or rejected."""


class FeishuPermissionDeniedError(ConnectorPermissionError):
    """Raised when the Feishu app cannot access a resolved document."""


class FeishuDocumentNotFoundError(ConnectorNotFoundError):
    """Raised when a recognized Feishu document no longer exists."""


class FeishuUnsupportedDocumentTypeError(ConnectorValidationError):
    """Raised when the recognized Feishu source resolves to an unsupported document type."""


class _FeishuHTTPClient(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> FeishuHTTPResponse: ...


class _DefaultFeishuHTTPClient:
    """Small JSON-oriented HTTP client that is easy to replace in tests."""

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
        json_body: dict[str, Any] | None = None,
    ) -> FeishuHTTPResponse:
        normalized_path = "/" + str(path or "").lstrip("/")
        url = f"{self._base_url}{normalized_path}"
        request_headers = {
            "Accept": "application/json",
            "User-Agent": self.USER_AGENT,
        }
        if headers:
            request_headers.update(headers)

        raw_body: bytes | None = None
        if json_body is not None:
            request_headers.setdefault(
                "Content-Type", "application/json; charset=utf-8"
            )
            raw_body = json.dumps(json_body).encode("utf-8")

        request = Request(
            url, data=raw_body, headers=request_headers, method=method.upper()
        )
        try:
            response = urlopen(request, timeout=self._timeout_seconds)
        except HTTPError as exc:
            return FeishuHTTPResponse(
                status_code=int(exc.code),
                json_body=self._decode_json_body(exc.read()),
            )
        except URLError as exc:
            raise ConnectorNetworkError(
                f"Network unavailable while fetching Feishu source from '{url}': {exc.reason or exc}",
                source=url,
                details={"connector": "feishu_http"},
            ) from exc
        except TimeoutError as exc:
            raise ConnectorNetworkError(
                f"Network unavailable while fetching Feishu source from '{url}': request timed out",
                source=url,
                details={"connector": "feishu_http"},
            ) from exc

        with response:
            status_code_getter = getattr(response, "getcode", None)
            status_code = (
                int(status_code_getter()) if callable(status_code_getter) else 200
            )
            return FeishuHTTPResponse(
                status_code=status_code,
                json_body=self._decode_json_body(response.read()),
            )

    def _decode_json_body(self, raw_body: bytes) -> dict[str, Any]:
        normalized = bytes(raw_body or b"").strip()
        if not normalized:
            return {}
        try:
            decoded = json.loads(normalized.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"msg": normalized.decode("utf-8", errors="replace")}
        return decoded if isinstance(decoded, dict) else {"data": decoded}


class FeishuConnector(BaseConnector):
    """Fetch supported Feishu documents through the authenticated Open API."""

    FEISHU_SCHEME = "feishu"
    FEISHU_HOST_KEYWORDS = ("feishu.cn", "larksuite.com")
    APP_ID_ENV = "MARRDP_FEISHU_APP_ID"
    APP_SECRET_ENV = "MARRDP_FEISHU_APP_SECRET"
    BASE_URL_ENV = "MARRDP_FEISHU_OPEN_BASE_URL"
    DEFAULT_BASE_URL = "https://open.feishu.cn"
    SUPPORTED_DOCUMENT_KINDS = {"wiki", "docx", "docs"}

    def __init__(
        self,
        *,
        http_client: _FeishuHTTPClient | None = None,
        config: FeishuConfig | None = None,
    ) -> None:
        self._http_client = http_client
        self._config_override = config

    def can_handle(self, source: str) -> bool:
        normalized = str(source or "").strip()
        if not normalized:
            return False

        if normalized.lower().startswith(f"{self.FEISHU_SCHEME}://"):
            return True

        try:
            parsed = urlparse(normalized)
        except ValueError:
            return False

        if parsed.scheme in {"http", "https"}:
            host = (parsed.netloc or "").lower()
            return any(keyword in host for keyword in self.FEISHU_HOST_KEYWORDS)
        return False

    def get_content(self, source: str) -> SourceDocument:
        source_ref = self._parse_source(source)
        config = self._read_config()
        self._validate_source_ref(source_ref)
        http_client = self._http_client or _DefaultFeishuHTTPClient(
            base_url=config.base_url
        )
        tenant_access_token = self._authenticate(
            http_client=http_client, config=config, source_ref=source_ref
        )
        resolved_document = self._resolve_document(
            source_ref=source_ref,
            http_client=http_client,
            tenant_access_token=tenant_access_token,
        )
        title, content_markdown = self._fetch_document_content(
            source_ref=source_ref,
            resolved_document=resolved_document,
            http_client=http_client,
            tenant_access_token=tenant_access_token,
        )

        metadata = SourceMetadata(
            mime_type="text/markdown",
            encoding="utf-8",
            size_bytes=len(content_markdown.encode("utf-8")),
            extra={
                "source_kind": source_ref.source_kind,
                "document_kind": source_ref.document_kind,
                "resolved_document_kind": resolved_document.document_kind,
                "resolved_document_token": resolved_document.document_token,
                "wiki_space": source_ref.wiki_space,
                "host": source_ref.host,
                "path": source_ref.path,
                "base_url": config.base_url,
            },
        )
        return SourceDocument(
            source_type=SourceType.feishu,
            source=source_ref.raw_source,
            title=title,
            content_markdown=content_markdown,
            metadata=metadata,
        )

    def _parse_source(self, source: str) -> FeishuSourceRef:
        normalized = str(source or "").strip()
        if not self.can_handle(normalized):
            raise ConnectorUnsupportedSourceError(
                f"Unsupported Feishu source: {source}",
                source=str(source),
                details={"connector": "feishu"},
            )

        parsed = urlparse(normalized)
        is_custom_scheme = parsed.scheme.lower() == self.FEISHU_SCHEME
        host = (parsed.netloc or "").strip().lower()
        path = parsed.path or ""
        segments = [segment for segment in path.split("/") if segment]

        if is_custom_scheme:
            source_kind = "feishu_scheme"
            document_kind = host or (segments[0] if segments else "unknown")
            wiki_space = segments[0] if document_kind == "wiki" and segments else ""
            document_token = segments[-1] if segments else ""
            resolved_host = self.FEISHU_SCHEME
        else:
            source_kind = "https_url"
            document_kind = self._detect_document_kind(segments)
            wiki_space = self._detect_wiki_space(
                document_kind=document_kind, segments=segments
            )
            document_token = segments[-1] if segments else ""
            resolved_host = host

        return FeishuSourceRef(
            raw_source=normalized,
            source_kind=source_kind,
            host=resolved_host,
            path=path,
            document_kind=document_kind,
            document_token=document_token,
            wiki_space=wiki_space,
        )

    def _read_config(self) -> FeishuConfig:
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
                details={"connector": "feishu", "base_url_env": self.BASE_URL_ENV},
            )

        client_id = str(os.getenv(self.APP_ID_ENV, "") or "").strip()
        client_secret = str(os.getenv(self.APP_SECRET_ENV, "") or "").strip()
        if client_id and client_secret:
            auth = ConnectorAuthConfig(
                auth_type=ConnectorAuthType.oauth_client_credentials,
                client_id=client_id,
                client_secret=client_secret,
                extra={
                    "client_id_env": self.APP_ID_ENV,
                    "client_secret_env": self.APP_SECRET_ENV,
                },
            )
        else:
            auth = ConnectorAuthConfig(
                extra={
                    "expected_auth_type": ConnectorAuthType.oauth_client_credentials.value,
                    "client_id_env": self.APP_ID_ENV,
                    "client_secret_env": self.APP_SECRET_ENV,
                }
            )

        return FeishuConfig(auth=auth, base_url=raw_base_url.rstrip("/"))

    def _validate_source_ref(self, source_ref: FeishuSourceRef) -> None:
        if not source_ref.document_token:
            raise ConnectorValidationError(
                f"Feishu source is missing a document token: {source_ref.raw_source}",
                source=source_ref.raw_source,
                details={
                    "connector": "feishu",
                    "document_kind": source_ref.document_kind,
                },
            )
        if source_ref.document_kind == "wiki" and not source_ref.wiki_space:
            raise ConnectorValidationError(
                f"Feishu wiki source is missing a wiki space identifier: {source_ref.raw_source}",
                source=source_ref.raw_source,
                details={
                    "connector": "feishu",
                    "document_kind": source_ref.document_kind,
                },
            )

    def _authenticate(
        self,
        *,
        http_client: _FeishuHTTPClient,
        config: FeishuConfig,
        source_ref: FeishuSourceRef,
    ) -> str:
        if config.auth.auth_type != ConnectorAuthType.oauth_client_credentials:
            raise FeishuAuthenticationError(
                "Feishu authentication failed because app credentials are missing. "
                f"Set {self.APP_ID_ENV} and {self.APP_SECRET_ENV} before fetching '{source_ref.raw_source}'.",
                source=source_ref.raw_source,
                details={
                    "connector": "feishu",
                    "auth_type": str(config.auth.auth_type),
                },
            )
        payload = self._api_request(
            http_client=http_client,
            method="POST",
            path="/open-apis/auth/v3/tenant_access_token/internal",
            json_body={
                "app_id": config.auth.client_id,
                "app_secret": config.auth.client_secret,
            },
            source_ref=source_ref,
            failure_kind="auth",
        )
        data = self._extract_data(payload)
        access_token = str(
            payload.get("tenant_access_token") or data.get("tenant_access_token") or ""
        ).strip()
        if not access_token:
            raise FeishuAuthenticationError(
                f"Feishu authentication failed for '{source_ref.raw_source}': tenant_access_token missing from auth response.",
                source=source_ref.raw_source,
                details={
                    "connector": "feishu",
                    "auth_type": str(config.auth.auth_type),
                },
            )
        return access_token

    def _resolve_document(
        self,
        *,
        source_ref: FeishuSourceRef,
        http_client: _FeishuHTTPClient,
        tenant_access_token: str,
    ) -> FeishuResolvedDocument:
        document_kind = source_ref.document_kind
        if document_kind == "wiki":
            return self._resolve_wiki_document(
                source_ref=source_ref,
                http_client=http_client,
                tenant_access_token=tenant_access_token,
            )
        if document_kind == "docx":
            return FeishuResolvedDocument(
                source_document_kind=document_kind,
                document_kind=document_kind,
                document_token=source_ref.document_token,
            )
        if document_kind == "docs":
            return self._convert_legacy_document(
                source_ref=source_ref,
                http_client=http_client,
                tenant_access_token=tenant_access_token,
            )
        raise self._unsupported_document_type_error(
            source_ref=source_ref,
            resolved_document_kind=document_kind,
        )

    def _resolve_wiki_document(
        self,
        *,
        source_ref: FeishuSourceRef,
        http_client: _FeishuHTTPClient,
        tenant_access_token: str,
    ) -> FeishuResolvedDocument:
        payload = self._api_request(
            http_client=http_client,
            method="GET",
            path=f"/open-apis/wiki/v2/spaces/{source_ref.wiki_space}/nodes/{source_ref.document_token}",
            source_ref=source_ref,
            failure_kind="document",
            tenant_access_token=tenant_access_token,
        )
        data = self._extract_data(payload)
        node_payload = self._extract_mapping(data.get("node")) or data
        resolved_kind = (
            str(node_payload.get("obj_type") or node_payload.get("object_type") or "")
            .strip()
            .lower()
        )
        resolved_token = str(
            node_payload.get("obj_token") or node_payload.get("document_id") or ""
        ).strip()
        resolved_title = str(node_payload.get("title") or "").strip()
        if resolved_kind == "doc":
            resolved_kind = "docs"
        if resolved_kind not in {"docx", "docs"}:
            raise self._unsupported_document_type_error(
                source_ref=source_ref,
                resolved_document_kind=resolved_kind or "unknown",
            )
        if not resolved_token:
            raise FeishuDocumentNotFoundError(
                f"Feishu document not found for source '{source_ref.raw_source}': wiki node did not expose an object token.",
                source=source_ref.raw_source,
                details={
                    "connector": "feishu",
                    "document_kind": resolved_kind or "unknown",
                },
            )
        if resolved_kind == "docs":
            converted_ref = FeishuSourceRef(
                raw_source=source_ref.raw_source,
                source_kind=source_ref.source_kind,
                host=source_ref.host,
                path=source_ref.path,
                document_kind="docs",
                document_token=resolved_token,
                wiki_space=source_ref.wiki_space,
            )
            converted = self._convert_legacy_document(
                source_ref=converted_ref,
                http_client=http_client,
                tenant_access_token=tenant_access_token,
            )
            return FeishuResolvedDocument(
                source_document_kind=source_ref.document_kind,
                document_kind=converted.document_kind,
                document_token=converted.document_token,
                title=resolved_title or converted.title,
                wiki_space=source_ref.wiki_space,
            )
        return FeishuResolvedDocument(
            source_document_kind=source_ref.document_kind,
            document_kind="docx",
            document_token=resolved_token,
            title=resolved_title,
            wiki_space=source_ref.wiki_space,
        )

    def _convert_legacy_document(
        self,
        *,
        source_ref: FeishuSourceRef,
        http_client: _FeishuHTTPClient,
        tenant_access_token: str,
    ) -> FeishuResolvedDocument:
        payload = self._api_request(
            http_client=http_client,
            method="POST",
            path=f"/open-apis/docx/v1/documents/{source_ref.document_token}/convert",
            source_ref=source_ref,
            failure_kind="document",
            tenant_access_token=tenant_access_token,
        )
        data = self._extract_data(payload)
        document_payload = self._extract_mapping(data.get("document")) or data
        converted_token = str(
            document_payload.get("document_id")
            or document_payload.get("obj_token")
            or document_payload.get("token")
            or data.get("document_id")
            or ""
        ).strip()
        converted_title = str(
            document_payload.get("title") or data.get("title") or ""
        ).strip()
        if not converted_token:
            raise FeishuDocumentNotFoundError(
                f"Feishu document not found for source '{source_ref.raw_source}': legacy document conversion did not return a docx token.",
                source=source_ref.raw_source,
                details={"connector": "feishu", "document_kind": "docs"},
            )
        return FeishuResolvedDocument(
            source_document_kind=source_ref.document_kind,
            document_kind="docx",
            document_token=converted_token,
            title=converted_title,
            wiki_space=source_ref.wiki_space,
        )

    def _fetch_document_content(
        self,
        *,
        source_ref: FeishuSourceRef,
        resolved_document: FeishuResolvedDocument,
        http_client: _FeishuHTTPClient,
        tenant_access_token: str,
    ) -> tuple[str, str]:
        metadata_payload = self._api_request(
            http_client=http_client,
            method="GET",
            path=f"/open-apis/docx/v1/documents/{resolved_document.document_token}",
            source_ref=source_ref,
            failure_kind="document",
            tenant_access_token=tenant_access_token,
        )
        content_payload = self._api_request(
            http_client=http_client,
            method="GET",
            path=f"/open-apis/docx/v1/documents/{resolved_document.document_token}/raw_content",
            source_ref=source_ref,
            failure_kind="document",
            tenant_access_token=tenant_access_token,
        )

        title = self._extract_document_title(
            metadata_payload, fallback_title=resolved_document.title
        )
        content_markdown = self._extract_document_content(content_payload)
        if not content_markdown:
            raise ConnectorValidationError(
                f"Feishu document content is empty for source '{source_ref.raw_source}'",
                source=source_ref.raw_source,
                details={
                    "connector": "feishu",
                    "document_kind": resolved_document.document_kind,
                },
            )
        return title, content_markdown

    def _api_request(
        self,
        *,
        http_client: _FeishuHTTPClient,
        method: str,
        path: str,
        source_ref: FeishuSourceRef,
        failure_kind: str,
        tenant_access_token: str | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if tenant_access_token:
            headers["Authorization"] = f"Bearer {tenant_access_token}"
        response = http_client.request(
            method, path, headers=headers, json_body=json_body
        )
        payload = dict(response.json_body or {})
        if response.status_code < 200 or response.status_code >= 300:
            self._raise_api_error(
                source_ref=source_ref,
                status_code=response.status_code,
                payload=payload,
                failure_kind=failure_kind,
            )
        api_code = payload.get("code")
        if api_code not in (None, 0):
            self._raise_api_error(
                source_ref=source_ref,
                status_code=response.status_code,
                payload=payload,
                failure_kind=failure_kind,
            )
        return payload

    def _raise_api_error(
        self,
        *,
        source_ref: FeishuSourceRef,
        status_code: int,
        payload: dict[str, Any],
        failure_kind: str,
    ) -> None:
        api_code = payload.get("code")
        api_message = self._extract_api_message(payload)
        diagnostic = self._format_error_diagnostic(
            status_code=status_code, api_code=api_code, api_message=api_message
        )
        normalized_message = api_message.lower()

        if (
            failure_kind == "auth"
            or status_code == 401
            or any(
                keyword in normalized_message
                for keyword in (
                    "tenant_access_token",
                    "unauthorized",
                    "auth",
                    "credential",
                )
            )
        ):
            raise FeishuAuthenticationError(
                f"Feishu authentication failed for source '{source_ref.raw_source}': {diagnostic}",
                source=source_ref.raw_source,
                details={
                    "connector": "feishu",
                    "status_code": status_code,
                    "api_code": api_code,
                },
            )
        if status_code == 404 or any(
            keyword in normalized_message
            for keyword in ("not found", "not exist", "no such")
        ):
            raise FeishuDocumentNotFoundError(
                f"Feishu document not found for source '{source_ref.raw_source}': {diagnostic}",
                source=source_ref.raw_source,
                details={
                    "connector": "feishu",
                    "status_code": status_code,
                    "api_code": api_code,
                },
            )
        if status_code == 403 or any(
            keyword in normalized_message
            for keyword in ("permission", "forbidden", "scope", "access denied")
        ):
            raise FeishuPermissionDeniedError(
                f"Permission denied while fetching Feishu source '{source_ref.raw_source}': {diagnostic}",
                source=source_ref.raw_source,
                details={
                    "connector": "feishu",
                    "status_code": status_code,
                    "api_code": api_code,
                },
            )
        if any(
            keyword in normalized_message
            for keyword in ("unsupported", "not support", "doc type")
        ):
            raise self._unsupported_document_type_error(
                source_ref=source_ref,
                resolved_document_kind=source_ref.document_kind,
                detail=diagnostic,
            )
        raise ConnectorNetworkError(
            f"Failed to fetch Feishu source '{source_ref.raw_source}': {diagnostic}",
            source=source_ref.raw_source,
            details={
                "connector": "feishu",
                "status_code": status_code,
                "api_code": api_code,
            },
            retryable=status_code >= 500,
        )

    def _extract_data(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._extract_mapping(payload.get("data")) or {}

    def _extract_mapping(self, value: Any) -> dict[str, Any] | None:
        return extract_mapping(value)

    def _extract_api_message(self, payload: dict[str, Any]) -> str:
        return extract_message(payload) or "unknown Feishu API error"

    def _format_error_diagnostic(
        self, *, status_code: int, api_code: Any, api_message: str
    ) -> str:
        code_segment = f", code={api_code}" if api_code not in (None, "") else ""
        return f"HTTP {status_code}{code_segment}: {api_message}"

    def _extract_document_title(
        self, payload: dict[str, Any], *, fallback_title: str
    ) -> str:
        data = self._extract_data(payload)
        document_payload = self._extract_mapping(data.get("document")) or data
        title = str(
            document_payload.get("title") or data.get("title") or fallback_title or ""
        ).strip()
        return title or "feishu-document"

    def _extract_document_content(self, payload: dict[str, Any]) -> str:
        data = self._extract_data(payload)
        for key in ("content", "raw_content"):
            value = data.get(key)
            if isinstance(value, str):
                return value.strip()
        return ""

    def _unsupported_document_type_error(
        self,
        *,
        source_ref: FeishuSourceRef,
        resolved_document_kind: str,
        detail: str | None = None,
    ) -> FeishuUnsupportedDocumentTypeError:
        normalized_kind = (
            resolved_document_kind or source_ref.document_kind or "unknown"
        )
        supported_types = ", ".join(sorted(self.SUPPORTED_DOCUMENT_KINDS))
        suffix = f" Detail: {detail}" if detail else ""
        return FeishuUnsupportedDocumentTypeError(
            f"Unsupported Feishu document type '{normalized_kind}' for source '{source_ref.raw_source}'. "
            f"Supported types: {supported_types}.{suffix}",
            source=source_ref.raw_source,
            details={"connector": "feishu", "document_kind": normalized_kind},
        )

    def _detect_document_kind(self, segments: list[str]) -> str:
        for kind in ("wiki", "docx", "docs", "sheet", "base"):
            if kind in segments:
                return kind
        return "unknown"

    def _detect_wiki_space(self, *, document_kind: str, segments: list[str]) -> str:
        if document_kind != "wiki":
            return ""
        try:
            wiki_index = segments.index("wiki")
        except ValueError:
            return ""
        space_index = wiki_index + 1
        return segments[space_index] if len(segments) > space_index else ""
