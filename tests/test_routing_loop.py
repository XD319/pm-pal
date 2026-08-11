"""Routing-loop tests for the requirement-review workflow.

Validates that:
- high_risk_ratio > 0.4 triggers a clarification loop
- the loop fires at most _MAX_REVISION_ROUNDS (2) times
- low high_risk_ratio goes straight to reporter
- the route_decider node records routing events in trace

All LLM calls are mocked — no API keys required.
"""

from __future__ import annotations

from pm_pal.state import ReviewState
from pm_pal.workflow import (
    _HIGH_RISK_THRESHOLD,
    _MAX_REVISION_ROUNDS,
    _route_decider_node,
    _route_next,
)

# ═══════════════════════════════════════════════════════════════════════════
# _route_next: pure routing function
# ═══════════════════════════════════════════════════════════════════════════


class TestRouteNext:
    """_route_next returns 'clarify' or 'reporter' based on routing_reason."""

    def test_loop_prefix_routes_to_clarify(self):
        state: ReviewState = {"routing_reason": "loop: high_risk_ratio=0.500"}
        assert _route_next(state) == "clarify"

    def test_finish_prefix_routes_to_reporter(self):
        state: ReviewState = {"routing_reason": "finish: high_risk_ratio=0.200"}
        assert _route_next(state) == "reporter"

    def test_empty_reason_routes_to_reporter(self):
        state: ReviewState = {"routing_reason": ""}
        assert _route_next(state) == "reporter"

    def test_none_reason_routes_to_reporter(self):
        state: ReviewState = {}
        assert _route_next(state) == "reporter"


# ═══════════════════════════════════════════════════════════════════════════
# _route_decider_node: stateful decision node
# ═══════════════════════════════════════════════════════════════════════════


class TestRouteDeciderNode:
    """_route_decider_node decides loop vs finish and records trace."""

    def test_high_ratio_round0_triggers_loop(self, sample_review_state: ReviewState):
        state: ReviewState = {
            **sample_review_state,
            "high_risk_ratio": 0.6,
            "revision_round": 0,
            "trace": {},
        }
        update = _route_decider_node(state)
        assert update["routing_reason"].startswith("loop:")
        assert update["revision_round"] == 1
        assert update["trace"]["router"]["decision"] == "clarify"

    def test_high_ratio_round1_triggers_second_loop(
        self, sample_review_state: ReviewState
    ):
        state: ReviewState = {
            **sample_review_state,
            "high_risk_ratio": 0.5,
            "revision_round": 1,
            "trace": {},
        }
        update = _route_decider_node(state)
        assert update["routing_reason"].startswith("loop:")
        assert update["revision_round"] == 2
        assert update["trace"]["router"]["decision"] == "clarify"

    def test_high_ratio_at_max_rounds_finishes(self):
        """Even with high risk, reaching _MAX_REVISION_ROUNDS stops the loop."""
        state: ReviewState = {
            "high_risk_ratio": 0.9,
            "revision_round": _MAX_REVISION_ROUNDS,
            "trace": {},
        }
        update = _route_decider_node(state)
        assert update["routing_reason"].startswith("finish:")
        assert update["revision_round"] == _MAX_REVISION_ROUNDS
        assert update["trace"]["router"]["decision"] == "reporter"

    def test_low_ratio_round0_finishes(self):
        state: ReviewState = {
            "high_risk_ratio": 0.2,
            "revision_round": 0,
            "trace": {},
        }
        update = _route_decider_node(state)
        assert update["routing_reason"].startswith("finish:")
        assert update["revision_round"] == 0
        assert update["trace"]["router"]["decision"] == "reporter"

    def test_exact_threshold_finishes(self):
        """Ratio == threshold (0.4) does NOT trigger loop (> required)."""
        state: ReviewState = {
            "high_risk_ratio": _HIGH_RISK_THRESHOLD,
            "revision_round": 0,
            "trace": {},
        }
        update = _route_decider_node(state)
        assert update["routing_reason"].startswith("finish:")
        assert update["trace"]["router"]["decision"] == "reporter"

    def test_routing_rounds_accumulate(self, sample_review_state: ReviewState):
        """Each call appends to trace.routing_rounds."""
        state: ReviewState = {
            **sample_review_state,
            "high_risk_ratio": 0.6,
            "revision_round": 0,
            "trace": {"routing_rounds": [{"round": 0, "decision": "previous"}]},
        }
        update = _route_decider_node(state)
        rounds = update["trace"]["routing_rounds"]
        assert len(rounds) == 2
        assert rounds[1]["decision"] == "clarify"


