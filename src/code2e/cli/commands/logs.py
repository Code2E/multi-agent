"""`code2e logs <run_id>` — 이벤트 로그 스트리밍 (v4 §4.1).

v1: events.jsonl 단발 출력 + grep 필터. --follow 는 v1.1 (file tail-follow).
"""

from __future__ import annotations

import re
from pathlib import Path

import typer

DEFAULT_RUNS_DIR = Path("runs")


def logs(
    run_id: str = typer.Argument(..., help="Run id."),
    runs_dir: Path = typer.Option(DEFAULT_RUNS_DIR, "--runs-dir"),
    follow: bool = typer.Option(
        False, "--follow", "-f", help="Tail-follow (v1.1, currently no-op)."
    ),
    grep: str | None = typer.Option(None, "--grep", help="Filter regex."),
) -> None:
    """Stream events from runs/{run_id}/logs/events.jsonl."""
    log_path = runs_dir / run_id / "logs" / "events.jsonl"
    if not log_path.exists():
        typer.echo(f"No log file: {log_path}", err=True)
        typer.echo(
            "(structlog 통합은 별도 commit. 'code2e inspect' 로 state 검사.)",
            err=True,
        )
        raise typer.Exit(2)

    if follow:
        typer.echo("(--follow 는 v1.1 — 단발 출력만 지원.)", err=True)

    pattern = re.compile(grep) if grep else None
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if pattern is None or pattern.search(line):
            typer.echo(line)
