"""`code2e cost <run_id>` — 비용 분석 (v4 §4.1)."""

from __future__ import annotations

import typer


def cost(run_id: str = typer.Argument(..., help="Run id.")) -> None:
    """Print phase / agent cost breakdown for a run."""
    raise NotImplementedError("cost — phase 2 구현 예정")
