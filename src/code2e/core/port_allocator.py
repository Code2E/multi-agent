"""PortAllocator — localhost 포트 자동 할당 (v4 §9.5, Q41 PORT_UNAVAILABLE).

socket.bind 시도로 free 포트 탐색. asyncio.Lock 으로 동시 acquire 보호.
hint 충돌 / range 고갈 시 PortUnavailableError raise (Orchestrator 가
TerminationReason.PORT_UNAVAILABLE 로 변환).
"""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass, field


class PortUnavailableError(Exception):
    """range 안에 free port 없음, 또는 hint 가 사용 불가."""


@dataclass
class PortAllocator:
    range_: tuple[int, int]  # inclusive [start, end].
    reserved: set[int] = field(default_factory=set)
    _held: set[int] = field(default_factory=set)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self, hint: int | None = None) -> int:
        """free port 반환. hint 가 사용 가능하면 우선. 없으면 range 순회."""
        async with self._lock:
            if (
                hint is not None
                and hint not in self._held
                and hint not in self.reserved
                and _is_free(hint)
            ):
                self._held.add(hint)
                return hint

            start, end = self.range_
            for port in range(start, end + 1):
                if port in self._held or port in self.reserved:
                    continue
                if _is_free(port):
                    self._held.add(port)
                    return port

            raise PortUnavailableError(
                f"no free port in range {self.range_} "
                f"(held={len(self._held)}, reserved={len(self.reserved)})"
            )

    async def release(self, port: int) -> None:
        async with self._lock:
            self._held.discard(port)


def _is_free(port: int) -> bool:
    """127.0.0.1:port 에 bind 시도 — 성공 시 free.

    LISTEN 안 하므로 TIME_WAIT 영향 없음. SO_REUSEADDR 미사용 — 정확하게
    "지금 누군가 bind 중인지" 만 확인.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        return False
    else:
        return True
    finally:
        s.close()
