"""Integration tests for Orchestrator._run_testing (Phase 3, v4 §6.1, §8.3).

Mock TestRunner + LLM stub 으로 testrun ↔ Executor revise loop 검증.
ProcessManager 는 None (restart skip) 으로 단순화.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
import structlog

from code2e.agents.advisor import AdvisorAgent
from code2e.agents.evaluator import EvaluatorTestgenAgent, EvaluatorTestrunAgent
from code2e.agents.executor import ExecutorAgent
from code2e.agents.planner import PlannerAgent
from code2e.core.budget import BudgetTracker
from code2e.core.cassette import CassetteStore
from code2e.core.llm_gateway import LlmGateway
from code2e.core.orchestrator import PHASE3_MAX_ITERATIONS, Orchestrator
from code2e.core.schemas import (
    BudgetState,
    HealthCheckSpec,
    LaunchInfo,
    LaunchSpec,
    Plan,
    PlanMeta,
    PlanState,
    PlanUnit,
    SystemState,
    TestCase,
    TestResult,
    TestRun,
    TestState,
    TestSummary,
)

# ---------- Mock LLM provider (executor 만 호출) ----------


@dataclass
class _MockProvider:
    name: ClassVar[str] = "mock"
    queues: dict[str, list[dict[str, object] | Exception]] = field(default_factory=dict)

    async def call(
        self,
        model: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, object]:
        agent = _identify_agent(system_prompt)
        queue = self.queues.get(agent)
        if not queue:
            raise RuntimeError(f"no mock response for agent={agent}")
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
    return {"text": json.dumps(payload), "tokens_in": 5, "tokens_out": 3, "raw": {}}


# ---------- Mock TestRunner ----------


@dataclass
class _MockRunner:
    name: ClassVar[str] = "mock"
    test_runs: list[TestRun] = field(default_factory=list)
    setup_calls: int = 0
    teardown_calls: int = 0
    _iter: Iterator[TestRun] = field(init=False)

    def __post_init__(self) -> None:
        self._iter = iter(self.test_runs)

    async def setup(self, workspace_dir: Path) -> None:
        self.setup_calls += 1

    async def run(
        self,
        suite: list[TestCase],
        ctx: object,
        base_url: str | None = None,
    ) -> TestRun:
        return next(self._iter)

    async def teardown(self) -> None:
        self.teardown_calls += 1


# ---------- builders ----------


def _testrun(results: list[TestResult]) -> TestRun:
    summary = TestSummary(
        passed=sum(1 for r in results if r.status == "passed"),
        failed=sum(1 for r in results if r.status == "failed"),
        errored=sum(1 for r in results if r.status == "errored"),
        total=len(results),
    )
    return TestRun(iteration=0, results=results, summary=summary, signature="")


def _passed(case_id: str = "T-001") -> TestResult:
    return TestResult(case_id=case_id, status="passed", duration_ms=10)


def _failed(case_id: str = "T-001", reason: str = "X") -> TestResult:
    return TestResult(
        case_id=case_id, status="failed", duration_ms=10, failure_reason=reason
    )


def _orchestrator(
    tmp_path: Path,
    *,
    runner: _MockRunner,
    executor_responses: list[dict[str, object] | Exception],
) -> tuple[Orchestrator, _MockRunner]:
    cassette = CassetteStore(name="phase3", dir=tmp_path / "cassettes", mode="off")
    budget = BudgetTracker(limit_usd=10.0, limit_tokens=100_000)
    provider = _MockProvider(queues={"executor": list(executor_responses)})
    gateway = LlmGateway(provider=provider, cassette=cassette, budget=budget)
    orch = Orchestrator(
        planner=PlannerAgent(),
        executor=ExecutorAgent(),
        advisor=AdvisorAgent(),
        evaluator_testgen=EvaluatorTestgenAgent(),
        evaluator_testrun=EvaluatorTestrunAgent(runner=runner),
        llm_gateway=gateway,
        budget=budget,
        workspace_root=tmp_path / "workspaces",
        # process_manager=None — restart skip (단순화).
        cancel_token=asyncio.Event(),
        logger=structlog.get_logger("test"),
    )
    return orch, runner


def _state_with_phase_l_done(suite: list[TestCase]) -> SystemState:
    final = Plan(
        version=3,
        content="## final",
        units=[
            PlanUnit(
                id="U-001",
                title="t",
                description="d",
                acceptance_criteria=["x"],
            )
        ],
        meta=PlanMeta(
            created_at=datetime(2026, 5, 5, tzinfo=UTC), tokens_in=0, tokens_out=0
        ),
    )
    spec = LaunchSpec(
        kind="http",
        command=["python", "-c", "pass"],
        port_hint=3000,
        health_check=HealthCheckSpec(method="TCP_CONNECT", target=""),
    )
    launch = LaunchInfo(
        pid=99999,
        port=3000,
        base_url="http://localhost:3000",
        started_at=datetime(2026, 5, 5, tzinfo=UTC),
        log_path="/tmp/x.log",
    )
    return SystemState(
        run_id="r_phase3_test",
        status="testing",
        user_input="task",
        plan=PlanState(iterations=[final], final=final, launch_spec=spec),
        launch=launch,
        test=TestState(suite=suite),
        budget=BudgetState(limit_usd=10.0, limit_tokens=100_000),
    )


_GOOD_CHANGE = {
    "files": [{"path": "main.py", "op": "update", "content": "fixed"}],
    "rationale": "fix",
}


def _case(uid: str = "T-001") -> TestCase:
    return TestCase(
        id=uid, scenario="s", given="g", when="w", then="t", runner_script="..."
    )


# ---------- happy paths ----------


@pytest.mark.asyncio
async def test_phase3_first_iter_all_pass_completes(tmp_path: Path) -> None:
    runner = _MockRunner(test_runs=[_testrun([_passed("T-001")])])
    orch, _r = _orchestrator(tmp_path, runner=runner, executor_responses=[])
    state = _state_with_phase_l_done([_case()])
    result = await orch._run_testing(state)

    assert result.status == "completed"
    assert result.test.status == "passed"
    assert len(result.test.runs) == 1
    assert result.test.runs[0].iteration == 1
    # Runner lifecycle 검증.
    assert runner.setup_calls == 1
    assert runner.teardown_calls == 1


@pytest.mark.asyncio
async def test_phase3_revise_then_pass(tmp_path: Path) -> None:
    """iter 1 fail → executor revise → iter 2 pass → completed."""
    runner = _MockRunner(
        test_runs=[
            _testrun([_failed("T-001", "first")]),
            _testrun([_passed("T-001")]),
        ]
    )
    orch, _r = _orchestrator(
        tmp_path, runner=runner, executor_responses=[_resp(_GOOD_CHANGE)]
    )
    state = _state_with_phase_l_done([_case()])
    result = await orch._run_testing(state)

    assert result.status == "completed"
    assert result.test.status == "passed"
    assert len(result.test.runs) == 2
    assert result.test.runs[0].iteration == 1
    assert result.test.runs[1].iteration == 2


# ---------- termination ----------


@pytest.mark.asyncio
async def test_phase3_max_iterations_force_stop(tmp_path: Path) -> None:
    """5 iter 모두 fail (각각 다른 reason → stagnation 회피) → MAX_ITERATIONS."""
    runner = _MockRunner(
        test_runs=[
            _testrun([_failed("T-001", f"reason-{i}")])
            for i in range(PHASE3_MAX_ITERATIONS)
        ]
    )
    orch, _r = _orchestrator(
        tmp_path,
        runner=runner,
        executor_responses=[_resp(_GOOD_CHANGE)] * PHASE3_MAX_ITERATIONS,
    )
    state = _state_with_phase_l_done([_case()])
    result = await orch._run_testing(state)

    assert result.status == "aborted"
    assert result.termination is not None
    assert result.termination.reason == "MAX_ITERATIONS"
    assert result.test.status == "force_stopped"
    assert len(result.test.runs) == PHASE3_MAX_ITERATIONS


@pytest.mark.asyncio
async def test_phase3_stagnation_force_stop(tmp_path: Path) -> None:
    """동일 failure_reason → 동일 signature 가 window 회 반복 → STAGNATION."""
    runner = _MockRunner(
        test_runs=[
            _testrun([_failed("T-001", "same-reason")])
            for _ in range(PHASE3_MAX_ITERATIONS)
        ]
    )
    orch, _r = _orchestrator(
        tmp_path,
        runner=runner,
        executor_responses=[_resp(_GOOD_CHANGE)] * PHASE3_MAX_ITERATIONS,
    )
    state = _state_with_phase_l_done([_case()])
    result = await orch._run_testing(state)

    assert result.status == "aborted"
    assert result.termination is not None
    assert result.termination.reason == "STAGNATION"
    # window=2 라 3 iter 정도 후 stagnant 검출.
    assert len(result.test.runs) <= 3


@pytest.mark.asyncio
async def test_phase3_security_violation_aborts(tmp_path: Path) -> None:
    """Executor 가 path traversal → SECURITY_VIOLATION."""
    runner = _MockRunner(test_runs=[_testrun([_failed()])])
    bad_change = {
        "files": [{"path": "/etc/evil", "op": "create", "content": "x"}],
        "rationale": "bad",
    }
    orch, _r = _orchestrator(
        tmp_path, runner=runner, executor_responses=[_resp(bad_change)]
    )
    state = _state_with_phase_l_done([_case()])
    result = await orch._run_testing(state)

    assert result.status == "aborted"
    assert result.termination is not None
    assert result.termination.reason == "SECURITY_VIOLATION"


# ---------- regression detection (ADR-039) ----------


@pytest.mark.asyncio
async def test_phase3_regression_passed_to_executor(tmp_path: Path) -> None:
    """iter 1 에서 T-001 통과, iter 2 에서 T-001 실패 → executor 입력에
    regression_context.previously_passing_case_ids = ['T-001'].
    """
    runner = _MockRunner(
        test_runs=[
            _testrun([_passed("T-001"), _failed("T-002", "still failing")]),
            _testrun([_failed("T-001", "regressed"), _failed("T-002", "still failing")]),
            _testrun([_passed("T-001"), _passed("T-002")]),
        ]
    )
    # Executor 의 user message 를 검증하기 위해 provider.queues 직접 확인.
    cassette = CassetteStore(name="phase3-r", dir=tmp_path / "cassettes", mode="off")
    budget = BudgetTracker(limit_usd=10.0, limit_tokens=100_000)
    provider = _MockProvider(
        queues={"executor": [_resp(_GOOD_CHANGE), _resp(_GOOD_CHANGE)]}
    )
    gateway = LlmGateway(provider=provider, cassette=cassette, budget=budget)
    orch = Orchestrator(
        planner=PlannerAgent(),
        executor=ExecutorAgent(),
        advisor=AdvisorAgent(),
        evaluator_testgen=EvaluatorTestgenAgent(),
        evaluator_testrun=EvaluatorTestrunAgent(runner=runner),
        llm_gateway=gateway,
        budget=budget,
        workspace_root=tmp_path / "workspaces",
        cancel_token=asyncio.Event(),
        logger=structlog.get_logger("test"),
    )

    # _MockProvider 호출 추적용 patch.
    captured_calls: list[str] = []
    original_call = provider.call

    async def tracking_call(
        model: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, object]:
        if _identify_agent(system_prompt) == "executor":
            captured_calls.append(messages[0]["content"])
        return await original_call(
            model, system_prompt, messages, temperature, max_tokens
        )

    provider.call = tracking_call  # type: ignore[method-assign]

    state = _state_with_phase_l_done([_case("T-001"), _case("T-002")])
    result = await orch._run_testing(state)

    assert result.status == "completed"
    # 두 번째 executor 호출 (iter 2 실패 후) 이 regression_context 포함.
    assert len(captured_calls) >= 2
    second_call = captured_calls[1]
    assert "T-001" in second_call  # regression case id.
    # regression_context 의 note 가 포함되어야 (executor.py 의 _dump_optional 에서 dump).
    assert "이전에 통과하던 케이스" in second_call


# ---------- guards ----------


@pytest.mark.asyncio
async def test_phase3_aborts_when_deps_missing(tmp_path: Path) -> None:
    cassette = CassetteStore(name="x", dir=tmp_path / "c", mode="off")
    budget = BudgetTracker(limit_usd=1.0, limit_tokens=1000)
    provider = _MockProvider()
    gateway = LlmGateway(provider=provider, cassette=cassette, budget=budget)
    orch = Orchestrator(
        planner=PlannerAgent(), llm_gateway=gateway, budget=budget
        # executor / evaluator_testrun / workspace_root 미주입.
    )
    result = await orch._run_testing(_state_with_phase_l_done([_case()]))
    assert result.status == "aborted"
    assert result.termination is not None
    assert result.termination.reason == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_phase3_aborts_when_suite_missing(tmp_path: Path) -> None:
    runner = _MockRunner()
    orch, _r = _orchestrator(tmp_path, runner=runner, executor_responses=[])
    state = _state_with_phase_l_done([])
    state = state.model_copy(update={"test": TestState(suite=None)})  # 명시적으로 None.
    result = await orch._run_testing(state)
    assert result.status == "aborted"
    assert result.termination is not None
    assert result.termination.reason == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_phase3_aborts_when_launch_missing(tmp_path: Path) -> None:
    runner = _MockRunner()
    orch, _r = _orchestrator(tmp_path, runner=runner, executor_responses=[])
    state = _state_with_phase_l_done([_case()])
    state = state.model_copy(update={"launch": None})
    result = await orch._run_testing(state)
    assert result.status == "aborted"
    assert result.termination is not None
    assert result.termination.reason == "INTERNAL_ERROR"


# ---------- runner lifecycle ----------


@pytest.mark.asyncio
async def test_phase3_runner_teardown_called_on_exception(tmp_path: Path) -> None:
    """Runner.run 이 exception 던져도 teardown 은 finally 에서 호출."""

    class _ExplodingRunner(_MockRunner):
        async def run(self, *args: object, **kwargs: object) -> TestRun:
            raise RuntimeError("boom")

    runner = _ExplodingRunner()
    orch, _r = _orchestrator(tmp_path, runner=runner, executor_responses=[])
    state = _state_with_phase_l_done([_case()])

    with pytest.raises(RuntimeError, match="boom"):
        await orch._run_testing(state)

    assert runner.teardown_calls == 1
