"""Lightweight review-memory abstractions backed by JSON files."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .normalizer import NormalizedRequirement


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    reference_id: str
    source_kind: str
    title: str
    summary: str
    requirement_summary: str
    finding_excerpt: str = ""
    review_mode: str = "quick"
    tags: tuple[str, ...] = ()
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MemoryHit:
    reference_id: str
    source_kind: str
    title: str
    summary: str
    score: float
    finding_excerpt: str = ""
    review_mode: str = "quick"
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["score"] = round(self.score, 4)
        return payload


class BaseMemoryStore:
    backend_name = "unknown"

    def import_seeds(self, *, force: bool = False) -> list[str]:
        raise NotImplementedError

    def retrieve_similar(
        self, requirement: NormalizedRequirement, *, limit: int = 3
    ) -> list[MemoryHit]:
        raise NotImplementedError

    def store_review_case(
        self,
        *,
        run_id: str,
        requirement: NormalizedRequirement,
        review_payload: dict[str, Any],
    ) -> str | None:
        raise NotImplementedError


class NoopMemoryStore(BaseMemoryStore):
    backend_name = "noop"

    def import_seeds(self, *, force: bool = False) -> list[str]:
        return []

    def retrieve_similar(
        self, requirement: NormalizedRequirement, *, limit: int = 3
    ) -> list[MemoryHit]:
        return []

    def store_review_case(
        self,
        *,
        run_id: str,
        requirement: NormalizedRequirement,
        review_payload: dict[str, Any],
    ) -> str | None:
        return None


class FileBackedMemoryStore(BaseMemoryStore):
    backend_name = "file"

    def __init__(
        self, storage_path: str | Path, *, seeds_dir: str | Path | None = None
    ) -> None:
        self.storage_path = Path(storage_path)
        default_seeds_dir = Path(__file__).resolve().parents[2] / "memory" / "seeds"
        self.seeds_dir = Path(seeds_dir) if seeds_dir is not None else default_seeds_dir

    def import_seeds(self, *, force: bool = False) -> list[str]:
        payload = self._load_payload()
        imported_seed_ids = set(
            str(item)
            for item in payload.get("imported_seed_ids", [])
            if str(item).strip()
        )
        records = list(payload.get("records", []) or [])
        imported: list[str] = []

        if not self.seeds_dir.exists():
            return []

        for seed_file in sorted(self.seeds_dir.glob("*.json")):
            try:
                seed_payload = json.loads(seed_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            raw_records = (
                seed_payload.get("records", seed_payload)
                if isinstance(seed_payload, dict)
                else seed_payload
            )
            if not isinstance(raw_records, list):
                continue
            for item in raw_records:
                record = _coerce_record(item, fallback_source_kind="seed")
                if record is None:
                    continue
                if not force and record.reference_id in imported_seed_ids:
                    continue
                records = [
                    existing
                    for existing in records
                    if str(existing.get("reference_id", "") or "")
                    != record.reference_id
                ]
                records.append(record.to_dict())
                imported_seed_ids.add(record.reference_id)
                imported.append(record.reference_id)

        payload["records"] = records
        payload["imported_seed_ids"] = sorted(imported_seed_ids)
        self._write_payload(payload)
        return imported

    def retrieve_similar(
        self, requirement: NormalizedRequirement, *, limit: int = 3
    ) -> list[MemoryHit]:
        self.import_seeds()
        records = [
            _coerce_record(item)
            for item in self._load_payload().get("records", []) or []
        ]
        query_tokens = _requirement_tokens(requirement)
        scored: list[MemoryHit] = []
        for record in records:
            if record is None:
                continue
            score = _similarity_score(query_tokens, _record_tokens(record))
            if score <= 0:
                continue
            scored.append(
                MemoryHit(
                    reference_id=record.reference_id,
                    source_kind=record.source_kind,
                    title=record.title,
                    summary=record.summary,
                    score=score,
                    finding_excerpt=record.finding_excerpt,
                    review_mode=record.review_mode,
                    tags=record.tags,
                    metadata=record.metadata,
                )
            )
        return sorted(scored, key=lambda item: item.score, reverse=True)[
            : max(0, int(limit))
        ]

    def store_review_case(
        self,
        *,
        run_id: str,
        requirement: NormalizedRequirement,
        review_payload: dict[str, Any],
    ) -> str | None:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return None

        payload = self._load_payload()
        records = [
            item
            for item in payload.get("records", []) or []
            if str(item.get("reference_id", "") or "") != f"review:{normalized_run_id}"
        ]
        findings = (
            review_payload.get("findings", [])
            if isinstance(review_payload.get("findings"), list)
            else []
        )
        summary = (
            review_payload.get("summary", {})
            if isinstance(review_payload.get("summary"), dict)
            else {}
        )
        record = MemoryRecord(
            reference_id=f"review:{normalized_run_id}",
            source_kind="history",
            title=f"Review {normalized_run_id}",
            summary=str(summary.get("overall_risk", "unknown")).strip()
            + " risk review with persisted outcome context.",
            requirement_summary=requirement.summary,
            finding_excerpt=_build_finding_excerpt(findings),
            review_mode=str(
                review_payload.get("review_mode", review_payload.get("mode", "quick"))
                or "quick"
            ),
            tags=tuple(str(item) for item in requirement.modules[:4]),
            created_at=datetime.now(timezone.utc).isoformat(),
            metadata={
                "run_id": normalized_run_id,
                "overall_risk": summary.get("overall_risk", "unknown"),
                "reviewers_used": list(review_payload.get("reviewers_used", []) or []),
                "similar_reviews_referenced": list(
                    review_payload.get("similar_reviews_referenced", []) or []
                ),
            },
        )
        records.append(record.to_dict())
        payload["records"] = records
        self._write_payload(payload)
        return record.reference_id

    def _load_payload(self) -> dict[str, Any]:
        if not self.storage_path.exists():
            return {"version": 1, "records": [], "imported_seed_ids": []}
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "records": [], "imported_seed_ids": []}
        if not isinstance(payload, dict):
            return {"version": 1, "records": [], "imported_seed_ids": []}
        payload.setdefault("version", 1)
        payload.setdefault("records", [])
        payload.setdefault("imported_seed_ids", [])
        return payload

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class ChromaMemoryStore(NoopMemoryStore):
    """Reserved extension point for a future vector-backed store."""

    backend_name = "chroma"


def _coerce_record(
    payload: Any, fallback_source_kind: str = "history"
) -> MemoryRecord | None:
    if not isinstance(payload, dict):
        return None
    reference_id = str(payload.get("reference_id", "") or "").strip()
    title = str(payload.get("title", "") or "").strip()
    summary = str(payload.get("summary", "") or "").strip()
    if not reference_id or not title or not summary:
        return None
    metadata = (
        dict(payload.get("metadata", {}) or {})
        if isinstance(payload.get("metadata"), dict)
        else {}
    )
    return MemoryRecord(
        reference_id=reference_id,
        source_kind=str(
            payload.get("source_kind", fallback_source_kind) or fallback_source_kind
        ),
        title=title,
        summary=summary,
        requirement_summary=str(payload.get("requirement_summary", "") or "").strip(),
        finding_excerpt=str(payload.get("finding_excerpt", "") or "").strip(),
        review_mode=str(payload.get("review_mode", "quick") or "quick"),
        tags=tuple(str(item) for item in payload.get("tags", []) or []),
        created_at=str(payload.get("created_at", "") or "").strip(),
        metadata=metadata,
    )


def _tokenize(text: str) -> set[str]:
    return {item for item in re.findall(r"[a-z0-9]{3,}", str(text or "").lower())}


def _requirement_tokens(requirement: NormalizedRequirement) -> set[str]:
    return _tokenize(
        " ".join(
            [
                requirement.summary,
                " ".join(requirement.scenarios),
                " ".join(requirement.acceptance_criteria),
                " ".join(requirement.modules),
                " ".join(requirement.dependency_hints),
                " ".join(requirement.risk_hints),
            ]
        )
    )


def _record_tokens(record: MemoryRecord) -> set[str]:
    return _tokenize(
        " ".join(
            [
                record.title,
                record.summary,
                record.requirement_summary,
                record.finding_excerpt,
                " ".join(record.tags),
                json.dumps(record.metadata, ensure_ascii=False),
            ]
        )
    )


def _similarity_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    shared = left & right
    if not shared:
        return 0.0
    return (len(shared) / len(left | right)) + (0.02 * len(shared))


def _build_finding_excerpt(findings: list[dict[str, Any]]) -> str:
    excerpts: list[str] = []
    for item in findings[:2]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "") or "").strip()
        detail = str(item.get("detail", item.get("description", "")) or "").strip()
        if title and detail:
            excerpts.append(f"{title}: {detail}")
        elif title:
            excerpts.append(title)
    return " | ".join(excerpts)
