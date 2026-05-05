"""`code2e inspect <run_id>` — 정적 HTML 리포트 (v4 §4.6).

DECISION: Q24 — 단일 HTML, JS 최소.
"""

from __future__ import annotations

import typer


def inspect(
    run_id: str = typer.Argument(..., help="Run id (e.g., r_1700000000_abcd)."),
    phase: str | None = typer.Option(None, "--phase", help="Filter to a specific phase."),
    unit: str | None = typer.Option(None, "--unit", help="Filter to a specific plan unit."),
    open_after: bool = typer.Option(False, "--open", help="Open report in browser."),
) -> None:
    """Generate an HTML inspection report for a past run."""
    raise NotImplementedError("inspect — v4 §4.6 Jinja2 템플릿 렌더링 구현 예정")
