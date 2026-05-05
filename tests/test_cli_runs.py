"""Unit tests for `code2e runs ls / gc / rm` CLI commands."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from code2e.cli.app import app
from code2e.cli.commands.runs import (
    RUN_ID_PREFIX,
    _cutoff_from_duration,
    _is_run_dir,
    collect_row,
    format_size,
)

runner = CliRunner()


# ---------- helpers ----------


def _make_run(runs_dir: Path, run_id: str, status: str = "completed", phase: str = "completed") -> Path:
    rd = runs_dir / run_id
    rd.mkdir(parents=True, exist_ok=True)
    cp_dir = rd / "checkpoints"
    cp_dir.mkdir(exist_ok=True)
    (cp_dir / f"after_{phase}.json").write_text(
        json.dumps({"run_id": run_id, "status": status})
    )
    return rd


# ---------- format_size ----------


def test_format_size_bytes() -> None:
    assert format_size(0).endswith("B")
    assert format_size(512).endswith("B")


def test_format_size_kb() -> None:
    out = format_size(2048)
    assert "KB" in out


def test_format_size_mb() -> None:
    out = format_size(2 * 1024 * 1024)
    assert "MB" in out


# ---------- _cutoff_from_duration ----------


def test_cutoff_30d() -> None:
    cutoff = _cutoff_from_duration("30d")
    expected = datetime.now(UTC) - timedelta(days=30)
    # 1초 차이 허용.
    assert abs((cutoff - expected).total_seconds()) < 5


def test_cutoff_24h() -> None:
    cutoff = _cutoff_from_duration("24h")
    expected = datetime.now(UTC) - timedelta(hours=24)
    assert abs((cutoff - expected).total_seconds()) < 5


def test_cutoff_invalid_format_raises() -> None:
    with pytest.raises(typer.BadParameter):
        _cutoff_from_duration("30 days")


# ---------- _is_run_dir ----------


def test_is_run_dir_accepts_r_prefix(tmp_path: Path) -> None:
    p = tmp_path / "r_1700_a1b2"
    p.mkdir()
    assert _is_run_dir(p) is True


def test_is_run_dir_rejects_other_names(tmp_path: Path) -> None:
    p = tmp_path / "checkpoints"
    p.mkdir()
    assert _is_run_dir(p) is False


def test_is_run_dir_rejects_files(tmp_path: Path) -> None:
    p = tmp_path / "r_1700_a1b2"
    p.write_text("not a dir")
    assert _is_run_dir(p) is False


# ---------- collect_row ----------


def test_collect_row_with_checkpoint(tmp_path: Path) -> None:
    rd = _make_run(tmp_path, "r_1234_aaaa", status="completed", phase="testing")
    row = collect_row(rd)
    assert row.run_id == "r_1234_aaaa"
    assert row.last_phase == "testing"
    assert row.status == "completed"
    assert row.size_bytes > 0


def test_collect_row_without_checkpoints(tmp_path: Path) -> None:
    rd = tmp_path / "r_5678_bbbb"
    rd.mkdir()
    row = collect_row(rd)
    assert row.last_phase == "—"
    assert row.status == "—"


def test_collect_row_handles_corrupt_checkpoint(tmp_path: Path) -> None:
    rd = tmp_path / "r_9999_cccc"
    cp = rd / "checkpoints"
    cp.mkdir(parents=True)
    (cp / "after_planning.json").write_text("not valid json {{")
    row = collect_row(rd)
    assert row.status == "—"


# ---------- ls ----------


def test_ls_empty_directory(tmp_path: Path) -> None:
    result = runner.invoke(app, ["runs", "ls", "--runs-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "No runs" in result.output


def test_ls_missing_directory(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["runs", "ls", "--runs-dir", str(tmp_path / "no")]
    )
    assert result.exit_code == 0
    assert "No runs directory" in result.output


def test_ls_lists_runs(tmp_path: Path) -> None:
    _make_run(tmp_path, "r_1700_aaaa")
    _make_run(tmp_path, "r_1800_bbbb")
    result = runner.invoke(app, ["runs", "ls", "--runs-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "r_1700_aaaa" in result.output
    assert "r_1800_bbbb" in result.output


def test_ls_skips_non_run_directories(tmp_path: Path) -> None:
    _make_run(tmp_path, "r_1700_aaaa")
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "stray-file.txt").write_text("x")
    result = runner.invoke(app, ["runs", "ls", "--runs-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "r_1700_aaaa" in result.output
    assert "stray-file.txt" not in result.output
    assert "checkpoints" not in result.output


# ---------- gc ----------


def test_gc_dry_run_does_not_delete(tmp_path: Path) -> None:
    """30 일 전 mtime 으로 위조 후 dry-run."""
    rd = _make_run(tmp_path, "r_old_aaaa")
    old_ts = time.time() - 60 * 24 * 60 * 60  # 60 일 전.
    os.utime(rd, (old_ts, old_ts))

    result = runner.invoke(
        app, ["runs", "gc", "--older-than", "30d", "--dry-run", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "r_old_aaaa" in result.output
    assert "Would delete" in result.output
    assert rd.exists()  # dry-run 이라 안 지움.


def test_gc_actually_deletes(tmp_path: Path) -> None:
    rd = _make_run(tmp_path, "r_old_bbbb")
    old_ts = time.time() - 60 * 24 * 60 * 60
    os.utime(rd, (old_ts, old_ts))

    result = runner.invoke(
        app, ["runs", "gc", "--older-than", "30d", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "Deleted" in result.output
    assert not rd.exists()


def test_gc_skips_recent_runs(tmp_path: Path) -> None:
    rd = _make_run(tmp_path, "r_new_cccc")
    # mtime 은 현재 시각 (방금 생성).
    result = runner.invoke(
        app, ["runs", "gc", "--older-than", "30d", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "Skipped (newer than cutoff): 1" in result.output
    assert rd.exists()


def test_gc_invalid_duration_format(tmp_path: Path) -> None:
    _make_run(tmp_path, "r_x_yyyy")
    result = runner.invoke(
        app,
        [
            "runs",
            "gc",
            "--older-than",
            "30 days",
            "--runs-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "invalid duration" in result.output.lower()


def test_gc_missing_runs_dir(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["runs", "gc", "--runs-dir", str(tmp_path / "absent")]
    )
    assert result.exit_code == 0
    assert "No runs directory" in result.output


# ---------- rm ----------


def test_rm_deletes_run(tmp_path: Path) -> None:
    rd = _make_run(tmp_path, "r_del_zzzz")
    result = runner.invoke(
        app, ["runs", "rm", "r_del_zzzz", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "Deleted" in result.output
    assert not rd.exists()


def test_rm_rejects_invalid_run_id(tmp_path: Path) -> None:
    """run_id 가 r_ prefix 가 아니면 거부 — 다른 디렉토리 삭제 방지."""
    (tmp_path / "important_data").mkdir()
    result = runner.invoke(
        app, ["runs", "rm", "important_data", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 2
    assert RUN_ID_PREFIX in result.output
    assert (tmp_path / "important_data").exists()  # 안 지워졌음.


def test_rm_exits_2_when_not_found(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["runs", "rm", "r_does_not_exist", "--runs-dir", str(tmp_path)]
    )
    assert result.exit_code == 2
    assert "not found" in result.output.lower()


# ---------- top-level help ----------


def test_runs_help() -> None:
    result = runner.invoke(app, ["runs", "--help"])
    assert result.exit_code == 0
    out = result.output
    for sub in ("ls", "gc", "rm"):
        assert sub in out
