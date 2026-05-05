"""Planner — 사용자 입력으로 3-round refined Plan 생성 (v4 §3.4, §6.1).

Round 별 temperature: 1=0.7, 2=0.3, 3=0.3 (창의적 탐색 → 수렴).
Round 3 출력은 PlanUnit 리스트 + LaunchSpec frontmatter 를 포함해야 한다 (FR-003).

DECISION: Q10 — stateless. PlannerAgent 인스턴스는 model / prompts_dir 만 보유,
런타임 상태는 invoke 호출 시 InvocationContext (ctx.llm) 로 주입.

PlannerLlmOutput 은 LLM 의 직접 응답 형태. invoke 가 이를 Plan(version, content,
units, meta) 으로 합성 — meta 는 invoke 시 datetime 으로 채움. v1 에서는 round 별
정확 토큰 카운트는 0 으로 두고 budget tracker 누적으로 갈음 (LlmGateway 시그니처
확장 시 보강).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from code2e.agents.base import InvocationContext
from code2e.core.schemas import Plan, PlanMeta, PlannerInput, PlanUnit

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# v4 §3.4 표.
TIMEOUT_S = 60
RETRIES = 3
TEMPERATURE_BY_ROUND: dict[int, float] = {1: 0.7, 2: 0.3, 3: 0.3}

_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


class PlannerLlmOutput(BaseModel):
    """Planner 의 LLM 직접 출력. Plan.meta 는 invoke 가 채움."""

    content: str
    units: list[PlanUnit] = Field(default_factory=list)


class PlannerAgent:
    name: ClassVar[str] = "planner"
    version: ClassVar[str] = "1.0"
    InputModel: ClassVar[type[PlannerInput]] = PlannerInput
    OutputModel: ClassVar[type[Plan]] = Plan

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        prompts_dir: Path | None = None,
    ) -> None:
        self.model = model
        self.prompts_dir = prompts_dir or DEFAULT_PROMPTS_DIR

    async def invoke(self, inp: PlannerInput, ctx: InvocationContext) -> Plan:
        prompt_path = self.prompts_dir / f"planner_round_{inp.round}.md"
        system_prompt, user_template = parse_prompt_file(prompt_path)

        prev_plan_text = inp.prev_plan.content if inp.prev_plan else "(none — round 1)"
        user_msg = render_prompt(
            user_template,
            user_input=inp.user_input,
            prev_plan=prev_plan_text,
            round=str(inp.round),
        )

        prompt_key = f"planner_round_{inp.round}_v{self.version}"
        temperature = TEMPERATURE_BY_ROUND[inp.round]

        result = await ctx.llm.call(
            agent_name=self.name,
            agent_version=self.version,
            prompt_key=prompt_key,
            model=self.model,
            temperature=temperature,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
            output_model=PlannerLlmOutput,
        )
        assert isinstance(result, PlannerLlmOutput)

        return Plan(
            version=inp.round,
            content=result.content,
            units=result.units,
            meta=PlanMeta(
                created_at=datetime.now(UTC),
                tokens_in=0,
                tokens_out=0,
            ),
        )


# ---------- prompt 파일 파싱 utilities ----------


def parse_prompt_file(path: Path) -> tuple[str, str]:
    """프롬프트 파일에서 (system_prompt, user_template) 추출.

    파일 형식 (v4 §13.1):
        ---frontmatter---
        [system]
        ...system text...
        [user]
        ...user template with {placeholders}...
    """
    text = path.read_text(encoding="utf-8")
    text = _FRONTMATTER_RE.sub("", text, count=1)

    sys_idx = text.find("[system]")
    user_idx = text.find("[user]")
    if sys_idx < 0 or user_idx < 0 or user_idx <= sys_idx:
        raise ValueError(f"prompt file must contain [system] before [user]: {path}")

    system = text[sys_idx + len("[system]") : user_idx].strip()
    user = text[user_idx + len("[user]") :].strip()
    return system, user


def render_prompt(template: str, **placeholders: str) -> str:
    """`{key}` placeholder 치환. str.format 대신 replace 사용 — 본문 안의 다른
    `{` (예: JSON 예시) 가 format 에서 충돌하는 문제 회피.
    """
    out = template
    for k, v in placeholders.items():
        out = out.replace(f"{{{k}}}", v)
    return out
