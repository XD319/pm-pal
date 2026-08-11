from __future__ import annotations

import json
from pathlib import Path

from pm_pal.review.memory_store import FileBackedMemoryStore
from pm_pal.review.normalizer import normalize_requirement


def test_file_backed_memory_store_imports_seeds_retrieves_similar_and_persists_history(
    tmp_path,
):
    store = FileBackedMemoryStore(
        tmp_path / "review_memory.json", seeds_dir=Path("memory/seeds")
    )

    imported = store.import_seeds()
    assert imported

    requirement = normalize_requirement(
        "# Summary\nAdmin export needs schema versioning for downstream analytics and rollback support.\n\n# Acceptance Criteria\n- Version export schema before rollout\n- Add rollback plan for partner ingestion\n"
    )
    hits = store.retrieve_similar(requirement, limit=3)

    assert hits
    assert hits[0].reference_id == "seed:export-contract-hardening"

    stored_ref = store.store_review_case(
        run_id="20260311T010203Z",
        requirement=requirement,
        review_payload={
            "review_mode": "full",
            "summary": {"overall_risk": "medium"},
            "findings": [
                {
                    "title": "Schema drift risk",
                    "detail": "Partner ingestion can break without versioning.",
                }
            ],
            "reviewers_used": ["product", "engineering"],
            "similar_reviews_referenced": [hit.reference_id for hit in hits],
        },
    )

    assert stored_ref == "review:20260311T010203Z"
    payload = json.loads((tmp_path / "review_memory.json").read_text(encoding="utf-8"))
    refs = {item["reference_id"] for item in payload["records"]}
    assert "review:20260311T010203Z" in refs
    assert "seed:export-contract-hardening" in refs
