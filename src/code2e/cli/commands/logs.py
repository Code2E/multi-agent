"""`code2e logs <run_id>` — 이벤트 로그 스트리밍 (v4 §4.1)."""

from __future__ import annotations

import typer


def logs(
    run_id: str = typer.Argument(..., help="Run id."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Tail-follow."),
    grep: str | None = typer.Option(None, "--grep", help="Filter pattern."),
) -> None:
    """Stream events from runs/{run_id}/logs/events.jsonl."""
    raise NotImplementedError("logs — phase 2 구현 예정")
