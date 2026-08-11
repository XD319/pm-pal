"""Risk-tool tests: catalog search hit / miss / disabled switch.

Covers three behaviors of the risk agent's tool integration:
1. Tool enabled + catalog hits → evidence attached to risks
2. Tool enabled + no catalog hits → risks returned without evidence
3. Tool disabled via env switch → tool skipped, degraded trace recorded

All LLM calls are mocked — no API keys required.
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pm_pal.skills.executor import SkillExecutor
from pm_pal.skills.registry import get_skill_spec
from pm_pal.state import ReviewState
from pm_pal.subflows.risk_analysis import run_risk_analysis_subflow
from pm_pal.tools.risk_catalog_search import search_risk_catalog

LLM_CALL_TARGET = "pm_pal.agents.structured_runner.llm_structured_call"
CONFIG_TARGET = "pm_pal.agents.structured_runner.Config"


class _FakeConfig:
    llm_model = "test-model"
    llm_provider = "test-provider"

    def resolve_llm(self):
        return self.llm_provider, self.llm_model


# ═══════════════════════════════════════════════════════════════════════════
# search_risk_catalog: local TF-IDF tool
# ═══════════════════════════════════════════════════════════════════════════


SAMPLE_CATALOG = [
    {
        "id": "RC-001",
        "title": "Single owner bottleneck on backend integration",
        "description": "Too many integration tasks assigned to one backend engineer.",
        "triggers": ["More than 40% tasks owned by one role"],
        "mitigations": ["Rebalance ownership across BE and FE"],
        "tags": ["resource", "backend", "bottleneck"],
    },
    {
        "id": "RC-003",
        "title": "Insufficient schedule buffer",
        "description": "Buffer days below the typical 15% contingency threshold.",
        "triggers": ["buffer_days / total_days < 0.15"],
        "mitigations": ["Increase buffer to 15-20%"],
        "tags": ["buffer", "estimation", "schedule"],
    },
    {
        "id": "RC-004",
        "title": "Optimistic QA effort estimate",
        "description": "Testing work underestimated relative to scope.",
        "triggers": ["QA tasks under 10% of total effort"],
        "mitigations": ["Allocate dedicated regression window"],
        "tags": ["qa", "estimation", "quality"],
    },
]


class TestRiskCatalogSearch:
    """search_risk_catalog with an in-memory catalog fixture."""

    @pytest.fixture(autouse=True)
    def _write_catalog(self, tmp_path):
        catalog_file = tmp_path / "risk_catalog.json"
        catalog_file.write_text(json.dumps(SAMPLE_CATALOG), encoding="utf-8")
        self.catalog_path = str(catalog_file)

    def test_hit_returns_matching_entries(self):
        results = search_risk_catalog(
            "backend bottleneck integration tasks",
            catalog_path=self.catalog_path,
            top_k=3,
        )
        assert len(results) >= 1
        ids = [r["id"] for r in results]
        assert "RC-001" in ids

    def test_hit_includes_score_and_snippet(self):
        results = search_risk_catalog(
            "buffer estimation schedule",
            catalog_path=self.catalog_path,
            top_k=2,
        )
        assert len(results) >= 1
        top = results[0]
        assert "score" in top and top["score"] > 0
        assert "snippet" in top and len(top["snippet"]) > 0

    def test_miss_returns_empty(self):
        results = search_risk_catalog(
            "xyzzy frobnicator quantum",
            catalog_path=self.catalog_path,
            top_k=5,
        )
        assert results == []

    def test_empty_query_returns_empty(self):
        assert search_risk_catalog("", catalog_path=self.catalog_path) == []

    def test_whitespace_query_returns_empty(self):
        assert search_risk_catalog("   ", catalog_path=self.catalog_path) == []

    def test_top_k_limits_results(self):
        results = search_risk_catalog(
            "estimation buffer QA backend",
            catalog_path=self.catalog_path,
            top_k=1,
        )
        assert len(results) <= 1

    def test_matched_terms_present(self):
        results = search_risk_catalog(
            "backend bottleneck",
            catalog_path=self.catalog_path,
            top_k=3,
        )
        hit = next((r for r in results if r["id"] == "RC-001"), None)
        assert hit is not None
        assert "backend" in hit["matched_terms"] or "bottleneck" in hit["matched_terms"]


@pytest.fixture(autouse=True)
def _clear_skill_cache():
    SkillExecutor.clear_cache()
    yield
    SkillExecutor.clear_cache()


class TestSkillExecutorCache:
    """SkillExecutor cache behavior within one process lifecycle."""

    @pytest.mark.asyncio
    async def test_same_executor_second_call_hits_cache(self):
        executor = SkillExecutor(cache_enabled=True)
        trace_first: dict = {}
        trace_second: dict = {}
        spec = get_skill_spec("risk_catalog.search")
        mock_hits = [
            {
                "id": "RC-003",
                "title": "Insufficient schedule buffer",
                "score": 5.0,
                "snippet": "Buffer below 15%",
                "matched_terms": ["buffer"],
            }
        ]

        with patch(
            "pm_pal.skills.risk_catalog.search_risk_catalog",
            return_value=mock_hits,
        ) as mock_search:
            first = await executor.execute(
                spec,
                {"query": "buffer estimation schedule", "top_k": 5},
                trace=trace_first,
            )
            second = await executor.execute(
                spec,
                {"query": "buffer estimation schedule", "top_k": 5},
                trace=trace_second,
            )

        assert first.hits[0].id == "RC-003"
        assert second.hits[0].id == "RC-003"
        assert trace_first["risk_catalog.search"]["cache_hit"] is False
        assert trace_second["risk_catalog.search"]["cache_hit"] is True
        assert trace_second["risk_catalog.search"]["ttl_sec"] == 300
        mock_search.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# Risk agent: tool enabled + hits
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def base_state(sample_review_state: ReviewState) -> ReviewState:
    return {
        "tasks": sample_review_state["tasks"],
        "milestones": sample_review_state["milestones"],
        "dependencies": sample_review_state["dependencies"],
        "estimation": {"total_days": 10, "buffer_days": 1},
        "trace": {},
        "run_dir": "",
    }


MOCK_LLM_RISK_OUTPUT = {
    "risks": [
        {
            "id": "R-1",
            "description": "Tight buffer for backend tasks",
            "impact": "high",
            "mitigation": "Add 2 extra buffer days",
            "buffer_days": 2,
            "evidence_ids": [],
            "evidence_snippets": [],
        }
    ]
}


class TestRiskAgentToolEnabled:
    """Risk agent with tool enabled and catalog returning hits."""

    @pytest.fixture(autouse=True)
    def _write_catalog(self, tmp_path):
        catalog_file = tmp_path / "risk_catalog.json"
        catalog_file.write_text(json.dumps(SAMPLE_CATALOG), encoding="utf-8")
        self.catalog_path = str(catalog_file)

    @pytest.mark.asyncio
    async def test_tool_hit_attaches_evidence(self, base_state: ReviewState):
        catalog_hits = [
            {
                "id": "RC-003",
                "title": "Insufficient schedule buffer",
                "score": 5.0,
                "snippet": "Buffer below 15%",
                "matched_terms": ["buffer"],
            },
        ]
        with (
            patch(
                LLM_CALL_TARGET,
                new_callable=AsyncMock,
                return_value=MOCK_LLM_RISK_OUTPUT,
            ),
            patch(CONFIG_TARGET, _FakeConfig),
            patch(
                "pm_pal.skills.risk_catalog.search_risk_catalog",
                return_value=catalog_hits,
            ),
            patch.dict(
                os.environ,
                {
                    "RISK_AGENT_ENABLE_CATALOG_TOOL": "true",
                    "SKILLS_CACHE_ENABLED": "true",
                },
            ),
        ):
            from pm_pal.agents import risk_agent

            result = await risk_agent.run(base_state)

        assert len(result["risks"]) == 1
        risk = result["risks"][0]
        assert "RC-003" in risk["evidence_ids"]
        assert result["trace"]["risk"]["status"] == "ok"
        assert result["trace"]["risk"]["subflow_id"] == "risk_analysis.v1"
        assert (
            result["trace"]["risk_analysis.evidence"]["node_path"]
            == "risk_analysis.evidence"
        )
        assert result["trace"]["risk_catalog.search"]["cache_hit"] is False
        assert result["trace"]["risk_catalog.search"]["ttl_sec"] == 300

    @pytest.mark.asyncio
    async def test_tool_miss_returns_risks_without_evidence(
        self, base_state: ReviewState
    ):
        with (
            patch(
                LLM_CALL_TARGET,
                new_callable=AsyncMock,
                return_value=MOCK_LLM_RISK_OUTPUT,
            ),
            patch(CONFIG_TARGET, _FakeConfig),
            patch(
                "pm_pal.skills.risk_catalog.search_risk_catalog",
                return_value=[],
            ),
            patch.dict(
                os.environ,
                {
                    "RISK_AGENT_ENABLE_CATALOG_TOOL": "true",
                    "SKILLS_CACHE_ENABLED": "true",
                },
            ),
        ):
            from pm_pal.agents import risk_agent

            result = await risk_agent.run(base_state)

        assert len(result["risks"]) == 1
        risk = result["risks"][0]
        assert risk["evidence_ids"] == []
        assert result["trace"]["risk"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_tool_second_run_hits_cache(self, base_state: ReviewState):
        catalog_hits = [
            {
                "id": "RC-003",
                "title": "Insufficient schedule buffer",
                "score": 5.0,
                "snippet": "Buffer below 15%",
                "matched_terms": ["buffer"],
            },
        ]
        mock_search = MagicMock(return_value=catalog_hits)
        with (
            patch(
                LLM_CALL_TARGET,
                new_callable=AsyncMock,
                return_value=MOCK_LLM_RISK_OUTPUT,
            ),
            patch(CONFIG_TARGET, _FakeConfig),
            patch(
                "pm_pal.skills.risk_catalog.search_risk_catalog",
                mock_search,
            ),
            patch.dict(
                os.environ,
                {
                    "RISK_AGENT_ENABLE_CATALOG_TOOL": "true",
                    "SKILLS_CACHE_ENABLED": "true",
                },
            ),
        ):
            from pm_pal.agents import risk_agent

            first = await risk_agent.run(base_state)
            second = await risk_agent.run(base_state)

        assert first["trace"]["risk_catalog.search"]["cache_hit"] is False
        assert second["trace"]["risk_catalog.search"]["cache_hit"] is True
        assert second["trace"]["risk_catalog.search"]["cache_key_hash"]
        assert second["trace"]["risk_catalog.search"]["ttl_sec"] == 300
        mock_search.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# Risk agent: tool disabled via env switch
# ═══════════════════════════════════════════════════════════════════════════


class TestRiskAgentToolDisabled:
    """Risk agent with RISK_AGENT_ENABLE_CATALOG_TOOL=false."""

    @pytest.mark.asyncio
    async def test_tool_disabled_skips_catalog(self, base_state: ReviewState):
        mock_search = MagicMock(return_value=[])
        with (
            patch(
                LLM_CALL_TARGET,
                new_callable=AsyncMock,
                return_value=MOCK_LLM_RISK_OUTPUT,
            ),
            patch(CONFIG_TARGET, _FakeConfig),
            patch(
                "pm_pal.skills.risk_catalog.search_risk_catalog",
                mock_search,
            ),
            patch.dict(
                os.environ,
                {
                    "RISK_AGENT_ENABLE_CATALOG_TOOL": "false",
                    "SKILLS_CACHE_ENABLED": "true",
                },
            ),
        ):
            from pm_pal.agents import risk_agent

            result = await risk_agent.run(base_state)

        mock_search.assert_not_called()
        assert len(result["risks"]) == 1
        trace_risk = result["trace"]["risk"]
        assert trace_risk["status"] == "ok"
        assert trace_risk.get("risk_catalog_tool_status") == "degraded_disabled"

    @pytest.mark.asyncio
    async def test_tool_disabled_with_env_zero(self, base_state: ReviewState):
        mock_search = MagicMock(return_value=[])
        with (
            patch(
                LLM_CALL_TARGET,
                new_callable=AsyncMock,
                return_value=MOCK_LLM_RISK_OUTPUT,
            ),
            patch(CONFIG_TARGET, _FakeConfig),
            patch(
                "pm_pal.skills.risk_catalog.search_risk_catalog",
                mock_search,
            ),
            patch.dict(
                os.environ,
                {"RISK_AGENT_ENABLE_CATALOG_TOOL": "0", "SKILLS_CACHE_ENABLED": "true"},
            ),
        ):
            from pm_pal.agents import risk_agent

            result = await risk_agent.run(base_state)

        mock_search.assert_not_called()
        assert (
            result["trace"]["risk"].get("risk_catalog_tool_status")
            == "degraded_disabled"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Risk agent: tool enabled but raises exception → degraded
# ═══════════════════════════════════════════════════════════════════════════


class TestRiskAgentToolError:
    """Risk agent gracefully degrades when catalog tool throws."""

    @pytest.mark.asyncio
    async def test_tool_error_degrades_gracefully(self, base_state: ReviewState):
        with (
            patch(
                LLM_CALL_TARGET,
                new_callable=AsyncMock,
                return_value=MOCK_LLM_RISK_OUTPUT,
            ),
            patch(CONFIG_TARGET, _FakeConfig),
            patch(
                "pm_pal.skills.risk_catalog.search_risk_catalog",
                side_effect=RuntimeError("catalog file missing"),
            ),
            patch.dict(
                os.environ,
                {
                    "RISK_AGENT_ENABLE_CATALOG_TOOL": "true",
                    "SKILLS_CACHE_ENABLED": "true",
                },
            ),
        ):
            from pm_pal.agents import risk_agent

            result = await risk_agent.run(base_state)

        assert len(result["risks"]) == 1
        trace_risk = result["trace"]["risk"]
        assert trace_risk["status"] == "ok"
        assert trace_risk.get("risk_catalog_tool_status") == "degraded_error"
        assert "catalog file missing" in trace_risk.get("risk_catalog_tool_error", "")


# ═══════════════════════════════════════════════════════════════════════════
# Risk agent: empty tasks → early return
# ═══════════════════════════════════════════════════════════════════════════


class TestRiskAgentEmptyTasks:
    """Risk agent returns empty risks when tasks list is empty."""

    @pytest.mark.asyncio
    async def test_empty_tasks_returns_empty(self):
        state: ReviewState = {
            "tasks": [],
            "milestones": [],
            "dependencies": [],
            "estimation": {},
            "trace": {},
            "run_dir": "",
        }
        from pm_pal.agents import risk_agent

        result = await risk_agent.run(state)
        assert result["risks"] == []
        assert result["trace"]["risk"]["status"] == "error"
        assert "empty" in result["trace"]["risk"]["error_message"]


class TestRiskAnalysisSubflowContract:
    @pytest.mark.asyncio
    async def test_subflow_returns_contract_fields(self, base_state: ReviewState):
        with (
            patch(
                LLM_CALL_TARGET,
                new_callable=AsyncMock,
                return_value=MOCK_LLM_RISK_OUTPUT,
            ),
            patch(CONFIG_TARGET, _FakeConfig),
            patch(
                "pm_pal.skills.risk_catalog.search_risk_catalog",
                return_value=[],
            ),
            patch.dict(
                os.environ,
                {
                    "RISK_AGENT_ENABLE_CATALOG_TOOL": "true",
                    "SKILLS_CACHE_ENABLED": "true",
                },
            ),
        ):
            result = await run_risk_analysis_subflow(
                {
                    "structured_requirements": [
                        {
                            "id": "REQ-001",
                            "description": "System supports review planning",
                            "acceptance_criteria": ["Planner output is available"],
                        }
                    ],
                    "context": {
                        "tasks": base_state["tasks"],
                        "milestones": base_state["milestones"],
                        "dependencies": base_state["dependencies"],
                        "estimation": base_state["estimation"],
                        "trace": {},
                    },
                }
            )

        output = result["output"]
        assert list(output) == ["risks", "evidence_summary", "tool_actions"]
        assert isinstance(output["risks"], list)
        assert isinstance(output["evidence_summary"], dict)
        assert isinstance(output["tool_actions"], list)
        assert (
            result["trace"]["risk_analysis.generate"]["subflow_id"]
            == "risk_analysis.v1"
        )
