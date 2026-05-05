"""Unit + integration tests for code2e.agents.executor."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pytest
import structlog

from code2e.agents.base import InvocationContext
from code2e.agents.executor import (
    DEFAULT_PROMPTS_DIR,
    TEMPERATURE,
    ExecutorAgent,
    ExecutorLlmOutput,
)
from code2e.core.budget import BudgetTracker
from code2e.core.cassette import CassetteStore
from code2e.core.llm_gateway import LlmGateway
from code2e.core.schemas import (
    AdvisorFeedback,
    CodeChange,
    ExecutorInput,
    FeedbackComment,
    FileEdit,
    PlanUnit,
    RegressionContext,
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
            {"model": model, "system": system_prompt, "messages": list(messages), "temp": temperature}
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
    cassette = CassetteStore(name="exec-t", dir=tmp_path, mode="auto")
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


def _unit() -> PlanUnit:
    return PlanUnit(
        id="U-001",
        title="scaffold",
        description="create skeleton",
        acceptance_criteria=["compiles"],
    )


_HAPPY_RESPONSE = {
    "files": [
        {"path": "main.py", "op": "create", "content": "print('hello')"},
    ],
    "rationale": "create entry point",
}


# ---------- prompt file ----------


def test_real_executor_prompt_exists_and_parses() -> None:
    from code2e.agents.planner import parse_prompt_file

    path = DEFAULT_PROMPTS_DIR / "executor.md"
    assert path.exists(), f"missing: {path}"
    system, user = parse_prompt_file(path)
    assert "Executor" in system
    for placeholder in ("{unit}", "{files}", "{feedback}", "{test_failure}", "{regression_context}"):
        assert placeholder in user


# ---------- ExecutorLlmOutput schema ----------


def test_executor_llm_output_validates() -> None:
    out = ExecutorLlmOutput.model_validate(_HAPPY_RESPONSE)
    assert out.rationale == "create entry point"
    assert out.files[0].path == "main.py"


# ---------- integration ----------


@pytest.mark.asyncio
async def test_executor_scaffold_returns_codechange(tmp_path: Path) -> None:
    """feedback / test_failure / regression 모두 None → scaffold 모드."""
    provider = _MockProvider(responses=[_resp(_HAPPY_RESPONSE)])
    ctx = _ctx_with(provider, tmp_path)

    executor = ExecutorAgent()
    result = await executor.invoke(
        ExecutorInput(unit=_unit(), files=[]),
        ctx,
    )

    assert isinstance(result, CodeChange)
    assert result.unit_id == "U-001"  # invoke 가 inp.unit.id 로 자동 설정.
    assert result.rationale == "create entry point"
    assert len(result.files) == 1
    assert result.files[0].path == "main.py"


@pytest.mark.asyncio
async def test_executor_uses_temperature_0_2(tmp_path: Path) -> None:
    provider = _MockProvider(responses=[_resp(_HAPPY_RESPONSE)])
    ctx = _ctx_with(provider, tmp_path)
    executor = ExecutorAgent()
    await executor.invoke(ExecutorInput(unit=_unit(), files=[]), ctx)
    assert provider.calls[0]["temp"] == TEMPERATURE == 0.2


@pytest.mark.asyncio
async def test_executor_revise_includes_feedback_in_user_message(tmp_path: Path) -> None:
    provider = _MockProvider(responses=[_resp(_HAPPY_RESPONSE)])
    ctx = _ctx_with(provider, tmp_path)
    executor = ExecutorAgent()

    feedback = AdvisorFeedback(
        unit_id="U-001",
        decision="revise",
        comments=[FeedbackComment(message="MARKER-FEEDBACK-XYZ")],
        signature="sig123",
    )
    await executor.invoke(
        ExecutorInput(unit=_unit(), files=[], feedback=feedback),
        ctx,
    )

    user_content = provider.calls[0]["messages"][0]["content"]  # type: ignore[index]
    assert "MARKER-FEEDBACK-XYZ" in user_content  # type: ignore[operator]


@pytest.mark.asyncio
async def test_executor_omits_feedback_when_none(tmp_path: Path) -> None:
    """feedback=None → user_message 에 '(none)' 또는 placeholder 가 들어가야 함."""
    provider = _MockProvider(responses=[_resp(_HAPPY_RESPONSE)])
    ctx = _ctx_with(provider, tmp_path)
    executor = ExecutorAgent()
    await executor.invoke(ExecutorInput(unit=_unit(), files=[]), ctx)
    user_content = provider.calls[0]["messages"][0]["content"]  # type: ignore[index]
    assert "(none)" in user_content  # type: ignore[operator]


@pytest.mark.asyncio
async def test_executor_includes_regression_context(tmp_path: Path) -> None:
    provider = _MockProvider(responses=[_resp(_HAPPY_RESPONSE)])
    ctx = _ctx_with(provider, tmp_path)
    executor = ExecutorAgent()

    rc = RegressionContext(
        previously_passing_case_ids=["T-007", "T-009"],
        note="MARKER-REGRESSION-NOTE",
    )
    await executor.invoke(
        ExecutorInput(unit=_unit(), files=[], regression_context=rc),
        ctx,
    )

    user_content = provider.calls[0]["messages"][0]["content"]  # type: ignore[index]
    assert "T-007" in user_content  # type: ignore[operator]
    assert "MARKER-REGRESSION-NOTE" in user_content  # type: ignore[operator]


@pytest.mark.asyncio
async def test_executor_passes_existing_files_in_user_message(tmp_path: Path) -> None:
    provider = _MockProvider(responses=[_resp(_HAPPY_RESPONSE)])
    ctx = _ctx_with(provider, tmp_path)
    executor = ExecutorAgent()

    files = [
        FileEdit(path="existing.py", op="update", content="MARKER-EXISTING-CONTENT"),
    ]
    await executor.invoke(ExecutorInput(unit=_unit(), files=files), ctx)
    user_content = provider.calls[0]["messages"][0]["content"]  # type: ignore[index]
    assert "existing.py" in user_content  # type: ignore[operator]
    assert "MARKER-EXISTING-CONTENT" in user_content  # type: ignore[operator]
