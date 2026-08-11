"""Project materials upload, versioning, diff, and rollback."""

from __future__ import annotations

import difflib
import hashlib
import importlib.util
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import HTTPException

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}
MIME_BY_EXT = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def compute_checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_material(
    content: str,
    *,
    mime_type: str,
    size_bytes: int,
    filename: str,
) -> dict[str, Any]:
    issues: list[str] = []
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        issues.append("unsupported_extension")
    if size_bytes <= 0:
        issues.append("empty_file")
    if size_bytes > MAX_UPLOAD_BYTES:
        issues.append("file_too_large")
    if not content.strip():
        issues.append("empty_content")
    return {
        "valid": not issues,
        "issues": issues,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "filename": filename,
    }


def parse_upload_bytes(filename: str, raw: bytes) -> tuple[str, str]:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, detail=f"Unsupported file type: {ext or 'unknown'}")
    mime = MIME_BY_EXT[ext]
    if ext in {".md", ".txt"}:
        try:
            return raw.decode("utf-8"), mime
        except UnicodeDecodeError as exc:
            raise HTTPException(400, detail="Text file must be UTF-8 encoded.") from exc
    if ext == ".pdf":
        if importlib.util.find_spec("pypdf") is None:
            raise HTTPException(
                400,
                detail="PDF upload requires pypdf. Install with: pip install pypdf",
            )
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        pages = [(page.extract_text() or "") for page in reader.pages]
        return "\n".join(pages).strip(), mime
    if ext == ".docx":
        if importlib.util.find_spec("docx") is None:
            raise HTTPException(
                400,
                detail="DOCX upload requires python-docx. Install with: pip install python-docx",
            )
        from docx import Document

        doc = Document(io.BytesIO(raw))
        lines = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(lines).strip(), mime
    raise HTTPException(400, detail=f"Unsupported file type: {ext}")


def public_source(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["metadata"] = json.loads(item.pop("metadata_json", "{}") or "{}")
    item["is_prd"] = bool(item.get("is_prd"))
    return item


def resolve_version_info(
    store: Any,
    project_id: str,
    title: str,
    parent_source_id: str | None,
) -> tuple[int, str | None, str]:
    if parent_source_id:
        parent_rows = store.rows(
            "SELECT id,title,version FROM project_sources WHERE id=? AND project_id=?",
            (parent_source_id, project_id),
        )
        if not parent_rows:
            raise HTTPException(404, detail="Parent source not found")
        parent = parent_rows[0]
        return int(parent["version"]) + 1, parent_source_id, parent["title"]
    siblings = store.rows(
        "SELECT id,version FROM project_sources WHERE project_id=? AND title=? ORDER BY version DESC LIMIT 1",
        (project_id, title),
    )
    if siblings:
        return int(siblings[0]["version"]) + 1, siblings[0]["id"], title
    return 1, None, title


def insert_source(
    store: Any,
    *,
    source_id: str,
    project_id: str,
    title: str,
    source_type: str,
    content: str,
    source_url: str,
    is_prd: bool,
    version: int,
    parent_source_id: str | None,
    checksum: str,
    metadata: dict[str, Any],
    stamp: str,
) -> None:
    store.execute(
        "INSERT INTO project_sources "
        "(id,project_id,title,source_type,content,source_url,is_prd,version,created_at,parent_source_id,checksum,metadata_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            source_id,
            project_id,
            title,
            source_type,
            content,
            source_url,
            int(is_prd),
            version,
            stamp,
            parent_source_id,
            checksum,
            json.dumps(metadata),
        ),
    )


def record_event(
    store: Any,
    *,
    project_id: str,
    kind: str,
    label: str,
    source_id: str,
    new_id: Callable[[str], str],
    now: Callable[[], str],
) -> None:
    store.execute(
        "INSERT INTO project_events VALUES (?,?,?,?,?,?)",
        (new_id("event"), project_id, kind, label, source_id, now()),
    )


def get_source_or_404(store: Any, project_id: str, source_id: str) -> dict[str, Any]:
    rows = store.rows(
        "SELECT * FROM project_sources WHERE id=? AND project_id=?",
        (source_id, project_id),
    )
    if not rows:
        raise HTTPException(404, detail="Source not found")
    return rows[0]


