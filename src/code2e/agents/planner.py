"""Planner — 사용자 입력으로 3-round refined Plan 생성 (v4 §3.4, §6.1).

Round 별 temperature: 1=0.7, 2=0.3, 3=0.3 (창의적 탐색 → 수렴).
Round 3 출력은 PlanUnit 리스트 + LaunchSpec frontmatter 를 포함해야 한다 (FR-003).
"""

from __future__ import annotations

from typing import ClassVar

from code2e.agents.base import Agent, InvocationContext
from code2e.core.schemas import Plan, PlannerInput

TIMEOUT_S = 60
RETRIES = 3
TEMPERATURE_BY_ROUND: dict[int, float] = {1: 0.7, 2: 0.3, 3: 0.3}


class PlannerAgent(Agent):
    name: ClassVar[str] = "planner"
    version: ClassVar[str] = "1.0"
    InputModel: ClassVar[type[PlannerInput]] = PlannerInput
    OutputModel: ClassVar[type[Plan]] = Plan

    async def invoke(self, inp: PlannerInput, ctx: InvocationContext) -> Plan:  # type: ignore[override]
        raise NotImplementedError("Planner.invoke — v4 §6.1 round 1/2/3 구현 예정")
