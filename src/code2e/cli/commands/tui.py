"""`code2e tui` — 터미널 UI (B 옵션).

웹 없이 SSH / 헤드리스 환경에서도 동일한 멀티 에이전트 모니터링.
Rich Live + Layout 으로 2 패널 (Conversation / Pipeline).

iframe 미지원 — 산출 앱 base_url 만 표시. 브라우저로 직접 open 안내.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from code2e.core.event_emitter import EventEmitter

DEFAULT_CONFIG_PATH = Path("config/default.yaml")
DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_CASSETTES_DIR = Path("cassettes")

PHASES = ("planning", "building", "launching", "testing", "teardown")
PHASE_LABELS = {
    "planning": "Phase 1 · Planning",
    "building": "Phase 2 · Build + Testgen",
    "launching": "Phase L · Launching",
    "testing": "Phase 3 · Testing",
    "teardown": "Teardown",
}


def tui(
    cassette: str | None = typer.Option(None, "--cassette"),
    record: bool = typer.Option(False, "--record"),
    replay: bool = typer.Option(False, "--replay"),
    config_path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    runs_dir: Path = typer.Option(DEFAULT_RUNS_DIR, "--runs-dir"),
    cassettes_dir: Path = typer.Option(DEFAULT_CASSETTES_DIR, "--cassettes-dir"),
) -> None:
    """Code2E TUI — Rich Live 터미널 UI 로 멀티 에이전트 진행 모니터링."""
    if record and replay:
        typer.echo("Error: --record / --replay 동시 사용 불가.", err=True)
        raise typer.Exit(2)
    cassette_mode = "record" if record else "replay" if replay else "auto"

    if cassette_mode != "replay" and not os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo("Error: ANTHROPIC_API_KEY 가 설정되지 않았습니다.", err=True)
        raise typer.Exit(1)

    asyncio.run(_run_tui(
        config_path=config_path,
        runs_dir=runs_dir,
        cassettes_dir=cassettes_dir,
        cassette_mode=cassette_mode,
        cassette_name=cassette or "default",
    ))


async def _run_tui(
    *,
    config_path: Path,
    runs_dir: Path,
    cassettes_dir: Path,
    cassette_mode: str,
    cassette_name: str,
) -> None:
    from code2e.cli.commands.run import _build_orchestrator  # noqa: PLC0415

    cfg: dict[str, Any] = {}
    if config_path.is_file():
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    console = Console()
    console.print("[bold magenta]Code2E TUI[/bold magenta]  ·  Planner · Executor · Advisor · Evaluator")
    console.print("[dim]Ctrl+D 또는 빈 입력 = 종료. 모든 task 는 한 세션 안에 누적.[/dim]\n")

    history: list[dict[str, Any]] = []

    while True:
        try:
            task = Prompt.ask("[bold cyan]task[/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]종료.[/dim]")
            return
        if not task:
            console.print("[dim]종료.[/dim]")
            return

        try:
            budget = float(Prompt.ask("[dim]budget USD[/dim]", default="5"))
        except ValueError:
            budget = 5.0

        # 새 orchestrator + emitter per task.
        emitter = EventEmitter()
        orch = _build_orchestrator(
            config=cfg,
            cassette_name=cassette_name,
            cassette_mode=cassette_mode,
            cassettes_dir=cassettes_dir,
            runs_dir=runs_dir,
            budget_usd_override=budget,
        )
        orch.emitter = emitter

        # phase 상태 + stats 보관용.
        ui_state: dict[str, Any] = {
            "phases": {p: {"status": "pending", "detail": ""} for p in PHASES},
            "usd_used": 0.0,
            "tokens_used": 0,
            "run_id": "—",
            "base_url": None,
        }

        run_task = asyncio.create_task(orch.start(task, run_id=None, skip_teardown=True))

        with Live(_render(ui_state, task), console=console, refresh_per_second=8, transient=False) as live:
            async for evt in emitter.subscribe():
                _apply_event(ui_state, evt)
                live.update(_render(ui_state, task))
                if evt.type in ("run.completed", "run.aborted", "run.exception"):
                    break

        result = await run_task
        history.append(
            {
                "run_id": result.run_id,
                "status": result.status,
                "usd": orch.budget.usd_used,
                "base_url": result.launch.base_url if result.launch else None,
            }
        )

        # 산출 앱이 살아있으면 base_url 표시.
        if result.launch is not None and result.launch.base_url:
            console.print(
                f"\n[green]✓ Live 산출 앱:[/green] [link={result.launch.base_url}]{result.launch.base_url}[/link]"
            )
            console.print("[dim]브라우저로 위 주소 열기. 다음 task 시작하면 자동 teardown.[/dim]\n")

        # 다음 run 시작 전에 이전 산출 process teardown (port 재사용).
        if result.launch is not None and orch.process_manager is not None:
            try:
                await orch.process_manager.teardown(result.launch, grace_s=3)
            except Exception:  # noqa: BLE001
                pass
            if orch.port_allocator is not None and result.launch.port is not None:
                try:
                    await orch.port_allocator.release(result.launch.port)
                except Exception:  # noqa: BLE001
                    pass


def _apply_event(ui_state: dict[str, Any], evt: Any) -> None:
    t = evt.type
    d = evt.data
    if "usd_used" in d:
        ui_state["usd_used"] = d["usd_used"]
    if "tokens_used" in d:
        ui_state["tokens_used"] = d["tokens_used"]
    if t == "run.start":
        ui_state["run_id"] = d.get("run_id", "—")
    elif t.startswith("phase."):
        # phase.<name>.<sub>
        parts = t.split(".")
        if len(parts) >= 3:
            name = parts[1]
            sub = parts[2]
            if name in ui_state["phases"]:
                if sub == "start":
                    ui_state["phases"][name]["status"] = "active"
                elif sub == "end":
                    ui_state["phases"][name]["status"] = "done"
                    detail = _phase_detail(name, d)
                    if detail:
                        ui_state["phases"][name]["detail"] = detail
                    if name == "launching" and d.get("base_url"):
                        ui_state["base_url"] = d["base_url"]
    elif t == "run.aborted":
        phase = d.get("phase")
        if phase in ui_state["phases"]:
            ui_state["phases"][phase]["status"] = "aborted"


def _phase_detail(name: str, d: dict[str, Any]) -> str:
    if name == "planning":
        return f"{d.get('rounds', 0)} round · units: {d.get('units', 0)}"
    if name == "building":
        return f"units: {d.get('units_approved', 0)}/{d.get('units_total', 0)} · test cases: {d.get('test_cases', 0)}"
    if name == "launching":
        return d.get("base_url") or "—"
    if name == "testing":
        return f"iter {d.get('iterations', 0)} · passed {d.get('passed', 0)} · failed {d.get('failed', 0)}"
    if name == "teardown":
        return "skipped (chat 모드)" if d.get("skipped") else "정리 완료"
    return ""


_ICON = {"pending": "○", "active": "⟳", "done": "✓", "aborted": "✗"}
_COLOR = {"pending": "dim", "active": "cyan", "done": "green", "aborted": "red"}


def _render(ui_state: dict[str, Any], task: str) -> Layout:
    layout = Layout()
    layout.split_row(
        Layout(name="conv", ratio=2),
        Layout(name="pipe", ratio=3),
    )

    # Conversation panel — 현재 task + 결과 메타.
    conv_body = Group(
        Text(task, style="bold"),
        Text(""),
        Text(f"run_id: {ui_state['run_id']}", style="dim"),
        Text(f"cost: ${ui_state['usd_used']:.4f} · tokens: {ui_state['tokens_used']:,}", style="dim"),
        Text(""),
        Text("산출 앱: " + (ui_state["base_url"] or "—"), style="green" if ui_state["base_url"] else "dim"),
    )
    layout["conv"].update(Panel(conv_body, title="Conversation", border_style="magenta"))

    # Pipeline panel — phase 진행 표.
    table = Table.grid(padding=(0, 2))
    table.add_column()
    table.add_column()
    for name in PHASES:
        ph = ui_state["phases"][name]
        status = ph["status"]
        icon = _ICON[status]
        color = _COLOR[status]
        label = PHASE_LABELS[name]
        detail = ph["detail"] or "—"
        table.add_row(
            Text(f"{icon} {label}", style=color),
            Text(detail, style="dim"),
        )
    layout["pipe"].update(Panel(table, title="Pipeline Monitor", border_style="cyan"))

    return layout
