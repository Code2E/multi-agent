"""Unit + integration tests for code2e.agents.advisor."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pytest
import structlog

from code2e.agents.advisor import (
    DEFAULT_PROMPTS_DIR,
    TEMPERATURE,
    AdvisorAgent,
    AdvisorLlmOutput,
)
from code2e.agents.base import InvocationContext
from code2e.core.budget import BudgetTracker
from code2e.core.cassette import CassetteStore
from code2e.core.llm_gateway import LlmGateway
from code2e.core.schemas import (
    AdvisorFeedback,
    AdvisorInput,
    FeedbackComment,
    FileEdit,
    PlanUnit,
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
    # mode='off' — 같은 tmp_path 를 공유하는 다중 호출이 cassette hit 으로 응답을 재사용
    # 하는 것을 막는다. signature 비교 테스트는 LLM 응답 차이를 검증하므로 cassette 우회.
    cassette = CassetteStore(name="adv-t", dir=tmp_path, mode="off")
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


def _unit(uid: str = "U-001") -> PlanUnit:
    return PlanUnit(
        id=uid,
        title="t",
        description="d",
        acceptance_criteria=["x"],
    )


def _files() -> list[FileEdit]:
    return [FileEdit(path="main.py", op="create", content="print('hi')")]


# ---------- prompt + schema ----------


def test_real_advisor_prompt_exists_and_parses() -> None:
    from code2e.agents.planner import parse_prompt_file

    path = DEFAULT_PROMPTS_DIR / "advisor.md"
    assert path.exists()
    system, user = parse_prompt_file(path)
    assert "Advisor" in system
    for placeholder in ("{unit}", "{code}", "{prior_feedback}"):
        assert placeholder in user


def test_advisor_llm_output_validates_approve() -> None:
    out = AdvisorLlmOutput.model_validate({"decision": "approve", "comments": []})
    assert out.decision == "approve"
    assert out.severity == "low"  # default


def test_advisor_llm_output_rejects_invalid_decision() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AdvisorLlmOutput.model_validate({"decision": "maybe"})


# ---------- integration ----------


@pytest.mark.asyncio
async def test_advisor_approve_returns_feedback_with_signature(tmp_path: Path) -> None:
    provider = _MockProvider(
        responses=[_resp({"decision": "approve", "severity": "low", "comments": []})]
    )
    ctx = _ctx_with(provider, tmp_path)
    advisor = AdvisorAgent()

    fb = await advisor.invoke(
        AdvisorInput(unit=_unit(), code=_files()),
        ctx,
    )

    assert isinstance(fb, AdvisorFeedback)
    assert fb.unit_id == "U-001"  # invoke 가 자동 설정.
    assert fb.decision == "approve"
    assert fb.comments == []
    assert fb.signature  # signature_fn 으로 자동 생성됨.
    assert len(fb.signature) == 16  # signature_fn 은 sha256 앞 16자.


@pytest.mark.asyncio
async def test_advisor_uses_temperature_0_3(tmp_path: Path) -> None:
    provider = _MockProvider(responses=[_resp({"decision": "approve", "comments": []})])
    ctx = _ctx_with(provider, tmp_path)
    advisor = AdvisorAgent()
    await advisor.invoke(AdvisorInput(unit=_unit(), code=_files()), ctx)
    assert provider.calls[0]["temp"] == TEMPERATURE == 0.3


@pytest.mark.asyncio
async def test_advisor_revise_preserves_comments(tmp_path: Path) -> None:
    provider = _MockProvider(
        responses=[
            _resp(
                {
                    "decision": "revise",
                    "severity": "med",
                    "comments": [
                        {"file": "main.py", "line": 5, "message": "missing return", "suggestion": "add return None"},
                        {"message": "rename foo to bar"},
                    ],
                }
            )
        ]
    )
    ctx = _ctx_with(provider, tmp_path)
    advisor = AdvisorAgent()
    fb = await advisor.invoke(AdvisorInput(unit=_unit(), code=_files()), ctx)

    assert fb.decision == "revise"
    assert fb.severity == "med"
    assert len(fb.comments) == 2
    assert fb.comments[0].message == "missing return"
    assert fb.comments[1].file is None  # 선택 필드 누락 OK.


@pytest.mark.asyncio
async def test_advisor_signature_deterministic_for_same_input(tmp_path: Path) -> None:
    """동일 unit_id + decision + comments → 동일 signature (Q12)."""
    payload = {
        "decision": "revise",
        "comments": [{"message": "fix X"}, {"message": "add Y"}],
    }
    advisor = AdvisorAgent()

    p1 = _MockProvider(responses=[_resp(payload)])
    fb1 = await advisor.invoke(AdvisorInput(unit=_unit(), code=_files()), _ctx_with(p1, tmp_path))

    p2 = _MockProvider(responses=[_resp(payload)])
    fb2 = await advisor.invoke(AdvisorInput(unit=_unit(), code=_files()), _ctx_with(p2, tmp_path))

    assert fb1.signature == fb2.signature


@pytest.mark.asyncio
async def test_advisor_signature_differs_for_different_unit_id(tmp_path: Path) -> None:
    payload = {"decision": "approve", "comments": []}
    advisor = AdvisorAgent()

    p1 = _MockProvider(responses=[_resp(payload)])
    fb1 = await advisor.invoke(
        AdvisorInput(unit=_unit("U-001"), code=_files()),
        _ctx_with(p1, tmp_path),
    )
    p2 = _MockProvider(responses=[_resp(payload)])
    fb2 = await advisor.invoke(
        AdvisorInput(unit=_unit("U-002"), code=_files()),
        _ctx_with(p2, tmp_path),
    )
    assert fb1.signature != fb2.signature


@pytest.mark.asyncio
async def test_advisor_signature_differs_for_different_comments(tmp_path: Path) -> None:
    """comments 가 다르면 signature 도 다름 (stagnation 회피 로직 의도)."""
    advisor = AdvisorAgent()

    p1 = _MockProvider(
        responses=[_resp({"decision": "revise", "comments": [{"message": "fix A"}]})]
    )
    fb1 = await advisor.invoke(AdvisorInput(unit=_unit(), code=_files()), _ctx_with(p1, tmp_path))

    p2 = _MockProvider(
        responses=[_resp({"decision": "revise", "comments": [{"message": "fix B"}]})]
    )
    fb2 = await advisor.invoke(AdvisorInput(unit=_unit(), code=_files()), _ctx_with(p2, tmp_path))

    assert fb1.signature != fb2.signature


@pytest.mark.asyncio
async def test_advisor_includes_prior_feedback_in_user_message(tmp_path: Path) -> None:
    prior = [
        AdvisorFeedback(
            unit_id="U-001",
            decision="revise",
            comments=[FeedbackComment(message="MARKER-PRIOR-COMMENT")],
            signature="prev-sig",
        )
    ]
    provider = _MockProvider(responses=[_resp({"decision": "approve", "comments": []})])
    ctx = _ctx_with(provider, tmp_path)
    advisor = AdvisorAgent()
    await advisor.invoke(
        AdvisorInput(unit=_unit(), code=_files(), prior_feedback=prior),
        ctx,
    )
    user_content = provider.calls[0]["messages"][0]["content"]  # type: ignore[index]
    assert "MARKER-PRIOR-COMMENT" in user_content  # type: ignore[operator]


@pytest.mark.asyncio
async def test_advisor_no_prior_feedback_uses_placeholder(tmp_path: Path) -> None:
    provider = _MockProvider(responses=[_resp({"decision": "approve", "comments": []})])
    ctx = _ctx_with(provider, tmp_path)
    advisor = AdvisorAgent()
    await advisor.invoke(AdvisorInput(unit=_unit(), code=_files()), ctx)
    user_content = provider.calls[0]["messages"][0]["content"]  # type: ignore[index]
    assert "(none" in user_content  # "(none — 첫 라운드)"  # type: ignore[operator]
