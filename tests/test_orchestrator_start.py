"""End-to-end tests for Orchestrator.start (Phase 1 → 2 → L stub).

start() 가 phase 를 순차 호출하고 매 phase 후 checkpoint 저장하는지 검증.
Phase L / 3 / Teardown 은 현재 stub (NotImplementedError) → INTERNAL_ERROR 로
우아하게 abort 되는 흐름까지 확인.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
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
from code2e.core.checkpoint import CheckpointWriter
from code2e.core.llm_gateway import LlmGateway
from code2e.core.orchestrator import Orchestrator

# ---------- _MockProvider (test_orchestrator_phase2 와 동일 패턴) ----------


@dataclass
class _MockProvider:
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
        self.calls.append({"agent": agent})
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
    m = _AGENT_RE.search(system_prompt)
    return m.group(1).lower() if m else "unknown"


def _resp(payload: dict[str, object]) -> dict[str, object]:
    return {"text": json.dumps(payload), "tokens_in": 10, "tokens_out": 5, "raw": {}}


# ---------- payload fixtures ----------


_PLANNER_R1 = {"content": "## R1 plan", "units": []}
_PLANNER_R2 = {"content": "## R2 plan", "units": []}
_PLANNER_R3 = {
    "content": "## R3 final",
    "units": [
        {
            "id": "U-001",
            "title": "scaffold",
            "description": "create skeleton",
            "acceptance_criteria": ["compiles"],
            "dependencies": [],
            "estimated_complexity": "low",
        }
    ],
}
_EXECUTOR = {
    "files": [{"path": "main.py", "op": "create", "content": "print('hi')"}],
    "rationale": "scaffold",
}
_APPROVE = {"decision": "approve", "comments": []}
_TESTCASES = {
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


# ---------- builder ----------


def _orchestrator(
    tmp_path: Path,
    *,
    queues: dict[str, list[dict[str, object] | Exception]],
    with_checkpoint: bool = True,
) -> tuple[Orchestrator, _MockProvider, CheckpointWriter | None]:
    provider = _MockProvider(queues={k: list(v) for k, v in queues.items()})
    cassette = CassetteStore(name="orch-start", dir=tmp_path / "cassettes", mode="off")
    budget = BudgetTracker(limit_usd=10.0, limit_tokens=100_000)
    gateway = LlmGateway(provider=provider, cassette=cassette, budget=budget)
    cw = CheckpointWriter(runs_root=tmp_path / "runs") if with_checkpoint else None
    orch = Orchestrator(
        planner=PlannerAgent(),
        executor=ExecutorAgent(),
        advisor=AdvisorAgent(),
        evaluator_testgen=EvaluatorTestgenAgent(),
        llm_gateway=gateway,
        budget=budget,
        workspace_root=tmp_path / "workspaces",
        checkpoint=cw,
        cancel_token=asyncio.Event(),
        logger=structlog.get_logger("test"),
    )
    return orch, provider, cw


def _full_happy_queues() -> dict[str, list[dict[str, object] | Exception]]:
    """Phase 1 (3 round) + Phase 2 (1 unit, 1 iter) + testgen 모두 성공 큐."""
    return {
        "planner": [_resp(_PLANNER_R1), _resp(_PLANNER_R2), _resp(_PLANNER_R3)],
        "executor": [_resp(_EXECUTOR)],
        "advisor": [_resp(_APPROVE)],
        "evaluator": [_resp(_TESTCASES)],
    }


# ---------- happy path: phase 1 → 2 → L stub abort ----------


@pytest.mark.asyncio
async def test_start_runs_phase1_and_phase2_then_aborts_at_launching(tmp_path: Path) -> None:
    """현재 phase L stub. start() 는 Phase 1+2 통과 후 L 진입 시 INTERNAL_ERROR."""
    orch, _p, _cw = _orchestrator(tmp_path, queues=_full_happy_queues())
    state = await orch.start("build a todo CLI")

    # Phase 1, 2 통과 → Phase L 진입 시 NotImplementedError → _safe_phase 가 INTERNAL_ERROR 로 변환.
    assert state.status == "aborted"
    assert state.termination is not None
    assert state.termination.reason == "INTERNAL_ERROR"
    assert state.termination.phase == "Phase L"
    # Phase 1, 2 의 결과가 보존되어야 한다.
    assert len(state.plan.iterations) == 3
    assert state.plan.final is not None
    assert len(state.build.units) == 1
    assert state.build.units[0].status == "approved"
    assert state.test.suite is not None
    assert len(state.test.suite) == 1


@pytest.mark.asyncio
async def test_start_assigns_run_id_when_not_given(tmp_path: Path) -> None:
    orch, _p, _cw = _orchestrator(tmp_path, queues=_full_happy_queues())
    state = await orch.start("task")
    assert state.run_id.startswith("r_")


@pytest.mark.asyncio
async def test_start_uses_explicit_run_id(tmp_path: Path) -> None:
    orch, _p, _cw = _orchestrator(tmp_path, queues=_full_happy_queues())
    state = await orch.start("task", run_id="r_explicit_001")
    assert state.run_id == "r_explicit_001"


# ---------- checkpoint 저장 검증 ----------


@pytest.mark.asyncio
async def test_start_saves_checkpoint_after_each_phase(tmp_path: Path) -> None:
    orch, _p, cw = _orchestrator(tmp_path, queues=_full_happy_queues())
    await orch.start("task", run_id="r_cp_test")

    assert cw is not None
    phases = cw.list_phases("r_cp_test")
    # Phase 1 완료 → "planning". Phase 2 완료 → "building". Phase L 진입(stub abort) →
    # "launching" 도 저장 (aborted 상태).
    assert "planning" in phases
    assert "building" in phases
    assert "launching" in phases
    # state 가 launching 단계에서 abort 했으니 "completed" 는 없음.
    assert "completed" not in phases


@pytest.mark.asyncio
async def test_start_works_without_checkpoint_writer(tmp_path: Path) -> None:
    """checkpoint=None 이어도 정상 흐름 진행 (디스크 저장만 skip)."""
    orch, _p, _cw = _orchestrator(
        tmp_path, queues=_full_happy_queues(), with_checkpoint=False
    )
    state = await orch.start("task")
    # Phase L stub 에서 abort.
    assert state.status == "aborted"
    assert state.termination is not None
    assert state.termination.reason == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_start_loaded_checkpoint_round_trips(tmp_path: Path) -> None:
    """저장된 checkpoint 를 다시 load 해도 핵심 필드 동일."""
    orch, _p, cw = _orchestrator(tmp_path, queues=_full_happy_queues())
    state = await orch.start("task", run_id="r_rt_001")

    assert cw is not None
    loaded = cw.load("r_rt_001", "building")
    assert loaded.run_id == state.run_id
    assert loaded.user_input == "task"
    assert len(loaded.plan.iterations) == 3


# ---------- 실패 라우팅 ----------


@pytest.mark.asyncio
async def test_start_aborts_in_phase1_does_not_invoke_phase2(tmp_path: Path) -> None:
    """Phase 1 의 round 3 결과가 units 없으면 UNIT_DECOMPOSITION_FAILED → Phase 2 미진입."""
    queues = {
        "planner": [
            _resp({"content": "## R1", "units": []}),
            _resp({"content": "## R2", "units": []}),
            _resp({"content": "## R3 with no units", "units": []}),
        ],
        # executor / advisor / evaluator 큐 비어있어도 Phase 2 진입 안 하니 OK.
        "executor": [],
        "advisor": [],
        "evaluator": [],
    }
    orch, provider, cw = _orchestrator(tmp_path, queues=queues)
    state = await orch.start("task", run_id="r_fail_p1")

    assert state.status == "aborted"
    assert state.termination is not None
    assert state.termination.reason == "UNIT_DECOMPOSITION_FAILED"
    # Phase 2 (executor/advisor) 호출 안 됨.
    agents_called = {c["agent"] for c in provider.calls}
    assert "executor" not in agents_called
    assert "advisor" not in agents_called

    # planning checkpoint 만 저장됐어야.
    assert cw is not None
    phases = cw.list_phases("r_fail_p1")
    assert phases == ["planning"]


@pytest.mark.asyncio
async def test_start_aborts_in_phase2_does_not_invoke_phase_l(tmp_path: Path) -> None:
    """Phase 2 에서 SECURITY_VIOLATION → Phase L 미진입."""
    bad_change = {
        "files": [{"path": "/etc/passwd", "op": "create", "content": "x"}],
        "rationale": "bad",
    }
    queues = {
        "planner": [_resp(_PLANNER_R1), _resp(_PLANNER_R2), _resp(_PLANNER_R3)],
        "executor": [_resp(bad_change)],
        "advisor": [_resp(_APPROVE)],
        "evaluator": [_resp(_TESTCASES)],
    }
    orch, _p, cw = _orchestrator(tmp_path, queues=queues)
    state = await orch.start("task", run_id="r_sec_p2")

    assert state.status == "aborted"
    assert state.termination is not None
    assert state.termination.reason == "SECURITY_VIOLATION"

    assert cw is not None
    phases = cw.list_phases("r_sec_p2")
    # planning + building 만. launching 미진입.
    assert "planning" in phases
    assert "building" in phases
    assert "launching" not in phases
