"""Agent Protocol + InvocationContext (v4 §3.4 라인 422-441).

DECISION: Q10 — 에이전트는 stateless. 매 invoke 마다 InvocationContext 로 의존성 주입.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

import structlog
from pydantic import BaseModel

if TYPE_CHECKING:
    from code2e.core.budget import BudgetTracker
    from code2e.core.llm_gateway import LlmGateway


@dataclass
class InvocationContext:
    trace_id: str
    attempt: int
    budget: "BudgetTracker"
    cancel_token: asyncio.Event
    logger: structlog.stdlib.BoundLogger
    llm: "LlmGateway"


@runtime_checkable
class Agent(Protocol):
    name: ClassVar[str]
    version: ClassVar[str]
    InputModel: ClassVar[type[BaseModel]]
    OutputModel: ClassVar[type[BaseModel]]

    async def invoke(self, inp: BaseModel, ctx: InvocationContext) -> BaseModel: ...
