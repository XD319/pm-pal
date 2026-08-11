"""Route normalized ingress requests to conservative review profiles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .ingress_normalization import CanonicalReviewRequest

DEFAULT_PROFILE = "default"
_SUPPORTED_PROFILES = {
    DEFAULT_PROFILE,
    "admin_backoffice",
    "data_sensitive",
    "approval_workflow",
    "growth_analytics",
}
_PROFILE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "admin_backoffice": (
        "admin",
        "backoffice",
        "back office",
        "ops console",
        "operator",
        "internal tool",
    ),
    "data_sensitive": (
        "pii",
        "privacy",
        "gdpr",
        "payment",
        "encryption",
        "sso",
        "oauth",
        "audit log",
    ),
    "approval_workflow": (
        "approval",
        "approver",
        "sign-off",
        "signoff",
        "workflow",
        "release gate",
        "change request",
    ),
    "growth_analytics": (
        "experiment",
        "a/b",
        "ab test",
        "funnel",
        "conversion",
        "retention",
        "cohort",
        "analytics",
    ),
}
_WORD_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class ReviewProfileRoutingResult:
    selected_profile: str
    confidence: float
    reason: str
    secondary_profiles: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_profile": self.selected_profile,
            "confidence": round(float(self.confidence), 2),
            "reason": self.reason,
            "secondary_profiles": list(self.secondary_profiles),
        }


def route_review_profile(
    request: CanonicalReviewRequest | Mapping[str, Any],
) -> ReviewProfileRoutingResult:
    normalized = _normalize_request(request)
    text = _build_signal_text(normalized)
    hint = str(normalized.get("review_profile_hint", "") or "").strip().lower()
    scores = {
        profile: 0 for profile in _SUPPORTED_PROFILES if profile != DEFAULT_PROFILE
    }
    reasons: dict[str, list[str]] = {profile: [] for profile in scores}

    for profile, keywords in _PROFILE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                scores[profile] += 2
                reasons[profile].append(f"keyword:{keyword}")
    if hint:
        mapped_hint = _map_hint_to_profile(hint)
        if mapped_hint:
            scores[mapped_hint] += 3
            reasons[mapped_hint].append(f"hint:{hint}")

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_profile, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0

    # Conservative fallback: require clear winning evidence and at least 2 signals.
    if top_score < 2 or top_score == second_score:
        reason = "No dominant profile signal found; fallback to default for conservative routing."
        return ReviewProfileRoutingResult(
            selected_profile=DEFAULT_PROFILE,
            confidence=0.35 if top_score else 0.2,
            reason=reason,
            secondary_profiles=tuple(profile for profile, score in ranked if score > 0)[
                :2
            ],
        )

    confidence = min(0.95, 0.5 + (top_score * 0.1))
    reason = f"Selected {top_profile} because signals were stronger than alternatives: {', '.join(reasons[top_profile][:3])}."
    secondary = tuple(profile for profile, score in ranked[1:] if score >= 2)[:2]
    return ReviewProfileRoutingResult(
        selected_profile=top_profile,
        confidence=confidence,
        reason=reason,
        secondary_profiles=secondary,
    )


def load_profile_pack(
    profile: str, secondary_profiles: tuple[str, ...] | list[str] | None = None
) -> dict[str, Any]:
    requested = str(profile or "").strip().lower() or DEFAULT_PROFILE
    selected = requested if requested in _SUPPORTED_PROFILES else DEFAULT_PROFILE
    root = Path(__file__).with_name("profile_packs")
    selected_root = root / selected

    cleaned_secondary = _normalize_secondary_profiles(selected, secondary_profiles)
    profile_sequence = [DEFAULT_PROFILE]
    if selected != DEFAULT_PROFILE:
        profile_sequence.append(selected)
    profile_sequence.extend(cleaned_secondary)

    checklist_items: list[dict[str, str]] = []
    rules_items: list[dict[str, str]] = []
    checklist_seen: set[str] = set()
    rules_seen: set[str] = set()
    checklist_sources: list[dict[str, str]] = []
    rules_sources: list[dict[str, str]] = []

    for profile_name in profile_sequence:
        profile_root = root / profile_name
        source = _source_for_profile(profile_name, selected)
        checklist_path = profile_root / "checklist.md"
        rules_path = profile_root / "rules.md"
        checklist_sources.append(
            {"source": source, "profile": profile_name, "path": str(checklist_path)}
        )
        rules_sources.append(
            {"source": source, "profile": profile_name, "path": str(rules_path)}
        )
        checklist_items.extend(
            _extract_sourced_items(
                checklist_path, source=source, profile=profile_name, seen=checklist_seen
            )
        )
        rules_items.extend(
            _extract_sourced_items(
                rules_path, source=source, profile=profile_name, seen=rules_seen
            )
        )

    checklist = _render_sourced_items(checklist_items)
    rules = _render_sourced_items(rules_items)
    return {
        "profile": selected,
        "requested_profile": requested,
        "secondary_profiles": cleaned_secondary,
        "pack_path": str(selected_root),
        "checklist_path": str(selected_root / "checklist.md"),
        "rules_path": str(selected_root / "rules.md"),
        "checklist": checklist,
        "rules": rules,
        "checklist_items": checklist_items,
        "rules_items": rules_items,
        "checklist_sources": checklist_sources,
        "rules_sources": rules_sources,
    }


def _normalize_request(
    request: CanonicalReviewRequest | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(request, CanonicalReviewRequest):
        return request.model_dump(mode="python")
    return dict(request)


def _build_signal_text(payload: Mapping[str, Any]) -> str:
    content = payload.get("content")
    text = ""
    if isinstance(content, Mapping):
        text = str(content.get("text", "") or "")
    fields = [
        text,
        str(payload.get("source", "") or ""),
        str(payload.get("requirement_type", "") or ""),
        str(payload.get("project_id", "") or ""),
    ]
    return _WORD_RE.sub(" ", " ".join(fields).lower()).strip()


def _map_hint_to_profile(hint: str) -> str | None:
    if hint in _SUPPORTED_PROFILES:
        return hint
    compact = hint.replace("-", "_").replace(" ", "_")
    if compact in _SUPPORTED_PROFILES:
        return compact
    alias = {
        "security_heavy": "data_sensitive",
        "security": "data_sensitive",
        "analytics": "growth_analytics",
        "admin": "admin_backoffice",
        "approval": "approval_workflow",
    }
    return alias.get(compact)


def _read_optional_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _normalize_secondary_profiles(
    selected: str, secondary_profiles: tuple[str, ...] | list[str] | None
) -> list[str]:
    if not secondary_profiles:
        return []
    accepted: list[str] = []
    seen: set[str] = set()
    for raw in secondary_profiles:
        profile = str(raw or "").strip().lower()
        if (
            not profile
            or profile in seen
            or profile == selected
            or profile == DEFAULT_PROFILE
        ):
            continue
        if profile not in _SUPPORTED_PROFILES:
            continue
        accepted.append(profile)
        seen.add(profile)
    return accepted


def _source_for_profile(profile: str, selected: str) -> str:
    if profile == DEFAULT_PROFILE:
        return "base"
    if profile == selected:
        return "selected_profile"
    return "secondary_profile"


def _extract_sourced_items(
    path: Path, *, source: str, profile: str, seen: set[str]
) -> list[dict[str, str]]:
    content = _read_optional_text(path)
    if not content:
        return []
    rows: list[dict[str, str]] = []
    for line in content.splitlines():
        item = _normalize_line_item(line)
        if not item:
            continue
        dedupe_key = _canonical_item_key(item)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rows.append({"text": item, "source": source, "profile": profile})
    return rows


def _normalize_line_item(line: str) -> str:
    normalized = line.strip()
    if not normalized:
        return ""
    normalized = re.sub(r"^[-*]\s*", "", normalized).strip()
    return normalized


def _canonical_item_key(item: str) -> str:
    compact = re.sub(r"[^a-z0-9]+", " ", item.lower()).strip()
    return _WORD_RE.sub(" ", compact)


def _render_sourced_items(items: list[dict[str, str]]) -> str:
    return "\n".join(f"- [{row['source']}] {row['text']}" for row in items)
