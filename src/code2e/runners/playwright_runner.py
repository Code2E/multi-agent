"""Playwright 기반 E2E 러너 (v4 §3.4 testrun, §5.4)."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from code2e.agents.base import InvocationContext
from code2e.core.schemas import TestCase, TestRun


class PlaywrightRunner:
    name: ClassVar[str] = "playwright"

    async def setup(self, workspace_dir: Path) -> None:
        raise NotImplementedError("PlaywrightRunner.setup — phase 2 구현 예정")

    async def run(
        self, suite: list[TestCase], ctx: InvocationContext, base_url: str | None = None
    ) -> TestRun:
        raise NotImplementedError("PlaywrightRunner.run — phase 2 구현 예정")

    async def teardown(self) -> None:
        raise NotImplementedError("PlaywrightRunner.teardown — phase 2 구현 예정")
