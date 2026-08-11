"""Prompt renderer foundation for semi-automatic coding-agent handoff."""

from .renderer import (
    render_claude_code_prompt,
    render_codex_prompt,
    render_openclaw_prompt,
)
from .templates import (
    BASE_SECTION_ORDER,
    CLAUDE_CODE_PROMPT_TEMPLATE,
    CODEX_PROMPT_TEMPLATE,
    OPENCLAW_PROMPT_TEMPLATE,
    PromptTemplate,
)

__all__ = [
    "BASE_SECTION_ORDER",
    "CLAUDE_CODE_PROMPT_TEMPLATE",
    "CODEX_PROMPT_TEMPLATE",
    "OPENCLAW_PROMPT_TEMPLATE",
    "PromptTemplate",
    "render_claude_code_prompt",
    "render_codex_prompt",
    "render_openclaw_prompt",
]
