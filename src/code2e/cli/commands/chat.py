"""`code2e chat` — 로컬 웹 인터페이스 entry point.

브라우저 자동 open + uvicorn 서버 시작. Ctrl+C 로 종료.
"""

from __future__ import annotations

import asyncio
import os
import webbrowser
from pathlib import Path

import typer

DEFAULT_CONFIG_PATH = Path("config/default.yaml")
DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_CASSETTES_DIR = Path("cassettes")


def chat(
    host: str = typer.Option("127.0.0.1", "--host", help="bind host"),
    port: int = typer.Option(9876, "--port", help="bind port"),
    cassette: str | None = typer.Option(
        None, "--cassette", help="cassette name (default: 'default')"
    ),
    record: bool = typer.Option(False, "--record", help="Record LLM calls to cassette."),
    replay: bool = typer.Option(False, "--replay", help="Replay from cassette only."),
    config_path: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", help="Path to config YAML."
    ),
    runs_dir: Path = typer.Option(
        DEFAULT_RUNS_DIR, "--runs-dir", help="Runs / checkpoints directory."
    ),
    cassettes_dir: Path = typer.Option(
        DEFAULT_CASSETTES_DIR, "--cassettes-dir", help="Cassettes root directory."
    ),
    no_browser: bool = typer.Option(False, "--no-browser", help="브라우저 자동 open 비활성."),
) -> None:
    """Code2E Chat — localhost 웹 인터페이스로 멀티 에이전트 진행 모니터링."""
    if record and replay:
        typer.echo("Error: --record / --replay 동시 사용 불가.", err=True)
        raise typer.Exit(2)
    cassette_mode = "record" if record else "replay" if replay else "auto"

    if cassette_mode != "replay" and not os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo("Error: ANTHROPIC_API_KEY 가 설정되지 않았습니다.", err=True)
        typer.echo("Hint: .env 에 키 입력 또는 --replay 사용.", err=True)
        raise typer.Exit(1)

    url = f"http://{host}:{port}"
    typer.echo(f"Code2E Chat — {url}")
    typer.echo("Ctrl+C 로 종료.")

    if not no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass

    from code2e.cli.chat_server import serve  # noqa: PLC0415

    try:
        asyncio.run(
            serve(
                host=host,
                port=port,
                config_path=config_path,
                runs_dir=runs_dir,
                cassettes_dir=cassettes_dir,
                cassette_mode=cassette_mode,
                cassette_name=cassette or "default",
            )
        )
    except KeyboardInterrupt:
        typer.echo("\n종료.")
