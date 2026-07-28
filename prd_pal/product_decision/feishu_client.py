"""Feishu fetch helpers for decision-workspace evidence sources."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from prd_pal.connectors.errors import (
    ConnectorNetworkError,
    ConnectorPermissionError,
    ConnectorValidationError,
)
from prd_pal.connectors.feishu import (
    FeishuConnector,
    FeishuHTTPResponse,
    _DefaultFeishuHTTPClient,
)

from .models import EvidenceRecord, EvidenceSource, EvidenceSourceType
from .repository import _quote, _summarize


class FeishuEvidenceHTTPClient(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> FeishuHTTPResponse: ...


@dataclass(frozen=True, slots=True)
class FetchedEvidencePage:
    records: list[EvidenceRecord]
    next_cursor: str
    done: bool
    source_version: str = ""


@dataclass
class FeishuEvidenceClient:
    """Reuse Feishu auth/HTTP for docs, meeting notes, and bitable pagination."""

    http_client: FeishuEvidenceHTTPClient | None = None
    page_size: int = 100
    _connector: FeishuConnector = field(default_factory=FeishuConnector)

    def fetch_page(self, source: EvidenceSource, *, cursor: str = "") -> FetchedEvidencePage:
        source_type = str(source.source_type)
        if source_type == EvidenceSourceType.feishu_bitable:
            return self._fetch_bitable_page(source, cursor=cursor)
        if source_type in {
            EvidenceSourceType.feishu_doc,
            EvidenceSourceType.feishu_meeting_notes,
        }:
            return self._fetch_document_page(source, cursor=cursor)
        raise ConnectorValidationError(
            f"Unsupported evidence source type: {source_type}",
            source=source.source_url or source.id,
            details={"source_type": source_type},
        )

    def _fetch_document_page(
        self, source: EvidenceSource, *, cursor: str = ""
    ) -> FetchedEvidencePage:
        # Documents are a single logical record; watermark stores content version hash.
        watermark = _parse_cursor(cursor)
        if watermark.get("done"):
            return FetchedEvidencePage(records=[], next_cursor=cursor or "{}", done=True)

        document = self._connector.get_content(source.source_url or source.external_id)
        content = str(document.content_markdown or "").strip()
        version = str(
            document.metadata.extra.get("resolved_document_token")
            or source.external_id
            or source.source_url
        )
        # Prefer revision from metadata size/title as a lightweight content version.
        version = f"{version}:{len(content)}:{document.title}"
        if watermark.get("watermark") == version:
            next_cursor = json.dumps(
                {"watermark": version, "done": True}, ensure_ascii=False
            )
            return FetchedEvidencePage(
                records=[], next_cursor=next_cursor, done=True, source_version=version
            )

        fragments = _split_meeting_fragments(content) if (
            str(source.source_type) == EvidenceSourceType.feishu_meeting_notes
        ) else [content]
        records: list[EvidenceRecord] = []
        for index, fragment in enumerate(fragments):
            external_id = source.external_id or source.id
            if len(fragments) > 1:
                external_id = f"{external_id}#seg-{index + 1}"
            records.append(
                EvidenceRecord(
                    id="",
                    source_id=source.id,
                    external_id=external_id,
                    product_id=source.product_id,
                    content=fragment,
                    summary=_summarize(fragment),
                    quote=_quote(fragment),
                    source_url=source.source_url,
                    author="",
                    occurred_at="",
                    source_version=version,
                    source_refs=[source.source_url] if source.source_url else [],
                    metadata={
                        "source_type": str(source.source_type),
                        "title": document.title,
                        "segment_index": index,
                    },
                )
            )
        next_cursor = json.dumps(
            {"watermark": version, "done": True}, ensure_ascii=False
        )
        return FetchedEvidencePage(
            records=records, next_cursor=next_cursor, done=True, source_version=version
        )

    def _fetch_bitable_page(
        self, source: EvidenceSource, *, cursor: str = ""
    ) -> FetchedEvidencePage:
        app_token, table_id = _resolve_bitable_ids(source)
        watermark_state = _parse_cursor(cursor)
        page_token = str(watermark_state.get("page_token") or "")
        high_water = str(watermark_state.get("watermark") or "")

        client, token = self._authenticated_client(source)
        path = (
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
            f"?page_size={max(1, min(self.page_size, 500))}"
        )
        if page_token:
            path += f"&page_token={page_token}"
        response = client.request(
            "GET",
            path,
            headers={"Authorization": f"Bearer {token}"},
        )
        payload = dict(response.json_body or {})
        if response.status_code in {401, 403} or payload.get("code") in {99991663, 99991668, 99991401}:
            raise ConnectorPermissionError(
                f"Permission denied while syncing bitable source '{source.id}'",
                source=source.source_url or source.id,
                details={"status_code": response.status_code, "api_code": payload.get("code")},
            )
        if response.status_code >= 500 or (
            response.status_code >= 400 and payload.get("code") not in (None, 0)
        ):
            raise ConnectorNetworkError(
                f"Network/API failure while syncing bitable source '{source.id}': "
                f"HTTP {response.status_code} code={payload.get('code')}",
                source=source.source_url or source.id,
                details={"status_code": response.status_code, "api_code": payload.get("code")},
                retryable=response.status_code >= 500,
            )
        if response.status_code < 200 or response.status_code >= 300 or payload.get("code") not in (None, 0):
            raise ConnectorValidationError(
                f"Invalid bitable response for source '{source.id}': HTTP {response.status_code}",
                source=source.source_url or source.id,
                details={"status_code": response.status_code, "api_code": payload.get("code")},
            )

        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        items = data.get("items") if isinstance(data.get("items"), list) else []
        mapping = dict(source.field_mapping or {})
        content_field = mapping.get("content") or mapping.get("text") or "content"
        author_field = mapping.get("author") or "author"
        occurred_field = mapping.get("occurred_at") or mapping.get("updated_at") or "updated_at"
        records: list[EvidenceRecord] = []
        newest = high_water
        for item in items:
            if not isinstance(item, dict):
                continue
            record_id = str(item.get("record_id") or item.get("id") or "").strip()
            fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
            updated_at = str(
                item.get("last_modified_time")
                or _field_text(fields, occurred_field)
                or ""
            ).strip()
            if high_water and updated_at and updated_at <= high_water:
                # Already below watermark; still keep for idempotent upsert when versions match.
                pass
            content = _field_text(fields, content_field) or json.dumps(
                fields, ensure_ascii=False
            )
            if not content.strip():
                continue
            version = updated_at or str(item.get("revision") or record_id)
            if newest < version:
                newest = version
            link = source.source_url
            if record_id and source.source_url:
                separator = "&" if "?" in source.source_url else "?"
                link = f"{source.source_url}{separator}record={record_id}"
            records.append(
                EvidenceRecord(
                    id="",
                    source_id=source.id,
                    external_id=record_id or version,
                    product_id=source.product_id,
                    content=content,
                    summary=_summarize(content),
                    quote=_quote(content),
                    source_url=link,
                    author=_field_text(fields, author_field),
                    occurred_at=updated_at,
                    source_version=version,
                    source_refs=[link] if link else [],
                    metadata={"fields": fields, "source_type": EvidenceSourceType.feishu_bitable},
                )
            )

        has_more = bool(data.get("has_more"))
        next_page = str(data.get("page_token") or "")
        if has_more and next_page:
            next_cursor = json.dumps(
                {"watermark": high_water, "page_token": next_page},
                ensure_ascii=False,
            )
            return FetchedEvidencePage(
                records=records,
                next_cursor=next_cursor,
                done=False,
                source_version=newest or high_water,
            )
        next_cursor = json.dumps(
            {"watermark": newest or high_water, "page_token": "", "done": True},
            ensure_ascii=False,
        )
        return FetchedEvidencePage(
            records=records,
            next_cursor=next_cursor,
            done=True,
            source_version=newest or high_water,
        )

    def _authenticated_client(
        self, source: EvidenceSource
    ) -> tuple[FeishuEvidenceHTTPClient, str]:
        config = self._connector._read_config()
        client: FeishuEvidenceHTTPClient = self.http_client or _DefaultFeishuHTTPClient(
            base_url=config.base_url
        )
        # Build a minimal source ref for auth error context.
        from prd_pal.connectors.feishu import FeishuSourceRef

        source_ref = FeishuSourceRef(
            raw_source=source.source_url or source.external_id or source.id,
            source_kind="https_url",
            host="feishu.cn",
            path="",
            document_kind="base",
            document_token=source.external_id or source.id,
            wiki_space="",
        )
        token = self._connector._authenticate(
            http_client=client, config=config, source_ref=source_ref
        )
        return client, token


def _parse_cursor(cursor: str) -> dict[str, Any]:
    raw = str(cursor or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"watermark": raw}
    return payload if isinstance(payload, dict) else {"watermark": raw}


def _resolve_bitable_ids(source: EvidenceSource) -> tuple[str, str]:
    metadata = dict(source.metadata or {})
    app_token = str(metadata.get("app_token") or "").strip()
    table_id = str(metadata.get("table_id") or "").strip()
    if ":" in (source.external_id or "") and (not app_token or not table_id):
        left, right = source.external_id.split(":", 1)
        app_token = app_token or left.strip()
        table_id = table_id or right.strip()
    if source.source_url and (not app_token or not table_id):
        parsed = urlparse(source.source_url)
        segments = [part for part in parsed.path.split("/") if part]
        if "base" in segments:
            app_token = app_token or segments[segments.index("base") + 1]
        query = parse_qs(parsed.query)
        table_id = table_id or str((query.get("table") or [""])[0]).strip()
    if not app_token or not table_id:
        raise ConnectorValidationError(
            f"Bitable source '{source.id}' is missing app_token/table_id",
            source=source.source_url or source.id,
            details={"external_id": source.external_id},
        )
    return app_token, table_id


def _field_text(fields: dict[str, Any], key: str) -> str:
    if not key:
        return ""
    value = fields.get(key)
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("name") or item.get("email")
                if text:
                    parts.append(str(text))
            else:
                parts.append(str(item))
        return " ".join(parts).strip()
    if isinstance(value, dict):
        for candidate in ("text", "name", "value"):
            if candidate in value:
                return str(value[candidate]).strip()
    return str(value).strip()


def _split_meeting_fragments(content: str, *, max_chars: int = 1200) -> list[str]:
    normalized = str(content or "").strip()
    if not normalized:
        return []
    blocks = [block.strip() for block in normalized.split("\n\n") if block.strip()]
    if not blocks:
        return [normalized]
    fragments: list[str] = []
    current = ""
    for block in blocks:
        if not current:
            current = block
            continue
        if len(current) + 2 + len(block) <= max_chars:
            current = f"{current}\n\n{block}"
        else:
            fragments.append(current)
            current = block
    if current:
        fragments.append(current)
    return fragments or [normalized]
