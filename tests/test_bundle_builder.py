from __future__ import annotations

import json

from pm_pal.packs.artifact_splitter import ArtifactSplitter
from pm_pal.packs.bundle_builder import DeliveryBundleBuilder
from pm_pal.packs.delivery_bundle import BundleStatus, DeliveryBundle


def test_bundle_builder_builds_bundle_from_mock_data(
    tmp_path, sample_report_json: dict
):
    artifact_refs = ArtifactSplitter().split(sample_report_json, tmp_path)
    builder = DeliveryBundleBuilder()

    bundle = builder.build(
        run_output={
            "run_id": "20260307T120000Z",
            "report_paths": {"report_json": str(tmp_path / "report.json")},
        },
        artifact_refs=artifact_refs,
        pack_paths={
            "implementation_pack": str(tmp_path / "implementation_pack.json"),
            "test_pack": str(tmp_path / "test_pack.json"),
            "execution_pack": str(tmp_path / "execution_pack.json"),
        },
    )

    assert bundle.bundle_id == "bundle-20260307T120000Z"
    assert bundle.status == "draft"
    assert bundle.source_run_id == "20260307T120000Z"
    assert bundle.artifacts.open_questions.artifact_type == "open_questions"


def test_bundle_builder_save_round_trips_schema(tmp_path, sample_report_json: dict):
    artifact_refs = ArtifactSplitter().split(sample_report_json, tmp_path)
    builder = DeliveryBundleBuilder()
    bundle = builder.build(
        run_output={"run_id": "20260307T120001Z", "report_paths": {}},
        artifact_refs=artifact_refs,
        pack_paths={
            "implementation_pack": str(tmp_path / "implementation_pack.json"),
            "test_pack": str(tmp_path / "test_pack.json"),
            "execution_pack": str(tmp_path / "execution_pack.json"),
        },
    )

    path = builder.save(bundle, tmp_path)
    loaded = DeliveryBundle.model_validate(json.loads(path.read_text(encoding="utf-8")))

    assert loaded.bundle_id == "bundle-20260307T120001Z"
    assert loaded.status == BundleStatus.draft


def test_bundle_builder_contains_all_expected_artifact_refs(
    tmp_path, sample_report_json: dict
):
    artifact_refs = ArtifactSplitter().split(sample_report_json, tmp_path)
    bundle = DeliveryBundleBuilder().build(
        run_output={"run_id": "20260307T120002Z", "report_paths": {}},
        artifact_refs=artifact_refs,
        pack_paths={
            "implementation_pack": str(tmp_path / "implementation_pack.json"),
            "test_pack": str(tmp_path / "test_pack.json"),
            "execution_pack": str(tmp_path / "execution_pack.json"),
        },
    )

    payload = bundle.model_dump(mode="python")
    assert set(payload["artifacts"]) == {
        "prd_review_report",
        "open_questions",
        "scope_boundary",
        "tech_design_draft",
        "test_checklist",
        "implementation_pack",
        "test_pack",
        "execution_pack",
    }
