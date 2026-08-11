from __future__ import annotations

from pm_pal.review.normalizer_cache import (
    FileBackedNormalizerCache,
    InMemoryNormalizerCache,
    normalize_requirement_with_cache,
)


def test_in_memory_normalizer_cache_reuses_identical_prd():
    cache = InMemoryNormalizerCache()
    prd = "# Summary\nExport flow needs downstream schema versioning.\n\n# Acceptance Criteria\n- Version the schema"

    first = normalize_requirement_with_cache(prd, cache=cache)
    second = normalize_requirement_with_cache(prd, cache=cache)

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.requirement.summary == first.requirement.summary


def test_file_backed_normalizer_cache_persists_across_instances(tmp_path):
    cache_path = tmp_path / "normalizer_cache.json"
    prd = "# Summary\nProfile sync sends customer data to an external service.\n\n# Risks\n- Privacy review required"

    first = normalize_requirement_with_cache(
        prd, cache=FileBackedNormalizerCache(cache_path)
    )
    second = normalize_requirement_with_cache(
        prd, cache=FileBackedNormalizerCache(cache_path)
    )

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.cache_backend == "file"
