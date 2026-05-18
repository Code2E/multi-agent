"""`code2e run` — 파이프라인 실행 (v4 §4.1, §4.3).

DECISION:
- Q30 — task 입력은 인자 또는 --task-file 만. stdin pipe 는 v1.1.
- Q38 — 입력 정규화는 trim + NFC.
- Q19 — runs/.global.lock 으로 동시 run 단일화.

cassette mode 우선순위: --record > --replay > config.cassettes.mode > "auto".
budget 우선순위: --budget-usd > config.budget.max_total_usd > 5.0.
"""

from __future__ import annotations

import asyncio
import os
import unicodedata
from pathlib import Path
from typing import Any

import typer
import yaml

from code2e.agents.advisor import AdvisorAgent
from code2e.agents.evaluator import EvaluatorTestgenAgent, EvaluatorTestrunAgent
from code2e.agents.executor import ExecutorAgent
from code2e.agents.planner import PlannerAgent
from code2e.core.budget import BudgetTracker
from code2e.core.cassette import CassetteStore
from code2e.core.checkpoint import CheckpointWriter
from code2e.core.llm_gateway import AnthropicProvider, LlmGateway
from code2e.core.orchestrator import Orchestrator
from code2e.core.port_allocator import PortAllocator
from code2e.core.process_manager import ProcessManager
from code2e.core.schemas import SystemState
from code2e.runners.playwright_runner import PlaywrightRunner

DEFAULT_CONFIG_PATH = Path("config/default.yaml")
DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_CASSETTES_DIR = Path("cassettes")


def run(
    task: str | None = typer.Argument(
        None, help="Task description in natural language."
    ),
    task_file: Path | None = typer.Option(
        None, "--task-file", help="Path to task definition (.md/.txt/.py/.sh)."
    ),
    budget_usd: float | None = typer.Option(
        None, "--budget-usd", help="Hard budget cap in USD (overrides config)."
    ),
    cassette: str | None = typer.Option(
        None, "--cassette", help="Cassette name (default: 'default')."
    ),
    record: bool = typer.Option(False, "--record", help="Record LLM calls to cassette."),
    replay: bool = typer.Option(
        False,
        "--replay",
        help="Replay from cassette only (no real LLM call). Misses raise CassetteMiss.",
    ),
    config_path: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", help="Path to config YAML."
    ),
    runs_dir: Path = typer.Option(
        DEFAULT_RUNS_DIR, "--runs-dir", help="Runs / checkpoints directory."
    ),
    cassettes_dir: Path = typer.Option(
        DEFAULT_CASSETTES_DIR, "--cassettes-dir", help="Cassettes root directory."
    ),
    run_id: str | None = typer.Option(
        None, "--run-id", help="Explicit run id (overrides --name and auto slug)."
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        help="Run label for human-readable run dir (e.g., 'calculator'). "
        "Becomes 'r_<name>_<unix>'. Ignored if --run-id given.",
    ),
) -> None:
    """Run the multi-agent pipeline end-to-end."""
    user_input = _resolve_task(task, task_file)

    if record and replay:
        typer.echo("Error: --record and --replay 동시 사용 불가.", err=True)
        raise typer.Exit(2)

    config = _load_config(config_path)
    cassette_mode = (
        "record"
        if record
        else "replay"
        if replay
        else _get(config, "cassettes.mode", default="auto")
    )

    if cassette_mode != "replay" and not os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo("Error: ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.", err=True)
        typer.echo(
            "Hint: cp .env.example .env 후 키 입력, 또는 --replay 로 cassette 재생.",
            err=True,
        )
        raise typer.Exit(1)

    orch = _build_orchestrator(
        config=config,
        cassette_name=cassette or "default",
        cassette_mode=cassette_mode,
        cassettes_dir=cassettes_dir,
        runs_dir=runs_dir,
        budget_usd_override=budget_usd,
    )

    # run_id 우선순위: --run-id (완전 명시) > --name (slug) > task 기반 자동.
    if run_id is None and name is not None:
        from code2e.core.state import new_run_id  # noqa: PLC0415

        run_id = new_run_id(slug=name)

    state = asyncio.run(orch.start(user_input, run_id=run_id))
    _render_result(state)
    if state.status == "aborted":
        raise typer.Exit(1)


# ---------- helpers ----------


