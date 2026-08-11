from __future__ import annotations

import json

from pm_pal.mcp_server import server as mcp_server
from pm_pal.service.report_service import DEFAULT_MD_LIMIT, get_report_for_mcp


def test_get_report_rejects_invalid_run_id(tmp_path):
    result = get_report_for_mcp(run_id="../escape", outputs_root=tmp_path)

    assert result["error"]["code"] == "invalid_run_id"


def test_get_report_returns_not_found_for_missing_run(tmp_path):
    result = get_report_for_mcp(run_id="20260304T010203Z", outputs_root=tmp_path)

    assert result["error"]["code"] == "not_found"


def test_get_report_md_uses_default_limit_and_truncates(tmp_path):
    run_id = "20260304T010203Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text("A" * (DEFAULT_MD_LIMIT + 100), encoding="utf-8")
    (run_dir / "report.json").write_text("{}", encoding="utf-8")

    result = get_report_for_mcp(run_id=run_id, outputs_root=tmp_path)

    assert "error" not in result
    assert result["format"] == "md"
    assert len(result["content"]) == DEFAULT_MD_LIMIT
    assert result["truncated"] is True


def test_get_report_md_supports_offset_and_limit(tmp_path):
    run_id = "20260304T010204Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text("0123456789", encoding="utf-8")
    (run_dir / "report.json").write_text("{}", encoding="utf-8")

    result = get_report_for_mcp(run_id=run_id, offset=2, limit=4, outputs_root=tmp_path)

    assert result["content"] == "2345"
    assert result["offset"] == 2
    assert result["limit"] == 4
    assert result["truncated"] is True


def test_get_report_json_returns_object(tmp_path):
    run_id = "20260304T010205Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text("# report", encoding="utf-8")
    payload = {"run_id": run_id, "status": "completed"}
    (run_dir / "report.json").write_text(json.dumps(payload), encoding="utf-8")

    result = get_report_for_mcp(run_id=run_id, format="json", outputs_root=tmp_path)

    assert "error" not in result
    assert result["format"] == "json"
    assert result["content"] == payload


def test_mcp_get_report_tool_wrapper_works_with_outputs_root_option(tmp_path):
    run_id = "20260304T010206Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text("# report", encoding="utf-8")
    (run_dir / "report.json").write_text('{"ok":true}', encoding="utf-8")

    result = mcp_server.get_report(
        run_id=run_id, options={"outputs_root": str(tmp_path)}
    )

    assert "error" not in result
    assert result["run_id"] == run_id
    assert result["content"] == "# report"