# ═══════════════════════════════════════════════════════════════════════════
# End-to-end loop simulation with mocked agents
# ═══════════════════════════════════════════════════════════════════════════


def _mock_parser_output():
    return {
        "parsed_items": [
            {"id": "REQ-001", "description": "User login", "acceptance_criteria": []},
            {"id": "REQ-002", "description": "Dashboard", "acceptance_criteria": []},
        ],
        "trace": {},
    }


def _mock_planner_output():
    return {
        "tasks": [
            {
                "id": "T-1",
                "title": "Design",
                "owner": "BE",
                "requirement_ids": ["REQ-001"],
                "depends_on": [],
                "estimate_days": 3,
            }
        ],
        "milestones": [
            {"id": "M-1", "title": "MVP", "includes": ["T-1"], "target_days": 5}
        ],
        "dependencies": [],
        "estimation": {"total_days": 10, "buffer_days": 2},
        "trace": {},
    }


def _mock_risk_output():
    return {
        "risks": [
            {
                "id": "R-1",
                "description": "Tight buffer",
                "impact": "high",
                "mitigation": "Add buffer",
                "buffer_days": 2,
            }
        ],
        "trace": {},
    }


def _mock_reviewer_high_risk():
    """Reviewer output where all items are flagged → high_risk_ratio = 1.0."""
    return {
        "review_results": [
            {
                "id": "REQ-001",
                "is_clear": False,
                "is_testable": False,
                "is_ambiguous": True,
                "issues": ["Vague"],
                "suggestions": "Fix",
            },
            {
                "id": "REQ-002",
                "is_clear": False,
                "is_testable": False,
                "is_ambiguous": True,
                "issues": ["Vague"],
                "suggestions": "Fix",
            },
        ],
        "plan_review": {"coverage": "OK", "milestones": "OK", "estimation": "OK"},
        "trace": {},
    }


def _mock_reviewer_low_risk():
    """Reviewer output where all items pass → high_risk_ratio = 0.0."""
    return {
        "review_results": [
            {
                "id": "REQ-001",
                "is_clear": True,
                "is_testable": True,
                "is_ambiguous": False,
                "issues": [],
                "suggestions": "",
            },
            {
                "id": "REQ-002",
                "is_clear": True,
                "is_testable": True,
                "is_ambiguous": False,
                "issues": [],
                "suggestions": "",
            },
        ],
        "plan_review": {"coverage": "OK", "milestones": "OK", "estimation": "OK"},
        "trace": {},
    }


def _mock_reporter_output():
    return {"final_report": "# Report\nDone.", "metrics": {}, "trace": {}}


class TestFullLoopSimulation:
    """Simulate the loop by chaining route_decider decisions."""

    def test_two_loops_then_forced_finish(self):
        """high_risk_ratio stays high → loops twice → forced finish at round 2."""
        state: ReviewState = {
            "high_risk_ratio": 0.8,
            "revision_round": 0,
            "trace": {},
        }

        decisions = []
        for _ in range(_MAX_REVISION_ROUNDS + 1):
            update = _route_decider_node(state)
            decision = update["trace"]["router"]["decision"]
            decisions.append(decision)

            state = {**state, **update}
            if decision == "reporter":
                break

        assert decisions == ["clarify", "clarify", "reporter"]
        assert state["revision_round"] == _MAX_REVISION_ROUNDS

    def test_immediate_finish_when_low_risk(self):
        """low risk ratio → no loop at all."""
        state: ReviewState = {
            "high_risk_ratio": 0.1,
            "revision_round": 0,
            "trace": {},
        }
        update = _route_decider_node(state)
        assert update["trace"]["router"]["decision"] == "reporter"
        assert update["revision_round"] == 0

    def test_single_loop_then_quality_improves(self):
        """First round triggers loop, second round quality improves → finish."""
        state: ReviewState = {
            "high_risk_ratio": 0.6,
            "revision_round": 0,
            "trace": {},
        }
        update = _route_decider_node(state)
        assert update["trace"]["router"]["decision"] == "clarify"
        state = {**state, **update}

        state["high_risk_ratio"] = 0.2
        update2 = _route_decider_node(state)
        assert update2["trace"]["router"]["decision"] == "reporter"
        assert update2["revision_round"] == 1
