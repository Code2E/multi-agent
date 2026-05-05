"""Executor — Plan Unit 단위로 코드 생성/수정 (v4 §3.4, §6.1).

DECISION: Q17 — 산출물 언어는 입력 언어 따름, 미명시 시 Python (프롬프트로 강제).
v4 보정 #3: regression_context 필드로 Phase 3 회귀 정보 수신.

DECISION: Q10 — stateless. instance 는 model + prompts_dir 만 보유.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from code2e.agents.base import InvocationContext
from code2e.agents.planner import parse_prompt_file, render_prompt
from code2e.core.schemas import CodeChange, ExecutorInput, FileEdit

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
PROMPT_FILE = "executor.md"

# v4 §3.4 표.
TIMEOUT_S = 90
RETRIES = 3
TEMPERATURE = 0.2


class ExecutorLlmOutput(BaseModel):
    """Executor 의 LLM 직접 출력. CodeChange.unit_id 는 invoke 가 채움."""

    files: list[FileEdit]
    rationale: str


class ExecutorAgent:
    name: ClassVar[str] = "executor"
    version: ClassVar[str] = "1.0"
    InputModel: ClassVar[type[ExecutorInput]] = ExecutorInput
    OutputModel: ClassVar[type[CodeChange]] = CodeChange

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        prompts_dir: Path | None = None,
    ) -> None:
        self.model = model
        self.prompts_dir = prompts_dir or DEFAULT_PROMPTS_DIR

    async def invoke(self, inp: ExecutorInput, ctx: InvocationContext) -> CodeChange:
        prompt_path = self.prompts_dir / PROMPT_FILE
        system_prompt, user_template = parse_prompt_file(prompt_path)

        user_msg = render_prompt(
            user_template,
            unit=_dump_json(inp.unit),
            files=_dump_json(inp.files),
            feedback=_dump_optional(inp.feedback),
            test_failure=_dump_optional(inp.test_failure),
            regression_context=_dump_optional(inp.regression_context),
        )

        prompt_key = f"executor_v{self.version}"

        result = await ctx.llm.call(
            agent_name=self.name,
            agent_version=self.version,
            prompt_key=prompt_key,
            model=self.model,
            temperature=TEMPERATURE,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
            output_model=ExecutorLlmOutput,
        )
        assert isinstance(result, ExecutorLlmOutput)

        return CodeChange(unit_id=inp.unit.id, files=result.files, rationale=result.rationale)


def _dump_json(obj: object) -> str:
    """Pydantic 모델 / list / dict 를 JSON 문자열로. v1 정렬은 안 함 (가독성 우선)."""
    if isinstance(obj, BaseModel):
        return obj.model_dump_json(indent=2)
    if isinstance(obj, list):
        return json.dumps(
            [m.model_dump(mode="json") if isinstance(m, BaseModel) else m for m in obj],
            indent=2,
            default=str,
        )
    return json.dumps(obj, indent=2, default=str)


def _dump_optional(value: BaseModel | None) -> str:
    if value is None:
        return "(none)"
    return value.model_dump_json(indent=2)
