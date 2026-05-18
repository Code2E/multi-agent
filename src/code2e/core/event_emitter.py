"""EventEmitter — Orchestrator 의 phase / agent 진행을 채팅 UI 같은 외부
관찰자에게 push 하기 위한 작은 in-process pubsub.

DECISION: v4 ADR-036 (batch + 결정성) 와 충돌 없음 — emitter 는 부수 효과,
파이프라인 흐름 변경 없음. emitter 가 None 이면 기존 동작 동일 (noop).

스레드/태스크 안전: asyncio.Queue 만 사용. 모든 emit 은 nowait — 호출자
(orchestrator) 가 절대 block 되지 않음. 구독자가 느려서 queue 가 차면
오래된 이벤트가 drop 되도록 max_size 지정 가능.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class Event:
    """SSE 로 직렬화 가능한 minimal payload.

    type 명명 규약: `<phase>.<sub>`  (예: planning.round.start, building.unit.pass)
    또는 `system.<sub>`  (run 시작/종료, error).
    data 는 JSON-serializable dict.
    """

    type: str
    data: dict[str, Any]
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data, "ts": self.ts.isoformat()}


class EventEmitter:
    """다수 subscriber 지원 (fan-out). 구독은 async generator 로.

    사용 흐름:
        emitter = EventEmitter()
        async for evt in emitter.subscribe():
            # SSE 로 stream
            ...

        # 다른 코루틴에서:
        emitter.emit("planning.round.start", {"round": 1})
    """

    def __init__(self, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._subscribers: list[asyncio.Queue[Event | None]] = []
        self._closed = False

    def emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """모든 subscriber 에 이벤트 push. queue 가 차면 drop (UI 가 느린 경우)."""
        if self._closed:
            return
        evt = Event(type=event_type, data=data or {})
        for q in self._subscribers:
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                # UI 가 느려 queue 가 찼음 — 가장 오래된 것 drop, 새 것 push.
                try:
                    q.get_nowait()
                    q.put_nowait(evt)
                except asyncio.QueueEmpty:
                    pass

    async def subscribe(self) -> AsyncIterator[Event]:
        """async generator. None sentinel 받으면 stream 종료."""
        q: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.append(q)
        try:
            while True:
                evt = await q.get()
                if evt is None:
                    return
                yield evt
        finally:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def close(self) -> None:
        """모든 subscriber 의 stream 을 우아하게 종료."""
        self._closed = True
        for q in self._subscribers:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
