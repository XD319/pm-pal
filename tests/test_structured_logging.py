from __future__ import annotations

import logging
from io import StringIO

from fastapi.testclient import TestClient

from prd_pal.server import app as app_module
from prd_pal.utils.logging import (
    RunLogContext,
    StructuredFormatter,
    get_logger,
    setup_logging,
)


def test_structured_formatter_includes_context_and_extra_fields() -> None:
    setup_logging(log_format="json")
    logger = get_logger("tests.logging")
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter())
    logging.getLogger().addHandler(handler)

    with RunLogContext("run_abc123"):
        logger.info(
            "hello %s",
            "world",
            extra={"node": "parser", "duration_ms": 2300, "custom_flag": True},
        )

    formatted = stream.getvalue().strip()
    logging.getLogger().removeHandler(handler)

    assert '"message": "hello world"' in formatted
    assert '"run_id": "run_abc123"' in formatted
    assert '"node": "parser"' in formatted
    assert '"duration_ms": 2300' in formatted
    assert '"custom_flag": true' in formatted


def test_request_logging_middleware_adds_trace_id_and_logs_request(
    tmp_path, monkeypatch, caplog
) -> None:
    monkeypatch.setenv("MARRDP_API_AUTH_DISABLED", "true")
    monkeypatch.setenv("MARRDP_API_RATE_LIMIT_DISABLED", "true")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()
    app_module._reset_submission_rate_limits()

    with caplog.at_level(logging.INFO, logger="prd_pal.server.http"):
        client = TestClient(app_module.app)
        response = client.get("/api/templates")

    assert response.status_code == 200
    assert response.headers["X-Trace-ID"]
    request_logs = [
        record for record in caplog.records if record.name == "prd_pal.server.http"
    ]
    assert request_logs
    assert request_logs[-1].trace_id == response.headers["X-Trace-ID"]
    assert request_logs[-1].path == "/api/templates"
    assert request_logs[-1].status_code == 200
