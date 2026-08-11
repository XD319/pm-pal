from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AllowedSource = Literal["feishu", "web", "cli", "mcp", "hook", "unknown"]
RequirementType = Literal[
    "product_requirement", "bug_report", "task_request", "unknown"
]
ContentKind = Literal["inline_text", "file_path", "connector_source"]

_ALLOWED_SOURCES = {"feishu", "web", "cli", "mcp", "hook"}
_REQUIREMENT_TYPE_MAP: dict[str, RequirementType] = {
    "prd": "product_requirement",
    "product_requirement": "product_requirement",
    "product-requirement": "product_requirement",
    "requirement": "product_requirement",
    "bug": "bug_report",
    "bugfix": "bug_report",
    "bug_report": "bug_report",
    "bug-report": "bug_report",
    "task": "task_request",
    "task_request": "task_request",
    "task-request": "task_request",
    "ticket": "task_request",
}


class CanonicalReviewAttachment(BaseModel):
    name: str | None = None
    url: str | None = None
    path: str | None = None
    mime_type: str | None = None


class CanonicalReviewContent(BaseModel):
    kind: ContentKind
    text: str
    source_ref: str | None = None


class CanonicalReviewRequest(BaseModel):
    run_id: str
    source: AllowedSource
    team_id: str | None = None
    project_id: str | None = None
    submitter_id: str | None = None
    requirement_type: RequirementType
    review_profile_hint: str | None = None
    content: CanonicalReviewContent
    attachments: list[CanonicalReviewAttachment] = Field(default_factory=list)
    normalization_notes: list[str] = Field(default_factory=list)


def normalize_ingress_request(
    *,
    run_id: str,
    requirement_doc: str,
    prd_text: str | None = None,
    prd_path: str | None = None,
    source_ref: str | None = None,
    audit_context: dict[str, Any] | None = None,
) -> CanonicalReviewRequest:
    notes: list[str] = []
    context = dict(audit_context) if isinstance(audit_context, dict) else {}
    client_metadata = (
        context.get("client_metadata")
        if isinstance(context.get("client_metadata"), dict)
        else {}
    )

    source = _resolve_source(context=context, notes=notes)
    team_id = _pick_first_str(
        client_metadata, context, keys=("team_id", "team", "workspace_id")
    )
    project_id = _pick_first_str(
        client_metadata, context, keys=("project_id", "project", "artifact_key")
    )
    submitter_id = _pick_first_str(
        client_metadata,
        context,
        keys=("submitter_id", "open_id", "user_id", "actor", "client_id"),
    )
    if team_id is None:
        notes.append("team_id is missing; left as null.")
    if project_id is None:
        notes.append("project_id is missing; left as null.")
    if submitter_id is None:
        notes.append("submitter_id is missing; left as null.")

    review_profile_hint = _pick_first_str(
        client_metadata, context, keys=("review_profile_hint", "review_profile")
    )
    requirement_type = _resolve_requirement_type(
        context=context,
        client_metadata=client_metadata,
        source_ref=source_ref,
        prd_text=prd_text,
        prd_path=prd_path,
        notes=notes,
    )
    attachments = _resolve_attachments(client_metadata, notes)
    content = _resolve_content(
        requirement_doc=requirement_doc,
        source_ref=source_ref,
        prd_path=prd_path,
    )

    return CanonicalReviewRequest(
        run_id=str(run_id or "").strip(),
        source=source,
        team_id=team_id,
        project_id=project_id,
        submitter_id=submitter_id,
        requirement_type=requirement_type,
        review_profile_hint=review_profile_hint,
        content=content,
        attachments=attachments,
        normalization_notes=notes,
    )


def _resolve_source(*, context: dict[str, Any], notes: list[str]) -> AllowedSource:
    raw_source = str(context.get("source") or "").strip().lower()
    if raw_source in _ALLOWED_SOURCES:
        return raw_source  # type: ignore[return-value]
    if raw_source:
        notes.append(f"Unrecognized source '{raw_source}'; normalized to 'unknown'.")
    else:
        notes.append("source is missing; normalized to 'unknown'.")
    return "unknown"


def _resolve_requirement_type(
    *,
    context: dict[str, Any],
    client_metadata: dict[str, Any],
    source_ref: str | None,
    prd_text: str | None,
    prd_path: str | None,
    notes: list[str],
) -> RequirementType:
    explicit = _pick_first_str(
        client_metadata, context, keys=("requirement_type", "req_type", "type")
    )
    if explicit:
        mapped = _REQUIREMENT_TYPE_MAP.get(explicit.strip().lower())
        if mapped is not None:
            return mapped
        notes.append(
            f"Explicit requirement_type '{explicit}' is not recognized; normalized to 'unknown'."
        )
        return "unknown"

    if isinstance(prd_text, str) and prd_text.strip():
        notes.append(
            "requirement_type inferred as product_requirement from prd_text presence."
        )
        return "product_requirement"
    if isinstance(prd_path, str) and prd_path.strip():
        notes.append(
            "requirement_type inferred as product_requirement from prd_path presence."
        )
        return "product_requirement"
    if isinstance(source_ref, str) and source_ref.strip().lower().startswith("jira://"):
        notes.append(
            "requirement_type inferred as task_request from jira source prefix."
        )
        return "task_request"

    notes.append("requirement_type is ambiguous; normalized to 'unknown'.")
    return "unknown"


def _resolve_attachments(
    client_metadata: dict[str, Any], notes: list[str]
) -> list[CanonicalReviewAttachment]:
    raw_attachments = client_metadata.get("attachments")
    if raw_attachments is None:
        notes.append("attachments are not provided; normalized to empty list.")
        return []
    if not isinstance(raw_attachments, list):
        notes.append("attachments is not a list; normalized to empty list.")
        return []

    normalized: list[CanonicalReviewAttachment] = []
    for item in raw_attachments:
        if isinstance(item, str):
            normalized.append(CanonicalReviewAttachment(url=item.strip() or None))
            continue
        if isinstance(item, dict):
            normalized.append(
                CanonicalReviewAttachment(
                    name=str(item.get("name") or "").strip() or None,
                    url=str(item.get("url") or "").strip() or None,
                    path=str(item.get("path") or "").strip() or None,
                    mime_type=str(item.get("mime_type") or "").strip() or None,
                )
            )
    return normalized


def _resolve_content(
    *, requirement_doc: str, source_ref: str | None, prd_path: str | None
) -> CanonicalReviewContent:
    if isinstance(source_ref, str) and source_ref.strip():
        return CanonicalReviewContent(
            kind="connector_source", text=requirement_doc, source_ref=source_ref.strip()
        )
    if isinstance(prd_path, str) and prd_path.strip():
        return CanonicalReviewContent(
            kind="file_path", text=requirement_doc, source_ref=prd_path.strip()
        )
    return CanonicalReviewContent(kind="inline_text", text=requirement_doc)


def _pick_first_str(*dicts: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        for data in dicts:
            value = data.get(key)
            normalized = str(value or "").strip()
            if normalized:
                return normalized
    return None
