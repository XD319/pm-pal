"""Lightweight tracing helpers for agent nodes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pm_pal.templates import TemplateDefinition


class Span:
    """Timing context for a single agent invocation.

    Create via :func:`trace_start`; call :meth:`end` once the agent is done.
    """

    __slots__ = ("_attrs", "_start_dt", "agent_name", "input_chars", "model")

    def __init__(self, agent_name: str, *, model: str, input_chars: int) -> None:
        self.agent_name = agent_name
        self.model = model
        self.input_chars = input_chars
        self._start_dt = datetime.now(UTC)
        self._attrs: dict[str, Any] = {}

    def set_attr(self, key: str, value: Any) -> None:
        """Attach an extra attribute to this span."""
        self._attrs[key] = value

    def set_attrs(self, attrs: dict[str, Any]) -> None:
        """Attach multiple extra attributes to this span."""
        self._attrs.update(attrs)

    def set_template(self, template: TemplateDefinition) -> None:
        """Attach template metadata to the span."""
        self._attrs.update(template.prompt_trace_metadata())

    def end(
        self,
        *,
        status: str = "ok",
        output_chars: int = 0,
        raw_output_path: str = "",
        error_message: str = "",
    ) -> dict[str, Any]:
        """Return the finalised trace dict for this span."""
        end_dt = datetime.now(UTC)
        duration_ms = int((end_dt - self._start_dt).total_seconds() * 1000)
        data = {
            "start": self._start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "duration_ms": duration_ms,
            "model": self.model,
            "status": status,
            "input_chars": self.input_chars,
            "output_chars": output_chars,
            "raw_output_path": raw_output_path,
            "error_message": error_message,
        }
        if "template_version" in self._attrs and "prompt_version" not in self._attrs:
            data["prompt_version"] = self._attrs["template_version"]
        if self._attrs:
            data.update(self._attrs)
        return data


def trace_start(
    agent_name: str,
    *,
    model: str = "unknown",
    input_chars: int = 0,
) -> Span:
    """Begin a trace span.  Assign ``span.model`` later if unknown at call time."""
    return Span(agent_name, model=model, input_chars=input_chars)
