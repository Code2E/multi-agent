"""LLM Gateway (v4 §3.9, §16, ADR-043, NFR-R-1).

파이프라인: cassette hit → budget check → provider call (retry) → strip fence
        → json parse → Pydantic validate → repair (max 2) → cassette record → budget add.

DECISION:
- ADR-043: Repair 2회 (REPAIR_MAX_ATTEMPTS).
- NFR-R-1: 5xx / timeout / 429 자동 retry, exp backoff, max 3 (LLM_RETRY_MAX).
- v4 §16.4: repair 차수도 cassette 키에 포함 → 정확한 재생.
- v4 §16.3: repair prompt 는 [system + user(원본)] + [assistant(invalid)] + [user(error+schema)].
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from code2e.core.budget import BudgetTracker
from code2e.core.cassette import (
    CASSETTE_SCHEMA_VERSION,
    CassetteStore,
    canonicalize,
    compute_key,
)
from code2e.core.schemas import CassetteEntry

REPAIR_MAX_ATTEMPTS = 2  # ADR-043
LLM_RETRY_MAX = 3  # NFR-R-1
LLM_RETRY_BASE_MS = 1000

# 응답 텍스트의 ```json … ``` 또는 ``` … ``` 코드 펜스 제거.
_CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)

logger = logging.getLogger(__name__)


# ---------- Provider Protocol ----------


@runtime_checkable
class LlmProvider(Protocol):
    """v4 §2.2 + §3.12: provider adapter 추상화."""

    name: str

    async def call(
        self,
        model: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, object]:
        """반환: {"text": str, "tokens_in": int, "tokens_out": int, "raw": dict}."""
        ...

    def estimate_cost(self, tokens_in: int, tokens_out: int, model: str) -> float: ...


# ---------- Errors ----------


class LlmTransientError(Exception):
    """5xx / 429 / timeout — retry 대상 (NFR-R-1)."""


class LlmPermanentError(Exception):
    """4xx (auth) — 즉시 실패."""


class CassetteMissError(Exception):
    """cassette mode='replay' 인데 hit 없음."""


class RepairExhaustedError(LlmPermanentError):
    """Pydantic 검증이 REPAIR_MAX_ATTEMPTS 회 모두 실패.

    Orchestrator 가 TerminationReason.VALIDATION_FAILURE 로 변환.
    """


# ---------- Helpers ----------


def strip_code_fence(text: str) -> str:
    """응답에서 ```json ... ``` 펜스 제거. 없으면 그대로 반환."""
    m = _CODE_FENCE_RE.match(text)
    return m.group(1) if m else text


def parse_json_lenient(text: str) -> object:
    """1차 strict parse, 실패 시 펜스 제거 후 1회 더 시도. 둘 다 실패면 raise."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(strip_code_fence(text))


def build_repair_messages(
    original_messages: list[dict[str, str]],
    invalid_text: str,
    error_msg: str,
    schema_json: str,
) -> list[dict[str, str]]:
    """v4 §16.3 repair template. system_prompt 는 호출자가 그대로 재사용한다."""
    repair_user = (
        "위 출력은 다음 검증에 실패했습니다:\n"
        f"{error_msg}\n\n"
        "동일한 작업을 정확한 JSON 스키마로 다시 출력하세요. "
        "코드 펜스 / 설명 텍스트 없이 JSON 만 반환하세요.\n\n"
        f"스키마:\n{schema_json}"
    )
    return [
        *original_messages,
        {"role": "assistant", "content": invalid_text},
        {"role": "user", "content": repair_user},
    ]


def estimate_tokens_in(system_prompt: str, messages: list[dict[str, str]]) -> int:
    """v1 단순 휴리스틱: 글자수 / 4. anthropic count_tokens API 는 추후 도입."""
    total_chars = len(system_prompt) + sum(len(m.get("content", "")) for m in messages)
    return max(1, total_chars // 4)


# ---------- Anthropic Provider ----------

# 모델별 USD/1M tokens. v1 가정값. 실제 가격 변동 시 갱신.
_ANTHROPIC_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-7": {"input": 15.0, "output": 75.0},
    "claude-haiku-4-5": {"input": 0.8, "output": 4.0},
}


