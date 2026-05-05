"""Unit + integration tests for code2e.agents.evaluator (testgen 만; testrun 은 별도)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
import structlog

from code2e.agents.base import InvocationContext
from code2e.agents.evaluator import (
    DEFAULT_PROMPTS_DIR,
    TESTGEN_TEMPERATURE,
    EvaluatorTestgenAgent,
    EvaluatorTestgenLlmOutput,
    EvaluatorTestrunAgent,
)
from code2e.core.budget import BudgetTracker
from code2e.core.cassette import CassetteStore
from code2e.core.llm_gateway import LlmGateway
from code2e.core.schemas import (
    EvaluatorTestgenInput,
    EvaluatorTestrunInput,
    Plan,
    PlanMeta,
    PlanUnit,
    TestCase,
)

# ---------- helpers ----------


@dataclass
class _MockProvider:
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
            {"model": model, "messages": list(messages), "temp": temperature}
        )
        item = next(self._iter)
        if isinstance(item, Exception):
            raise item
        return item

    def estimate_cost(self, tokens_in: int, tokens_out: int, model: str) -> float:
        return 0.0


def _resp(payload: dict[str, object]) -> dict[str, object]:
    return {"text": json.dumps(payload), "tokens_in": 10, "tokens_out": 5, "raw": {}}


def _ctx_with(provider: _MockProvider, tmp_path: Path) -> InvocationContext:
    cassette = CassetteStore(name="eval-t", dir=tmp_path, mode="off")
    budget = BudgetTracker(limit_usd=10.0, limit_tokens=100_000)
    gateway = LlmGateway(provider=provider, cassette=cassette, budget=budget)
    return InvocationContext(
        trace_id="t-1",
        attempt=0,
        budget=budget,
        cancel_token=asyncio.Event(),
        logger=structlog.get_logger("test"),
        llm=gateway,
    )


def _plan(units: list[PlanUnit] | None = None, content: str = "## Final\nbody") -> Plan:
    return Plan(
        version=3,
        content=content,
        units=units or [
            PlanUnit(
                id="U-001",
                title="scaffold",
                description="bootstrap",
                acceptance_criteria=["compiles"],
            )
        ],
        meta=PlanMeta(
            created_at=datetime(2026, 5, 5, tzinfo=UTC),
            tokens_in=0,
            tokens_out=0,
        ),
    )


_HAPPY_CASES_PAYLOAD = {
    "cases": [
        {
            "id": "T-001",
            "scenario": "homepage loads",
            "given": "server is up",
            "when": "user visits /",
            "then": "h1 shows 'Hello'",
            "runner_script": "await page.goto(BASE_URL + '/')\nawait expect(page.locator('h1')).to_have_text('Hello')",
            "plan_unit_refs": ["U-001"],
        },
        {
            "id": "T-002",
            "scenario": "create todo",
            "given": "homepage open",
            "when": "user submits form",
            "then": "todo appears in list",
            "runner_script": "await page.fill('input', 'buy milk')\nawait page.click('button')",
            "plan_unit_refs": ["U-001"],
        },
    ]
}


# ---------- prompt + schema ----------


def test_real_evaluator_testgen_prompt_exists_and_parses() -> None:
    from code2e.agents.planner import parse_prompt_file

    path = DEFAULT_PROMPTS_DIR / "evaluator_testgen.md"
    assert path.exists()
    system, user = parse_prompt_file(path)
    assert "Evaluator" in system
    for placeholder in ("{plan_content}", "{units}"):
        assert placeholder in user


def test_evaluator_testgen_llm_output_validates() -> None:
    out = EvaluatorTestgenLlmOutput.model_validate(_HAPPY_CASES_PAYLOAD)
    assert len(out.cases) == 2
    assert out.cases[0].id == "T-001"
    assert out.cases[0].plan_unit_refs == ["U-001"]


def test_evaluator_testgen_llm_output_rejects_missing_required_field() -> None:
    from pydantic import ValidationError

    bad = {"cases": [{"id": "T-1"}]}  # scenario / given / when / then / runner_script 누락
    with pytest.raises(ValidationError):
        EvaluatorTestgenLlmOutput.model_validate(bad)


# ---------- integration ----------


@pytest.mark.asyncio
async def test_testgen_returns_list_of_test_cases(tmp_path: Path) -> None:
    provider = _MockProvider(responses=[_resp(_HAPPY_CASES_PAYLOAD)])
    ctx = _ctx_with(provider, tmp_path)
    agent = EvaluatorTestgenAgent()

    cases = await agent.invoke(EvaluatorTestgenInput(final_plan=_plan()), ctx)

    assert isinstance(cases, list)
    assert len(cases) == 2
    assert all(isinstance(c, TestCase) for c in cases)
    assert cases[0].id == "T-001"
    assert cases[1].plan_unit_refs == ["U-001"]


@pytest.mark.asyncio
async def test_testgen_uses_temperature_0_2(tmp_path: Path) -> None:
    provider = _MockProvider(responses=[_resp(_HAPPY_CASES_PAYLOAD)])
    ctx = _ctx_with(provider, tmp_path)
    agent = EvaluatorTestgenAgent()
    await agent.invoke(EvaluatorTestgenInput(final_plan=_plan()), ctx)
    assert provider.calls[0]["temp"] == TESTGEN_TEMPERATURE == 0.2


@pytest.mark.asyncio
async def test_testgen_passes_plan_content_and_units_to_user_message(tmp_path: Path) -> None:
    units = [
        PlanUnit(
            id="U-001",
            title="MARKER-UNIT-TITLE",
            description="d",
            acceptance_criteria=["a"],
        ),
    ]
    plan = _plan(units=units, content="MARKER-PLAN-CONTENT")
    provider = _MockProvider(responses=[_resp(_HAPPY_CASES_PAYLOAD)])
    ctx = _ctx_with(provider, tmp_path)
    agent = EvaluatorTestgenAgent()
    await agent.invoke(EvaluatorTestgenInput(final_plan=plan), ctx)

    user_content = provider.calls[0]["messages"][0]["content"]  # type: ignore[index]
    assert "MARKER-PLAN-CONTENT" in user_content  # type: ignore[operator]
    assert "MARKER-UNIT-TITLE" in user_content  # type: ignore[operator]


@pytest.mark.asyncio
async def test_testgen_repair_path_on_invalid_then_success(tmp_path: Path) -> None:
    """LLM 1회 invalid → repair 1회로 성공."""
    provider = _MockProvider(
        responses=[
            _resp({"WRONG_FIELD": "bad"}),  # cases 키 없음 → repair
            _resp(_HAPPY_CASES_PAYLOAD),
        ]
    )
    ctx = _ctx_with(provider, tmp_path)
    agent = EvaluatorTestgenAgent()
    cases = await agent.invoke(EvaluatorTestgenInput(final_plan=_plan()), ctx)
    assert len(cases) == 2
    assert len(provider.calls) == 2  # 원본 + repair 1


# ---------- testrun stub ----------


@pytest.mark.asyncio
async def test_testrun_still_raises_not_implemented(tmp_path: Path) -> None:
    """testrun 은 Playwright 통합 시점까지 stub."""
    agent = EvaluatorTestrunAgent()
    ctx = _ctx_with(_MockProvider(), tmp_path)
    with pytest.raises(NotImplementedError):
        await agent.invoke(
            EvaluatorTestrunInput(workspace="/tmp/ws", suite=[], base_url=None),
            ctx,
        )
