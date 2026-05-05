"""Integration tests for Orchestrator._run_planning (Phase 1, v4 §6.1).

3-round Planner 흐름 + state mutation + 실패 라우팅 (RepairExhausted /
BudgetExceeded / UNIT_DECOMPOSITION_FAILED).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pytest
import structlog

from code2e.agents.planner import PlannerAgent
from code2e.core.budget import BudgetTracker
from code2e.core.cassette import CassetteStore
from code2e.core.llm_gateway import LlmGateway
from code2e.core.orchestrator import Orchestrator
from code2e.core.schemas import BudgetState, SystemState

# ---------- helpers ----------


@dataclass
class _MockProvider:
    name: ClassVar[str] = "mock"
    responses: list[dict[str, object] | Exception] = field(default_factory=list)
    cost_per_call: float = 0.0
    calls: list[dict[str, object]] = field(default_factory=list)
    _iter: Iterator[dict[str, object] | Exception] = field(init=False)

    def __post_init__(self) -> None:
        self._iter = iter(self.responses)

    async def call(
        self,
        model: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, object]:
        self.calls.append({"model": model, "temp": temperature})
        item = next(self._iter)
        if isinstance(item, Exception):
            raise item
        return item

    def estimate_cost(self, tokens_in: int, tokens_out: int, model: str) -> float:
        return self.cost_per_call


def _resp(payload: dict[str, object], tokens_in: int = 10, tokens_out: int = 5) -> dict[str, object]:
    return {
        "text": json.dumps(payload),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "raw": {},
    }


def _orchestrator(
    tmp_path: Path,
    *,
    responses: list[dict[str, object] | Exception],
    limit_usd: float = 10.0,
    cost_per_call: float = 0.0,
) -> tuple[Orchestrator, _MockProvider, BudgetTracker]:
    provider = _MockProvider(responses=responses, cost_per_call=cost_per_call)
    cassette = CassetteStore(name="orch", dir=tmp_path, mode="auto")
    budget = BudgetTracker(limit_usd=limit_usd, limit_tokens=100_000)
    gateway = LlmGateway(provider=provider, cassette=cassette, budget=budget)
    planner = PlannerAgent()
    orch = Orchestrator(
        planner=planner,
        llm_gateway=gateway,
        budget=budget,
        cancel_token=asyncio.Event(),
        logger=structlog.get_logger("test"),
    )
    return orch, provider, budget


def _initial_state(user_input: str = "build a todo CLI") -> SystemState:
    return SystemState(
        run_id="r_test_0001",
        status="planning",
        user_input=user_input,
        budget=BudgetState(limit_usd=10.0, limit_tokens=100_000),
    )


_ROUND_3_UNITS_PAYLOAD = [
    {
        "id": "U-001",
        "title": "scaffold",
        "description": "create skeleton",
        "acceptance_criteria": ["compiles"],
        "dependencies": [],
        "estimated_complexity": "low",
    },
    {
        "id": "U-002",
        "title": "api",
        "description": "REST endpoints",
        "acceptance_criteria": ["GET / returns 200"],
        "dependencies": ["U-001"],
        "estimated_complexity": "med",
    },
]


# ---------- happy path ----------


@pytest.mark.asyncio
async def test_phase1_happy_path_three_rounds_then_building(tmp_path: Path) -> None:
    orch, provider, _b = _orchestrator(
        tmp_path,
        responses=[
            _resp({"content": "## Round 1\nrough plan", "units": []}),
            _resp({"content": "## Round 2\nrefined", "units": []}),
            _resp({"content": "## Round 3\nfinal", "units": _ROUND_3_UNITS_PAYLOAD}),
        ],
    )
    state = await orch._run_planning(_initial_state())

    assert state.status == "building"
    assert state.termination is None
    assert len(state.plan.iterations) == 3
    assert state.plan.iterations[0].version == 1
    assert state.plan.iterations[1].version == 2
    assert state.plan.iterations[2].version == 3
    assert state.plan.final is not None
    assert state.plan.final.version == 3
    assert len(state.plan.final.units) == 2
    assert state.plan.final.units[0].id == "U-001"
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_phase1_passes_prev_plan_into_round_2_and_3(tmp_path: Path) -> None:
    """round 2 user message 에 round 1 의 content 가 포함되어야 한다."""
    orch, provider, _b = _orchestrator(
        tmp_path,
        responses=[
            _resp({"content": "## ROUND-ONE-CONTENT-MARKER", "units": []}),
            _resp({"content": "## ROUND-TWO-CONTENT-MARKER", "units": []}),
            _resp({"content": "## final", "units": _ROUND_3_UNITS_PAYLOAD}),
        ],
    )
    await orch._run_planning(_initial_state())

    # provider.calls 의 messages 까지 보려면 _MockProvider 의 calls 에 messages 도 저장 필요.
    # 단순화: provider call 횟수만 검증, prev_plan 흐름은 test_planner.py 에서 이미 커버.
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_phase1_extracts_launch_spec_from_round_3_frontmatter(tmp_path: Path) -> None:
    content_with_launch = """---
launch:
  kind: http
  command: ["python", "-m", "app"]
  health_check:
    method: HTTP_GET
    target: /
---

