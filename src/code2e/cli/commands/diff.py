"""`code2e diff <a> <b>` — 두 run 비교 (v4 §4.7)."""

from __future__ import annotations

import typer


def diff(
    a: str = typer.Argument(..., help="First run id."),
    b: str = typer.Argument(..., help="Second run id."),
    section: str | None = typer.Option(None, "--section", help="plan|code|tests|state"),
) -> None:
    """Compare two runs side by side."""
    raise NotImplementedError("diff — phase 2 구현 예정")
