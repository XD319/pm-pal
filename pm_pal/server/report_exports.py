from __future__ import annotations

import csv
import html
import json
from io import StringIO
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from pm_pal.service.review_service import (
    _derive_review_findings,
    _derive_review_mode,
    _derive_reviewers_used,
)
from pm_pal.service.report_service import RUN_ID_PATTERN


def _run_id_to_datetime(run_id: str):
    normalized = str(run_id or "").strip()
    if not RUN_ID_PATTERN.fullmatch(normalized):
        return None
    from datetime import datetime, timezone

    return datetime.strptime(normalized, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _timestamp_to_iso(timestamp: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _safe_iso_to_datetime(value: str):
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def load_report_payload(run_dir: Path) -> dict[str, Any]:
    report_json_path = run_dir / "report.json"
    if not report_json_path.exists():
        raise HTTPException(
            status_code=404, detail=f"report.json not found for run_id={run_dir.name}"
        )

    try:
        payload = json.loads(report_json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"report.json parse failed for run_id={run_dir.name}: {exc}",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=500,
            detail=f"report.json must contain an object for run_id={run_dir.name}",
        )
    return payload


def _derive_report_title(report_payload: dict[str, Any], report_md: str) -> str:
    candidate_paths = (
        ("source_metadata", "title"),
        ("source_document", "title"),
        ("source", "title"),
    )
    for parent_key, child_key in candidate_paths:
        parent = report_payload.get(parent_key)
        if isinstance(parent, dict):
            value = str(parent.get(child_key, "") or "").strip()
            if value:
                return value

    for key in ("prd_title", "title", "document_title"):
        value = str(report_payload.get(key, "") or "").strip()
        if value:
            return value

    for line in report_md.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            heading = stripped[2:].strip()
            if heading and heading.lower() != "requirement review report":
                return heading

    return "Requirement Review Report"


def _derive_review_timestamp(report_payload: dict[str, Any], run_dir: Path) -> str:
    for key in ("updated_at", "created_at", "timestamp", "review_time"):
        value = str(report_payload.get(key, "") or "").strip()
        if value:
            return value

    run_dt = _run_id_to_datetime(run_dir.name)
    if run_dt is not None:
        return run_dt.isoformat()
    return _timestamp_to_iso((run_dir / "report.json").stat().st_mtime)


def _format_report_timestamp_display(value: str) -> str:
    parsed = _safe_iso_to_datetime(value)
    if parsed is None:
        return value
    from datetime import timezone

    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = escaped.replace("**", "\u0000")
    parts = escaped.split("\u0000")
    if len(parts) > 1:
        rebuilt: list[str] = []
        for index, part in enumerate(parts):
            if index % 2 == 1:
                rebuilt.append(f"<strong>{part}</strong>")
            else:
                rebuilt.append(part)
        escaped = "".join(rebuilt)
    return escaped


def _render_markdown_html(markdown_text: str) -> str:
    try:
        import markdown as markdown_lib

        return markdown_lib.markdown(
            markdown_text, extensions=["extra", "tables", "fenced_code", "sane_lists"]
        )
    except Exception:
        pass

    lines = markdown_text.splitlines()
    blocks: list[str] = []
    paragraph_lines: list[str] = []
    list_items: list[str] = []
    table_lines: list[str] = []
    in_code = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if paragraph_lines:
            content = " ".join(part.strip() for part in paragraph_lines if part.strip())
            if content:
                blocks.append(f"<p>{_inline_markdown(content)}</p>")
            paragraph_lines = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            items = "".join(f"<li>{_inline_markdown(item)}</li>" for item in list_items)
            blocks.append(f"<ul>{items}</ul>")
            list_items = []

    def flush_table() -> None:
        nonlocal table_lines
        if len(table_lines) < 2:
            for line in table_lines:
                if line.strip():
                    paragraph_lines.append(line.strip())
            table_lines = []
            return

        parsed_rows: list[list[str]] = []
        for raw_line in table_lines:
            stripped = raw_line.strip().strip("|")
            parsed_rows.append([cell.strip() for cell in stripped.split("|")])

        if len(parsed_rows) < 2:
            table_lines = []
            return

        header = parsed_rows[0]
        body_rows = parsed_rows[2:] if len(parsed_rows) > 2 else []
        head_html = "".join(f"<th>{_inline_markdown(cell)}</th>" for cell in header)
        body_html = "".join(
            "<tr>"
            + "".join(f"<td>{_inline_markdown(cell)}</td>" for cell in row)
            + "</tr>"
            for row in body_rows
        )
        blocks.append(
            f"<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>"
        )
        table_lines = []

    def flush_code() -> None:
        nonlocal code_lines
        blocks.append(
            f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>"
        )
        code_lines = []

    for line in lines:
        stripped = line.rstrip()

        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            flush_table()
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
            continue

        if in_code:
            code_lines.append(stripped)
            continue

        if (
            "|" in stripped
            and stripped.strip().startswith("|")
            and stripped.strip().endswith("|")
        ):
            flush_paragraph()
            flush_list()
            table_lines.append(stripped)
            continue

        if table_lines:
            flush_table()

        if not stripped.strip():
            flush_paragraph()
            flush_list()
            continue

        heading_level = 0
        while heading_level < len(stripped) and stripped[heading_level] == "#":
            heading_level += 1
        if (
            1 <= heading_level <= 6
            and stripped[heading_level : heading_level + 1] == " "
        ):
            flush_paragraph()
            flush_list()
            blocks.append(
                f"<h{heading_level}>{_inline_markdown(stripped[heading_level + 1 :].strip())}</h{heading_level}>"
            )
            continue

        if stripped.lstrip().startswith(("- ", "* ")):
            flush_paragraph()
            list_items.append(stripped.lstrip()[2:].strip())
            continue

        paragraph_lines.append(stripped)

    if table_lines:
        flush_table()
    if in_code:
        flush_code()
    flush_paragraph()
    flush_list()
    return "\n".join(blocks)


def build_report_html(
    *, run_id: str, report_payload: dict[str, Any], report_md: str, run_dir: Path
) -> str:
    title = _derive_report_title(report_payload, report_md)
    review_time = _derive_review_timestamp(report_payload, run_dir)
    review_mode = _derive_review_mode(report_payload) or str(
        report_payload.get("mode", "unknown") or "unknown"
    )
    reviewers = _derive_reviewers_used(report_payload)
    reviewers_label = ", ".join(reviewers) if reviewers else "N/A"
    content_html = _render_markdown_html(report_md)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - Review Report</title>
  <style>
    :root {{
      color-scheme: light;
      --text: #1f2937;
      --muted: #6b7280;
      --border: #d1d5db;
      --surface: #ffffff;
      --surface-soft: #f8fafc;
      --accent: #0f172a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #eef2f7;
      color: var(--text);
      font: 16px/1.65 "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }}
    .page {{
      width: min(960px, calc(100vw - 48px));
      margin: 24px auto;
      background: var(--surface);
      box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
    }}
    .cover {{
      min-height: 100vh;
      padding: 72px 72px 56px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      page-break-after: always;
    }}
    .cover-kicker {{
      margin: 0 0 16px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 12px;
    }}
    .cover h1 {{
      margin: 0;
      font-size: 40px;
      line-height: 1.2;
    }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px 24px;
      margin-top: 40px;
      padding-top: 24px;
      border-top: 1px solid var(--border);
    }}
    .meta-card {{
      padding: 16px 18px;
      background: var(--surface-soft);
      border: 1px solid var(--border);
      border-radius: 10px;
    }}
    .meta-label {{
      display: block;
      margin-bottom: 6px;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .content {{
      padding: 56px 72px 72px;
    }}
    h1, h2, h3, h4, h5, h6 {{
      color: var(--accent);
      page-break-after: avoid;
    }}
    h1 {{ font-size: 32px; margin-top: 0; }}
    h2 {{
      margin-top: 40px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--border);
      font-size: 24px;
    }}
    h3 {{ margin-top: 28px; font-size: 18px; }}
    p, li {{ orphans: 3; widows: 3; }}
    pre {{
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
      padding: 16px;
      background: var(--surface-soft);
      border: 1px solid var(--border);
      border-radius: 8px;
    }}
    code {{
      font-family: "Cascadia Code", "SFMono-Regular", Consolas, monospace;
      font-size: 0.92em;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin: 20px 0;
      font-size: 14px;
    }}
    th, td {{
      padding: 10px 12px;
      border: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: var(--surface-soft);
    }}
    @page {{
      size: A4;
      margin: 16mm;
    }}
    @media print {{
      body {{ background: #fff; }}
      .page {{
        width: auto;
        margin: 0;
        box-shadow: none;
      }}
      .cover, .content {{
        padding-left: 0;
        padding-right: 0;
      }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="cover">
      <div>
        <p class="cover-kicker">PRD Review Report</p>
        <h1>{html.escape(title)}</h1>
      </div>
      <div class="meta-grid">
        <div class="meta-card">
          <span class="meta-label">Run ID</span>
          <strong>{html.escape(run_id)}</strong>
        </div>
        <div class="meta-card">
          <span class="meta-label">Review Time</span>
          <strong>{html.escape(_format_report_timestamp_display(review_time))}</strong>
        </div>
        <div class="meta-card">
          <span class="meta-label">Mode</span>
          <strong>{html.escape(review_mode)}</strong>
        </div>
        <div class="meta-card">
          <span class="meta-label">Reviewers</span>
          <strong>{html.escape(reviewers_label)}</strong>
        </div>
      </div>
    </section>
    <section class="content">
      {content_html}
    </section>
  </main>
</body>
</html>
"""


def build_report_csv(report_payload: dict[str, Any]) -> str:
    findings = _derive_review_findings(report_payload)
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(
        [
            "id",
            "requirement",
            "severity",
            "category",
            "description",
            "suggestion",
            "reviewer",
        ]
    )

    for item in findings:
        reviewers = item.get("reviewers")
        if isinstance(reviewers, list):
            reviewer = ", ".join(
                str(value).strip() for value in reviewers if str(value).strip()
            )
        else:
            reviewer = str(item.get("source_reviewer", "") or "").strip()
        writer.writerow(
            [
                str(item.get("finding_id", item.get("id", "")) or "").strip(),
                str(
                    item.get(
                        "requirement_id", item.get("requirement", item.get("title", ""))
                    )
                    or ""
                ).strip(),
                str(item.get("severity", "") or "").strip(),
                str(item.get("category", "") or "").strip(),
                str(item.get("description", item.get("detail", "")) or "").strip(),
                str(
                    item.get("suggestion", item.get("suggested_action", "")) or ""
                ).strip(),
                reviewer,
            ]
        )

    return "\ufeff" + buffer.getvalue()