def _resolve_task(task: str | None, task_file: Path | None) -> str:
    """task 또는 task_file → user_input. Q38: trim + NFC."""
    if task and task_file:
        typer.echo("Error: task 인자와 --task-file 동시 사용 불가.", err=True)
        raise typer.Exit(2)
    if task_file is not None:
        if not task_file.exists():
            typer.echo(f"Error: task file not found: {task_file}", err=True)
            raise typer.Exit(2)
        text = task_file.read_text(encoding="utf-8")
    elif task is not None:
        text = task
    else:
        typer.echo("Error: task 인자 또는 --task-file 가 필요합니다.", err=True)
        raise typer.Exit(2)
    return unicodedata.normalize("NFC", text.strip())


def _load_config(config_path: Path) -> dict[str, Any]:
    """config YAML 로드. 파일 없으면 빈 dict (모든 필드 default 적용)."""
    if not config_path.exists():
        return {}
    parsed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _get(config: dict[str, Any], dotted: str, default: Any = None) -> Any:  # noqa: ANN401
    """`a.b.c` 경로의 값을 dict 에서 조회 — 단계마다 dict 가 아니면 default."""
    cur: Any = config
    for key in dotted.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _build_orchestrator(
    *,
    config: dict[str, Any],
    cassette_name: str,
    cassette_mode: str,
    cassettes_dir: Path,
    runs_dir: Path,
    budget_usd_override: float | None,
) -> Orchestrator:
    """모든 의존성을 빌드해 Orchestrator 인스턴스 반환."""
    limit_usd = (
        budget_usd_override
        if budget_usd_override is not None
        else float(_get(config, "budget.max_total_usd", default=5.0))
    )
    limit_tokens = int(_get(config, "budget.max_total_tokens", default=1_000_000))
    warn_threshold = float(_get(config, "budget.warn_threshold", default=0.8))

    cassette_store = CassetteStore(
        name=cassette_name,
        dir=cassettes_dir,
        mode=cassette_mode,  # type: ignore[arg-type]
    )
    budget = BudgetTracker(
        limit_usd=limit_usd, limit_tokens=limit_tokens, warn_threshold=warn_threshold
    )
    provider = AnthropicProvider()
    gateway = LlmGateway(provider=provider, cassette=cassette_store, budget=budget)

    default_model = _get(config, "agents.planner.model", default="claude-sonnet-4-6")
    planner = PlannerAgent(
        model=_get(config, "agents.planner.model", default=default_model)
    )
    executor = ExecutorAgent(
        model=_get(config, "agents.executor.model", default=default_model)
    )
    advisor = AdvisorAgent(
        model=_get(config, "agents.advisor.model", default=default_model)
    )
    evaluator_testgen = EvaluatorTestgenAgent(
        model=_get(config, "agents.evaluator.model", default=default_model)
    )
    evaluator_testrun = EvaluatorTestrunAgent(runner=PlaywrightRunner())

    port_range_cfg = _get(config, "generated_app.port_range", default=[3000, 3999])
    port_allocator = PortAllocator(range_=(int(port_range_cfg[0]), int(port_range_cfg[1])))
    process_manager = ProcessManager(run_dir=runs_dir)
    checkpoint = CheckpointWriter(runs_root=runs_dir)

    return Orchestrator(
        planner=planner,
        executor=executor,
        advisor=advisor,
        evaluator_testgen=evaluator_testgen,
        evaluator_testrun=evaluator_testrun,
        llm_gateway=gateway,
        budget=budget,
        workspace_root=runs_dir,
        process_manager=process_manager,
        port_allocator=port_allocator,
        checkpoint=checkpoint,
    )


def _render_result(state: SystemState) -> None:
    """terminal 친화 단순 출력. 추후 Rich pretty / JSONL renderer 통합."""
    typer.echo("")
    typer.echo(f"=== Run {state.run_id} ===")
    typer.echo(f"Status:  {state.status}")
    if state.termination is not None:
        typer.echo(f"Reason:  {state.termination.reason} ({state.termination.phase})")
        typer.echo(f"Details: {state.termination.details}")
        typer.echo(f"Next:    {state.termination.suggested_next}")
    typer.echo(
        f"Budget:  ${state.budget.usd_used:.4f} / ${state.budget.limit_usd:.2f}"
    )
    typer.echo(
        f"Tokens:  {state.budget.tokens_used:,} / {state.budget.limit_tokens:,}"
    )
    if state.plan.iterations:
        typer.echo(f"Plans:   {len(state.plan.iterations)} round(s)")
    if state.build.units:
        approved = sum(1 for u in state.build.units if u.status == "approved")
        typer.echo(f"Units:   {approved}/{len(state.build.units)} approved")
    if state.test.runs:
        last = state.test.runs[-1]
        typer.echo(
            f"Tests:   iter {last.iteration} — "
            f"{last.summary.passed}/{last.summary.total} passed"
        )
