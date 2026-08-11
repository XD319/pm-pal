from pm_pal.review import GatingConfig, decide_review_mode


def test_sparse_requirement_skips_review():
    decision = decide_review_mode("Need login page update")

    assert decision.selected_mode == "skip"
    assert decision.skipped is True
    assert any("too sparse" in reason for reason in decision.reasons)


def test_compact_structured_requirement_uses_quick_triage():
    prd_text = """
# Candidate portal login

Allow recruiters to sign in with email and password.

## Scenarios
- Recruiter opens the login page and submits valid credentials.

## Acceptance Criteria
- User reaches the dashboard after successful login.
- Invalid credentials show an inline error message.
"""

    decision = decide_review_mode(prd_text)

    assert decision.selected_mode == "quick"
    assert decision.skipped is False
    assert decision.completeness_score >= 3
    assert decision.cross_system_hits == 0


def test_cross_system_high_risk_requirement_uses_full_review():
    prd_text = """
# Cross-system billing sync

This feature updates recruiter billing status across several systems and requires coordination across FE, BE, QA, DevOps, and Security.

## Modules
- `billing-api`
- `recruiter-portal`
- `finance-worker`
- `audit-service`

## Scenarios
- Finance updates a subscription and the recruiter portal reflects the new quota.
- The backend retries an external webhook when the downstream finance platform is unavailable.

## Acceptance Criteria
- The recruiter portal shows the latest subscription state.
- The webhook retry flow preserves idempotent writes and audit logs.
- Rollback steps are documented for payment incidents.
"""

    decision = decide_review_mode(prd_text)

    assert decision.selected_mode == "full"
    assert decision.complexity_score >= 2
    assert decision.module_count >= 4
    assert decision.cross_system_hits >= 1
    assert decision.risk_keyword_hits >= 2


def test_explicit_mode_override_is_respected():
    config = GatingConfig(risk_keyword_threshold=3, full_score_threshold=5)
    decision = decide_review_mode(
        """
# Tiny request

## Scenarios
- Update copy.

## Acceptance Criteria
- New copy is visible.
""",
        config=config,
        requested_mode="full",
    )

    assert decision.selected_mode == "full"
    assert any("explicitly requested" in reason for reason in decision.reasons)
