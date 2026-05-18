"""Typer 앱 + 글로벌 플래그 + 서브커맨드 등록 (v4 §4)."""

from __future__ import annotations

import typer

from code2e.cli.commands import (
    cassettes,
    chat,
    cost,
    diff,
    doctor,
    init,
    inspect,
    logs,
    prompt,
    run,
    runs,
    tui,
)

app = typer.Typer(
    name="code2e",
    help="Multi-agent code generation CLI (localhost-only).",
    no_args_is_help=True,
)

app.command("init")(init.init)
app.command("doctor")(doctor.doctor)
app.command("run")(run.run)
app.command("chat")(chat.chat)
app.command("tui")(tui.tui)
app.command("inspect")(inspect.inspect)
app.command("diff")(diff.diff)

app.add_typer(runs.app, name="runs", help="Manage past runs (ls / gc / rm).")
app.command("logs")(logs.logs)
app.command("cost")(cost.cost)

app.add_typer(prompt.app, name="prompt", help="Edit / test / lint prompt files.")
app.add_typer(cassettes.app, name="cassettes", help="Manage LLM cassettes.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
