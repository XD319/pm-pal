"""Extract a compact, reviewer-friendly requirement view from raw PRD text."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Iterable


_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")
_MODULE_INLINE_RE = re.compile(r"`([A-Za-z0-9_./:-]{2,80})`")
_SCENARIO_TITLES = (
    "scenario",
    "scenarios",
    "use case",
    "use cases",
    "flow",
    "flows",
    "journey",
    "journeys",
)
_ACCEPTANCE_TITLES = ("acceptance criteria", "acceptance criterion", "done when")
_IN_SCOPE_TITLES = ("in scope", "scope")
_OUT_OF_SCOPE_TITLES = ("out of scope", "non goals", "non-goals", "not in scope")
_MAX_SUMMARY_CHARS = 280

_ROLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "frontend",
        re.compile(
            r"\b(frontend|front-end|ui engineer|web engineer|fe)\b", re.IGNORECASE
        ),
    ),
    (
        "backend",
        re.compile(r"\b(backend|back-end|server engineer|be)\b", re.IGNORECASE),
    ),
    (
        "qa",
        re.compile(r"\b(qa|tester|test engineer|quality assurance)\b", re.IGNORECASE),
    ),
    ("product", re.compile(r"\b(pm|product manager|product owner)\b", re.IGNORECASE)),
    ("design", re.compile(r"\b(design|designer|ux|ui/ux)\b", re.IGNORECASE)),
    ("devops", re.compile(r"\b(devops|sre|platform engineer|ops)\b", re.IGNORECASE)),
    (
        "security",
        re.compile(r"\b(security|infosec|application security)\b", re.IGNORECASE),
    ),
    (
        "data",
        re.compile(
            r"\b(data engineer|analytics engineer|bi|data platform)\b", re.IGNORECASE
        ),
    ),
    ("mobile", re.compile(r"\b(mobile|ios|android)\b", re.IGNORECASE)),
)

_DEPENDENCY_HINT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(depends on|dependency|dependent on|requires|rely on|integration|upstream|downstream)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(api|event|queue|webhook|cron|batch|sync|async|sso|oauth|database|cache|redis|kafka)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(third[- ]party|external|vendor|legacy system|shared service)\b",
        re.IGNORECASE,
    ),
)

_RISK_HINT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(risk|rollback|migration|security|privacy|compliance|audit|pii|payment)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(latency|performance|timeout|failure|fallback|retry|rate limit|idempotent)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(data loss|data consistency|breaking change|availability|sla)\b",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True, slots=True)
class NormalizedRequirement:
    """Compact PRD representation used by the gating layer and reviewers."""

    source_text: str
    summary: str
    scenarios: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    dependency_hints: tuple[str, ...] = ()
    risk_hints: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    headings: tuple[str, ...] = ()
    in_scope: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()
    completeness_signals: tuple[str, ...] = ()
    memory_mode: str = "off"
    reviewer_memory_context: tuple[str, ...] = ()

    def for_reviewer(self, reviewer: str) -> str:
        return build_reviewer_input(self, reviewer)

    def for_reviewers(self, reviewers: Iterable[str] | None = None) -> dict[str, str]:
        return build_reviewer_inputs(self, reviewers=reviewers)

    def with_memory_context(
        self,
        *,
        memory_mode: str,
        reviewer_memory_context: Iterable[str] | None = None,
    ) -> "NormalizedRequirement":
        return replace(
            self,
            memory_mode=str(memory_mode or "off").strip().lower() or "off",
            reviewer_memory_context=tuple(
                str(item) for item in reviewer_memory_context or [] if str(item).strip()
            ),
        )


def normalize_requirement(prd_text: str) -> NormalizedRequirement:
    """Extract summary, scenarios, acceptance criteria, and review hints."""

    text = str(prd_text or "").strip()
    if not text:
        raise ValueError("prd_text must not be empty")

    lines = text.splitlines()
    summary = _extract_summary(lines)
    scenarios = _extract_section_items(lines, _SCENARIO_TITLES)
    if not scenarios:
        scenarios = _extract_matching_lines(
            lines,
            (re.compile(r"\b(scenario|flow|journey|user story)\b", re.IGNORECASE),),
        )

    acceptance_criteria = _extract_section_items(lines, _ACCEPTANCE_TITLES)
    if not acceptance_criteria:
        acceptance_criteria = _extract_matching_lines(
            lines,
            (
                re.compile(r"^\s*(?:given|when|then|must|should)\b", re.IGNORECASE),
                re.compile(
                    r"\b(acceptance|verify|validated|success criteria)\b", re.IGNORECASE
                ),
            ),
        )

    dependency_hints = _extract_matching_lines(lines, _DEPENDENCY_HINT_PATTERNS)
    risk_hints = _extract_matching_lines(lines, _RISK_HINT_PATTERNS)
    modules = _extract_modules(text)
    roles = _extract_roles(text)
    headings = _extract_headings(lines)
    in_scope = _extract_scope_items(
        lines, include_titles=_IN_SCOPE_TITLES, exclude_titles=_OUT_OF_SCOPE_TITLES
    )
    out_of_scope = _extract_scope_items(lines, include_titles=_OUT_OF_SCOPE_TITLES)
    completeness_signals = _derive_completeness_signals(
        summary=summary,
        scenarios=scenarios,
        acceptance_criteria=acceptance_criteria,
        modules=modules,
        roles=roles,
        dependency_hints=dependency_hints,
        headings=headings,
        in_scope=in_scope,
        out_of_scope=out_of_scope,
    )

    return NormalizedRequirement(
        source_text=text,
        summary=summary,
        scenarios=tuple(scenarios),
        acceptance_criteria=tuple(acceptance_criteria),
        dependency_hints=tuple(dependency_hints),
        risk_hints=tuple(risk_hints),
        modules=tuple(modules),
        roles=tuple(roles),
        headings=tuple(headings),
        in_scope=tuple(in_scope),
        out_of_scope=tuple(out_of_scope),
        completeness_signals=tuple(completeness_signals),
    )


def build_reviewer_inputs(
    requirement: NormalizedRequirement,
    reviewers: Iterable[str] | None = None,
) -> dict[str, str]:
    reviewer_names = tuple(
        reviewers or ("general", "architecture", "qa", "security", "delivery")
    )
    return {
        reviewer: build_reviewer_input(requirement, reviewer)
        for reviewer in reviewer_names
    }


def build_reviewer_input(requirement: NormalizedRequirement, reviewer: str) -> str:
    normalized_reviewer = str(reviewer or "general").strip().lower() or "general"
    sections: list[tuple[str, tuple[str, ...] | str]] = [
        ("Summary", requirement.summary)
    ]

    if requirement.modules:
        sections.append(("Impacted Modules", requirement.modules))

    if (
        normalized_reviewer in {"general", "architecture", "delivery"}
        and requirement.scenarios
    ):
        sections.append(("Key Scenarios", requirement.scenarios))

    if (
        normalized_reviewer in {"general", "qa", "delivery"}
        and requirement.acceptance_criteria
    ):
        sections.append(("Acceptance Criteria", requirement.acceptance_criteria))

    if (
        normalized_reviewer in {"architecture", "security", "delivery"}
        and requirement.dependency_hints
    ):
        sections.append(("Dependency Hints", requirement.dependency_hints))

    if normalized_reviewer in {"qa", "security", "delivery"} and requirement.risk_hints:
        sections.append(("Risk Hints", requirement.risk_hints))

    if normalized_reviewer in {"general", "delivery"} and requirement.roles:
        sections.append(("Roles", requirement.roles))

    if requirement.in_scope:
        sections.append(("In Scope", requirement.in_scope))

    if requirement.out_of_scope:
        sections.append(("Out of Scope", requirement.out_of_scope))
    if requirement.reviewer_memory_context:
        sections.append(("Memory Assistance", requirement.reviewer_memory_context))

    rendered_sections: list[str] = []
    for title, body in sections:
        if isinstance(body, str):
            rendered_sections.append(f"{title}:\n{body}")
            continue
        if not body:
            continue
        rendered_sections.append(
            f"{title}:\n" + "\n".join(f"- {item}" for item in body)
        )
    return "\n\n".join(rendered_sections)


def _extract_summary(lines: list[str]) -> str:
    paragraphs = _paragraphs_from_lines(lines)
    for paragraph in paragraphs:
        candidate = _normalize_inline_text(paragraph)
        if not candidate:
            continue
        if candidate.lower() in {"prd", "product requirements document"}:
            continue
        return _truncate(candidate, _MAX_SUMMARY_CHARS)
    return "Requirement summary unavailable."


def _extract_section_items(
    lines: list[str], section_titles: tuple[str, ...]
) -> list[str]:
    items: list[str] = []
    active = False
    for line in lines:
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            heading = heading_match.group(1).strip().lower()
            active = any(title in heading for title in section_titles)
            continue
        if not active:
            continue
        bullet_match = _BULLET_RE.match(line)
        candidate = bullet_match.group(1) if bullet_match else line
        cleaned = _normalize_inline_text(candidate)
        if cleaned:
            items.append(cleaned)
    return _dedupe(items)


def _extract_matching_lines(
    lines: list[str], patterns: tuple[re.Pattern[str], ...]
) -> list[str]:
    matches: list[str] = []
    for line in lines:
        bullet_match = _BULLET_RE.match(line)
        candidate = bullet_match.group(1) if bullet_match else line
        normalized = _normalize_inline_text(candidate)
        if normalized and any(pattern.search(normalized) for pattern in patterns):
            matches.append(normalized)
    return _dedupe(matches)


def _extract_modules(text: str) -> list[str]:
    modules = [match.group(1).strip() for match in _MODULE_INLINE_RE.finditer(text)]
    return _dedupe(candidate for candidate in modules if _looks_like_module(candidate))


def _extract_roles(text: str) -> list[str]:
    roles: list[str] = []
    for role_name, pattern in _ROLE_PATTERNS:
        if pattern.search(text):
            roles.append(role_name)
    return roles


def _extract_headings(lines: list[str]) -> list[str]:
    headings: list[str] = []
    for line in lines:
        heading_match = _HEADING_RE.match(line)
        if not heading_match:
            continue
        headings.append(_normalize_inline_text(heading_match.group(1)))
    return _dedupe(headings)


def _extract_scope_items(
    lines: list[str],
    *,
    include_titles: tuple[str, ...],
    exclude_titles: tuple[str, ...] = (),
) -> list[str]:
    items: list[str] = []
    active = False
    for line in lines:
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            heading = _normalize_inline_text(heading_match.group(1)).lower()
            active = any(title in heading for title in include_titles) and not any(
                title in heading for title in exclude_titles
            )
            continue
        if not active:
            continue
        bullet_match = _BULLET_RE.match(line)
        candidate = bullet_match.group(1) if bullet_match else line
        cleaned = _normalize_inline_text(candidate)
        if cleaned:
            items.append(cleaned)
    return _dedupe(items)


def _derive_completeness_signals(
    *,
    summary: str,
    scenarios: list[str],
    acceptance_criteria: list[str],
    modules: list[str],
    roles: list[str],
    dependency_hints: list[str],
    headings: list[str],
    in_scope: list[str],
    out_of_scope: list[str],
) -> list[str]:
    signals: list[str] = []
    if summary and summary != "Requirement summary unavailable.":
        signals.append("summary_present")
    if headings:
        signals.append("structured_headings_present")
    if scenarios:
        signals.append("scenarios_present")
    if acceptance_criteria:
        signals.append("acceptance_present")
    if modules:
        signals.append("modules_present")
    if roles:
        signals.append("roles_present")
    if dependency_hints:
        signals.append("dependencies_present")
    if in_scope:
        signals.append("in_scope_present")
    if out_of_scope:
        signals.append("out_of_scope_present")
    return signals


def _paragraphs_from_lines(lines: list[str]) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if _HEADING_RE.match(line):
            continue
        bullet_match = _BULLET_RE.match(line)
        normalized = _normalize_inline_text(
            bullet_match.group(1) if bullet_match else line
        )
        if normalized:
            current.append(normalized)
            continue
        if current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def _looks_like_module(candidate: str) -> bool:
    if not candidate:
        return False
    if len(candidate) < 2 or len(candidate) > 80:
        return False
    if candidate.lower() in {"module", "service", "api", "screen", "page"}:
        return False
    return bool(re.search(r"[A-Za-z]", candidate))


def _normalize_inline_text(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -\t")


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _dedupe(values: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_inline_text(value)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped
