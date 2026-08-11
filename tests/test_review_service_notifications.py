from __future__ import annotations

import pytest

from pm_pal.notifications import NotificationType
from pm_pal.service import review_service


@pytest.mark.asyncio
async def test_review_prd_text_async_dispatches_feishu_submitted_and_clarification_notifications(
    tmp_path, monkeypatch
):
    captured: list[dict[str, object]] = []

    async def fake_run_review(requirement_doc: str, **kwargs):
        return {
            "run_id": "20260311T010101Z",
            "run_dir": str(tmp_path / "20260311T010101Z"),
            "result": {
                "metrics": {"coverage_ratio": 0.8},
                "trace": {"reporter": {"status": "ok"}},
                "high_risk_ratio": 0.2,
                "revision_round": 0,
                "clarification": {
                    "triggered": True,
                    "status": "pending",
                    "questions": [{"id": "clarify-1", "question": "Who owns rollout?"}],
                },
            },
            "report_paths": {
                "report_md": str(tmp_path / "20260311T010101Z" / "report.md"),
                "report_json": str(tmp_path / "20260311T010101Z" / "report.json"),
                "run_trace": str(tmp_path / "20260311T010101Z" / "run_trace.json"),
            },
        }

    def fake_dispatch_notification(run_dir, **kwargs):
        captured.append({"run_dir": run_dir, **kwargs})
        return None

    monkeypatch.setattr(review_service, "run_review", fake_run_review)
    monkeypatch.setattr(
        review_service, "build_delivery_handoff_outputs", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr(
        review_service, "dispatch_notification", fake_dispatch_notification
    )

    await review_service.review_prd_text_async(
        prd_text="# Feishu review",
        run_id="20260311T010101Z",
        config_overrides={
            "outputs_root": str(tmp_path),
            "audit_context": {
                "source": "feishu",
                "client_metadata": {"trigger_source": "feishu"},
            },
        },
    )

    assert [item["notification_type"] for item in captured] == [
        NotificationType.review_submitted,
        NotificationType.clarification_required,
    ]
    assert captured[0]["run_id"] == "20260311T010101Z"
    assert captured[1]["metadata"]["clarification_question_count"] == 1
    assert captured[1]["metadata"]["review_run_status"] == "clarification_required"


@pytest.mark.asyncio
async def test_review_prd_text_async_dispatches_feishu_failed_notification_when_run_crashes(
    tmp_path, monkeypatch
):
    captured: list[dict[str, object]] = []

    async def fake_run_review(requirement_doc: str, **kwargs):
        raise RuntimeError("upstream review failure")

    def fake_dispatch_notification(run_dir, **kwargs):
        captured.append({"run_dir": run_dir, **kwargs})
        return None

    monkeypatch.setattr(review_service, "run_review", fake_run_review)
    monkeypatch.setattr(
        review_service, "dispatch_notification", fake_dispatch_notification
    )

    with pytest.raises(RuntimeError, match="upstream review failure"):
        await review_service.review_prd_text_async(
            prd_text="# Feishu review",
            run_id="20260311T010102Z",
            config_overrides={
                "outputs_root": str(tmp_path),
                "audit_context": {
                    "source": "feishu",
                    "client_metadata": {"trigger_source": "feishu"},
                },
            },
        )

    assert [item["notification_type"] for item in captured] == [
        NotificationType.review_submitted,
        NotificationType.review_failed,
    ]
    assert "upstream review failure" in str(captured[1]["summary"])
