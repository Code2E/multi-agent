"""`code2e init` — 대화형 프로젝트 초기화 (v4 §4.4, ADR-042).

DECISION: Q50 — 키 미입력 상태에서도 init 진행. provider 선택만 하고 키는 doctor / .env 로.
"""

from __future__ import annotations

import typer


def init(
    name: str | None = typer.Argument(None, help="Project directory name."),
    yes: bool = typer.Option(False, "--yes", help="Non-interactive mode (use defaults)."),
) -> None:
    """Initialize a new code2e project (interactive)."""
    raise NotImplementedError("init — v4 §4.4 대화형 흐름 구현 예정")