@dataclass
class AnthropicProvider:
    """anthropic SDK 의 AsyncAnthropic 래퍼.

    실제 LLM 호출만 담당 — retry / cassette / budget 은 LlmGateway 가 처리.
    """

    api_key: str | None = None  # None 이면 ANTHROPIC_API_KEY env 사용 (SDK 디폴트)
    name: str = "anthropic"

    def _client(self) -> object:
        """Lazy init: 임포트 + 인스턴스화. 단위 테스트는 이 경로를 우회한다."""
        from anthropic import AsyncAnthropic  # noqa: PLC0415

        return AsyncAnthropic(api_key=self.api_key) if self.api_key else AsyncAnthropic()

    async def call(
        self,
        model: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, object]:
        from anthropic import APIStatusError  # noqa: PLC0415

        client = self._client()
        try:
            resp = await client.messages.create(  # type: ignore[union-attr]
                model=model,
                system=system_prompt,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except APIStatusError as e:
            status = getattr(e, "status_code", None)
            if status is not None and (status >= 500 or status == 429):
                raise LlmTransientError(f"anthropic transient: {status}") from e
            raise LlmPermanentError(f"anthropic permanent: {status}") from e

        # Response 에서 첫 텍스트 블록 추출.
        content = getattr(resp, "content", [])
        text = ""
        for block in content:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", "")
                break

        usage = getattr(resp, "usage", None)
        tokens_in = getattr(usage, "input_tokens", 0) if usage else 0
        tokens_out = getattr(usage, "output_tokens", 0) if usage else 0
        stop_reason = getattr(resp, "stop_reason", None)

        return {
            "text": text,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "stop_reason": stop_reason,
            "raw": {
                "model": getattr(resp, "model", model),
                "id": getattr(resp, "id", ""),
                "stop_reason": stop_reason,
            },
        }

    def estimate_cost(self, tokens_in: int, tokens_out: int, model: str) -> float:
        prices = _ANTHROPIC_PRICING.get(model)
        if prices is None:
            return 0.0
        return (tokens_in * prices["input"] + tokens_out * prices["output"]) / 1_000_000


# ---------- LlmGateway ----------


@dataclass
class LlmGateway:
    provider: LlmProvider
    cassette: CassetteStore
    budget: BudgetTracker
    max_tokens: int = 4096

    async def call(
        self,
        agent_name: str,
        agent_version: str,
        prompt_key: str,
        model: str,
        temperature: float,
        system_prompt: str,
        messages: list[dict[str, str]],
        output_model: type[BaseModel],
    ) -> BaseModel:
        """완전 파이프라인. 성공 시 검증된 BaseModel, 실패 시 raise.

        prompt_key: 프롬프트 파일 식별자 (e.g. 'planner_round_3_v1'). 같은 key 의
        프롬프트 내용이 바뀌면 호출자가 새 key 를 부여해 cassette 를 무효화한다.
        """
        canonical = canonicalize({"system": system_prompt, "messages": messages})

        invalid_history: list[tuple[str, str]] = []  # (text, error_msg) for repair attempts
        for repair_round in range(REPAIR_MAX_ATTEMPTS + 1):  # 0, 1, 2 — 총 3회 (원본 + repair 2회)
            key = compute_key(
                agent=agent_name,
                agent_version=agent_version,
                prompt_hash=prompt_key,
                canonical_input=canonical,
                model=model,
                temperature=temperature,
                repair_round=repair_round,
            )

            # 이번 round 의 messages 구성.
            if repair_round == 0:
                round_messages = messages
            else:
                last_text, last_err = invalid_history[-1]
                round_messages = build_repair_messages(
                    original_messages=messages,
                    invalid_text=last_text,
                    error_msg=last_err,
                    schema_json=json.dumps(output_model.model_json_schema()),
                )

            # 1) cassette hit?
            entry = self.cassette.try_hit(key) if self.cassette.mode != "off" else None
            if entry is not None:
                text = str(entry.response.get("text", ""))
                tokens_in: int = entry.tokens_in
                tokens_out: int = entry.tokens_out
                cost_usd: float = entry.cost_usd
                # cassette hit 도 누적: "이 run 이 LLM 에 부담시킨 가치" 추적.
                # 실제 청구는 0 이지만 metric / inspect / cost 명령 의미를 유지하기 위함.
                self.budget.add(tokens_in, tokens_out, cost_usd)
            else:
                if self.cassette.mode == "replay":
                    raise CassetteMissError(f"replay miss: agent={agent_name} key={key[:8]}")

                # 2) budget 사전 검사 (estimate 기반).
                est_tokens = estimate_tokens_in(system_prompt, round_messages)
                est_cost = self.provider.estimate_cost(est_tokens, self.max_tokens, model)
                self.budget.check_headroom(est_cost_usd=est_cost, est_tokens=est_tokens)

                # 3) provider 호출 (NFR-R-1 retry).
                resp = await self._call_with_retry(
                    model=model,
                    system_prompt=system_prompt,
                    messages=round_messages,
                    temperature=temperature,
                )
                text = str(resp.get("text", ""))
                raw_in = resp.get("tokens_in", 0)
                raw_out = resp.get("tokens_out", 0)
                tokens_in = raw_in if isinstance(raw_in, int) else 0
                tokens_out = raw_out if isinstance(raw_out, int) else 0
                cost_usd = self.provider.estimate_cost(tokens_in, tokens_out, model)
                # max_tokens 도달 감지: 출력이 한도에 막혀 JSON 이 잘렸을 가능성 ↑.
                # 다음 단계 (parse + validate) 가 실패할 확률이 높으니 사용자에게 미리 알림.
                if resp.get("stop_reason") == "max_tokens":
                    logger.warning(
                        "max_tokens reached (agent=%s, prompt_key=%s, tokens_out=%d) — "
                        "응답이 잘렸을 수 있습니다. units / cases 수를 줄이거나 "
                        "max_tokens 한도를 높이세요.",
                        agent_name,
                        prompt_key,
                        tokens_out,
                    )

                # 4) cassette record (mode=record/auto).
                if self.cassette.mode in {"record", "auto"}:
                    self.cassette.record(
                        CassetteEntry(
                            schema_version=CASSETTE_SCHEMA_VERSION,
                            key=key,
                            agent=agent_name,
                            agent_version=agent_version,
                            model_id=model,
                            request={
                                "system": system_prompt,
                                "messages": round_messages,
                                "temperature": temperature,
                                "max_tokens": self.max_tokens,
                                "repair_round": repair_round,
                            },
                            response={"text": text, "raw": resp.get("raw", {})},
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                            cost_usd=cost_usd,
                            recorded_at=datetime.now(UTC),
                        )
                    )

                # 5) budget 누적.
                self.budget.add(tokens_in, tokens_out, cost_usd)

            # 6) parse + validate.
            try:
                parsed = parse_json_lenient(text)
                return output_model.model_validate(parsed)
            except (json.JSONDecodeError, ValidationError) as e:
                invalid_history.append((text, str(e)))
                logger.warning(
                    "validation failed (round=%d, agent=%s): %s",
                    repair_round,
                    agent_name,
                    e,
                )
                continue

        # 모든 repair 소진 → RepairExhaustedError (Orchestrator 가 VALIDATION_FAILURE 로 변환).
        last_err = invalid_history[-1][1] if invalid_history else "unknown"
        raise RepairExhaustedError(
            f"agent={agent_name} repair max ({REPAIR_MAX_ATTEMPTS}) exhausted: {last_err}"
        )

    async def _call_with_retry(
        self,
        model: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        temperature: float,
    ) -> dict[str, object]:
        """NFR-R-1: exp backoff + jitter, max LLM_RETRY_MAX."""
        last_exc: Exception | None = None
        for attempt in range(LLM_RETRY_MAX):
            try:
                return await self.provider.call(
                    model=model,
                    system_prompt=system_prompt,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=self.max_tokens,
                )
            except LlmTransientError as e:
                last_exc = e
                if attempt == LLM_RETRY_MAX - 1:
                    break
                delay_ms = LLM_RETRY_BASE_MS * (2**attempt)
                jitter_ms = random.randint(0, delay_ms // 2)
                await asyncio.sleep((delay_ms + jitter_ms) / 1000.0)
                continue
            # LlmPermanentError 등은 그대로 전파.
        assert last_exc is not None
        raise last_exc
