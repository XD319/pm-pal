from __future__ import annotations

import json

from pm_pal import main as cli
from pm_pal.service.review_service import ReviewResultSummary


def test_cli_review_legacy_mode_emits_json(monkeypatch, capsys) -> None:
    async def fake_review_prd_text_async(**kwargs):
        assert kwargs["prd_path"] == "docs/sample_prd.md"
        return ReviewResultSummary(
            run_id="20260309T120000Z",
            report_md_path="outputs/20260309T120000Z/report.md",
            report_json_path="outputs/20260309T120000Z/report.json",
            run_trace_path="outputs/20260309T120000Z/run_trace.json",
            implementation_pack_path="outputs/20260309T120000Z/implementation_pack.json",
            test_pack_path="outputs/20260309T120000Z/test_pack.json",
            execution_pack_path="outputs/20260309T120000Z/execution_pack.json",
            delivery_bundle_path="outputs/20260309T120000Z/delivery_bundle.json",
            high_risk_ratio=0.1,
            coverage_ratio=0.9,
            revision_round=1,
            status="completed",
        )

    monkeypatch.setattr(cli, "review_prd_text_async", fake_review_prd_text_async)

    exit_code = cli.run_cli(["--input", "docs/sample_prd.md", "--json"])
    captured = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["run_id"] == "20260309T120000Z"
    assert captured["artifacts"]["delivery_bundle_path"].endswith(
        "delivery_bundle.json"
    )


def test_cli_prepare_handoff_command_prints_request_paths(monkeypatch, capsys) -> None:
    async def fake_prepare_agent_handoff_for_mcp_async(**kwargs):
        assert kwargs["agent"] == "openclaw"
        assert kwargs["run_id"] == "20260309T120000Z"
        return {
            "run_id": "20260309T120000Z",
            "bundle_id": "bundle-20260309T120000Z",
            "status": "prepared",
            "agent_selection": "openclaw",
            "request_count": 1,
            "requests": [
                {
                    "agent": "openclaw",
                    "request_path": "outputs/20260309T120000Z/openclaw_request.json",
                }
            ],
            "paths": {},
        }

    monkeypatch.setattr(
        cli,
        "prepare_agent_handoff_for_mcp_async",
        fake_prepare_agent_handoff_for_mcp_async,
    )

    exit_code = cli.run_cli(
        ["prepare-handoff", "--run-id", "20260309T120000Z", "--agent", "openclaw"]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "bundle-20260309T120000Z" in output
    assert "openclaw_request.json" in output


def test_cli_report_command_prints_markdown(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "get_report_for_mcp",
        lambda **kwargs: {
            "run_id": kwargs["run_id"],
            "format": kwargs["format"],
            "content": "# Review Report",
            "paths": {
                "report_md_path": "outputs/20260309T120000Z/report.md",
                "report_json_path": "outputs/20260309T120000Z/report.json",
            },
        },
    )

    exit_code = cli.run_cli(["report", "--run-id", "20260309T120000Z"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "# Review Report" in output


def test_cli_doctor_emits_json_and_passes_without_runtime_checks(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "load_dotenv", lambda *args, **kwargs: None)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=test-key\n", encoding="utf-8")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text(
        '{"name":"frontend"}', encoding="utf-8"
    )
    monkeypatch.setenv("PM_PAL_LLM", "openai:gpt-5-nano")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    exit_code = cli.run_cli(["doctor", "--skip-runtime", "--json"])
    captured = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["status"] == "warn"
    assert any(
        item["name"] == "python" and item["status"] == "pass"
        for item in captured["checks"]
    )
    assert any(
        item["name"] == "feishu" and item["status"] == "warn"
        for item in captured["checks"]
    )


def test_cli_doctor_fails_when_required_model_key_is_missing(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "load_dotenv", lambda *args, **kwargs: None)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text(
        '{"name":"frontend"}', encoding="utf-8"
    )
    monkeypatch.setenv("PM_PAL_LLM", "openai:gpt-5-nano")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    exit_code = cli.run_cli(["doctor", "--skip-runtime"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "OPENAI_API_KEY is missing" in output


def test_cli_doctor_checks_runtime_endpoints(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli,
        "run_doctor",
        lambda **kwargs: {
            "status": "pass",
            "summary": {"pass": 3, "warn": 0, "fail": 0},
            "checks": [
                {
                    "name": "backend_health",
                    "status": "pass",
                    "summary": "ok",
                    "detail": "",
                },
                {
                    "name": "backend_ready",
                    "status": "pass",
                    "summary": "ok",
                    "detail": "",
                },
                {
                    "name": "frontend_runtime",
                    "status": "pass",
                    "summary": "ok",
                    "detail": "",
                },
            ],
        },
    )

    exit_code = cli.run_cli(["doctor"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "backend_health" in output
    assert "frontend_runtime" in output
