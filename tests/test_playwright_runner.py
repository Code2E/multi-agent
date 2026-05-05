"""Unit tests for PlaywrightRunner.

실제 Playwright 브라우저 호출은 통합 테스트 영역. 여기는:
- wrap_script / compile_script (LLM-generated async fragment 래핑) 검증.
- _run_one 의 result 라우팅 (passed/failed/errored) — fake page/expect 로 실행.

setup / teardown / run 은 외부 의존이라 단위 테스트 안 함 (NotImplementedError 도 아니고
실제 동작이지만 unit 영역 밖).
"""

from __future__ import annotations

import asyncio
from pathlib import Path  # noqa: F401 — runner_script 에서 사용 가능.
from types import SimpleNamespace
from typing import Any

import pytest

from code2e.core.schemas import TestCase
from code2e.runners.playwright_runner import (
    PlaywrightRunner,
    compile_script,
    wrap_script,
)

# ---------- wrap_script ----------


def test_wrap_script_creates_async_function() -> None:
    out = wrap_script("await page.goto(BASE_URL)")
    assert out.startswith("async def _run(page, expect, BASE_URL):\n")
    assert "    await page.goto(BASE_URL)" in out


def test_wrap_script_indents_multiline_body() -> None:
    script = "x = 1\nawait page.goto(BASE_URL)"
    out = wrap_script(script)
    assert "    x = 1" in out
    assert "    await page.goto(BASE_URL)" in out


def test_wrap_script_handles_empty_lines() -> None:
    script = "await page.goto(BASE_URL)\n\nawait page.click('button')"
    out = wrap_script(script)
    # 빈 줄은 indent 영향 안 받지만 구조는 유지.
    assert "await page.click('button')" in out


def test_wrap_script_terminates_with_newline() -> None:
    out = wrap_script("await page.goto(BASE_URL)")
    assert out.endswith("\n")


# ---------- compile_script ----------


def test_compile_script_returns_async_callable() -> None:
    fn = compile_script("pass")
    assert callable(fn)
    assert asyncio.iscoroutinefunction(fn)


@pytest.mark.asyncio
async def test_compile_script_can_execute_simple_body() -> None:
    """간단한 script 가 fake args 로 동작하는지."""
    fn = compile_script("x = BASE_URL + '/x'\nassert x == 'http://localhost/x'")
    await fn(SimpleNamespace(), SimpleNamespace(), "http://localhost")  # no raise.


@pytest.mark.asyncio
async def test_compile_script_propagates_assertion_error() -> None:
    fn = compile_script("assert BASE_URL == 'wrong'")
    with pytest.raises(AssertionError):
        await fn(SimpleNamespace(), SimpleNamespace(), "actual")


@pytest.mark.asyncio
async def test_compile_script_can_call_methods_on_page() -> None:
    """page / expect 가 LLM-generated 코드에서 사용되는 패턴."""
    visited_urls: list[str] = []

    async def goto(url: str) -> None:
        visited_urls.append(url)

    fake_page = SimpleNamespace(goto=goto)

    fn = compile_script("await page.goto(BASE_URL + '/home')")
    await fn(fake_page, SimpleNamespace(), "http://localhost")
    assert visited_urls == ["http://localhost/home"]


def test_compile_script_raises_on_invalid_syntax() -> None:
    with pytest.raises(SyntaxError):
        compile_script("this is :: invalid python")


# ---------- _run_one (fake browser) ----------


def _case(script: str, uid: str = "T-001") -> TestCase:
    return TestCase(
        id=uid,
        scenario="s",
        given="g",
        when="w",
        then="t",
        runner_script=script,
    )


class _FakePage:
    """playwright Page 의 일부만 흉내."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    async def new_page(self) -> _FakePage:
        return self._page


@pytest.mark.asyncio
async def test_run_one_passed_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """script 가 정상 종료 → passed."""

    async def _stub_expect(*args: object, **kwargs: object) -> object:
        return SimpleNamespace()

    monkeypatch.setattr(
        "code2e.runners.playwright_runner.expect", _stub_expect, raising=False
    )

    page = _FakePage()
    runner = PlaywrightRunner()
    runner._browser = _FakeBrowser(page)

    case = _case("pass")  # body=pass → 즉시 종료.
    result = await runner._run_one(case, "http://localhost")
    assert result.status == "passed"
    assert result.case_id == "T-001"
    assert page.closed is True


@pytest.mark.asyncio
async def test_run_one_assertion_failure_maps_to_failed() -> None:
    page = _FakePage()
    runner = PlaywrightRunner()
    runner._browser = _FakeBrowser(page)

    case = _case("assert BASE_URL == 'something-else'")
    result = await runner._run_one(case, "http://localhost")
    assert result.status == "failed"
    assert result.failure_reason is not None
    assert page.closed is True


@pytest.mark.asyncio
async def test_run_one_runtime_error_maps_to_errored() -> None:
    page = _FakePage()
    runner = PlaywrightRunner()
    runner._browser = _FakeBrowser(page)

    case = _case("raise ValueError('boom')")
    result = await runner._run_one(case, "http://localhost")
    assert result.status == "errored"
    assert "ValueError" in (result.failure_reason or "")
    assert "boom" in (result.failure_reason or "")
    assert page.closed is True


@pytest.mark.asyncio
async def test_run_one_syntax_error_maps_to_errored() -> None:
    page = _FakePage()
    runner = PlaywrightRunner()
    runner._browser = _FakeBrowser(page)

    case = _case("this is :: invalid")
    result = await runner._run_one(case, "")
    assert result.status == "errored"
    assert "SyntaxError" in (result.failure_reason or "")


@pytest.mark.asyncio
async def test_run_without_setup_raises() -> None:
    runner = PlaywrightRunner()
    with pytest.raises(RuntimeError, match="setup"):
        await runner.run([], ctx=Any, base_url=None)  # type: ignore[arg-type]
