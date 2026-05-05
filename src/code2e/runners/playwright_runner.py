"""Playwright 기반 E2E 러너 (v4 §3.4 testrun, §5.4).

LLM 이 생성한 runner_script (async fragment) 를 `async def _run(page, expect, BASE_URL)`
로 래핑 + exec 로 컴파일 + Playwright async API 로 실행.

보안: localhost-trusted (NG-2). LLM 생성 코드를 시스템이 실행하므로 외부 신뢰 불가.
v4 §1.3 NG 명시 — 자동 보안/성능 튜닝은 비목표.

설치 의존성: `playwright install chromium` 별도 필요 (도커리스 환경).
단위 테스트는 wrap_script / compile_script 만 검증. 실제 브라우저 호출은 통합
테스트 영역 (CI 분리 또는 skip).
"""

from __future__ import annotations

import asyncio
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from code2e.agents.base import InvocationContext
from code2e.core.schemas import TestCase, TestResult, TestRun, TestSummary

PLAYWRIGHT_PER_CASE_TIMEOUT_S = 30


def wrap_script(script: str) -> str:
    """LLM-generated async fragment 를 `async def _run(page, expect, BASE_URL)` 로 래핑.

    예시 입력:
        await page.goto(BASE_URL + '/')
        await expect(page.locator('h1')).to_have_text('Hello')

    예시 출력:
        async def _run(page, expect, BASE_URL):
            await page.goto(BASE_URL + '/')
            await expect(page.locator('h1')).to_have_text('Hello')
    """
    indented = textwrap.indent(script, "    ")
    if not indented.endswith("\n"):
        indented += "\n"
    return f"async def _run(page, expect, BASE_URL):\n{indented}"


def compile_script(script: str) -> Any:
    """wrap_script + exec → callable async function 반환.

    호출자: `await fn(page, expect, base_url)`. 컴파일 실패 시 SyntaxError 등 raise.
    """
    wrapped = wrap_script(script)
    local_ns: dict[str, Any] = {}
    exec(wrapped, {"__builtins__": __builtins__}, local_ns)  # noqa: S102 — LLM 코드 실행 의도.
    return local_ns["_run"]


class PlaywrightRunner:
    name: ClassVar[str] = "playwright"

    def __init__(self) -> None:
        self._browser: Any = None
        self._playwright: Any = None

    async def setup(self, workspace_dir: Path) -> None:
        from playwright.async_api import async_playwright  # noqa: PLC0415

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)

    async def run(
        self,
        suite: list[TestCase],
        ctx: InvocationContext,
        base_url: str | None = None,
    ) -> TestRun:
        if self._browser is None:
            raise RuntimeError("PlaywrightRunner.setup() must be called before run()")

        results: list[TestResult] = []
        for case in suite:
            result = await self._run_one(case, base_url or "")
            results.append(result)

        summary = TestSummary(
            passed=sum(1 for r in results if r.status == "passed"),
            failed=sum(1 for r in results if r.status == "failed"),
            errored=sum(1 for r in results if r.status == "errored"),
            total=len(results),
        )
        return TestRun(iteration=0, results=results, summary=summary, signature="")

    async def _run_one(self, case: TestCase, base_url: str) -> TestResult:
        from playwright.async_api import expect  # noqa: PLC0415

        page = await self._browser.new_page()
        start = datetime.now(UTC)
        try:
            try:
                run_fn = compile_script(case.runner_script)
            except SyntaxError as e:
                return _result(
                    case.id, "errored", start, f"runner_script SyntaxError: {e}"
                )

            try:
                await asyncio.wait_for(
                    run_fn(page, expect, base_url),
                    timeout=PLAYWRIGHT_PER_CASE_TIMEOUT_S,
                )
            except TimeoutError:
                return _result(
                    case.id,
                    "errored",
                    start,
                    f"timeout after {PLAYWRIGHT_PER_CASE_TIMEOUT_S}s",
                )
            except AssertionError as e:
                return _result(case.id, "failed", start, str(e) or "AssertionError")
            except Exception as e:  # noqa: BLE001 — LLM 코드의 임의 예외 catch.
                return _result(
                    case.id, "errored", start, f"{type(e).__name__}: {e}"
                )
            return _result(case.id, "passed", start, None)
        finally:
            await page.close()

    async def teardown(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


def _result(
    case_id: str,
    status: Literal["passed", "failed", "errored", "skipped"],
    start: datetime,
    reason: str | None,
) -> TestResult:
    duration_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)
    return TestResult(
        case_id=case_id,
        status=status,
        duration_ms=duration_ms,
        failure_reason=reason,
    )
