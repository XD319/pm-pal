from pm_pal.review.reviewer_agents.delivery_reviewer import arbitrate_conflict


def test_delivery_reviewer_auto_resolves_high_severity_mismatch():
    resolution = arbitrate_conflict(
        {
            "type": "severity_mismatch",
            "topic": "Security review gate required",
            "finding_severity": "medium, high",
            "risk_severity": "high",
        },
        product_summary="Product believes the flow is ready.",
        security_summary="Security requires approval before release.",
    )

    assert (
        resolution.recommendation
        == "Treat 'Security review gate required' as high severity until reviewers align on one shared label."
    )
    assert resolution.decided_by == "delivery_reviewer.rules"
    assert resolution.needs_human is False
    assert "higher severity of high" in resolution.reasoning
    assert "product: Product believes the flow is ready." in resolution.reasoning
    assert (
        "security: Security requires approval before release." in resolution.reasoning
    )


def test_delivery_reviewer_escalates_scope_dependency_conflict_to_human():
    resolution = arbitrate_conflict(
        {
            "type": "scope_inclusion_vs_dependency_blocker",
            "description": "Product indicates scope is covered, but Engineering flags blockers.",
        },
        product_summary="Product thinks scope is already in the PRD.",
        engineering_summary="Engineering still sees sequencing blockers.",
    )

    assert resolution.recommendation == (
        "Escalate to product and engineering leads to confirm scope ownership, dependency sequencing, and release impact."
    )
    assert resolution.decided_by == "delivery_reviewer.rules"
    assert resolution.needs_human is True
    assert (
        "Product scope confidence conflicts with engineering dependency risk."
        in resolution.reasoning
    )