def create_source_version(
    store: Any,
    *,
    project_id: str,
    title: str,
    source_type: str,
    content: str,
    source_url: str,
    is_prd: bool,
    parent_source_id: str | None,
    metadata_extra: dict[str, Any] | None,
    new_id: Callable[[str], str],
    now: Callable[[], str],
    event_kind: str = "source_added",
) -> dict[str, Any]:
    version, parent_id, resolved_title = resolve_version_info(
        store, project_id, title, parent_source_id
    )
    validation = validate_material(
        content,
        mime_type=metadata_extra.get("mime_type", "text/plain")
        if metadata_extra
        else "text/plain",
        size_bytes=len(content.encode("utf-8")),
        filename=metadata_extra.get("filename", f"{resolved_title}.txt")
        if metadata_extra
        else f"{resolved_title}.txt",
    )
    metadata = {"validation": validation, **(metadata_extra or {})}
    checksum = compute_checksum(content)
    source_id = new_id("source")
    stamp = now()
    insert_source(
        store,
        source_id=source_id,
        project_id=project_id,
        title=resolved_title,
        source_type=source_type,
        content=content,
        source_url=source_url,
        is_prd=is_prd,
        version=version,
        parent_source_id=parent_id,
        checksum=checksum,
        metadata=metadata,
        stamp=stamp,
    )
    record_event(
        store,
        project_id=project_id,
        kind=event_kind,
        label=f"{resolved_title} v{version}",
        source_id=source_id,
        new_id=new_id,
        now=now,
    )
    store.execute("UPDATE projects SET updated_at=? WHERE id=?", (stamp, project_id))
    return {
        "id": source_id,
        "version": version,
        "checksum": checksum,
        "metadata": metadata,
    }


def diff_sources(
    store: Any, project_id: str, source_id: str, against_id: str
) -> dict[str, Any]:
    left = get_source_or_404(store, project_id, source_id)
    right = get_source_or_404(store, project_id, against_id)
    diff_lines = difflib.unified_diff(
        (left["content"] or "").splitlines(keepends=True),
        (right["content"] or "").splitlines(keepends=True),
        fromfile=f"{left['title']} v{left['version']}",
        tofile=f"{right['title']} v{right['version']}",
    )
    return {
        "source_id": source_id,
        "against_id": against_id,
        "diff": "".join(diff_lines),
        "from_version": left["version"],
        "to_version": right["version"],
    }


def rollback_source(
    store: Any,
    *,
    project_id: str,
    source_id: str,
    new_id: Callable[[str], str],
    now: Callable[[], str],
) -> dict[str, Any]:
    historical = get_source_or_404(store, project_id, source_id)
    meta = json.loads(historical.get("metadata_json") or "{}")
    siblings = store.rows(
        "SELECT id,version FROM project_sources WHERE project_id=? AND title=? ORDER BY version DESC LIMIT 1",
        (project_id, historical["title"]),
    )
    version = int(siblings[0]["version"]) + 1 if siblings else 1
    validation = validate_material(
        historical["content"],
        mime_type=meta.get("mime_type", "text/plain"),
        size_bytes=len((historical["content"] or "").encode("utf-8")),
        filename=meta.get("filename", f"{historical['title']}.txt"),
    )
    metadata = {
        **meta,
        "validation": validation,
        "rollback_from": source_id,
        "rollback_from_version": historical["version"],
    }
    checksum = compute_checksum(historical["content"] or "")
    new_source_id = new_id("source")
    stamp = now()
    insert_source(
        store,
        source_id=new_source_id,
        project_id=project_id,
        title=historical["title"],
        source_type=historical["source_type"],
        content=historical["content"],
        source_url=historical.get("source_url") or "",
        is_prd=bool(historical.get("is_prd")),
        version=version,
        parent_source_id=source_id,
        checksum=checksum,
        metadata=metadata,
        stamp=stamp,
    )
    record_event(
        store,
        project_id=project_id,
        kind="source_rollback",
        label=f"{historical['title']} v{version}",
        source_id=new_source_id,
        new_id=new_id,
        now=now,
    )
    store.execute("UPDATE projects SET updated_at=? WHERE id=?", (stamp, project_id))
    return {
        "id": new_source_id,
        "version": version,
        "checksum": checksum,
        "metadata": metadata,
    }
