"""Codex adapter request builder."""

from __future__ import annotations

from pathlib import Path

from pm_pal.execution.models import ExecutionTask
from pm_pal.handoff import render_codex_prompt
from pm_pal.packs.delivery_bundle import DeliveryBundle
from pm_pal.packs.schemas import ExecutionPack

from .base import BaseAdapter


class CodexAdapter(BaseAdapter):
    """Build offline Codex execution requests."""

    adapter_name = "codex"
    display_name = "Codex"
    request_filename = "codex_request.json"
    request_type = "codex.run_pack"

    def _render_prompt(self, execution_pack: ExecutionPack) -> str:
        return render_codex_prompt(execution_pack)

    def _build_adapter_payload(
        self,
        *,
        task: ExecutionTask,
        bundle: DeliveryBundle,
        execution_pack: ExecutionPack,
        prompt: str,
        context_path: Path,
    ) -> dict[str, object]:
        implementation_pack = execution_pack.implementation_pack
        return {
            "input": {
                **self._base_input_payload(
                    bundle=bundle, prompt=prompt, context_path=context_path
                ),
                "target_modules": implementation_pack.target_modules,
                "implementation_steps": implementation_pack.implementation_steps,
                "acceptance_criteria": implementation_pack.acceptance_criteria,
                "constraints": implementation_pack.constraints,
            },
            "callback": self._deferred_file_callback(
                task=task, context_path=context_path
            ),
        }
