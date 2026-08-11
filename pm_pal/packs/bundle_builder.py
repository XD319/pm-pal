"""Build and persist DeliveryBundle payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pm_pal.packs.delivery_bundle import (
    ArtifactRef,
    BundleStatus,
    DeliveryArtifacts,
    DeliveryBundle,
)
from pm_pal.utils.time import utc_now_iso as _utc_now_iso


class DeliveryBundleBuilder:
    """Assemble the canonical delivery bundle."""

    def build(
        self,
        run_output: dict[str, Any],
        artifact_refs: dict[str, ArtifactRef],
        pack_paths: dict[str, str],
    ) -> DeliveryBundle:
        run_id = str(run_output.get("run_id", "") or "").strip()
        created_at = _utc_now_iso()

        bundle_artifacts = DeliveryArtifacts(
            prd_review_report=artifact_refs["prd_review_report"],
            open_questions=artifact_refs["open_questions"],
            scope_boundary=artifact_refs["scope_boundary"],
            tech_design_draft=artifact_refs["tech_design_draft"],
            test_checklist=artifact_refs["test_checklist"],
            implementation_pack=ArtifactRef(
                artifact_type="implementation_pack",
                path=str(pack_paths["implementation_pack"]),
            ),
            test_pack=ArtifactRef(
                artifact_type="test_pack", path=str(pack_paths["test_pack"])
            ),
            execution_pack=ArtifactRef(
                artifact_type="execution_pack", path=str(pack_paths["execution_pack"])
            ),
        )

        return DeliveryBundle(
            bundle_id=f"bundle-{run_id}" if run_id else "bundle-unknown",
            created_at=created_at,
            status=BundleStatus.draft,
            source_run_id=run_id,
            artifacts=bundle_artifacts,
            approval_history=[],
            metadata={
                "source_report_paths": run_output.get("report_paths", {}),
                "generated_from": "review_service",
            },
        )

    def save(self, bundle: DeliveryBundle, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "delivery_bundle.json"
        path.write_text(
            json.dumps(bundle.model_dump(mode="python"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
