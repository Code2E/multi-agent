"""Hook system (v4 §3.11, ADR-022, FR-022).

DECISION: Q25 — read-only. hook 은 state 를 변이할 수 없다 (관찰만).
5s 타임아웃, 에러 격리 (한 hook 의 실패가 다른 hook 에 영향 X).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal, Protocol, runtime_checkable

HookEvent = Literal[
    "phase_started",
    "phase_ended",
    "agent_invoked",
    "agent_completed",
    "loop_iteration",
    "termination",
]

HOOK_TIMEOUT_S = 5


@dataclass
class HookEventPayload:
    event: HookEvent
    run_id: str
    phase: str
    data: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class Hook(Protocol):
    name: ClassVar[str]
    on: ClassVar[list[HookEvent]]

    async def handle(self, event: HookEventPayload) -> None: ...


@dataclass
class HookLoader:
    hooks_dir: Path

    def discover(self) -> list[Hook]:
        """hooks/*.py 자동 로드 (importlib). Protocol 검증 + read-only 강제."""
        raise NotImplementedError("HookLoader.discover — phase 2 구현 예정")


@dataclass
class EventBus:
    """asyncio.Queue 기반 in-process bus (v4 §3.7)."""

    hooks: list[Hook] = field(default_factory=list)
    _queue: asyncio.Queue[HookEventPayload] = field(default_factory=asyncio.Queue)

    async def publish(self, event: HookEventPayload) -> None:
        raise NotImplementedError("EventBus.publish — phase 2 구현 예정")

    async def dispatch_loop(self) -> None:
        """백그라운드 task: queue 에서 꺼내 hook 들에게 5s 타임아웃 + 격리로 fan-out."""
        raise NotImplementedError("EventBus.dispatch_loop — phase 2 구현 예정")
