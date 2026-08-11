from __future__ import annotations

from pm_pal.review.ingress_normalization import normalize_ingress_request


def test_normalize_ingress_request_feishu_payload() -> None:
    canonical = normalize_ingress_request(
        run_id="20260414T010203Z",
        requirement_doc="# PRD\n\nEnable SSO login.",
        source_ref="feishu://docx/abc",
        audit_context={
            "source": "feishu",
            "actor": "ou_123",
            "client_metadata": {
                "open_id": "ou_123",
                "team_id": "team-feishu",
                "project_id": "proj-login",
                "requirement_type": "prd",
                "review_profile_hint": "security-heavy",
                "attachments": [
                    {
                        "name": "diagram",
                        "url": "https://example.com/a.png",
                        "mime_type": "image/png",
                    }
                ],
            },
        },
    )

    assert canonical.source == "feishu"
    assert canonical.team_id == "team-feishu"
    assert canonical.project_id == "proj-login"
    assert canonical.submitter_id == "ou_123"
    assert canonical.requirement_type == "product_requirement"
    assert canonical.review_profile_hint == "security-heavy"
    assert canonical.content.kind == "connector_source"
    assert len(canonical.attachments) == 1


def test_normalize_ingress_request_web_payload() -> None:
    canonical = normalize_ingress_request(
        run_id="20260414T010204Z",
        requirement_doc="Add dashboard filters",
        prd_text="Add dashboard filters",
        audit_context={
            "source": "web",
            "actor": "web-user-1",
            "client_metadata": {
                "team_id": "team-web",
                "project_id": "proj-dash",
            },
        },
    )
    assert canonical.source == "web"
    assert canonical.content.kind == "inline_text"
    assert canonical.requirement_type == "product_requirement"


def test_normalize_ingress_request_missing_team_project_metadata() -> None:
    canonical = normalize_ingress_request(
        run_id="20260414T010205Z",
        requirement_doc="Fix flaky build",
        prd_text="Fix flaky build",
        audit_context={"source": "cli", "actor": "cli"},
    )

    assert canonical.source == "cli"
    assert canonical.team_id is None
    assert canonical.project_id is None
    assert any("team_id is missing" in note for note in canonical.normalization_notes)
    assert any(
        "project_id is missing" in note for note in canonical.normalization_notes
    )


def test_normalize_ingress_request_ambiguous_requirement_type_is_conservative() -> None:
    canonical = normalize_ingress_request(
        run_id="20260414T010206Z",
        requirement_doc="Please check this quickly",
        source_ref="unknown://x",
        audit_context={
            "source": "hook",
            "client_metadata": {
                "type": "maybe-something",
            },
        },
    )

    assert canonical.requirement_type == "unknown"
    assert any("not recognized" in note for note in canonical.normalization_notes)
