"""Delivery ports and Feishu adapters for approved PRD packages."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from prd_pal.connectors.errors import (
    ConnectorNetworkError,
    ConnectorPermissionError,
    ConnectorValidationError,
)
from prd_pal.connectors.feishu import (
    FeishuConnector,
    FeishuHTTPResponse,
    FeishuSourceRef,
    _DefaultFeishuHTTPClient,
)
from prd_pal.utils.time import utc_now_iso

from .models import (
    DecisionAuditEvent,
    DeliveryExport,
    DeliveryExportStatus,
    PrdVersion,
    PrdVersionStatus,
    WriteReceipt,
)
from .repository import ProductDecisionRepository
from .services import DecisionDomainError


@dataclass(frozen=True, slots=True)
class DeliveryPackage:
    prd_version: PrdVersion
    fields: dict[str, Any]
    audit_id: str


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    target_kind: str
    status: DeliveryExportStatus
    external_url: str = ""
    external_id: str = ""
    failure_reason: str = ""
    degraded_from: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class DeliveryTarget(Protocol):
    kind: str

    def export(self, package: DeliveryPackage) -> DeliveryResult: ...


def build_delivery_fields(prd: PrdVersion, *, audit_id: str) -> dict[str, Any]:
    return {
        "requirement": prd.title,
        "user_stories": prd.markdown,
        "acceptance_criteria": prd.metadata.get("acceptance_criteria") or [],
        "risks": prd.metadata.get("risks") or [],
        "owner": prd.metadata.get("owner") or "",
        "quality_decision": prd.quality_decision,
        "prd_link": prd.id,
        "evidence_links": list(prd.source_urls or prd.evidence_refs),
        "audit_id": audit_id,
    }


def delivery_idempotency_key(prd_version_id: str, target_kind: str) -> str:
    digest = hashlib.sha256(f"{prd_version_id}:{target_kind}".encode("utf-8")).hexdigest()[:16]
    return f"delivery:{prd_version_id}:{target_kind}:{digest}"


@dataclass
class FeishuBitableDeliveryTarget:
    """Default delivery target: create/update one Feishu bitable record."""

    app_token: str
    table_id: str
    field_mapping: dict[str, str] = field(default_factory=dict)
    base_url: str = ""
    http_client: Any = None
    kind: str = "feishu_bitable"

    def export(self, package: DeliveryPackage) -> DeliveryResult:
        if not self.app_token or not self.table_id:
            raise ConnectorValidationError(
                "Bitable delivery target is missing app_token/table_id",
                details={"target": self.kind},
            )
        connector = FeishuConnector(http_client=self.http_client)
        config = connector._read_config()
        client = self.http_client or _DefaultFeishuHTTPClient(base_url=config.base_url)
        source_ref = FeishuSourceRef(
            raw_source=f"bitable://{self.app_token}/{self.table_id}",
            source_kind="https_url",
            host="feishu.cn",
            path="",
            document_kind="base",
            document_token=self.app_token,
            wiki_space="",
        )
        token = connector._authenticate(
            http_client=client, config=config, source_ref=source_ref
        )
        mapped_fields = _map_fields(package.fields, self.field_mapping)
        path = f"/open-apis/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records"
        response = client.request(
            "POST",
            path,
            headers={"Authorization": f"Bearer {token}"},
            json_body={"fields": mapped_fields},
        )
        payload = dict(response.json_body or {})
        if response.status_code in {401, 403} or payload.get("code") in {
            99991663,
            99991668,
            99991401,
        }:
            raise ConnectorPermissionError(
                "Permission denied while exporting to Feishu bitable",
                details={"status_code": response.status_code, "api_code": payload.get("code")},
            )
        if response.status_code >= 400 or payload.get("code") not in (None, 0):
            raise ConnectorNetworkError(
                f"Bitable export failed: HTTP {response.status_code} code={payload.get('code')}",
                details={"status_code": response.status_code, "api_code": payload.get("code")},
                retryable=response.status_code >= 500,
            )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        record = data.get("record") if isinstance(data.get("record"), dict) else data
        record_id = str(record.get("record_id") or record.get("id") or uuid.uuid4().hex[:8])
        link = (
            self.base_url
            or f"https://feishu.cn/base/{self.app_token}?table={self.table_id}&record={record_id}"
        )
        return DeliveryResult(
            target_kind=self.kind,
            status=DeliveryExportStatus.succeeded,
            external_url=link,
            external_id=record_id,
            metadata={"fields": mapped_fields},
        )


@dataclass
class FeishuProjectDeliveryTarget:
    """Optional Feishu Project adapter; degrades to bitable on permission/mapping failure."""

    project_key: str
    field_mapping: dict[str, str] = field(default_factory=dict)
    fallback: FeishuBitableDeliveryTarget | None = None
    http_client: Any = None
    enabled: bool = True
    kind: str = "feishu_project"

    def export(self, package: DeliveryPackage) -> DeliveryResult:
        if not self.enabled or not self.project_key:
            return self._degrade(package, "Feishu project adapter is not configured")
        try:
            if not self.field_mapping.get("requirement"):
                raise ConnectorValidationError(
                    "Feishu project field mapping is invalid: requirement field missing",
                    details={"target": self.kind},
                )
            connector = FeishuConnector(http_client=self.http_client)
            config = connector._read_config()
            client = self.http_client or _DefaultFeishuHTTPClient(base_url=config.base_url)
            source_ref = FeishuSourceRef(
                raw_source=f"project://{self.project_key}",
                source_kind="https_url",
                host="feishu.cn",
                path="",
                document_kind="project",
                document_token=self.project_key,
                wiki_space="",
            )
            token = connector._authenticate(
                http_client=client, config=config, source_ref=source_ref
            )
            mapped = _map_fields(package.fields, self.field_mapping)
            response = client.request(
                "POST",
                f"/open-apis/project/v1/projects/{self.project_key}/work_items",
                headers={"Authorization": f"Bearer {token}"},
                json_body={"work_item": mapped},
            )
            payload = dict(response.json_body or {})
            if response.status_code in {401, 403} or payload.get("code") not in (None, 0):
                raise ConnectorPermissionError(
                    "Feishu project permission/export failed",
                    details={"status_code": response.status_code, "api_code": payload.get("code")},
                )
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            work_item_id = str(data.get("work_item_id") or data.get("id") or "")
            return DeliveryResult(
                target_kind=self.kind,
                status=DeliveryExportStatus.succeeded,
                external_url=str(data.get("url") or f"https://project.feishu.cn/{self.project_key}/{work_item_id}"),
                external_id=work_item_id,
                metadata={"fields": mapped},
            )
        except (ConnectorPermissionError, ConnectorValidationError, ConnectorNetworkError) as exc:
            return self._degrade(package, str(exc))

    def _degrade(self, package: DeliveryPackage, reason: str) -> DeliveryResult:
        if self.fallback is None:
            return DeliveryResult(
                target_kind=self.kind,
                status=DeliveryExportStatus.failed,
                failure_reason=reason,
            )
        fallback_result = self.fallback.export(package)
        return DeliveryResult(
            target_kind=fallback_result.target_kind,
            status=DeliveryExportStatus.degraded,
            external_url=fallback_result.external_url,
            external_id=fallback_result.external_id,
            failure_reason=reason,
            degraded_from=self.kind,
            metadata=dict(fallback_result.metadata or {}),
        )


class DeliveryService:
    def __init__(self, repository: ProductDecisionRepository) -> None:
        self.repository = repository

    async def export_prd(
        self,
        prd_version_id: str,
        *,
        target: DeliveryTarget,
        actor_open_id: str = "",
    ) -> tuple[DeliveryExport, WriteReceipt]:
        version_result = await self.repository.get_prd_version(prd_version_id)
        if not version_result.ok or version_result.value is None:
            raise DecisionDomainError(
                "prd_version_not_found", f"prd version not found: {prd_version_id}"
            )
        version = version_result.value
        if str(version.status) != PrdVersionStatus.ready_for_delivery:
            raise DecisionDomainError(
                "prd_not_ready_for_delivery",
                "Only ready_for_delivery PRD versions can be exported.",
            )
        key = delivery_idempotency_key(prd_version_id, target.kind)
        existing = await self.repository.get_delivery_by_idempotency_key(key)
        if existing.ok and existing.value is not None:
            export = existing.value
            return export, WriteReceipt(
                artifact_id=export.id,
                version=1,
                audit_id=export.audit_id,
                next_human_action="open_external_link",
                status=str(export.status),
            )

        audit_id = f"audit-{uuid.uuid4().hex[:12]}"
        fields = build_delivery_fields(version, audit_id=audit_id)
        package = DeliveryPackage(prd_version=version, fields=fields, audit_id=audit_id)
        try:
            result = target.export(package)
        except Exception as exc:
            export = DeliveryExport(
                id=f"delivery-{uuid.uuid4().hex[:12]}",
                prd_version_id=prd_version_id,
                product_id=version.product_id,
                target_kind=target.kind,
                idempotency_key=key,
                status=DeliveryExportStatus.failed,
                failure_reason=str(exc),
                field_payload=fields,
                audit_id=audit_id,
                evidence_refs=list(version.evidence_refs),
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
            )
            await self.repository.upsert_delivery_export(export)
            await self.repository.append_audit(
                DecisionAuditEvent(
                    id=audit_id,
                    product_id=version.product_id,
                    artifact_type="delivery_export",
                    artifact_id=export.id,
                    action="export_failed",
                    actor=actor_open_id,
                    reason=str(exc),
                    artifact_version=version.version,
                    created_at=export.created_at,
                )
            )
            raise DecisionDomainError("delivery_export_failed", str(exc)) from exc

        export = DeliveryExport(
            id=f"delivery-{uuid.uuid4().hex[:12]}",
            prd_version_id=prd_version_id,
            product_id=version.product_id,
            target_kind=result.target_kind,
            idempotency_key=key,
            status=result.status,
            external_url=result.external_url,
            external_id=result.external_id,
            failure_reason=result.failure_reason,
            degraded_from=result.degraded_from,
            field_payload=fields,
            audit_id=audit_id,
            evidence_refs=list(version.evidence_refs),
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
            metadata=dict(result.metadata or {}),
        )
        saved = await self.repository.upsert_delivery_export(export)
        await self.repository.append_audit(
            DecisionAuditEvent(
                id=audit_id,
                product_id=version.product_id,
                artifact_type="delivery_export",
                artifact_id=export.id,
                action="export",
                actor=actor_open_id,
                reason=result.failure_reason,
                artifact_version=version.version,
                created_at=export.created_at,
                metadata={
                    "external_url": result.external_url,
                    "status": str(result.status),
                    "degraded_from": result.degraded_from,
                },
            )
        )
        return saved.value or export, WriteReceipt(
            artifact_id=export.id,
            version=1,
            audit_id=audit_id,
            next_human_action="open_external_link",
            status=str(result.status),
        )


def _map_fields(fields: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    if not mapping:
        return {
            key: _stringify(value)
            for key, value in fields.items()
        }
    mapped: dict[str, Any] = {}
    for source_key, target_key in mapping.items():
        if source_key in fields:
            mapped[target_key] = _stringify(fields[source_key])
    # Ensure required export columns survive even with partial maps.
    for required in ("requirement", "quality_decision", "audit_id", "prd_link"):
        target = mapping.get(required, required)
        if target not in mapped and required in fields:
            mapped[target] = _stringify(fields[required])
    return mapped


def _stringify(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return json.dumps(value, ensure_ascii=False)
