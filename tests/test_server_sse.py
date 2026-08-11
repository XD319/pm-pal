from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from pm_pal.server import app as app_module
from tests.project_review_helpers import (
    create_test_project,
    link_run_to_project,
    project_review_path,
)
from pm_pal.server.sse import ProgressBroadcaster


def _write_completed_feishu_run(
    tmp_path,
    run_id: str,
    *,
    submitter_open_id: str = "ou_owner",
    tenant_key: str = "tenant-a",
) -> None:
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text("# done", encoding="utf-8")
    (run_dir / "report.json").write_text("{}", encoding="utf-8")
    (run_dir / "entry_context.json").write_text(
        json.dumps(
            {
                "source_origin": "feishu",
                "entry_mode": "plugin",
                "submitter_open_id": submitter_open_id,
                "tenant_key": tenant_key,
                "trigger_source": "feishu",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _read_sse_payloads(response) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for line in response.iter_lines():
        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    return payloads


def test_progress_broadcaster_publishes_to_multiple_subscribers() -> None:
    async def _run() -> None:
        broadcaster = ProgressBroadcaster()
        run_id = "20260325T101010Z"
        stream_one = broadcaster.subscribe(run_id)
        stream_two = broadcaster.subscribe(run_id)

        async def _read_once(stream):
            event = await anext(stream)
            return json.loads(event.removeprefix("data: ").strip())

        first_task = asyncio.create_task(_read_once(stream_one))
        second_task = asyncio.create_task(_read_once(stream_two))
        await asyncio.sleep(0)

        broadcaster.publish(
            run_id,
            "progress",
            {
                "node": "parser",
                "status": "start",
                "timestamp": "2026-03-25T10:10:10+00:00",
            },
        )

        first_payload, second_payload = await asyncio.gather(first_task, second_task)
        assert (
            first_payload
            == second_payload
            == {
                "node": "parser",
                "status": "start",
                "timestamp": "2026-03-25T10:10:10+00:00",
                "event_type": "progress",
            }
        )

        await stream_one.aclose()
        await stream_two.aclose()

    asyncio.run(_run())


def test_progress_stream_endpoint_returns_sse_headers_and_terminal_event(
    tmp_path, monkeypatch
) -> None:
    run_id = "20260325T111111Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text("# done", encoding="utf-8")
    (run_dir / "report.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = TestClient(app_module.app)
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    with client.stream(
        "GET", project_review_path(project_id, run_id, "/progress/stream")
    ) as response:
        payloads = _read_sse_payloads(response)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["connection"] == "keep-alive"
        assert response.headers["x-accel-buffering"] == "no"

    assert payloads[-1]["node"] == "run"
    assert payloads[-1]["status"] == "completed"
    assert payloads[-1]["run_id"] == run_id
    assert payloads[-1]["terminal"] is True
    app_module._jobs.clear()


def test_progress_stream_allows_feishu_run_with_matching_context(
    tmp_path, monkeypatch
) -> None:
    run_id = "20260325T141414Z"
    _write_completed_feishu_run(tmp_path, run_id)
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = TestClient(app_module.app)
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    with client.stream(
        "GET",
        project_review_path(
            project_id,
            run_id,
            "/progress/stream?open_id=ou_owner&tenant_key=tenant-a&embed=feishu",
        ),
    ) as response:
        payloads = _read_sse_payloads(response)

    assert response.status_code == 200
    assert payloads[-1]["terminal"] is True
    app_module._jobs.clear()


def test_progress_stream_closes_after_completed_run(tmp_path, monkeypatch) -> None:
    run_id = "20260325T121212Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    snapshot = {
        "run_id": run_id,
        "status": "completed",
        "created_at": "2026-03-25T12:12:12+00:00",
        "updated_at": "2026-03-25T12:13:00+00:00",
        "progress": {
            "percent": 100,
            "current_node": "",
            "nodes": {"parser": {"status": "completed", "runs": 1}},
            "updated_at": "2026-03-25T12:13:00+00:00",
            "error": "",
        },
        "report_paths": {},
    }
    (run_dir / app_module.RUN_PROGRESS_FILENAME).write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = TestClient(app_module.app)
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    with client.stream(
        "GET", project_review_path(project_id, run_id, "/progress/stream")
    ) as response:
        payloads = _read_sse_payloads(response)

    assert len(payloads) == 1
    assert payloads[0]["status"] == "completed"
    assert payloads[0]["terminal"] is True
    app_module._jobs.clear()
