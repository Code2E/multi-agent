"""LLM Gateway (v4 §3.9, §16).

파이프라인: cassette hit → budget check → provider call → Pydantic validate
        → repair (max 2, ADR-043) → cassette record → budget add.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from code2e.core.budget import BudgetTracker
from code2e.core.cassette import CassetteStore

REPAIR_MAX_ATTEMPTS = 2  # ADR-043


@runtime_checkable
class LlmProvider(Protocol):
    """v4 §2.2 + §3.12: provider adapter 추상화 (Anthropic 1차)."""

    name: str

    async def call(
        self, model: str, messages: list[dict[str, object]], temperature: float
    ) -> dict[str, object]: ...

    def estimate_cost(self, tokens_in: int, tokens_out: int, model: str) -> float: ...


@dataclass
class LlmGateway:
    provider: LlmProvider
    cassette: CassetteStore
    budget: BudgetTracker

    async def call(
        self,
        prompt_key: str,
        agent_name: str,
        agent_version: str,
        model: str,
        temperature: float,
        messages: list[dict[str, object]],
        output_model: type[BaseModel],
    ) -> BaseModel:
        raise NotImplementedError("LlmGateway.call — phase 2 구현 예정 (v4 §3.9 파이프라인)")
