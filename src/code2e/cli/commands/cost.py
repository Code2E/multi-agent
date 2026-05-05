"""`code2e cost <run_id>` — run 별 비용 분석 (v4 §4.1).

v1: state.budget 의 누적 USD / tokens 출력. phase / agent 별 분해는 v1.1
(events.jsonl 통합 필요).
"""

from __future__ import annotations

from pathlib import Path

import typer

from code2e.cli.commands.inspect import _load_latest_state
from code2e.core.checkpoint import CheckpointError, CheckpointWriter

DEFAULT_RUNS_DIR = Path("runs")


def cost(
    run_id: str = typer.Argument(..., help="Run id."),
    runs_dir: Path = typer.Option(DEFAULT_RUNS_DIR, "--runs-dir"),
) -> None:
    """Print USD / token usage for a run."""
    cw = CheckpointWriter(runs_root=runs_dir)
    try:
        state, source_phase = _load_latest_state(cw, run_id)
    except CheckpointError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2) from e

    typer.echo(f"=== Cost: {run_id} ===")
    typer.echo(f"Source phase: {source_phase}")
    typer.echo(
        f"USD used:     ${state.budget.usd_used:.4f} / ${state.budget.limit_usd:.2f}"
    )
    typer.echo(
        f"Tokens used:  {state.budget.tokens_used:,} / {state.budget.limit_tokens:,}"
    )
    if state.budget.limit_usd > 0:
        ratio = state.budget.usd_used / state.budget.limit_usd
        typer.echo(f"Usage ratio:  {ratio:.1%}")
    typer.echo("")
    typer.echo("(phase / agent 별 분해는 v1.1 — events.jsonl 통합 후 지원.)")
