"""Executor routing for approved delivery bundles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pm_pal.execution.models import ExecutionEvent, ExecutionMode, ExecutionTask
from pm_pal.packs.delivery_bundle import BundleStatus, DeliveryBundle
from pm_pal.utils.time import utc_now_iso as _utc_now_iso

DEFAULT_PACK_SPECS: tuple[tuple[str, str], ...] = (
    ("implementation_pack", "codex"),
    ("test_pack", "claude_code"),
)


class BundleNotApprovedError(ValueError):
    """Raised when trying to route a bundle that is not approved."""


class ExecutorRouter:
    """Route approved bundles into executor-specific execution tasks."""

    def __init__(
        self,
        default_mode: ExecutionMode = ExecutionMode.agent_assisted,
        pack_specs: tuple[tuple[str, str], ...] | None = None,
    ):
        self.default_mode = default_mode
        self.pack_specs = tuple(pack_specs or DEFAULT_PACK_SPECS)

    def route(self, bundle: DeliveryBundle) -> list[ExecutionTask]:
        if bundle.status != BundleStatus.approved:
            raise BundleNotApprovedError(
                "bundle must be approved before execution routing"
            )

        effective_mode = self._resolve_mode(bundle)
        tasks: list[ExecutionTask] = []
        now = _utc_now_iso()
        for source_pack_type, executor_type in self.pack_specs:
            artifact_ref = getattr(bundle.artifacts, source_pack_type, None)
            if artifact_ref is None or not str(artifact_ref.path or "").strip():
                continue
            pack_payload = self._load_json(Path(str(artifact_ref.path)))
            task_id = f"{bundle.bundle_id}:{source_pack_type}"
            metadata: dict[str, Any] = {"artifact_path": artifact_ref.path}
            plan_task_id = str(pack_payload.get("task_id", "")).strip()
            if plan_task_id:
                metadata["plan_task_id"] = plan_task_id
            tasks.append(
                ExecutionTask(
                    task_id=task_id,
                    bundle_id=bundle.bundle_id,
                    source_pack_type=source_pack_type,
                    executor_type=executor_type,
                    execution_mode=effective_mode,
                    created_at=now,
                    updated_at=now,
                    execution_log=[
                        ExecutionEvent(
                            event_id=f"{task_id}:created",
                            timestamp=now,
                            event_type="created",
                            detail=f"Routed from {source_pack_type}",
                            actor="executor_router",
                        )
                    ],
                    metadata=metadata,
                )
            )
        return tasks

    def reassign(
        self, task: ExecutionTask, new_executor: str, new_mode: ExecutionMode
    ) -> ExecutionTask:
        now = _utc_now_iso()
        payload = task.model_dump(mode="python")
        execution_log = list(task.execution_log)
        execution_log.append(
            ExecutionEvent(
                event_id=f"{task.task_id}:reassign:{len(execution_log) + 1}",
                timestamp=now,
                event_type="assigned",
                detail=f"executor={new_executor}, mode={new_mode}",
                actor="executor_router",
            )
        )
        payload.update(
            {
                "executor_type": str(new_executor or "").strip(),
                "execution_mode": new_mode,
                "updated_at": now,
                "execution_log": execution_log,
            }
        )
        return ExecutionTask.model_validate(payload)

    def _resolve_mode(self, bundle: DeliveryBundle) -> ExecutionMode:
        if self.default_mode == ExecutionMode.human_only:
            return ExecutionMode.human_only
        if self._bundle_has_high_risk(bundle):
            return ExecutionMode.agent_assisted
        return self.default_mode

    def _bundle_has_high_risk(self, bundle: DeliveryBundle) -> bool:
        metadata = bundle.metadata if isinstance(bundle.metadata, dict) else {}
        risks = metadata.get("risk_summary")
        if isinstance(risks, list):
            for risk in risks:
                if (
                    isinstance(risk, dict)
                    and str(risk.get("level", "")).lower() == "high"
                ):
                    return True

        execution_pack_path = Path(
            str(bundle.artifacts.execution_pack.path or "").strip()
        )
        payload = self._load_json(execution_pack_path)
        risk_pack = payload.get("risk_pack")
        if not isinstance(risk_pack, list):
            return False
        return any(
            isinstance(item, dict) and str(item.get("level", "")).lower() == "high"
            for item in risk_pack
        )

    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}
