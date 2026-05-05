"""Evaluator — testgen (Final Plan → TestSuite) + testrun (워크스페이스 → TestRun).

testgen 은 LLM 호출, testrun 은 TestRunner (Playwright) 위임 (v4 §3.4, §6.1).
DECISION: Q34 — v1 은 1 회 실행, flaky 다수결은 v1.1.
"""

from __future__ import annotations

from typing import ClassVar

from code2e.agents.base import Agent, InvocationContext
from code2e.core.schemas import (
    EvaluatorTestgenInput,
    EvaluatorTestrunInput,
    TestCase,
    TestRun,
)

TESTGEN_TIMEOUT_S = 90
TESTGEN_RETRIES = 3
TESTGEN_TEMPERATURE = 0.2

TESTRUN_TIMEOUT_S = 5 * 60
TESTRUN_RETRIES = 0


class EvaluatorTestgenAgent(Agent):
    name: ClassVar[str] = "evaluator.testgen"
    version: ClassVar[str] = "1.0"
    InputModel: ClassVar[type[EvaluatorTestgenInput]] = EvaluatorTestgenInput
    OutputModel: ClassVar[type[TestCase]] = TestCase  # 실제로는 list[TestCase] 반환 (런타임 검증)

    async def invoke(  # type: ignore[override]
        self, inp: EvaluatorTestgenInput, ctx: InvocationContext
    ) -> list[TestCase]:
        raise NotImplementedError("Evaluator.testgen — v4 §6.1 구현 예정")


class EvaluatorTestrunAgent(Agent):
    name: ClassVar[str] = "evaluator.testrun"
    version: ClassVar[str] = "1.0"
    InputModel: ClassVar[type[EvaluatorTestrunInput]] = EvaluatorTestrunInput
    OutputModel: ClassVar[type[TestRun]] = TestRun

    async def invoke(  # type: ignore[override]
        self, inp: EvaluatorTestrunInput, ctx: InvocationContext
    ) -> TestRun:
        raise NotImplementedError("Evaluator.testrun — v4 §6.1 Playwright 위임 구현 예정")
