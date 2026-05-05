"""`code2e runs ...` — run 메타 관리 (v4 §4.1)."""

from __future__ import annotations

import typer

app = typer.Typer(help="Manage past runs.")


@app.command("ls")
def ls() -> None:
    """List past runs."""
    raise NotImplementedError("runs ls — phase 2 구현 예정")


@app.command("gc")
def gc(
    older_than: str = typer.Option("30d", "--older-than", help="Delete runs older than DURATION."),
) -> None:
    """Garbage-collect old runs."""
    raise NotImplementedError("runs gc — phase 2 구현 예정")


@app.command("rm")
def rm(run_id: str = typer.Argument(..., help="Run id to delete.")) -> None:
    """Delete a single run."""
    raise NotImplementedError("runs rm — phase 2 구현 예정")
