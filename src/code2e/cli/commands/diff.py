"""`code2e diff <a> <b>` — 두 run 의 SystemState 비교 (v4 §4.7).

v1: 핵심 메트릭 (status / budget / iteration counts / termination) 만 비교.
section 별 깊은 diff 는 v1.1.
"""

from __future__ import annotations

from pathlib import Path

import typer

from code2e.cli.commands.inspect import _load_latest_state
from code2e.core.checkpoint import CheckpointError, CheckpointWriter
from code2e.core.schemas import SystemState

DEFAULT_RUNS_DIR = Path("runs")


def diff(
    a: str = typer.Argument(..., help="First run id."),
    b: str = typer.Argument(..., help="Second run id."),
    runs_dir: Path = typer.Option(DEFAULT_RUNS_DIR, "--runs-dir"),
) -> None:
    """Compare two runs side by side (high-level metrics only)."""
    cw = CheckpointWriter(runs_root=runs_dir)
    try:
        state_a, _ = _load_latest_state(cw, a)
        state_b, _ = _load_latest_state(cw, b)
    except CheckpointError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2) from e

    typer.echo(f"=== Diff: {a}  vs  {b} ===")
    for label, va, vb in _build_diff_rows(state_a, state_b):
        marker = "  " if va == vb else "≠ "
        typer.echo(f"{marker}{label:<20} {va!s:<28} → {vb!s}")


def _build_diff_rows(a: SystemState, b: SystemState) -> list[tuple[str, object, object]]:
    """비교할 (label, a_value, b_value) 튜플 리스트."""
    rows: list[tuple[str, object, object]] = [
        ("status", a.status, b.status),
        ("USD used", f"${a.budget.usd_used:.4f}", f"${b.budget.usd_used:.4f}"),
        ("tokens used", a.budget.tokens_used, b.budget.tokens_used),
        ("plan rounds", len(a.plan.iterations), len(b.plan.iterations)),
        (
            "plan units",
            len(a.plan.final.units) if a.plan.final else 0,
            len(b.plan.final.units) if b.plan.final else 0,
        ),
        ("build units", len(a.build.units), len(b.build.units)),
        ("test runs", len(a.test.runs), len(b.test.runs)),
        (
            "termination",
            a.termination.reason if a.termination else "—",
            b.termination.reason if b.termination else "—",
        ),
    ]
    return rows
