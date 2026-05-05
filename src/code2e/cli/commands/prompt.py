"""`code2e prompt ...` — 프롬프트 편집/테스트 (v4 §4.1, §13)."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(help="Edit / test / lint prompt files.")


@app.command("list")
def list_prompts() -> None:
    """List all prompt files with versions."""
    raise NotImplementedError("prompt list — phase 2 구현 예정")


@app.command("edit")
def edit(agent: str, round: int | None = typer.Argument(None)) -> None:
    """Open a prompt file in $EDITOR."""
    raise NotImplementedError("prompt edit — phase 2 구현 예정")


@app.command("diff")
def prompt_diff(agent: str) -> None:
    """Show diff between current and last-committed prompt."""
    raise NotImplementedError("prompt diff — phase 2 구현 예정")


@app.command("test")
def test(
    agent: str,
    round: int | None = typer.Option(None, "--round"),
    fixture: str | None = typer.Option(None, "--fixture"),
    cassette: str | None = typer.Option(None, "--cassette"),
    record: bool = typer.Option(False, "--record"),
    replay: bool = typer.Option(False, "--replay"),
    assert_snapshot: Path | None = typer.Option(None, "--assert-snapshot"),
) -> None:
    """Test a single agent prompt against a fixture / cassette."""
    raise NotImplementedError("prompt test — phase 2 구현 예정")


@app.command("lint")
def lint() -> None:
    """Lint all prompt files (delimiters / secrets / schema / TODOs)."""
    raise NotImplementedError("prompt lint — phase 2 구현 예정 (v4 §13.3)")
