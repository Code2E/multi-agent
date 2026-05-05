"""`code2e cassettes ...` — cassette 관리 (v4 §4.1)."""

from __future__ import annotations

import typer

app = typer.Typer(help="Manage LLM call cassettes.")


@app.command("ls")
def ls() -> None:
    """List all cassettes."""
    raise NotImplementedError("cassettes ls — phase 2 구현 예정")


@app.command("inspect")
def inspect(name: str = typer.Argument(...)) -> None:
    """Show entries / schema version of a cassette."""
    raise NotImplementedError("cassettes inspect — phase 2 구현 예정")


@app.command("redact")
def redact(name: str = typer.Argument(...)) -> None:
    """Re-redact secrets in a cassette."""
    raise NotImplementedError("cassettes redact — phase 2 구현 예정")
