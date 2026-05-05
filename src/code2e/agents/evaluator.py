"""Evaluator — testgen (Final Plan → list[TestCase]) + testrun (Playwright 위임).

testgen 은 LLM 호출 (이번 commit 에서 구현), testrun 은 TestRunner Protocol 로
Playwright 에 위임 (별도 commit). v4 §3.4, §6.1.

DECISION:
- Q10: stateless. instance 는 model + prompts_dir 만 보유.
- Q34: v1 은 1 회 실행, flaky 다수결 v1.1.

Pydantic 은 list 단독 OutputModel 을 받지 못하므로 EvaluatorTestgenLlmOutput
래퍼를 두고 invoke 가 .cases 를 추출해 list[TestCase] 반환.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from code2e.agents.base import InvocationContext
from code2e.agents.planner import parse_prompt_file, render_prompt
from code2e.core.schemas import (
    EvaluatorTestgenInput,
    EvaluatorTestrunInput,
    TestCase,
    TestRun,
)

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
TESTGEN_PROMPT_FILE = "evaluator_testgen.md"

# v4 §3.4 표.
TESTGEN_TIMEOUT_S = 90
TESTGEN_RETRIES = 3
TESTGEN_TEMPERATURE = 0.2

TESTRUN_TIMEOUT_S = 5 * 60
TESTRUN_RETRIES = 0


class EvaluatorTestgenLlmOutput(BaseModel):
    """testgen LLM 직접 출력 — invoke 가 .cases 만 추출해 반환."""

    cases: list[TestCase]


class EvaluatorTestgenAgent:
    name: ClassVar[str] = "evaluator.testgen"
    version: ClassVar[str] = "1.0"
    InputModel: ClassVar[type[EvaluatorTestgenInput]] = EvaluatorTestgenInput
    OutputModel: ClassVar[type[EvaluatorTestgenLlmOutput]] = EvaluatorTestgenLlmOutput

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        prompts_dir: Path | None = None,
    ) -> None:
        self.model = model
        self.prompts_dir = prompts_dir or DEFAULT_PROMPTS_DIR

    async def invoke(
        self, inp: EvaluatorTestgenInput, ctx: InvocationContext
    ) -> list[TestCase]:
        prompt_path = self.prompts_dir / TESTGEN_PROMPT_FILE
        system_prompt, user_template = parse_prompt_file(prompt_path)

        plan = inp.final_plan
        user_msg = render_prompt(
            user_template,
            plan_content=plan.content,
            units=json.dumps(
                [u.model_dump(mode="json") for u in plan.units],
                indent=2,
                default=str,
            ),
        )

        prompt_key = f"evaluator_testgen_v{self.version}"

        result = await ctx.llm.call(
            agent_name=self.name,
            agent_version=self.version,
            prompt_key=prompt_key,
            model=self.model,
            temperature=TESTGEN_TEMPERATURE,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
            output_model=EvaluatorTestgenLlmOutput,
        )
        assert isinstance(result, EvaluatorTestgenLlmOutput)
        return result.cases


class EvaluatorTestrunAgent:
    name: ClassVar[str] = "evaluator.testrun"
    version: ClassVar[str] = "1.0"
    InputModel: ClassVar[type[EvaluatorTestrunInput]] = EvaluatorTestrunInput
    OutputModel: ClassVar[type[TestRun]] = TestRun

    async def invoke(self, inp: EvaluatorTestrunInput, ctx: InvocationContext) -> TestRun:
        # Playwright runner 통합은 별도 commit. PlaywrightRunner.run 위임 + retry.
        raise NotImplementedError(
            "EvaluatorTestrunAgent.invoke — v4 §6.1 Playwright 위임 구현 예정 (별도 commit)"
        )
