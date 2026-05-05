"""PortAllocator (v4 §9.5).

socket.bind 시도로 free 포트 탐색. 동시 acquire 는 asyncio.Lock 으로 보호.
hint 충돌 시 1 회 재시도 → 실패하면 PORT_UNAVAILABLE.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class PortAllocator:
    range_: tuple[int, int]
    reserved: set[int] = field(default_factory=set)
    _held: set[int] = field(default_factory=set)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self, hint: int | None = None) -> int:
        raise NotImplementedError("PortAllocator.acquire — phase 2 구현 예정")

    async def release(self, port: int) -> None:
        raise NotImplementedError("PortAllocator.release — phase 2 구현 예정")
