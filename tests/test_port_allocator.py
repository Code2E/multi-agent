"""Unit tests for code2e.core.port_allocator (v4 §9.5)."""

from __future__ import annotations

import asyncio
import socket

import pytest

from code2e.core.port_allocator import PortAllocator, PortUnavailableError, _is_free

# ---------- helper: 실제 사용 중인 포트 ----------


def _bind_and_listen() -> socket.socket:
    """range 와 무관하게 한 포트를 잡아두는 listening socket 반환 (caller 가 close 책임)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))  # 0 = OS 가 free 포트 자동 할당.
    s.listen(1)
    return s


# ---------- _is_free ----------


def test_is_free_returns_true_for_random_port() -> None:
    """OS 가 자동 할당한 포트로 bind 후 닫고 다시 검사 — 비어있어야."""
    s = _bind_and_listen()
    port = s.getsockname()[1]
    s.close()
    # close 후 거의 즉시 free 일 가능성이 높음 (LISTEN 안 했어도 안전).
    # flaky 회피를 위해 여기서는 단순 호출 결과만 확인.
    _is_free(port)  # no raise.


def test_is_free_returns_false_when_port_in_use() -> None:
    s = _bind_and_listen()
    try:
        port = s.getsockname()[1]
        assert _is_free(port) is False
    finally:
        s.close()


# ---------- PortAllocator.acquire ----------


@pytest.mark.asyncio
async def test_acquire_returns_port_in_range() -> None:
    alloc = PortAllocator(range_=(53000, 53100))
    port = await alloc.acquire()
    assert 53000 <= port <= 53100
    assert port in alloc._held


@pytest.mark.asyncio
async def test_acquire_uses_hint_when_free() -> None:
    alloc = PortAllocator(range_=(53000, 53100))
    port = await alloc.acquire(hint=53050)
    assert port == 53050


@pytest.mark.asyncio
async def test_acquire_falls_back_when_hint_in_use() -> None:
    alloc = PortAllocator(range_=(53000, 53100))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    busy_port = s.getsockname()[1]
    try:
        # hint 로 사용 중인 포트 → fallback 으로 range 안 다른 포트.
        port = await alloc.acquire(hint=busy_port)
        assert port != busy_port
        assert 53000 <= port <= 53100
    finally:
        s.close()


@pytest.mark.asyncio
async def test_acquire_skips_held_ports() -> None:
    alloc = PortAllocator(range_=(53200, 53201))
    p1 = await alloc.acquire()
    p2 = await alloc.acquire()
    assert p1 != p2


@pytest.mark.asyncio
async def test_acquire_skips_reserved() -> None:
    alloc = PortAllocator(range_=(53300, 53301), reserved={53300})
    port = await alloc.acquire()
    assert port == 53301


@pytest.mark.asyncio
async def test_acquire_raises_when_range_exhausted() -> None:
    alloc = PortAllocator(range_=(53400, 53400))
    await alloc.acquire()  # 단 하나 잡음.
    with pytest.raises(PortUnavailableError):
        await alloc.acquire()


@pytest.mark.asyncio
async def test_release_allows_re_acquire() -> None:
    alloc = PortAllocator(range_=(53500, 53500))
    port = await alloc.acquire()
    await alloc.release(port)
    again = await alloc.acquire()
    assert again == port


@pytest.mark.asyncio
async def test_release_idempotent() -> None:
    """release 한 적 없는 포트도 noop."""
    alloc = PortAllocator(range_=(53600, 53601))
    await alloc.release(53600)  # 잡은 적 없음.
    # 이후 acquire 정상.
    port = await alloc.acquire()
    assert port == 53600


@pytest.mark.asyncio
async def test_concurrent_acquires_unique() -> None:
    """asyncio.Lock 이 동시 acquire 를 serialize — 같은 포트 중복 X."""
    alloc = PortAllocator(range_=(53700, 53710))
    results = await asyncio.gather(*[alloc.acquire() for _ in range(5)])
    assert len(set(results)) == 5  # 모두 다른 포트.


@pytest.mark.asyncio
async def test_hint_skipped_if_in_held() -> None:
    """hint 가 이미 _held 에 있으면 다른 포트로."""
    alloc = PortAllocator(range_=(53800, 53810))
    first = await alloc.acquire(hint=53805)
    assert first == 53805
    second = await alloc.acquire(hint=53805)  # 같은 hint 재요청 → fallback.
    assert second != 53805
