"""Integration tests for Orchestrator._run_building_and_testgen (Phase 2, v4 §6.1, §3.8).

TaskGroup 분기 (build branch A + testgen branch B) + max 5 iter feedback loop +
stagnation/Q11/path-traversal 종료 + state mutation.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
import structlog

from code2e.agents.advisor import AdvisorAgent
from code2e.agents.evaluator import EvaluatorTestgenAgent
from code2e.agents.executor import ExecutorAgent
from code2e.agents.planner import PlannerAgent
from code2e.core.budget import BudgetTracker
from code2e.core.cassette import CassetteStore
from code2e.core.llm_gateway import LlmGateway
from code2e.core.orchestrator import PHASE2_MAX_ITERATIONS, Orchestrator
from code2e.core.schemas import (
    BudgetState,
    Plan,
    PlanMeta,
    PlanState,
    PlanUnit,
    SystemState,
)

# ---------- helpers ----------


@dataclass
class _MockProvider:
    """planner / executor / advisor / evaluator_testgen 의 프롬프트를 식별해서 다른 응답 반환.

    Phase 2 통합 테스트는 LLM 호출이 4개 에이전트 섞여서 발생하므로, 시스템 프롬프트의
    "Planner" / "Executor" / "Advisor" / "Evaluator" 키워드로 라우팅.
    """

    name: ClassVar[str] = "mock"
    queues: dict[str, list[dict[str, object] | Exception]] = field(default_factory=dict)
    calls: list[dict[str, object]] = field(default_factory=list)

    async def call(
        self,
        model: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, object]:
        agent = _identify_agent(system_prompt)
        self.calls.append({"agent": agent, "messages": list(messages), "temp": temperature})
        queue = self.queues.get(agent)
        if not queue:
            raise RuntimeError(f"no mock response queued for agent={agent}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def estimate_cost(self, tokens_in: int, tokens_out: int, model: str) -> float:
        return 0.0


_AGENT_RE = re.compile(r"당신은 Code2E 의 (Planner|Executor|Advisor|Evaluator)")


def _identify_agent(system_prompt: str) -> str:
    """프롬프트 안 다른 에이전트 이름 언급에 의한 오인식을 막기 위해 정규식 사용."""
    m = _AGENT_RE.search(system_prompt)
    return m.group(1).lower() if m else "unknown"


def _resp(payload: dict[str, object]) -> dict[str, object]:
    return {"text": json.dumps(payload), "tokens_in": 10, "tokens_out": 5, "raw": {}}


def _orchestrator(
    tmp_path: Path,
    *,
    queues: dict[str, list[dict[str, object] | Exception]],
) -> tuple[Orchestrator, _MockProvider]:
    provider = _MockProvider(queues={k: list(v) for k, v in queues.items()})
    cassette = CassetteStore(name="orch-p2", dir=tmp_path / "cassettes", mode="off")
    budget = BudgetTracker(limit_usd=10.0, limit_tokens=100_000)
    gateway = LlmGateway(provider=provider, cassette=cassette, budget=budget)
    orch = Orchestrator(
        planner=PlannerAgent(),
        executor=ExecutorAgent(),
        advisor=AdvisorAgent(),
        evaluator_testgen=EvaluatorTestgenAgent(),
        llm_gateway=gateway,
        budget=budget,
        workspace_root=tmp_path / "workspaces",
        cancel_token=asyncio.Event(),
        logger=structlog.get_logger("test"),
    )
    return orch, provider


def _state_with_final_plan(units: list[PlanUnit]) -> SystemState:
    final = Plan(
        version=3,
        content="## final",
        units=units,
        meta=PlanMeta(
            created_at=datetime(2026, 5, 5, tzinfo=UTC), tokens_in=0, tokens_out=0
        ),
    )
    return SystemState(
        run_id="r_test_p2_0001",
        status="building",
        user_input="task",
        plan=PlanState(iterations=[final], final=final),
        budget=BudgetState(limit_usd=10.0, limit_tokens=100_000),
    )


def _unit(uid: str, deps: list[str] | None = None) -> PlanUnit:
    return PlanUnit(
        id=uid,
        title=f"unit {uid}",
        description="d",
        acceptance_criteria=["x"],
        dependencies=deps or [],
    )


_GOOD_CODE_CHANGE = {
    "files": [{"path": "main.py", "op": "create", "content": "print('hi')"}],
    "rationale": "scaffold",
}
_APPROVE_FEEDBACK = {"decision": "approve", "comments": []}
_REVISE_FEEDBACK = {
    "decision": "revise",
    "comments": [{"message": "fix X"}],
}
_TESTCASES_PAYLOAD = {
    "cases": [
        {
            "id": "T-001",
            "scenario": "smoke",
            "given": "running",
            "when": "GET /",
            "then": "200",
            "runner_script": "await page.goto(BASE_URL)",
            "plan_unit_refs": ["U-001"],
        }
    ]
}


# ---------- happy paths ----------


@pytest.mark.asyncio
async def test_phase2_single_unit_approve_first_iter(tmp_path: Path) -> None:
    orch, _p = _orchestrator(
        tmp_path,
        queues={
            "executor": [_resp(_GOOD_CODE_CHANGE)],
            "advisor": [_resp(_APPROVE_FEEDBACK)],
            "evaluator": [_resp(_TESTCASES_PAYLOAD)],
        },
    )
    state = await orch._run_building_and_testgen(_state_with_final_plan([_unit("U-001")]))

    assert state.status == "launching"
    assert state.termination is None
    assert len(state.build.units) == 1
    assert state.build.units[0].status == "approved"
    assert state.build.units[0].iteration == 1
    assert state.test.suite is not None
    assert len(state.test.suite) == 1
    assert state.test.suite[0].id == "T-001"


@pytest.mark.asyncio
async def test_phase2_revise_then_approve(tmp_path: Path) -> None:
    orch, _p = _orchestrator(
        tmp_path,
        queues={
            "executor": [_resp(_GOOD_CODE_CHANGE), _resp(_GOOD_CODE_CHANGE)],
            "advisor": [_resp(_REVISE_FEEDBACK), _resp(_APPROVE_FEEDBACK)],
            "evaluator": [_resp(_TESTCASES_PAYLOAD)],
        },
    )
    state = await orch._run_building_and_testgen(_state_with_final_plan([_unit("U-001")]))

    assert state.status == "launching"
    assert state.build.units[0].status == "approved"
    assert state.build.units[0].iteration == 2
    assert len(state.build.units[0].feedback_history) == 2


@pytest.mark.asyncio
async def test_phase2_multi_units_topological_order(tmp_path: Path) -> None:
    """U-001 → U-002 의존. U-001 이 먼저 처리되어야."""
    orch, provider = _orchestrator(
        tmp_path,
        queues={
            "executor": [
                _resp(_GOOD_CODE_CHANGE),
                _resp(_GOOD_CODE_CHANGE),
            ],
            "advisor": [
                _resp(_APPROVE_FEEDBACK),
                _resp(_APPROVE_FEEDBACK),
            ],
            "evaluator": [_resp(_TESTCASES_PAYLOAD)],
        },
    )
    units = [_unit("U-002", deps=["U-001"]), _unit("U-001")]  # 입력 순서 뒤집힘.
    state = await orch._run_building_and_testgen(_state_with_final_plan(units))

    assert state.status == "launching"
    # 처리 순서가 위상 정렬 결과 (U-001 → U-002).
    ids = [u.unit_id for u in state.build.units]
    assert ids == ["U-001", "U-002"]


# ---------- termination paths ----------


@pytest.mark.asyncio
async def test_phase2_max_iterations_force_stop(tmp_path: Path) -> None:
    """5회 모두 revise → MAX_ITERATIONS force_stop. phase 는 계속."""
    revise_responses = [
        _resp(
            {
                "decision": "revise",
                # 매번 다른 message → stagnation 회피, max iter 까지 도달.
                "comments": [{"message": f"fix iteration {i}"}],
            }
        )
        for i in range(PHASE2_MAX_ITERATIONS)
    ]
    orch, _p = _orchestrator(
        tmp_path,
        queues={
            "executor": [_resp(_GOOD_CODE_CHANGE)] * PHASE2_MAX_ITERATIONS,
            "advisor": revise_responses,
            "evaluator": [_resp(_TESTCASES_PAYLOAD)],
        },
    )
    state = await orch._run_building_and_testgen(_state_with_final_plan([_unit("U-001")]))

    # MAX_ITERATIONS 는 unit-level force_stop. phase 는 launching 으로 전이 (다른 critical 없음).
    assert state.status == "launching"
    assert state.build.units[0].status == "force_stopped"
    assert state.build.units[0].force_stop_reason == "MAX_ITERATIONS"
    assert state.build.units[0].iteration == PHASE2_MAX_ITERATIONS


@pytest.mark.asyncio
async def test_phase2_stagnation_force_stop(tmp_path: Path) -> None:
    """동일 signature 가 연속 → STAGNATION force_stop."""
    same_revise = _resp(
        {"decision": "revise", "comments": [{"message": "same problem"}]}
    )
    orch, _p = _orchestrator(
        tmp_path,
        queues={
            "executor": [_resp(_GOOD_CODE_CHANGE)] * 5,
            "advisor": [same_revise] * 5,  # 동일 응답 → 동일 signature.
            "evaluator": [_resp(_TESTCASES_PAYLOAD)],
        },
    )
    state = await orch._run_building_and_testgen(_state_with_final_plan([_unit("U-001")]))

    assert state.status == "launching"
    assert state.build.units[0].status == "force_stopped"
    assert state.build.units[0].force_stop_reason == "STAGNATION"
    # 3회 안에 stagnant 검출 (window=2: 2번째 revise 후 검사 시점).
    assert state.build.units[0].iteration <= 3


@pytest.mark.asyncio
async def test_phase2_empty_revise_force_stop_q11(tmp_path: Path) -> None:
    """revise + comments=[] → 즉시 STAGNATION (Q11)."""
    empty_revise = _resp({"decision": "revise", "comments": []})
    orch, _p = _orchestrator(
        tmp_path,
        queues={
            "executor": [_resp(_GOOD_CODE_CHANGE)],
            "advisor": [empty_revise],
            "evaluator": [_resp(_TESTCASES_PAYLOAD)],
        },
    )
    state = await orch._run_building_and_testgen(_state_with_final_plan([_unit("U-001")]))

    assert state.status == "launching"
    assert state.build.units[0].force_stop_reason == "STAGNATION"
    assert state.build.units[0].iteration == 1


@pytest.mark.asyncio
async def test_phase2_security_violation_aborts_phase(tmp_path: Path) -> None:
    """Executor 가 path traversal 경로 출력 → SECURITY_VIOLATION → phase abort."""
    bad_change = {
        "files": [{"path": "/etc/passwd", "op": "create", "content": "x"}],
        "rationale": "evil",
    }
    orch, _p = _orchestrator(
        tmp_path,
        queues={
            "executor": [_resp(bad_change)],
            "advisor": [_resp(_APPROVE_FEEDBACK)],  # 미사용
            "evaluator": [_resp(_TESTCASES_PAYLOAD)],
        },
    )
    state = await orch._run_building_and_testgen(_state_with_final_plan([_unit("U-001")]))

    assert state.status == "aborted"
    assert state.termination is not None
    assert state.termination.reason == "SECURITY_VIOLATION"
    # build 진행 결과는 보존 (unit 1개의 force_stop 기록).
    assert len(state.build.units) == 1
    assert state.build.units[0].force_stop_reason == "SECURITY_VIOLATION"


# ---------- branch B (testgen) + dependency check ----------


@pytest.mark.asyncio
async def test_phase2_aborts_when_dependencies_missing(tmp_path: Path) -> None:
    """phase 2 의존성 미주입 시 INTERNAL_ERROR."""
    state = _state_with_final_plan([_unit("U-001")])
    cassette = CassetteStore(name="x", dir=tmp_path / "c", mode="off")
    budget = BudgetTracker(limit_usd=1.0, limit_tokens=1000)
    provider = _MockProvider()
    gateway = LlmGateway(provider=provider, cassette=cassette, budget=budget)
    orch = Orchestrator(
        planner=PlannerAgent(), llm_gateway=gateway, budget=budget
        # executor / advisor / evaluator_testgen / workspace_root 미주입.
    )
    result = await orch._run_building_and_testgen(state)
    assert result.status == "aborted"
    assert result.termination is not None
    assert result.termination.reason == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_phase2_aborts_when_plan_final_missing(tmp_path: Path) -> None:
    """plan.final 이 None 이면 INTERNAL_ERROR (phase 1 미완 상태에서 진입)."""
    orch, _p = _orchestrator(
        tmp_path,
        queues={"executor": [], "advisor": [], "evaluator": []},
    )
    state = SystemState(
        run_id="r_test",
        status="building",
        user_input="x",
        budget=BudgetState(limit_usd=1.0, limit_tokens=1000),
    )  # plan.final 없음.
    result = await orch._run_building_and_testgen(state)
    assert result.status == "aborted"
    assert result.termination is not None
    assert result.termination.reason == "INTERNAL_ERROR"
