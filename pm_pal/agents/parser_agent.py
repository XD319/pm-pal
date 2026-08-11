"""Parser agent LangGraph node that decomposes a requirement document."""

from __future__ import annotations

from typing import Any

from .structured_runner import run_structured_node
from ..prompt_quality.context_trimmer import trim_context_for_node
from ..prompt_registry import load_prompt_template
from ..prompts import (
    CLARIFY_PARSER_SYSTEM_PROMPT,
    CLARIFY_PARSER_USER_PROMPT,
    PARSER_SYSTEM_PROMPT,
    PARSER_USER_PROMPT,
)
from ..schemas import ParserOutput, validate_parser_output
from ..state import ReviewState
from ..templates.registry import CLARIFY_PARSER_REVIEW_PROMPT, PARSER_REVIEW_PROMPT
from ..utils.logging import get_logger
from ..utils.trace import trace_start

_AGENT = "parser"
_DEFAULT_PROMPT_VERSION = PARSER_REVIEW_PROMPT.version
_CLARIFY_PROMPT_VERSION = CLARIFY_PARSER_REVIEW_PROMPT.version
log = get_logger(_AGENT)


async def run(state: ReviewState) -> ReviewState:
    """Parse the requirement document into structured requirement items."""

    requirement_doc: str = state.get("requirement_doc", "")
    trace: dict[str, Any] = dict(state.get("trace", {}))
    run_dir: str = state.get("run_dir", "")
    prompt_version = str(
        state.get("parser_prompt_version", _DEFAULT_PROMPT_VERSION)
        or _DEFAULT_PROMPT_VERSION
    )
    log.info("Parser started", extra={"node": _AGENT})

    span = trace_start(_AGENT, input_chars=len(requirement_doc))

    if prompt_version == _CLARIFY_PROMPT_VERSION:
        prompt_template = CLARIFY_PARSER_REVIEW_PROMPT
        system_prompt = CLARIFY_PARSER_SYSTEM_PROMPT
        user_prompt = CLARIFY_PARSER_USER_PROMPT
    else:
        prompt_template = PARSER_REVIEW_PROMPT
        try:
            record = load_prompt_template(_AGENT)
            system_prompt = record.system_prompt
            user_prompt = record.user_prompt_template.replace(
                "{input_text}", "{requirement_doc}"
            )
        except Exception:
            system_prompt = PARSER_SYSTEM_PROMPT
            user_prompt = PARSER_USER_PROMPT
    span.set_template(prompt_template)

    trimmed_context = trim_context_for_node(_AGENT, requirement_doc)
    if trimmed_context.was_trimmed:
        span.set_attr("trimmed_context", True)
        span.set_attr("original_input_chars", trimmed_context.original_chars)
        span.set_attr("trimmed_input_chars", trimmed_context.trimmed_chars)
        requirement_doc = trimmed_context.text

    prompt = f"{system_prompt}\n\n{user_prompt.format(requirement_doc=requirement_doc)}"
    result = await run_structured_node(
        agent_name=_AGENT,
        prompt=prompt,
        schema=ParserOutput,
        validate_output=validate_parser_output,
        empty_output=lambda: ParserOutput().model_dump(mode="python"),
        trace=trace,
        run_dir=run_dir,
        span=span,
    )

    parsed_items = result.output.get("parsed_items", [])
    log_fn = log.info if result.status == "ok" else log.warning
    log_fn(
        "Parser completed with %s parsed items",
        len(parsed_items),
        extra={"node": _AGENT},
    )
    return {"parsed_items": parsed_items, "trace": result.trace}
