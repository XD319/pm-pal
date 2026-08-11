from __future__ import annotations

from pm_pal.review.ingress_normalization import normalize_ingress_request
from pm_pal.review.profile_router import load_profile_pack, route_review_profile


def test_profile_router_selects_data_sensitive_for_security_signals() -> None:
    canonical = normalize_ingress_request(
        run_id="20260414T112300Z",
        requirement_doc="Add OAuth login and encrypt PII export for admin approval.",
        prd_text="Add OAuth login and encrypt PII export for admin approval.",
        audit_context={"source": "web"},
    )

    routed = route_review_profile(canonical)

    assert routed.selected_profile == "data_sensitive"
    assert routed.confidence >= 0.6
    assert "Selected data_sensitive" in routed.reason


def test_profile_router_fallbacks_to_default_when_ambiguous() -> None:
    canonical = normalize_ingress_request(
        run_id="20260414T112301Z",
        requirement_doc="Polish wording and improve experience.",
        audit_context={"source": "cli"},
    )

    routed = route_review_profile(canonical)

    assert routed.selected_profile == "default"
    assert "fallback to default" in routed.reason.lower()


def test_load_profile_pack_returns_profile_paths() -> None:
    pack = load_profile_pack("approval_workflow")
    checklist_path = str(pack["checklist_path"]).replace("\\", "/")
    rules_path = str(pack["rules_path"]).replace("\\", "/")

    assert pack["profile"] == "approval_workflow"
    assert checklist_path.endswith("approval_workflow/checklist.md")
    assert rules_path.endswith("approval_workflow/rules.md")
    assert "approval" in pack["checklist"].lower()
    assert any(item["source"] == "base" for item in pack["checklist_items"])
    assert any(item["source"] == "selected_profile" for item in pack["checklist_items"])


def test_load_profile_pack_expands_selected_and_secondary_with_sources() -> None:
    pack = load_profile_pack("data_sensitive", secondary_profiles=["approval_workflow"])

    checklist_sources = {item["source"] for item in pack["checklist_items"]}
    rules_sources = {item["source"] for item in pack["rules_items"]}

    assert pack["secondary_profiles"] == ["approval_workflow"]
    assert checklist_sources == {"base", "selected_profile", "secondary_profile"}
    assert rules_sources == {"base", "selected_profile", "secondary_profile"}
    assert any(
        "scope are explicit" in item["text"].lower() and item["source"] == "base"
        for item in pack["checklist_items"]
    )
    assert any(
        "encryption" in item["text"].lower() and item["source"] == "selected_profile"
        for item in pack["checklist_items"]
    )
    assert any(
        "approval states" in item["text"].lower()
        and item["source"] == "secondary_profile"
        for item in pack["checklist_items"]
    )


def test_load_profile_pack_filters_irrelevant_secondary_profiles() -> None:
    pack = load_profile_pack(
        "data_sensitive",
        secondary_profiles=[
            "data_sensitive",
            "default",
            "unknown_profile",
            "approval_workflow",
            "approval_workflow",
        ],
    )

    assert pack["secondary_profiles"] == ["approval_workflow"]
