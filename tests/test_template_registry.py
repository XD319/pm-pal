from __future__ import annotations

import pytest

from pm_pal.templates import (
    ReviewPromptTemplate,
    TemplateNotFoundError,
    TemplateTypeNotFoundError,
    TemplateVersionNotFoundError,
    get_adapter_prompt_template,
    get_default_template,
    get_delivery_artifact_template,
    get_review_prompt_template,
    get_template_by_version,
    get_templates_by_type,
    list_template_records,
    list_templates,
    register_template,
)


def test_registry_returns_versioned_review_templates() -> None:
    planner = get_review_prompt_template("review.planner")

    assert planner.template_id == "review.planner"
    assert planner.template_type == "review_prompt"
    assert planner.version == "v1.1"
    assert "delivery plan" in planner.description
    assert "{items_json}" in planner.user_prompt


def test_registry_returns_versioned_adapter_templates() -> None:
    codex = get_adapter_prompt_template("adapter.codex.handoff_markdown")
    openclaw = get_adapter_prompt_template("adapter.openclaw.handoff_markdown")

    assert codex.template_type == "adapter_prompt"
    assert codex.version == "handoff_markdown_v1"
    assert codex.agent_name == "Codex"
    assert codex.section_order
    assert openclaw.agent_name == "OpenClaw"


def test_registry_returns_delivery_artifact_templates_with_renderers() -> None:
    template = get_delivery_artifact_template("delivery_artifact.test_checklist")
    rendered = template.renderer(
        {
            "test_plan": {
                "test_scope": ["Login API"],
                "edge_cases": ["Expired token"],
                "regression_focus": ["Password login"],
            },
            "review_results": [
                {"id": "REQ-001", "issues": ["Clarify provider mapping"]}
            ],
        }
    )

    assert template.template_type == "delivery_artifact_template"
    assert template.version == "v1"
    assert template.file_name == "test_checklist.md"
    assert rendered.startswith("# Test Checklist")
    assert "Clarify provider mapping" in rendered


def test_registry_lists_all_supported_template_types() -> None:
    template_types = {template.template_type for template in list_templates()}

    assert {
        "review_prompt",
        "adapter_prompt",
        "delivery_artifact_template",
    } <= template_types


def test_registry_supports_default_version_and_type_lookups() -> None:
    default_template = get_default_template("review.parser")
    adapter_templates = get_templates_by_type("adapter_prompt")

    assert default_template.template_id == "review.parser"
    assert default_template.version == "v1.1"
    assert {template.template_type for template in adapter_templates} == {
        "adapter_prompt"
    }
    assert {template.template_id for template in adapter_templates} == {
        "adapter.claude_code.handoff_markdown",
        "adapter.codex.handoff_markdown",
        "adapter.openclaw.handoff_markdown",
    }


def test_registry_supports_explicit_version_lookup() -> None:
    template = get_template_by_version(
        "adapter.codex.handoff_markdown", "handoff_markdown_v1"
    )

    assert template.template_id == "adapter.codex.handoff_markdown"
    assert template.version == "handoff_markdown_v1"


def test_registry_returns_controlled_errors_for_missing_templates() -> None:
    with pytest.raises(TemplateNotFoundError):
        get_default_template("review.unknown")

    with pytest.raises(TemplateTypeNotFoundError):
        get_templates_by_type("unknown_type")

    with pytest.raises(TemplateVersionNotFoundError):
        get_template_by_version("review.parser", "missing-version")


def test_registry_records_include_default_flag_and_status() -> None:
    template_id = "review.test.registry_metadata"
    register_template(
        ReviewPromptTemplate(
            template_id=template_id,
            template_type="review_prompt",
            version="v0",
            description="Older registry test prompt.",
            system_prompt="system v0",
            user_prompt="user v0",
        ),
        is_default=False,
    )
    register_template(
        ReviewPromptTemplate(
            template_id=template_id,
            template_type="review_prompt",
            version="v1",
            description="Latest registry test prompt.",
            system_prompt="system v1",
            user_prompt="user v1",
        )
    )

    records = list_template_records(template_type="review_prompt")
    record_by_version = {
        record["version"]: record
        for record in records
        if record["template_id"] == template_id
    }

    assert record_by_version["v0"]["is_default"] is False
    assert record_by_version["v0"]["status"] == "registered"
    assert record_by_version["v1"]["is_default"] is True
    assert record_by_version["v1"]["status"] == "active"
