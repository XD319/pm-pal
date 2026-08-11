"""Markdown prompt templates for coding-agent handoff."""

from pm_pal.templates import (
    AdapterPromptTemplate,
    BASE_SECTION_ORDER,
    get_adapter_prompt_template,
)

PromptTemplate = AdapterPromptTemplate
CODEX_PROMPT_TEMPLATE = get_adapter_prompt_template("adapter.codex.handoff_markdown")
CLAUDE_CODE_PROMPT_TEMPLATE = get_adapter_prompt_template(
    "adapter.claude_code.handoff_markdown"
)
OPENCLAW_PROMPT_TEMPLATE = get_adapter_prompt_template(
    "adapter.openclaw.handoff_markdown"
)

__all__ = [
    "BASE_SECTION_ORDER",
    "CLAUDE_CODE_PROMPT_TEMPLATE",
    "CODEX_PROMPT_TEMPLATE",
    "OPENCLAW_PROMPT_TEMPLATE",
    "PromptTemplate",
]
