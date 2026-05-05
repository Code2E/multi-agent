"""Unit tests for `code2e inspect <run_id>` CLI command."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from code2e.cli.app import app
from code2e.cli.commands.inspect import (
    PHASE_PRIORITY,
    _load_latest_state,
    render_report,
)
from code2e.core.checkpoint import CheckpointError, CheckpointWriter
from code2e.core.schemas import (
    BudgetState,
    LaunchInfo,
    Plan,
    PlanMeta,
    PlanState,
    PlanUnit,
    SystemState,
    TerminationInfo,
    TestResult,
    TestRun,
    TestState,
    TestSummary,
    UnitState,
)

runner = CliRunner()


# ---------- builders ----------


def _state(
    run_id: str = "r_inspect_001",
    status: str = "completed",
) -> SystemState:
    return SystemState(
        run_id=run_id,
        status=status,  # type: ignore[arg-type]
        user_input="task",
        budget=BudgetState(
            limit_usd=5.0, limit_tokens=100_000, usd_used=0.123, tokens_used=4567
        ),
    )


def _plan(version: int = 3, units: list[PlanUnit] | None = None) -> Plan:
    return Plan(
        version=version,  # type: ignore[arg-type]
        content=f"## Round {version}",
        units=units or [],
        meta=PlanMeta(
            created_at=datetime(2026, 5, 5, tzinfo=UTC), tokens_in=10, tokens_out=20
        ),
    )


def _unit(uid: str, status: str = "approved") -> UnitState:
    return UnitState(
        unit_id=uid,
        status=status,  # type: ignore[arg-type]
        iteration=1,
    )


def _testrun(passed: int, failed: int) -> TestRun:
    results = [
        TestResult(case_id=f"T-{i}", status="passed", duration_ms=10)
        for i in range(passed)
    ] + [
        TestResult(
            case_id=f"T-F-{i}",
            status="failed",
            duration_ms=20,
            failure_reason="X",
        )
        for i in range(failed)
    ]
    return TestRun(
        iteration=1,
        results=results,
        summary=TestSummary(
            passed=passed, failed=failed, errored=0, total=passed + failed
        ),
        signature="sig",
    )


# ---------- _load_latest_state ----------


def test_load_latest_state_picks_highest_priority(tmp_path: Path) -> None:
    cw = CheckpointWriter(runs_root=tmp_path)
    state = _state()
    # 여러 phase 저장 — completed 가 가장 우선.
    for phase in ("planning", "building", "completed"):
        cw.save(state, phase)

    loaded, source = _load_latest_state(cw, state.run_id)
    assert source == "completed"
    assert loaded.run_id == state.run_id


def test_load_latest_state_falls_back_to_planning(tmp_path: Path) -> None:
    cw = CheckpointWriter(runs_root=tmp_path)
    state = _state()
    cw.save(state, "planning")
    loaded, source = _load_latest_state(cw, state.run_id)
    assert source == "planning"
    assert loaded.run_id == state.run_id


def test_load_latest_state_raises_when_no_checkpoints(tmp_path: Path) -> None:
    cw = CheckpointWriter(runs_root=tmp_path)
    with pytest.raises(CheckpointError, match="no checkpoints"):
        _load_latest_state(cw, "r_doesnotexist")


def test_load_latest_state_handles_unknown_phase_name(tmp_path: Path) -> None:
    """PHASE_PRIORITY 에 없는 이름 (e.g. 'custom') 만 있으면 그것 사용."""
    cw = CheckpointWriter(runs_root=tmp_path)
    state = _state()
    cw.save(state, "custom_phase")
    loaded, source = _load_latest_state(cw, state.run_id)
    assert source == "custom_phase"
    assert loaded.run_id == state.run_id


# ---------- render_report ----------


def test_render_report_creates_html_file(tmp_path: Path) -> None:
    template_dir = (
        Path(__file__).resolve().parent.parent / "src" / "code2e" / "reports"
    )
    state = _state()
    out = render_report(
        state=state,
        source_phase="completed",
        template_dir=template_dir,
        runs_dir=tmp_path,
    )
    assert out.exists()
    assert out.parent == tmp_path / state.run_id / "report"
    assert out.name == "index.html"
    html = out.read_text(encoding="utf-8")
    assert state.run_id in html
    assert "<!doctype html>" in html.lower() or "<!DOCTYPE html>" in html


def test_render_report_includes_termination_when_aborted(tmp_path: Path) -> None:
    template_dir = (
        Path(__file__).resolve().parent.parent / "src" / "code2e" / "reports"
    )
    state = _state(status="aborted").model_copy(
        update={
            "termination": TerminationInfo(
                reason="LAUNCH_SPEC_MISSING",
                phase="Phase L",
                details="no launch block",
                suggested_next="prompt 점검",
            )
        }
    )
    out = render_report(
        state=state,
        source_phase="launching",
        template_dir=template_dir,
        runs_dir=tmp_path,
    )
    html = out.read_text(encoding="utf-8")
    assert "LAUNCH_SPEC_MISSING" in html
    assert "prompt 점검" in html


def test_render_report_includes_plan_units(tmp_path: Path) -> None:
    template_dir = (
        Path(__file__).resolve().parent.parent / "src" / "code2e" / "reports"
    )
    units = [
        PlanUnit(
            id="U-001",
            title="MARKER-PLAN-UNIT",
            description="d",
            acceptance_criteria=["a"],
        )
    ]
    state = _state().model_copy(
        update={
            "plan": PlanState(
                iterations=[_plan(units=units)],
                final=_plan(units=units),
            )
        }
    )
    out = render_report(
        state=state,
        source_phase="completed",
        template_dir=template_dir,
        runs_dir=tmp_path,
    )
    html = out.read_text(encoding="utf-8")
    assert "MARKER-PLAN-UNIT" in html
    assert "U-001" in html


def test_render_report_includes_build_units(tmp_path: Path) -> None:
    template_dir = (
        Path(__file__).resolve().parent.parent / "src" / "code2e" / "reports"
    )
    state = _state().model_copy(
        update={
            "build": __import__(
                "code2e.core.schemas", fromlist=["BuildState"]
            ).BuildState(
                units=[_unit("U-001", "approved"), _unit("U-002", "force_stopped")]
            )
        }
    )
    out = render_report(
        state=state,
        source_phase="building",
        template_dir=template_dir,
        runs_dir=tmp_path,
    )
    html = out.read_text(encoding="utf-8")
    assert "U-001" in html
    assert "approved" in html
    assert "U-002" in html
    assert "force_stopped" in html


def test_render_report_includes_test_summary(tmp_path: Path) -> None:
    template_dir = (
        Path(__file__).resolve().parent.parent / "src" / "code2e" / "reports"
    )
    state = _state().model_copy(
        update={
            "test": TestState(
                runs=[_testrun(passed=2, failed=1)],
                status="passed",
                suite=None,
            )
        }
    )
    out = render_report(
        state=state,
        source_phase="testing",
        template_dir=template_dir,
        runs_dir=tmp_path,
    )
    html = out.read_text(encoding="utf-8")
    assert "T-0" in html
    assert "T-F-0" in html


def test_render_report_includes_launch_info(tmp_path: Path) -> None:
    template_dir = (
        Path(__file__).resolve().parent.parent / "src" / "code2e" / "reports"
    )
    state = _state().model_copy(
        update={
            "launch": LaunchInfo(
                pid=12345,
                port=3742,
                base_url="http://localhost:3742",
                started_at=datetime(2026, 5, 5, tzinfo=UTC),
                log_path="/tmp/app.log",
            )
        }
    )
    out = render_report(
        state=state,
        source_phase="launching",
        template_dir=template_dir,
        runs_dir=tmp_path,
    )
    html = out.read_text(encoding="utf-8")
    assert "12345" in html
    assert "3742" in html
    assert "http://localhost:3742" in html


# ---------- CLI invocation ----------


def test_inspect_help() -> None:
    result = runner.invoke(app, ["inspect", "--help"])
    assert result.exit_code == 0
    assert "<run_id>" in result.output.lower() or "run_id" in result.output.lower()


def test_inspect_full_command(tmp_path: Path) -> None:
    """checkpoint 저장 후 inspect 호출 → HTML 생성."""
    cw = CheckpointWriter(runs_root=tmp_path)
    state = _state(run_id="r_cli_001")
    cw.save(state, "completed")

    result = runner.invoke(
        app, ["inspect", "r_cli_001", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "Report:" in result.output
    assert (tmp_path / "r_cli_001" / "report" / "index.html").exists()


def test_inspect_exits_2_when_no_checkpoint(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["inspect", "r_no_run", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 2
    assert "no checkpoints" in result.output.lower() or "Error" in result.output


def test_phase_priority_constants_match_v4() -> None:
    """v4 §3.5 의 상태 머신 순서와 일치 (역순으로 가장 진행된 phase 우선)."""
    assert PHASE_PRIORITY[0] == "completed"
    assert PHASE_PRIORITY[-1] == "planning"
