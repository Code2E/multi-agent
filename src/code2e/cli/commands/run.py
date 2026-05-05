"""`code2e run` — 파이프라인 실행 (v4 §4.1, §4.3).

DECISION: Q30 — task 입력은 인자 또는 --task-file 만. stdin pipe 는 v1.1.
DECISION: Q38 — 입력 정규화는 trim + NFC 만.
"""

from __future__ import annotations

from pathlib import Path

import typer


def run(
    task: str | None = typer.Argument(None, help="Task description in natural language."),
    task_file: Path | None = typer.Option(None, "--task-file", help="Path to .md/.txt/.py/.sh."),
    until: str | None = typer.Option(None, "--until", help="Stop after a specific phase."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only, no LLM calls."),
    resume: str | None = typer.Option(None, "--resume", help="Run id to resume."),
    replay: str | None = typer.Option(None, "--replay", help="Run id to replay (cassette)."),
    cassette: str | None = typer.Option(None, "--cassette", help="Cassette name."),
    record: bool = typer.Option(False, "--record", help="Record LLM calls."),
    step: bool = typer.Option(False, "--step", help="Pause between phases (TTY only)."),
    budget_usd: float | None = typer.Option(None, "--budget-usd", help="Hard budget cap."),
    estimate: bool = typer.Option(False, "--estimate", help="Print estimate and exit."),
) -> None:
    """Run the multi-agent pipeline end-to-end."""
    raise NotImplementedError("run — phase 2 구현 예정")
