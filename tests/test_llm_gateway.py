"""Unit tests for code2e.core.llm_gateway (v4 §3.9, §16, ADR-043, NFR-R-1).

MockProvider 로 LLM 실호출 없이 파이프라인 6단계 + retry + repair 시나리오 검증.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel

from code2e.core.budget import BudgetExceededError, BudgetTracker
from code2e.core.cassette import CassetteStore
from code2e.core.llm_gateway import (
    LLM_RETRY_MAX,
    REPAIR_MAX_ATTEMPTS,
    CassetteMissError,
    LlmGateway,
    LlmTransientError,
    RepairExhaustedError,
    build_repair_messages,
    estimate_tokens_in,
    parse_json_lenient,
    strip_code_fence,
)

# ---------- Test fixtures ----------


class _OutModel(BaseModel):
    value: int
    label: str


@dataclass
class _MockProvider:
    """미리 큐에 넣어둔 응답들을 순서대로 반환. 예외도 큐에 넣을 수 있다."""

    name: ClassVar[str] = "mock"
    responses: list[dict[str, object] | Exception] = field(default_factory=list)
    cost_per_call: float = 0.001
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
        return self.cost_per_call


def _resp(text: str, tokens_in: int = 10, tokens_out: int = 5) -> dict[str, object]:
    return {"text": text, "tokens_in": tokens_in, "tokens_out": tokens_out, "raw": {}}


def _gateway(
    tmp_path: Path,
    *,
    responses: list[dict[str, object] | Exception],
    mode: str = "auto",
    limit_usd: float = 1.0,
    cost_per_call: float = 0.001,
) -> tuple[LlmGateway, _MockProvider, CassetteStore, BudgetTracker]:
    provider = _MockProvider(responses=responses, cost_per_call=cost_per_call)
    cassette = CassetteStore(name="t", dir=tmp_path, mode=mode)  # type: ignore[arg-type]
    budget = BudgetTracker(limit_usd=limit_usd, limit_tokens=10_000)
    return LlmGateway(provider=provider, cassette=cassette, budget=budget), provider, cassette, budget


_DEFAULT_KW = {
    "agent_name": "planner",
    "agent_version": "1.0",
    "prompt_key": "planner_round_1_v1",
    "model": "claude-sonnet-4-6",
    "temperature": 0.7,
    "system_prompt": "you are planner",
    "messages": [{"role": "user", "content": "make a todo app"}],
    "output_model": _OutModel,
}


# ---------- helpers ----------


def test_strip_code_fence_removes_json_fence() -> None:
    assert strip_code_fence("```json\n{\"x\":1}\n```") == '{"x":1}'


def test_strip_code_fence_no_fence_passthrough() -> None:
    assert strip_code_fence('{"x":1}') == '{"x":1}'


def test_parse_json_lenient_handles_fenced() -> None:
    out = parse_json_lenient('```json\n{"x":1}\n```')
    assert out == {"x": 1}


def test_estimate_tokens_in_uses_char_div_4() -> None:
    n = estimate_tokens_in("hello world", [{"role": "user", "content": "hi"}])
    assert n == max(1, (len("hello world") + len("hi")) // 4)


def test_build_repair_messages_appends_assistant_and_user() -> None:
    out = build_repair_messages(
        original_messages=[{"role": "user", "content": "hi"}],
        invalid_text="not-json",
        error_msg="oops",
        schema_json='{"type":"object"}',
    )
    assert out[0]["role"] == "user"
    assert out[1]["role"] == "assistant"
    assert out[1]["content"] == "not-json"
    assert out[2]["role"] == "user"
    assert "oops" in out[2]["content"]
    assert '"type":"object"' in out[2]["content"]


# ---------- happy path ----------


@pytest.mark.asyncio
async def test_gateway_call_happy_path(tmp_path: Path) -> None:
    gw, provider, cassette, budget = _gateway(
        tmp_path, responses=[_resp('{"value": 42, "label": "ok"}')]
    )
    result = await gw.call(**_DEFAULT_KW)  # type: ignore[arg-type]
    assert isinstance(result, _OutModel)
    assert result.value == 42
    assert len(provider.calls) == 1
    assert budget.tokens_used == 15  # 10 + 5
    # cassette 가 record 됐는지.
    assert any((tmp_path / "t").glob("*.json"))


@pytest.mark.asyncio
async def test_gateway_call_strips_code_fence_from_response(tmp_path: Path) -> None:
    gw, _provider, _cassette, _budget = _gateway(
        tmp_path, responses=[_resp('```json\n{"value": 7, "label": "x"}\n```')]
    )
    result = await gw.call(**_DEFAULT_KW)  # type: ignore[arg-type]
    assert result.value == 7  # type: ignore[attr-defined]


# ---------- cassette modes ----------


@pytest.mark.asyncio
async def test_gateway_uses_cassette_hit_and_skips_provider(tmp_path: Path) -> None:
    """1회차: record. 2회차: hit (provider 호출 0회)."""
    gw1, p1, _c, _b = _gateway(tmp_path, responses=[_resp('{"value": 1, "label": "a"}')])
    await gw1.call(**_DEFAULT_KW)  # type: ignore[arg-type]
    assert len(p1.calls) == 1

    # 같은 cassette dir + 같은 입력 → 새 gateway 도 hit 만으로 처리.
    gw2, p2, _c2, b2 = _gateway(tmp_path, responses=[])  # provider 호출되면 StopIteration
    result = await gw2.call(**_DEFAULT_KW)  # type: ignore[arg-type]
    assert result.value == 1  # type: ignore[attr-defined]
    assert len(p2.calls) == 0  # provider 미호출
    # cassette hit 일 때도 budget 누적은 cassette entry 의 cost 를 더하지 않는다 (이미 기록된 비용).
    # 현재 구현: hit 시 budget.add 호출 안 함 → 검증.
    assert b2.usd_used == 0.0


@pytest.mark.asyncio
async def test_gateway_replay_mode_misses_raise(tmp_path: Path) -> None:
    gw, _p, _c, _b = _gateway(tmp_path, responses=[_resp('{}')], mode="replay")
    with pytest.raises(CassetteMissError):
        await gw.call(**_DEFAULT_KW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_gateway_off_mode_skips_cassette(tmp_path: Path) -> None:
    gw, _p, cassette, _b = _gateway(
        tmp_path, responses=[_resp('{"value": 1, "label": "a"}')], mode="off"
    )
    await gw.call(**_DEFAULT_KW)  # type: ignore[arg-type]
    # mode='off' → record 안 함.
    assert not any((tmp_path / "t").glob("*.json"))


# ---------- repair (ADR-043, max 2) ----------


@pytest.mark.asyncio
async def test_repair_succeeds_on_first_attempt(tmp_path: Path) -> None:
    gw, provider, _c, _b = _gateway(
        tmp_path,
        responses=[
            _resp("not json at all"),  # 원본 실패
            _resp('{"value": 5, "label": "ok"}'),  # repair 1 성공
        ],
    )
    result = await gw.call(**_DEFAULT_KW)  # type: ignore[arg-type]
    assert result.value == 5  # type: ignore[attr-defined]
    assert len(provider.calls) == 2
    # repair 호출의 messages 에 assistant turn (invalid) + user (error) 가 포함.
    repair_msgs = provider.calls[1]["messages"]
    assert any(m["role"] == "assistant" for m in repair_msgs)  # type: ignore[index, union-attr]


@pytest.mark.asyncio
async def test_repair_succeeds_on_second_attempt(tmp_path: Path) -> None:
    gw, provider, _c, _b = _gateway(
        tmp_path,
        responses=[
            _resp("garbage 1"),
            _resp("garbage 2"),
            _resp('{"value": 9, "label": "third try"}'),
        ],
    )
    result = await gw.call(**_DEFAULT_KW)  # type: ignore[arg-type]
    assert result.value == 9  # type: ignore[attr-defined]
    assert len(provider.calls) == 3  # 원본 + repair 1 + repair 2


@pytest.mark.asyncio
async def test_repair_exhausted_raises_repair_exhausted_error(tmp_path: Path) -> None:
    gw, provider, _c, _b = _gateway(
        tmp_path,
        responses=[_resp("g1"), _resp("g2"), _resp("g3")],
    )
    with pytest.raises(RepairExhaustedError):
        await gw.call(**_DEFAULT_KW)  # type: ignore[arg-type]
    # 시도 횟수 = 1 (원본) + REPAIR_MAX_ATTEMPTS (2).
    assert len(provider.calls) == REPAIR_MAX_ATTEMPTS + 1


@pytest.mark.asyncio
async def test_pydantic_validation_failure_triggers_repair(tmp_path: Path) -> None:
    """JSON 은 valid 하지만 schema 가 안 맞으면 repair."""
    gw, provider, _c, _b = _gateway(
        tmp_path,
        responses=[
            _resp('{"value": "not_an_int"}'),  # type 위반
            _resp('{"value": 1, "label": "fixed"}'),
        ],
    )
    result = await gw.call(**_DEFAULT_KW)  # type: ignore[arg-type]
    assert result.value == 1  # type: ignore[attr-defined]
    assert len(provider.calls) == 2


# ---------- retry (NFR-R-1) ----------


@pytest.mark.asyncio
async def test_provider_retries_on_transient_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """5xx 2회 → 3번째 성공. retry 동안 sleep 은 monkeypatch 로 0 처리."""

    async def _no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)

    gw, provider, _c, _b = _gateway(
        tmp_path,
        responses=[
            LlmTransientError("503"),
            LlmTransientError("503"),
            _resp('{"value": 1, "label": "ok"}'),
        ],
    )
    result = await gw.call(**_DEFAULT_KW)  # type: ignore[arg-type]
    assert result.value == 1  # type: ignore[attr-defined]
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_provider_retry_exhausted_raises_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)

    gw, provider, _c, _b = _gateway(
        tmp_path,
        responses=[LlmTransientError("503") for _ in range(LLM_RETRY_MAX + 1)],
    )
    with pytest.raises(LlmTransientError):
        await gw.call(**_DEFAULT_KW)  # type: ignore[arg-type]
    assert len(provider.calls) == LLM_RETRY_MAX


# ---------- budget ----------


@pytest.mark.asyncio
async def test_budget_check_headroom_blocks_call(tmp_path: Path) -> None:
    """estimate 가 한도 초과면 provider 호출 전에 raise."""
    gw, provider, _c, _b = _gateway(
        tmp_path,
        responses=[_resp("ignored")],
        limit_usd=0.0001,
        cost_per_call=10.0,  # estimate * 1 = 10 → 한도 초과
    )
    with pytest.raises(BudgetExceededError):
        await gw.call(**_DEFAULT_KW)  # type: ignore[arg-type]
    assert len(provider.calls) == 0  # provider 호출 안 됨


@pytest.mark.asyncio
async def test_budget_accumulates_after_successful_call(tmp_path: Path) -> None:
    gw, _p, _c, budget = _gateway(
        tmp_path,
        responses=[_resp('{"value": 1, "label": "ok"}', tokens_in=100, tokens_out=50)],
        cost_per_call=0.05,
    )
    await gw.call(**_DEFAULT_KW)  # type: ignore[arg-type]
    assert budget.tokens_used == 150
    assert budget.usd_used == pytest.approx(0.05)


# ---------- cassette key 안정성 ----------


@pytest.mark.asyncio
async def test_repair_round_changes_cassette_key(tmp_path: Path) -> None:
    """repair 차수가 다르면 cassette 파일이 별도로 기록 (v4 §16.4)."""
    gw, _p, _c, _b = _gateway(
        tmp_path,
        responses=[_resp("garbage"), _resp('{"value": 1, "label": "ok"}')],
    )
    await gw.call(**_DEFAULT_KW)  # type: ignore[arg-type]
    files = list((tmp_path / "t").glob("*.json"))
    # 원본 round (실패한 응답) + repair round (성공) 둘 다 record 됨.
    assert len(files) == 2
    keys = {f.name.split(".")[1] for f in files}
    assert len(keys) == 2  # 다른 key 8자
