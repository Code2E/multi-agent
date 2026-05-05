"""TestRunner Protocol (v4 §3.12, §5.4).

확장점: runners/*.py + config.runners.test 로 등록.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable

from code2e.agents.base import InvocationContext
from code2e.core.schemas import TestCase, TestRun


@runtime_checkable
class TestRunner(Protocol):
    name: ClassVar[str]

    async def setup(self, workspace_dir: Path) -> None: ...

    async def run(
        self, suite: list[TestCase], ctx: InvocationContext, base_url: str | None = None
    ) -> TestRun: ...

    async def teardown(self) -> None: ...
