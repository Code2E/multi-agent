"""Advisor — Plan Unit 별 코드 리뷰, approve / revise 결정 (v4 §3.4, §6.1, §8.2).

DECISION: Q11 — revise + 빈 코멘트는 force-stop 처리 (termination.py).
"""

from __future__ import annotations

from typing import ClassVar

from code2e.agents.base import Agent, InvocationContext
from code2e.core.schemas import AdvisorFeedback, AdvisorInput

TIMEOUT_S = 60
RETRIES = 3
TEMPERATURE = 0.3


class AdvisorAgent(Agent):
    name: ClassVar[str] = "advisor"
    version: ClassVar[str] = "1.0"
    InputModel: ClassVar[type[AdvisorInput]] = AdvisorInput
    OutputModel: ClassVar[type[AdvisorFeedback]] = AdvisorFeedback

    async def invoke(self, inp: AdvisorInput, ctx: InvocationContext) -> AdvisorFeedback:  # type: ignore[override]
        raise NotImplementedError("Advisor.invoke — v4 §6.1 approve/revise 구현 예정")
