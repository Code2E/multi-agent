"""Executor — Plan Unit 단위로 코드 생성/수정 (v4 §3.4, §6.1).

DECISION: Q17 — 산출물 언어는 입력 언어 따름, 미명시 시 Python (프롬프트로 강제).
v4 보정 #3: regression_context 필드로 Phase 3 회귀 정보 수신.
"""

from __future__ import annotations

from typing import ClassVar

from code2e.agents.base import Agent, InvocationContext
from code2e.core.schemas import CodeChange, ExecutorInput

TIMEOUT_S = 90
RETRIES = 3
TEMPERATURE = 0.2


class ExecutorAgent(Agent):
    name: ClassVar[str] = "executor"
    version: ClassVar[str] = "1.0"
    InputModel: ClassVar[type[ExecutorInput]] = ExecutorInput
    OutputModel: ClassVar[type[CodeChange]] = CodeChange

    async def invoke(self, inp: ExecutorInput, ctx: InvocationContext) -> CodeChange:  # type: ignore[override]
        raise NotImplementedError("Executor.invoke — v4 §6.1 scaffold/revise 구현 예정")
