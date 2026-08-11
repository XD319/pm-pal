"""Window A review helpers for gating and requirement normalization."""

from .aggregator import (
    AggregatedReview,
    AggregatedReviewArtifacts,
    aggregate_review_results,
)
from .gating import GatingConfig, ReviewModeDecision, decide_review_mode
from .memory_store import (
    BaseMemoryStore,
    ChromaMemoryStore,
    FileBackedMemoryStore,
    MemoryHit,
    NoopMemoryStore,
)
from .normalizer import (
    NormalizedRequirement,
    build_reviewer_input,
    build_reviewer_inputs,
    normalize_requirement,
)
from .normalizer_cache import (
    BaseNormalizerCache,
    FileBackedNormalizerCache,
    InMemoryNormalizerCache,
    NormalizerCacheResult,
    normalize_requirement_with_cache,
)
from .parallel_review_manager import (
    ParallelReviewResult,
    run_parallel_review,
    run_parallel_review_async,
)
from .profile_router import (
    ReviewProfileRoutingResult,
    load_profile_pack,
    route_review_profile,
)

__all__ = [
    "AggregatedReview",
    "AggregatedReviewArtifacts",
    "BaseMemoryStore",
    "BaseNormalizerCache",
    "ChromaMemoryStore",
    "FileBackedMemoryStore",
    "FileBackedNormalizerCache",
    "GatingConfig",
    "InMemoryNormalizerCache",
    "MemoryHit",
    "NoopMemoryStore",
    "NormalizedRequirement",
    "NormalizerCacheResult",
    "ParallelReviewResult",
    "ReviewModeDecision",
    "ReviewProfileRoutingResult",
    "aggregate_review_results",
    "build_reviewer_input",
    "build_reviewer_inputs",
    "decide_review_mode",
    "normalize_requirement",
    "normalize_requirement_with_cache",
    "run_parallel_review",
    "run_parallel_review_async",
    "route_review_profile",
    "load_profile_pack",
]
