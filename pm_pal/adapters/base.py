"""Adapter abstractions for offline execution request generation."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pm_pal.execution.models import ExecutionTask
from pm_pal.packs.delivery_bundle import DeliveryBundle
from pm_pal.packs.schemas import ExecutionPack

EXECUTION_CONTEXT_FILENAME = "execution_context.md"
EXECUTION_PACK_FILENAME = "execution_pack.json"


class BaseAdapter(ABC):
    """Base class for adapter-specific request builders.

    Adapters in this layer only materialize request payloads and execution
    context files. They do not execute external commands.
    """

    adapter_name: str = ""
    display_name: str = ""
    request_filename: str = ""
    request_type: str = ""

    def build_pack(self, handoff_bundle: dict[str, Any]) -> dict[str, Any]:
        """Generate adapter-specific request and execution context files."""

        if not isinstance(handoff_bundle, dict):
            raise TypeError("handoff_bundle must be an object")

        task = self._coerce_task(handoff_bundle.get("task"))
        bundle = self._coerce_bundle(handoff_bundle.get("bundle"))
        pack_dir = self._resolve_pack_dir(bundle)
        pack_dir.mkdir(parents=True, exist_ok=True)

        prompt = self.build_prompt(str(pack_dir))
        context_path = pack_dir / EXECUTION_CONTEXT_FILENAME
        context_path.write_text(
            self._render_execution_context(task=task, bundle=bundle, prompt=prompt),
            encoding="utf-8",
        )

        request = self._create_execution_request(
            task=task,
            bundle=bundle,
            pack_dir=pack_dir,
            prompt=prompt,
            context_path=context_path,
        )
        request_path = pack_dir / self.request_filename
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        return {
            "adapter": self.adapter_name,
            "pack_dir": str(pack_dir),
            "request_path": str(request_path),
            "context_path": str(context_path),
            "request": request,
        }

    def build_prompt(self, pack_dir: str) -> str:
        """Render the adapter prompt from an execution pack directory."""

        execution_pack = self._load_execution_pack(Path(pack_dir))
        return self._render_prompt(execution_pack)

    def create_execution_request(
        self, task: ExecutionTask, bundle: DeliveryBundle
    ) -> dict[str, Any]:
        """Create an adapter-specific request payload without executing it."""

        resolved_task = self._coerce_task(task)
        resolved_bundle = self._coerce_bundle(bundle)
        return self._create_execution_request(
            task=resolved_task,
            bundle=resolved_bundle,
            pack_dir=self._resolve_pack_dir(resolved_bundle),
        )

    def _create_execution_request(
        self,
        *,
        task: ExecutionTask,
        bundle: DeliveryBundle,
        pack_dir: Path,
        prompt: str | None = None,
        context_path: Path | None = None,
    ) -> dict[str, Any]:
        self._validate_task(task)
        resolved_prompt = (
            prompt if prompt is not None else self.build_prompt(str(pack_dir))
        )
        resolved_context_path = context_path or (pack_dir / EXECUTION_CONTEXT_FILENAME)
        execution_pack = self._load_execution_pack(pack_dir)

        base_payload: dict[str, Any] = {
            "adapter": self.adapter_name,
            "request_type": self.request_type,
            "request_version": "1.0",
            "pack_dir": str(pack_dir),
            "prompt": resolved_prompt,
            "context_path": str(resolved_context_path),
            "task": {
                "task_id": task.task_id,
                "bundle_id": task.bundle_id,
                "source_pack_type": task.source_pack_type,
                "executor_type": task.executor_type,
                "execution_mode": task.execution_mode,
                "status": task.status,
                "assigned_to": task.assigned_to,
                "metadata": task.metadata,
            },
            "bundle": {
                "bundle_id": bundle.bundle_id,
                "status": bundle.status,
                "source_run_id": bundle.source_run_id,
                "created_at": bundle.created_at,
                "metadata": bundle.metadata,
            },
            "artifacts": bundle.artifacts.model_dump(mode="python"),
        }
        base_payload.update(
            self._build_adapter_payload(
                task=task,
                bundle=bundle,
                execution_pack=execution_pack,
                prompt=resolved_prompt,
                context_path=resolved_context_path,
            )
        )
        return base_payload

    def _validate_task(self, task: ExecutionTask) -> None:
        if not self.adapter_name:
            raise ValueError("adapter_name must be set on adapter subclasses")
        if task.executor_type != self.adapter_name:
            raise ValueError(
                f"task executor_type '{task.executor_type}' does not match adapter '{self.adapter_name}'"
            )

    def _resolve_pack_dir(self, bundle: DeliveryBundle) -> Path:
        execution_pack_path = Path(
            str(bundle.artifacts.execution_pack.path or "").strip()
        )
        if not execution_pack_path.name:
            raise ValueError("bundle is missing execution_pack path")
        return execution_pack_path.parent

    def _load_execution_pack(self, pack_dir: Path) -> ExecutionPack:
        execution_pack_path = pack_dir / EXECUTION_PACK_FILENAME
        if not execution_pack_path.exists():
            raise FileNotFoundError(f"execution pack not found: {execution_pack_path}")
        payload = json.loads(execution_pack_path.read_text(encoding="utf-8"))
        return ExecutionPack.model_validate(payload)

    def _render_execution_context(
        self, *, task: ExecutionTask, bundle: DeliveryBundle, prompt: str
    ) -> str:
        artifact_lines = [
            f"- `{name}`: {artifact.get('path', '')}"
            for name, artifact in bundle.artifacts.model_dump(mode="python").items()
            if isinstance(artifact, dict)
        ]
        metadata_block = (
            json.dumps(bundle.metadata, ensure_ascii=False, indent=2)
            if bundle.metadata
            else "{}"
        )
        return "\n".join(
            [
                f"# {self.display_name or self.adapter_name} Execution Context",
                "",
                "## Task",
                f"- Task ID: `{task.task_id}`",
                f"- Bundle ID: `{task.bundle_id}`",
                f"- Source Pack: `{task.source_pack_type}`",
                f"- Execution Mode: `{task.execution_mode}`",
                f"- Status: `{task.status}`",
                "",
                "## Bundle",
                f"- Bundle ID: `{bundle.bundle_id}`",
                f"- Status: `{bundle.status}`",
                f"- Source Run ID: `{bundle.source_run_id}`",
                "",
                "## Artifacts",
                *artifact_lines,
                "",
                "## Bundle Metadata",
                "```json",
                metadata_block,
                "```",
                "",
                "## Prompt",
                prompt.rstrip(),
                "",
            ]
        )

    @staticmethod
    def _coerce_task(task: ExecutionTask | dict[str, Any] | None) -> ExecutionTask:
        if task is None:
            raise ValueError("handoff_bundle.task is required")
        if isinstance(task, ExecutionTask):
            return task
        if isinstance(task, dict):
            return ExecutionTask.model_validate(task)
        raise TypeError(f"Unsupported task type: {type(task)!r}")

    @staticmethod
    def _coerce_bundle(
        bundle: DeliveryBundle | dict[str, Any] | None,
    ) -> DeliveryBundle:
        if bundle is None:
            raise ValueError("handoff_bundle.bundle is required")
        if isinstance(bundle, DeliveryBundle):
            return bundle
        if isinstance(bundle, dict):
            return DeliveryBundle.model_validate(bundle)
        raise TypeError(f"Unsupported bundle type: {type(bundle)!r}")

    def _base_input_payload(
        self,
        *,
        bundle: DeliveryBundle,
        prompt: str,
        context_path: Path,
    ) -> dict[str, object]:
        return {
            "prompt": prompt,
            "context_file": str(context_path),
            "workspace_root": str(bundle.metadata.get("workspace_root", "") or ""),
        }

    def _deferred_file_callback(
        self, *, task: ExecutionTask, context_path: Path
    ) -> dict[str, object]:
        return {
            "type": "file",
            "mode": "deferred",
            "task_id": task.task_id,
            "context_file": str(context_path),
        }

    @abstractmethod
    def _render_prompt(self, execution_pack: ExecutionPack) -> str:
        """Render an adapter-specific prompt."""

    @abstractmethod
    def _build_adapter_payload(
        self,
        *,
        task: ExecutionTask,
        bundle: DeliveryBundle,
        execution_pack: ExecutionPack,
        prompt: str,
        context_path: Path,
    ) -> dict[str, Any]:
        """Build adapter-specific request fields."""