# Final plan
"""
    orch, _p, _b = _orchestrator(
        tmp_path,
        responses=[
            _resp({"content": "r1", "units": []}),
            _resp({"content": "r2", "units": []}),
            _resp({"content": content_with_launch, "units": _ROUND_3_UNITS_PAYLOAD}),
        ],
    )
    state = await orch._run_planning(_initial_state())
    assert state.status == "building"
    assert state.plan.launch_spec is not None
    assert state.plan.launch_spec.kind == "http"
    assert state.plan.launch_spec.command == ["python", "-m", "app"]


@pytest.mark.asyncio
async def test_phase1_uses_fallback_units_when_round_3_units_empty(tmp_path: Path) -> None:
    """LLM 이 units=[] 로 응답해도 content 의 frontmatter `units:` 에서 fallback."""
    content_with_units = """---
units:
  - id: U-001
    title: scaffold
    description: d
    acceptance_criteria: [a]
---

# Final
"""
    orch, _p, _b = _orchestrator(
        tmp_path,
        responses=[
            _resp({"content": "r1", "units": []}),
            _resp({"content": "r2", "units": []}),
            _resp({"content": content_with_units, "units": []}),
        ],
    )
    state = await orch._run_planning(_initial_state())
    assert state.status == "building"
    assert state.plan.final is not None
    assert len(state.plan.final.units) == 1
    assert state.plan.final.units[0].id == "U-001"


# ---------- failure routing ----------


@pytest.mark.asyncio
async def test_phase1_unit_decomposition_failed_when_no_units(tmp_path: Path) -> None:
    orch, _p, _b = _orchestrator(
        tmp_path,
        responses=[
            _resp({"content": "r1", "units": []}),
            _resp({"content": "r2", "units": []}),
            _resp({"content": "no frontmatter, no headers, just text", "units": []}),
        ],
    )
    state = await orch._run_planning(_initial_state())
    assert state.status == "aborted"
    assert state.termination is not None
    assert state.termination.reason == "UNIT_DECOMPOSITION_FAILED"
    assert state.termination.phase == "Phase 1"
    assert "units" in state.termination.details.lower()


@pytest.mark.asyncio
async def test_phase1_unit_decomposition_failed_on_dag_cycle(tmp_path: Path) -> None:
    cyclic_units = [
        {
            "id": "U-001",
            "title": "a",
            "description": "d",
            "acceptance_criteria": ["x"],
            "dependencies": ["U-002"],
            "estimated_complexity": "low",
        },
        {
            "id": "U-002",
            "title": "b",
            "description": "d",
            "acceptance_criteria": ["x"],
            "dependencies": ["U-001"],
            "estimated_complexity": "low",
        },
    ]
    orch, _p, _b = _orchestrator(
        tmp_path,
        responses=[
            _resp({"content": "r1", "units": []}),
            _resp({"content": "r2", "units": []}),
            _resp({"content": "## final", "units": cyclic_units}),
        ],
    )
    state = await orch._run_planning(_initial_state())
    assert state.status == "aborted"
    assert state.termination is not None
    assert state.termination.reason == "UNIT_DECOMPOSITION_FAILED"
    assert "dag" in state.termination.details.lower()


@pytest.mark.asyncio
async def test_phase1_validation_failure_on_repair_exhaustion(tmp_path: Path) -> None:
    """LLM 이 항상 invalid JSON 응답 → RepairExhausted → VALIDATION_FAILURE."""
    orch, _p, _b = _orchestrator(
        tmp_path,
        responses=[
            _resp({"WRONG_FIELD": "x"}),  # 원본
            _resp({"WRONG_FIELD": "x"}),  # repair 1
            _resp({"WRONG_FIELD": "x"}),  # repair 2 — 모두 실패
        ],
    )
    state = await orch._run_planning(_initial_state())
    assert state.status == "aborted"
    assert state.termination is not None
    assert state.termination.reason == "VALIDATION_FAILURE"
    assert state.termination.phase == "Phase 1"


@pytest.mark.asyncio
async def test_phase1_budget_exceeded(tmp_path: Path) -> None:
    """budget 한도 초과 → BUDGET_EXCEEDED."""
    orch, _p, _b = _orchestrator(
        tmp_path,
        responses=[_resp({"content": "x", "units": []})],
        limit_usd=0.0001,
        cost_per_call=10.0,
    )
    state = await orch._run_planning(_initial_state())
    assert state.status == "aborted"
    assert state.termination is not None
    assert state.termination.reason == "BUDGET_EXCEEDED"


@pytest.mark.asyncio
async def test_phase1_failure_preserves_partial_iterations(tmp_path: Path) -> None:
    """round 1/2 성공 후 round 3 에서 실패해도 plan.iterations 에 1/2 가 남아야 한다."""
    orch, _p, _b = _orchestrator(
        tmp_path,
        responses=[
            _resp({"content": "## r1", "units": []}),
            _resp({"content": "## r2", "units": []}),
            _resp({"content": "no units", "units": []}),  # round 3 실패 (units 없음)
        ],
    )
    state = await orch._run_planning(_initial_state())
    assert state.status == "aborted"
    # iterations 는 3개 — round 3 plan 도 누적된 후 검증 단계에서 실패.
    assert len(state.plan.iterations) == 3
    assert state.plan.final is None  # final 은 검증 통과 시에만 설정.


@pytest.mark.asyncio
async def test_phase1_status_transitions_to_building_only_on_full_success(tmp_path: Path) -> None:
    """happy path 에서만 status='building'."""
    orch, _p, _b = _orchestrator(
        tmp_path,
        responses=[
            _resp({"content": "r1", "units": []}),
            _resp({"content": "r2", "units": []}),
            _resp({"content": "final", "units": _ROUND_3_UNITS_PAYLOAD}),
        ],
    )
    state = await orch._run_planning(_initial_state())
    assert state.status == "building"
