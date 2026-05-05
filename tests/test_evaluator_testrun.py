"""Unit tests for EvaluatorTestrunAgent (Runner 위임 + signature 자동 생성).

PlaywrightRunner 자체는 외부 의존이라 별도. 여기는 Mock TestRunner 로 위임 동작만.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pytest
import structlog

from code2e.agents.base import InvocationContext
from code2e.agents.evaluator import EvaluatorTestrunAgent
from code2e.core.budget import BudgetTracker
from code2e.core.cassette import CassetteStore
from code2e.core.llm_gateway import LlmGateway
from code2e.core.schemas import (
    EvaluatorTestrunInput,
    TestCase,
    TestResult,
    TestRun,
    TestSummary,
)

# ---------- Mock TestRunner ----------


@dataclass
class _MockRunner:
    name: ClassVar[str] = "mock"
    test_runs: list[TestRun] = field(default_factory=list)
    setup_calls: int = 0
    teardown_calls: int = 0
    run_calls: list[dict[str, object]] = field(default_factory=list)
    _iter: Iterator[TestRun] = field(init=False)

    def __post_init__(self) -> None:
        self._iter = iter(self.test_runs)

    async def setup(self, workspace_dir: Path) -> None:
        self.setup_calls += 1

    async def run(
        self,
        suite: list[TestCase],
        ctx: InvocationContext,
        base_url: str | None = None,
    ) -> TestRun:
        self.run_calls.append({"suite_size": len(suite), "base_url": base_url})
        return next(self._iter)

    async def teardown(self) -> None:
        self.teardown_calls += 1


class _StubProvider:
    name = "stub"

    async def call(self, *args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("LLM not used in testrun tests")

    def estimate_cost(self, tokens_in: int, tokens_out: int, model: str) -> float:
        return 0.0


def _ctx(tmp_path: Path) -> InvocationContext:
    cassette = CassetteStore(name="testrun-t", dir=tmp_path, mode="off")
    budget = BudgetTracker(limit_usd=1.0, limit_tokens=1000)
    gateway = LlmGateway(provider=_StubProvider(), cassette=cassette, budget=budget)
    return InvocationContext(
        trace_id="t-1",
        attempt=0,
        budget=budget,
        cancel_token=asyncio.Event(),
        logger=structlog.get_logger("test"),
        llm=gateway,
    )


def _testrun_with_results(results: list[TestResult]) -> TestRun:
    summary = TestSummary(
        passed=sum(1 for r in results if r.status == "passed"),
        failed=sum(1 for r in results if r.status == "failed"),
        errored=sum(1 for r in results if r.status == "errored"),
        total=len(results),
    )
    return TestRun(iteration=0, results=results, summary=summary, signature="")


def _case(uid: str = "T-001") -> TestCase:
    return TestCase(
        id=uid,
        scenario="s",
        given="g",
        when="w",
        then="t",
        runner_script="await page.goto(BASE_URL)",
    )


# ---------- delegation ----------


@pytest.mark.asyncio
async def test_testrun_delegates_to_runner(tmp_path: Path) -> None:
    expected_run = _testrun_with_results(
        [TestResult(case_id="T-001", status="passed", duration_ms=10)]
    )
    runner = _MockRunner(test_runs=[expected_run])
    agent = EvaluatorTestrunAgent(runner=runner)

    out = await agent.invoke(
        EvaluatorTestrunInput(
            workspace="/tmp/ws", suite=[_case()], base_url="http://localhost:3000"
        ),
        _ctx(tmp_path),
    )

    assert out.results == expected_run.results
    assert out.summary == expected_run.summary
    assert len(runner.run_calls) == 1
    assert runner.run_calls[0]["suite_size"] == 1
    assert runner.run_calls[0]["base_url"] == "http://localhost:3000"


@pytest.mark.asyncio
async def test_testrun_does_not_call_setup_or_teardown(tmp_path: Path) -> None:
    """invoke 는 .run 만 호출 — lifecycle 은 caller (Phase 3 loop) 책임."""
    runner = _MockRunner(test_runs=[_testrun_with_results([])])
    agent = EvaluatorTestrunAgent(runner=runner)
    await agent.invoke(
        EvaluatorTestrunInput(workspace="/tmp/ws", suite=[]),
        _ctx(tmp_path),
    )
    assert runner.setup_calls == 0
    assert runner.teardown_calls == 0


# ---------- signature ----------


@pytest.mark.asyncio
async def test_signature_generated_from_results(tmp_path: Path) -> None:
    run = _testrun_with_results(
        [TestResult(case_id="T-001", status="passed", duration_ms=10)]
    )
    agent = EvaluatorTestrunAgent(runner=_MockRunner(test_runs=[run]))
    out = await agent.invoke(
        EvaluatorTestrunInput(workspace="/tmp/ws", suite=[]),
        _ctx(tmp_path),
    )
    # signature_fn 결과 — 16자 hex.
    assert len(out.signature) == 16


@pytest.mark.asyncio
async def test_signature_deterministic_for_same_results(tmp_path: Path) -> None:
    results = [
        TestResult(
            case_id="T-001", status="failed", duration_ms=10, failure_reason="X"
        )
    ]
    out_a = await EvaluatorTestrunAgent(
        runner=_MockRunner(test_runs=[_testrun_with_results(results)])
    ).invoke(EvaluatorTestrunInput(workspace="/tmp/ws", suite=[]), _ctx(tmp_path))
    out_b = await EvaluatorTestrunAgent(
        runner=_MockRunner(test_runs=[_testrun_with_results(results)])
    ).invoke(EvaluatorTestrunInput(workspace="/tmp/ws", suite=[]), _ctx(tmp_path))
    assert out_a.signature == out_b.signature


@pytest.mark.asyncio
async def test_signature_differs_when_failure_reason_changes(tmp_path: Path) -> None:
    """동일 case_id + status 라도 failure_reason 다르면 signature 다름."""
    r1 = _testrun_with_results(
        [TestResult(case_id="T-001", status="failed", duration_ms=10, failure_reason="A")]
    )
    r2 = _testrun_with_results(
        [TestResult(case_id="T-001", status="failed", duration_ms=10, failure_reason="B")]
    )
    out1 = await EvaluatorTestrunAgent(runner=_MockRunner(test_runs=[r1])).invoke(
        EvaluatorTestrunInput(workspace="/tmp/ws", suite=[]), _ctx(tmp_path)
    )
    out2 = await EvaluatorTestrunAgent(runner=_MockRunner(test_runs=[r2])).invoke(
        EvaluatorTestrunInput(workspace="/tmp/ws", suite=[]), _ctx(tmp_path)
    )
    assert out1.signature != out2.signature


@pytest.mark.asyncio
async def test_empty_results_get_empty_signature(tmp_path: Path) -> None:
    run = _testrun_with_results([])
    agent = EvaluatorTestrunAgent(runner=_MockRunner(test_runs=[run]))
    out = await agent.invoke(
        EvaluatorTestrunInput(workspace="/tmp/ws", suite=[]),
        _ctx(tmp_path),
    )
    assert out.signature == "empty"


# ---------- pass-through ----------


@pytest.mark.asyncio
async def test_pass_through_summary_and_iteration(tmp_path: Path) -> None:
    """Runner 가 반환한 summary / iteration 은 보존 — signature 만 갱신."""
    run = TestRun(
        iteration=3,
        results=[TestResult(case_id="T-1", status="passed", duration_ms=5)],
        summary=TestSummary(passed=1, failed=0, errored=0, total=1),
        signature="dummy-from-runner",
    )
    agent = EvaluatorTestrunAgent(runner=_MockRunner(test_runs=[run]))
    out = await agent.invoke(
        EvaluatorTestrunInput(workspace="/tmp/ws", suite=[]),
        _ctx(tmp_path),
    )
    assert out.iteration == 3  # 보존.
    assert out.summary.passed == 1
    assert out.signature != "dummy-from-runner"  # 갱신됨.
