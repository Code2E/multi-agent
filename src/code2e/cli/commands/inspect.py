"""`code2e inspect <run_id>` — 정적 HTML 리포트 (v4 §4.6).

DECISION: Q24 — 단일 HTML 파일, JS 최소.

가장 최근 phase 의 checkpoint (after_<phase>.json) 를 SystemState 로 복구하고
Jinja2 템플릿(reports/index.html.j2) 로 렌더링. 출력은 runs/<run_id>/report/index.html.

phase 우선순위: completed > teardown > testing > launching > building > planning.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path

import typer
from jinja2 import Environment, FileSystemLoader, select_autoescape

from code2e import __version__ as _code2e_version
from code2e.core.checkpoint import CheckpointError, CheckpointWriter
from code2e.core.schemas import SystemState

DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "reports"
PHASE_PRIORITY = ("completed", "teardown", "testing", "launching", "building", "planning")


def inspect(
    run_id: str = typer.Argument(..., help="Run id (e.g., r_1700000000_abcd)."),
    runs_dir: Path = typer.Option(
        DEFAULT_RUNS_DIR, "--runs-dir", help="Runs directory."
    ),
    template_dir: Path = typer.Option(
        DEFAULT_TEMPLATE_DIR, "--template-dir", help="Jinja2 template directory."
    ),
    open_after: bool = typer.Option(
        False, "--open", help="Open report in default browser after generating."
    ),
) -> None:
    """Generate an HTML inspection report for a past run."""
    cw = CheckpointWriter(runs_root=runs_dir)
    try:
        state, phase = _load_latest_state(cw, run_id)
    except CheckpointError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2) from e

    output = render_report(
        state=state,
        source_phase=phase,
        template_dir=template_dir,
        runs_dir=runs_dir,
    )
    typer.echo(f"Report: {output}")
    if open_after:
        webbrowser.open(output.as_uri())


# ---------- helpers ----------


def _load_latest_state(cw: CheckpointWriter, run_id: str) -> tuple[SystemState, str]:
    """가장 최근 phase 의 SystemState 반환.

    우선순위: completed > teardown > testing > launching > building > planning.
    """
    phases = cw.list_phases(run_id)
    if not phases:
        raise CheckpointError(
            f"no checkpoints for run {run_id} in {cw.checkpoint_dir(run_id)}"
        )
    for candidate in PHASE_PRIORITY:
        if candidate in phases:
            return cw.load(run_id, candidate), candidate
    # PHASE_PRIORITY 에 없는 phase 만 있으면 알파벳 마지막을 사용 (best-effort).
    last = sorted(phases)[-1]
    return cw.load(run_id, last), last


def render_report(
    *,
    state: SystemState,
    source_phase: str,
    template_dir: Path,
    runs_dir: Path,
) -> Path:
    """Jinja2 렌더링 → runs/<run_id>/report/index.html 저장. 경로 반환."""
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("index.html.j2")
    rendered = template.render(
        state=state,
        source_phase=source_phase,
        code2e_version=_code2e_version,
    )
    output_dir = runs_dir / state.run_id / "report"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "index.html"
    output.write_text(rendered, encoding="utf-8")
    return output
