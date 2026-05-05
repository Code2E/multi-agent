"""Unit + integration tests for code2e.agents.planner.

- helper: parse_prompt_file / render_prompt
- integration: PlannerAgent.invoke 가 실제 프롬프트 파일을 읽고 LlmGateway 로 호출,
  MockProvider 응답으로부터 Plan 합성. (LLM 실호출 0)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import ClassVar

import pytest
import structlog

from code2e.agents.base import InvocationContext
from code2e.agents.planner import (
    DEFAULT_PROMPTS_DIR,
    TEMPERATURE_BY_ROUND,
    PlannerAgent,
    PlannerLlmOutput,
    parse_prompt_file,
    render_prompt,
)
from code2e.core.budget import BudgetTracker
from code2e.core.cassette import CassetteStore
from code2e.core.llm_gateway import LlmGateway
from code2e.core.schemas import Plan, PlanMeta, PlannerInput

# ---------- helper tests ----------


def test_render_prompt_replaces_simple_placeholder() -> None:
    out = render_prompt("hello {name}", name="world")
    assert out == "hello world"


def test_render_prompt_keeps_unrelated_braces_intact() -> None:
    """str.format 이라면 KeyError. replace 는 안전."""
    out = render_prompt('schema: {"x": 1} input: {user_input}', user_input="hi")
    assert out == 'schema: {"x": 1} input: hi'


def test_render_prompt_repeats_substitution() -> None:
    out = render_prompt("{x}-{x}", x="a")
    assert out == "a-a"


def test_parse_prompt_file_extracts_system_and_user(tmp_path: Path) -> None:
    p = tmp_path / "p.md"
    p.write_text(
        "---\nagent: x\n---\n[system]\nyou are X.\n[user]\ninput: {q}\n",
        encoding="utf-8",
    )
    system, user = parse_prompt_file(p)
    assert system == "you are X."
    assert user == "input: {q}"


def test_parse_prompt_file_missing_tags_raises(tmp_path: Path) -> None:
    p = tmp_path / "p.md"
    p.write_text("just plain text, no tags", encoding="utf-8")
    with pytest.raises(ValueError, match=r"\[system\]"):
        parse_prompt_file(p)


def test_parse_prompt_file_works_without_frontmatter(tmp_path: Path) -> None:
    p = tmp_path / "p.md"
    p.write_text("[system]\nS\n[user]\nU", encoding="utf-8")
    sys_text, user_text = parse_prompt_file(p)
    assert sys_text == "S"
    assert user_text == "U"


# ---------- real prompt files exist + parseable ----------


def test_real_prompt_files_parse_for_all_three_rounds() -> None:
    """src/code2e/prompts/planner_round_{1,2,3}.md 가 모두 존재하고 parse 가능."""
    for n in (1, 2, 3):
        path = DEFAULT_PROMPTS_DIR / f"planner_round_{n}.md"
        assert path.exists(), f"missing: {path}"
        system, user = parse_prompt_file(path)
        assert "Planner" in system
        assert "{user_input}" in user
    # round 2/3 은 prev_plan placeholder 도 포함.
    for n in (2, 3):
        _, user = parse_prompt_file(DEFAULT_PROMPTS_DIR / f"planner_round_{n}.md")
        assert "{prev_plan}" in user


# ---------- integration ----------


@dataclass
class _MockProvider:
    """test_llm_gateway 와 동일 패턴. 큐 순서대로 응답."""

    name: ClassVar[str] = "mock"
    responses: list[dict[str, object] | Exception] = field(default_factory=list)
    calls: list[dict[str, object]] = field(default_factory=list)
    _iter: Iterator[dict[str, object] | Exception] = field(init=False)

    def __post_init__(self) -> None:
        self._iter = iter(self.responses)

    async def call(
        self,
        model: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "model": model,
                "system": system_prompt,
                "messages": list(messages),
                "temp": temperature,
            }
        )
        item = next(self._iter)
        if isinstance(item, Exception):
            raise item
        return item

    def estimate_cost(self, tokens_in: int, tokens_out: int, model: str) -> float:
        return 0.0


def _resp(payload: dict[str, object]) -> dict[str, object]:
    return {
        "text": json.dumps(payload),
        "tokens_in": 10,
        "tokens_out": 5,
        "raw": {},
    }


def _ctx_with(provider: _MockProvider, tmp_path: Path) -> tuple[InvocationContext, BudgetTracker]:
    cassette = CassetteStore(name="planner-test", dir=tmp_path, mode="auto")
    budget = BudgetTracker(limit_usd=10.0, limit_tokens=100_000)
    gateway = LlmGateway(provider=provider, cassette=cassette, budget=budget)
    ctx = InvocationContext(
        trace_id="t-1",
        attempt=0,
        budget=budget,
        cancel_token=asyncio.Event(),
        logger=structlog.get_logger("test"),
        llm=gateway,
    )
    return ctx, budget


@pytest.mark.asyncio
async def test_planner_round_1_returns_plan_with_version_1(tmp_path: Path) -> None:
    provider = _MockProvider(
        responses=[_resp({"content": "## Goal\nbuild a todo CLI", "units": []})]
    )
    ctx, _ = _ctx_with(provider, tmp_path)

    planner = PlannerAgent()
    plan = await planner.invoke(
        PlannerInput(user_input="build a todo CLI", round=1),
        ctx,
    )

    assert isinstance(plan, Plan)
    assert plan.version == 1
    assert plan.content == "## Goal\nbuild a todo CLI"
    assert plan.units == []
    assert isinstance(plan.meta, PlanMeta)
    assert isinstance(plan.meta.created_at, datetime)


@pytest.mark.asyncio
async def test_planner_round_1_uses_temperature_0_7(tmp_path: Path) -> None:
    provider = _MockProvider(responses=[_resp({"content": "x", "units": []})])
    ctx, _ = _ctx_with(provider, tmp_path)
    planner = PlannerAgent()
    await planner.invoke(PlannerInput(user_input="q", round=1), ctx)
    assert provider.calls[0]["temp"] == TEMPERATURE_BY_ROUND[1] == 0.7


@pytest.mark.asyncio
async def test_planner_round_2_includes_prev_plan_in_user_message(tmp_path: Path) -> None:
    prev = Plan(
        version=1,
        content="## Round 1 plan body",
        units=[],
        meta=PlanMeta(
            created_at=datetime.fromtimestamp(0),
            tokens_in=0,
            tokens_out=0,
        ),
    )
    provider = _MockProvider(responses=[_resp({"content": "## Refined", "units": []})])
    ctx, _ = _ctx_with(provider, tmp_path)
    planner = PlannerAgent()

    plan = await planner.invoke(
        PlannerInput(user_input="task", prev_plan=prev, round=2),
        ctx,
    )

    assert plan.version == 2
    user_content = provider.calls[0]["messages"][0]["content"]  # type: ignore[index]
    assert "## Round 1 plan body" in user_content  # type: ignore[operator]
    assert "task" in user_content  # type: ignore[operator]
    assert provider.calls[0]["temp"] == TEMPERATURE_BY_ROUND[2] == 0.3


@pytest.mark.asyncio
async def test_planner_round_3_returns_units(tmp_path: Path) -> None:
    units_payload = [
        {
            "id": "U-001",
            "title": "scaffold",
            "description": "create project skeleton",
            "acceptance_criteria": ["compiles"],
            "dependencies": [],
            "estimated_complexity": "low",
        },
        {
            "id": "U-002",
            "title": "api",
            "description": "REST endpoints",
            "acceptance_criteria": ["GET / returns 200"],
            "dependencies": ["U-001"],
            "estimated_complexity": "med",
        },
    ]
    prev = Plan(
        version=2,
        content="## Round 2 plan",
        units=[],
        meta=PlanMeta(
            created_at=datetime.fromtimestamp(0),
            tokens_in=0,
            tokens_out=0,
        ),
    )
    provider = _MockProvider(
        responses=[_resp({"content": "## Final\nbody", "units": units_payload})]
    )
    ctx, _ = _ctx_with(provider, tmp_path)
    planner = PlannerAgent()

    plan = await planner.invoke(
        PlannerInput(user_input="todo app", prev_plan=prev, round=3),
        ctx,
    )

    assert plan.version == 3
    assert len(plan.units) == 2
    assert plan.units[0].id == "U-001"
    assert plan.units[1].dependencies == ["U-001"]


@pytest.mark.asyncio
async def test_planner_uses_custom_model(tmp_path: Path) -> None:
    provider = _MockProvider(responses=[_resp({"content": "x", "units": []})])
    ctx, _ = _ctx_with(provider, tmp_path)
    planner = PlannerAgent(model="claude-haiku-4-5")
    await planner.invoke(PlannerInput(user_input="q", round=1), ctx)
    assert provider.calls[0]["model"] == "claude-haiku-4-5"


@pytest.mark.asyncio
async def test_planner_repair_path_on_invalid_json_then_success(tmp_path: Path) -> None:
    """LLM 이 1회 invalid 출력 → repair 1 회로 성공."""
    provider = _MockProvider(
        responses=[
            _resp({"NO_content_field": "wrong"}),  # PlannerLlmOutput 검증 실패
            _resp({"content": "## fixed", "units": []}),  # repair 1 success
        ]
    )
    ctx, _ = _ctx_with(provider, tmp_path)
    planner = PlannerAgent()
    plan = await planner.invoke(PlannerInput(user_input="q", round=1), ctx)
    assert plan.content == "## fixed"
    assert len(provider.calls) == 2  # 원본 + repair 1


def test_planner_llm_output_validates_units_schema() -> None:
    """PlannerLlmOutput 의 units 가 PlanUnit 검증을 통과해야 한다."""
    out = PlannerLlmOutput.model_validate(
        {
            "content": "x",
            "units": [
                {
                    "id": "U-001",
                    "title": "t",
                    "description": "d",
                    "acceptance_criteria": ["c"],
                }
            ],
        }
    )
    assert out.units[0].id == "U-001"
