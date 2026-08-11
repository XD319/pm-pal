"""End-to-end traceability mapping for delivery and execution artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pm_pal.execution.models import ExecutionTask, TraceLink
from pm_pal.packs.delivery_bundle import DeliveryBundle


class TraceabilityMap:
    """Maintain requirement-to-execution traceability links."""

    def __init__(self) -> None:
        self._links: list[TraceLink] = []

    @property
    def links(self) -> list[TraceLink]:
        return list(self._links)

    def build_from_bundle(
        self, bundle: DeliveryBundle, tasks: list[ExecutionTask]
    ) -> TraceabilityMap:
        report_payload = self._load_report_payload(bundle)
        requirements = report_payload.get("parsed_items")
        review_results = report_payload.get("review_results")
        plan_tasks = report_payload.get("tasks")

        requirements = requirements if isinstance(requirements, list) else []
        review_results = review_results if isinstance(review_results, list) else []
        plan_tasks = plan_tasks if isinstance(plan_tasks, list) else []

        review_by_requirement = {
            str(item.get("id", "")).strip(): item
            for item in review_results
            if isinstance(item, dict)
        }
        tasks_by_requirement: dict[str, list[dict[str, Any]]] = {}
        for task in plan_tasks:
            if not isinstance(task, dict):
                continue
            for requirement_id in task.get("requirement_ids", []) or []:
                requirement_id_str = str(requirement_id or "").strip()
                if requirement_id_str:
                    tasks_by_requirement.setdefault(requirement_id_str, []).append(task)

        execution_by_plan_task: dict[str, list[ExecutionTask]] = {}
        execution_fallback: list[ExecutionTask] = []
        for execution_task in tasks:
            plan_task_id = str(execution_task.metadata.get("plan_task_id", "")).strip()
            if plan_task_id:
                execution_by_plan_task.setdefault(plan_task_id, []).append(
                    execution_task
                )
            else:
                execution_fallback.append(execution_task)

        links: list[TraceLink] = []
        for requirement in requirements:
            if not isinstance(requirement, dict):
                continue
            requirement_id = str(requirement.get("id", "")).strip()
            if not requirement_id:
                continue
            review_item_id = str(
                review_by_requirement.get(requirement_id, {}).get("id", "")
            ).strip()
            related_plan_tasks = tasks_by_requirement.get(requirement_id, [])

            if not related_plan_tasks:
                links.append(
                    TraceLink(
                        requirement_id=requirement_id,
                        review_item_id=review_item_id,
                        link_type="orphan",
                    )
                )
                continue

            for plan_task in related_plan_tasks:
                plan_task_id = str(plan_task.get("id", "")).strip()
                execution_tasks = (
                    execution_by_plan_task.get(plan_task_id) or execution_fallback
                )
                if not execution_tasks:
                    links.append(
                        TraceLink(
                            requirement_id=requirement_id,
                            review_item_id=review_item_id,
                            plan_task_id=plan_task_id,
                            test_item_id=f"test::{plan_task_id}"
                            if plan_task_id
                            else "",
                            link_type="partial",
                        )
                    )
                    continue

                for execution_task in execution_tasks:
                    links.append(
                        TraceLink(
                            requirement_id=requirement_id,
                            review_item_id=review_item_id,
                            plan_task_id=plan_task_id,
                            test_item_id=f"test::{plan_task_id}"
                            if plan_task_id
                            else "",
                            execution_task_id=execution_task.task_id,
                            link_type="full",
                        )
                    )

        self._links = links
        return self

    def query_by_requirement(self, requirement_id: str) -> list[TraceLink]:
        normalized = str(requirement_id or "").strip()
        return [link for link in self._links if link.requirement_id == normalized]

    def query_by_execution_task(self, task_id: str) -> list[TraceLink]:
        normalized = str(task_id or "").strip()
        return [link for link in self._links if link.execution_task_id == normalized]

    def to_dict(self) -> dict[str, Any]:
        return {
            "links": [link.model_dump(mode="python") for link in self._links],
            "counts": {
                "total": len(self._links),
                "full": sum(1 for link in self._links if link.link_type == "full"),
                "partial": sum(
                    1 for link in self._links if link.link_type == "partial"
                ),
                "orphan": sum(1 for link in self._links if link.link_type == "orphan"),
            },
        }

    def save(self, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load_report_payload(self, bundle: DeliveryBundle) -> dict[str, Any]:
        artifact_dir = (
            Path(str(bundle.artifacts.execution_pack.path or "")).resolve().parent
        )
        candidates = [
            artifact_dir / "report.json",
            artifact_dir / "traceability_source.json",
        ]
        metadata = bundle.metadata if isinstance(bundle.metadata, dict) else {}
        report_paths = metadata.get("source_report_paths")
        if isinstance(report_paths, dict):
            report_json_path = report_paths.get("report_json")
            if report_json_path:
                candidates.insert(0, Path(str(report_json_path)))

        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload
        return {}
