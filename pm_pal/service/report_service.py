"""Report retrieval helpers for MCP and API entrypoints."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

REPORT_FORMAT = Literal["md", "json"]
RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z$")
DEFAULT_MD_LIMIT = 20_000
MAX_MD_LIMIT = 200_000


def _error_payload(
    *,
    run_id: str,
    format: str,
    report_md_path: Path,
    report_json_path: Path,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "format": format,
        "content": "",
        "paths": {
            "report_md_path": str(report_md_path),
            "report_json_path": str(report_json_path),
        },
        "error": {
            "code": code,
            "message": message,
        },
    }


def _read_markdown_window(path: Path, *, offset: int, limit: int) -> tuple[str, bool]:
    with path.open("r", encoding="utf-8") as handle:
        if offset > 0:
            handle.read(offset)
        content = handle.read(limit)
        has_more = bool(handle.read(1))
    return content, has_more


def get_report_for_mcp(
    *,
    run_id: str,
    format: REPORT_FORMAT = "md",
    offset: int = 0,
    limit: int | None = None,
    outputs_root: str | Path = "outputs",
) -> dict[str, Any]:
    normalized_run_id = str(run_id or "").strip()
    normalized_format = str(format or "md").strip().lower()
    normalized_outputs_root = Path(outputs_root).resolve()
    run_dir = (normalized_outputs_root / normalized_run_id).resolve()
    report_md_path = run_dir / "report.md"
    report_json_path = run_dir / "report.json"

    if not RUN_ID_PATTERN.fullmatch(normalized_run_id):
        return _error_payload(
            run_id=normalized_run_id,
            format=normalized_format,
            report_md_path=report_md_path,
            report_json_path=report_json_path,
            code="invalid_run_id",
            message="run_id must match YYYYMMDDTHHMMSSZ",
        )

    # Defense in depth against path traversal, even if run_id validation changes in the future.
    if os.path.commonpath([str(normalized_outputs_root), str(run_dir)]) != str(
        normalized_outputs_root
    ):
        return _error_payload(
            run_id=normalized_run_id,
            format=normalized_format,
            report_md_path=report_md_path,
            report_json_path=report_json_path,
            code="invalid_run_id",
            message="run_id resolves outside outputs root",
        )

    if normalized_format not in ("md", "json"):
        return _error_payload(
            run_id=normalized_run_id,
            format=normalized_format,
            report_md_path=report_md_path,
            report_json_path=report_json_path,
            code="invalid_format",
            message='format must be either "md" or "json"',
        )

    if offset < 0:
        return _error_payload(
            run_id=normalized_run_id,
            format=normalized_format,
            report_md_path=report_md_path,
            report_json_path=report_json_path,
            code="invalid_input",
            message="offset must be >= 0",
        )

    if limit is not None and limit <= 0:
        return _error_payload(
            run_id=normalized_run_id,
            format=normalized_format,
            report_md_path=report_md_path,
            report_json_path=report_json_path,
            code="invalid_input",
            message="limit must be > 0 when provided",
        )

    if not run_dir.exists() or not run_dir.is_dir():
        return _error_payload(
            run_id=normalized_run_id,
            format=normalized_format,
            report_md_path=report_md_path,
            report_json_path=report_json_path,
            code="not_found",
            message=f"run_id not found: {normalized_run_id}",
        )

    if normalized_format == "json":
        if not report_json_path.exists():
            return _error_payload(
                run_id=normalized_run_id,
                format=normalized_format,
                report_md_path=report_md_path,
                report_json_path=report_json_path,
                code="not_found",
                message=f"report.json not found for run_id={normalized_run_id}",
            )
        try:
            content = json.loads(report_json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return _error_payload(
                run_id=normalized_run_id,
                format=normalized_format,
                report_md_path=report_md_path,
                report_json_path=report_json_path,
                code="invalid_report_content",
                message=f"report.json parse failed: {exc}",
            )
        if not isinstance(content, (dict, list)):
            return _error_payload(
                run_id=normalized_run_id,
                format=normalized_format,
                report_md_path=report_md_path,
                report_json_path=report_json_path,
                code="invalid_report_content",
                message="report.json must contain a JSON object or array",
            )
        return {
            "run_id": normalized_run_id,
            "format": normalized_format,
            "content": content,
            "paths": {
                "report_md_path": str(report_md_path),
                "report_json_path": str(report_json_path),
            },
        }

    effective_limit = DEFAULT_MD_LIMIT if limit is None else min(limit, MAX_MD_LIMIT)
    if not report_md_path.exists():
        return _error_payload(
            run_id=normalized_run_id,
            format=normalized_format,
            report_md_path=report_md_path,
            report_json_path=report_json_path,
            code="not_found",
            message=f"report.md not found for run_id={normalized_run_id}",
        )

    content, truncated = _read_markdown_window(
        report_md_path, offset=offset, limit=effective_limit
    )
    return {
        "run_id": normalized_run_id,
        "format": normalized_format,
        "content": content,
        "paths": {
            "report_md_path": str(report_md_path),
            "report_json_path": str(report_json_path),
        },
        "offset": offset,
        "limit": effective_limit,
        "truncated": truncated,
    }
