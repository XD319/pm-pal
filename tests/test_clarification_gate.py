from pm_pal.review.clarification_gate import (
    apply_clarification_answers,
    build_clarification_payload,
)


def test_build_clarification_payload_limits_to_three_high_unanswerable_questions():
    findings = [
        {
            "finding_id": f"finding-{index}",
            "title": f"Finding {index}",
            "severity": "high",
            "source_reviewer": reviewer,
            "reviewers": [reviewer],
            "ambiguity_type": "unanswerable",
        }
        for index, reviewer in enumerate(
            ["product", "engineering", "qa", "security"], start=1
        )
    ]
    reviewer_summaries = [
        {
            "reviewer": reviewer,
            "ambiguity_type": f"missing-{reviewer}",
            "clarification_question": f"Question for {reviewer}?",
        }
        for reviewer in ["product", "engineering", "qa", "security"]
    ]

    clarification = build_clarification_payload(findings, reviewer_summaries)

    assert clarification["triggered"] is True
    assert clarification["status"] == "pending"
    assert len(clarification["questions"]) == 3
    assert [item["reviewer"] for item in clarification["questions"]] == [
        "product",
        "engineering",
        "qa",
    ]
    assert all(
        item["ambiguity_type"] == "unanswerable" for item in clarification["questions"]
    )


def test_apply_clarification_answers_updates_targeted_findings_and_marks_answered():
    findings = [
        {
            "finding_id": "finding-1",
            "title": "Missing goal",
            "detail": "The PRD does not define the success metric.",
            "description": "The PRD does not define the success metric.",
            "severity": "high",
            "source_reviewer": "product",
            "reviewers": ["product"],
            "ambiguity_type": "unanswerable",
            "clarification_applied": False,
            "original_severity": "",
            "user_clarification": "",
        }
    ]
    clarification = build_clarification_payload(
        findings,
        [
            {
                "reviewer": "product",
                "ambiguity_type": "missing_goal",
                "clarification_question": "What outcome confirms success?",
            }
        ],
    )

    updated_findings, updated_clarification = apply_clarification_answers(
        findings,
        clarification,
        [
            {
                "question_id": clarification["questions"][0]["id"],
                "answer": "Success means recruiters complete login in under 30 seconds and can reach the dashboard.",
            }
        ],
    )

    assert updated_clarification["status"] == "answered"
    assert (
        updated_clarification["answers_applied"][0]["question_id"]
        == clarification["questions"][0]["id"]
    )
    assert updated_clarification["findings_updated"][0]["finding_id"] == "finding-1"
    assert updated_findings[0]["clarification_applied"] is True
    assert updated_findings[0]["original_severity"] == "high"
    assert updated_findings[0]["severity"] == "medium"
    assert "User clarification:" in updated_findings[0]["detail"]
    assert updated_findings[0]["user_clarification"].startswith(
        "Success means recruiters complete login"
    )
