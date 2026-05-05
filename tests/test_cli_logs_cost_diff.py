"""Unit tests for `code2e logs / cost / diff` CLI commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from code2e.cli.app import app
from code2e.cli.commands.diff import _build_diff_rows
from code2e.core.checkpoint import CheckpointWriter
from code2e.core.schemas import (
    BudgetState,
    SystemState,
    TerminationInfo,
)

runner = CliRunner()


# ---------- helpers ----------


def _state(
    run_id: str = "r_t_001", status: str = "completed", **budget_kwargs: object
) -> SystemState:
    bk = {
        "limit_usd": 5.0,
        "limit_tokens": 100_000,
        "usd_used": 0.0,
        "tokens_used": 0,
    }
    bk.update(budget_kwargs)
    return SystemState(
        run_id=run_id,
        status=status,  # type: ignore[arg-type]
        user_input="task",
        budget=BudgetState(**bk),  # type: ignore[arg-type]
    )


# ---------- logs ----------


def test_logs_help() -> None:
    result = runner.invoke(app, ["logs", "--help"])
    assert result.exit_code == 0
    assert "--follow" in result.output
    assert "--grep" in result.output


def test_logs_exits_2_when_no_log_file(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["logs", "r_no_run", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 2
    # stderr 메시지는 typer.echo(err=True) — Typer testing 의 .output 에 합쳐짐.
    assert "No log file" in result.output or "no log" in result.output.lower()


def test_logs_prints_lines_when_file_exists(tmp_path: Path) -> None:
    log = tmp_path / "r_x" / "logs" / "events.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        '{"level":"info","msg":"started"}\n{"level":"info","msg":"completed"}\n'
    )
    result = runner.invoke(app, ["logs", "r_x", "--runs-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "started" in result.output
    assert "completed" in result.output


def test_logs_grep_filter(tmp_path: Path) -> None:
    log = tmp_path / "r_y" / "logs" / "events.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        '{"level":"info","msg":"foo"}\n{"level":"info","msg":"bar"}\n'
    )
    result = runner.invoke(
        app, ["logs", "r_y", "--grep", "foo", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "foo" in result.output
    assert "bar" not in result.output


def test_logs_follow_warns_but_succeeds(tmp_path: Path) -> None:
    log = tmp_path / "r_z" / "logs" / "events.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("{}\n")
    result = runner.invoke(
        app, ["logs", "r_z", "--follow", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "v1.1" in result.output


# ---------- cost ----------


def test_cost_help() -> None:
    result = runner.invoke(app, ["cost", "--help"])
    assert result.exit_code == 0


def test_cost_exits_2_when_no_checkpoint(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["cost", "r_no", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 2


def test_cost_prints_usage_summary(tmp_path: Path) -> None:
    cw = CheckpointWriter(runs_root=tmp_path)
    cw.save(_state(run_id="r_c_001", usd_used=0.123, tokens_used=4567), "completed")
    result = runner.invoke(
        app, ["cost", "r_c_001", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "$0.1230" in result.output
    assert "4,567" in result.output
    assert "ratio" in result.output.lower()


def test_cost_handles_zero_budget(tmp_path: Path) -> None:
    cw = CheckpointWriter(runs_root=tmp_path)
    cw.save(
        _state(run_id="r_zero", limit_usd=0.0, limit_tokens=0).model_copy(
            update={
                "budget": BudgetState(limit_usd=0.0, limit_tokens=0)
            }
        ),
        "planning",
    )
    result = runner.invoke(
        app, ["cost", "r_zero", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    # ratio 줄이 빠져도 OK (limit_usd=0 일 때).
    assert "USD used" in result.output


# ---------- diff ----------


def test_diff_help() -> None:
    result = runner.invoke(app, ["diff", "--help"])
    assert result.exit_code == 0


def test_diff_exits_2_when_run_missing(tmp_path: Path) -> None:
    cw = CheckpointWriter(runs_root=tmp_path)
    cw.save(_state(run_id="r_a"), "completed")
    # b 는 없음.
    result = runner.invoke(
        app, ["diff", "r_a", "r_b", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 2


def test_diff_prints_comparison(tmp_path: Path) -> None:
    cw = CheckpointWriter(runs_root=tmp_path)
    cw.save(_state(run_id="r_a", usd_used=0.1), "completed")
    cw.save(_state(run_id="r_b", usd_used=0.5), "completed")
    result = runner.invoke(
        app, ["diff", "r_a", "r_b", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "r_a" in result.output
    assert "r_b" in result.output
    assert "$0.1000" in result.output
    assert "$0.5000" in result.output


def test_build_diff_rows_marks_termination_difference() -> None:
    a = _state(run_id="r_a", status="completed")
    b = _state(run_id="r_b", status="aborted").model_copy(
        update={
            "termination": TerminationInfo(
                reason="LAUNCH_TIMEOUT",
                phase="Phase L",
                details="x",
                suggested_next="y",
            )
        }
    )
    rows = _build_diff_rows(a, b)
    labels = {r[0] for r in rows}
    assert "termination" in labels
    term_row = next(r for r in rows if r[0] == "termination")
    assert term_row[1] == "—"
    assert term_row[2] == "LAUNCH_TIMEOUT"


def test_diff_marks_unchanged_with_space(tmp_path: Path) -> None:
    """동일 status 는 marker 가 '  ' (공백 2개)."""
    cw = CheckpointWriter(runs_root=tmp_path)
    cw.save(_state(run_id="r_a"), "completed")
    cw.save(_state(run_id="r_b"), "completed")
    result = runner.invoke(
        app, ["diff", "r_a", "r_b", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    # status 둘 다 completed → unchanged 마커.
    assert "  status" in result.output


def test_diff_marks_changed_with_neq(tmp_path: Path) -> None:
    """다른 status 는 marker 가 '≠ '."""
    cw = CheckpointWriter(runs_root=tmp_path)
    cw.save(_state(run_id="r_a", status="completed"), "completed")
    cw.save(_state(run_id="r_b", status="aborted"), "testing")
    result = runner.invoke(
        app, ["diff", "r_a", "r_b", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "≠" in result.output  # 적어도 한 row 는 변경됐음.
