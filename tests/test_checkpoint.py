"""Unit tests for code2e.core.checkpoint (v4 §3.13, FR-011, Q19)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from code2e.core.checkpoint import (
    GLOBAL_LOCK_FILE,
    CheckpointError,
    CheckpointWriter,
    GlobalLockHeldError,
)
from code2e.core.schemas import (
    BudgetState,
    Plan,
    PlanMeta,
    PlanState,
    SystemState,
    TerminationInfo,
)

# ---------- helpers ----------


def _state(run_id: str = "r_test_0001", status: str = "planning") -> SystemState:
    return SystemState(
        run_id=run_id,
        status=status,  # type: ignore[arg-type]
        user_input="task",
        budget=BudgetState(limit_usd=5.0, limit_tokens=100_000),
    )


# ---------- save / load ----------


def test_save_writes_after_phase_json(tmp_path: Path) -> None:
    cw = CheckpointWriter(runs_root=tmp_path)
    state = _state()
    path = cw.save(state, "planning")
    assert path == tmp_path / "r_test_0001" / "checkpoints" / "after_planning.json"
    assert path.exists()


def test_save_creates_nested_directories(tmp_path: Path) -> None:
    cw = CheckpointWriter(runs_root=tmp_path / "deep" / "runs")
    cw.save(_state(), "planning")
    assert (tmp_path / "deep" / "runs" / "r_test_0001" / "checkpoints").is_dir()


def test_load_round_trip(tmp_path: Path) -> None:
    cw = CheckpointWriter(runs_root=tmp_path)
    original = _state(run_id="r_round_0002")
    original = original.model_copy(
        update={
            "plan": PlanState(
                iterations=[
                    Plan(
                        version=1,
                        content="## v1",
                        meta=PlanMeta(
                            created_at=datetime(2026, 5, 5, tzinfo=UTC),
                            tokens_in=10,
                            tokens_out=20,
                        ),
                    )
                ]
            )
        }
    )
    cw.save(original, "planning")
    loaded = cw.load("r_round_0002", "planning")
    assert loaded.run_id == original.run_id
    assert loaded.user_input == original.user_input
    assert len(loaded.plan.iterations) == 1
    assert loaded.plan.iterations[0].content == "## v1"


def test_load_preserves_termination_info(tmp_path: Path) -> None:
    cw = CheckpointWriter(runs_root=tmp_path)
    state = _state(status="aborted").model_copy(
        update={
            "termination": TerminationInfo(
                reason="UNIT_DECOMPOSITION_FAILED",
                phase="Phase 1",
                details="no units",
                suggested_next="prompt 점검",
            )
        }
    )
    cw.save(state, "planning")
    loaded = cw.load("r_test_0001", "planning")
    assert loaded.termination is not None
    assert loaded.termination.reason == "UNIT_DECOMPOSITION_FAILED"


def test_load_missing_raises(tmp_path: Path) -> None:
    cw = CheckpointWriter(runs_root=tmp_path)
    with pytest.raises(CheckpointError, match="not found"):
        cw.load("r_nonexistent", "planning")


def test_save_overwrites_same_phase(tmp_path: Path) -> None:
    """같은 phase 에 두 번 save → 두 번째가 덮어쓰기."""
    cw = CheckpointWriter(runs_root=tmp_path)
    s1 = _state()
    s2 = s1.model_copy(update={"user_input": "second"})
    cw.save(s1, "planning")
    cw.save(s2, "planning")
    loaded = cw.load(s1.run_id, "planning")
    assert loaded.user_input == "second"


# ---------- list_phases ----------


def test_list_phases_empty_run(tmp_path: Path) -> None:
    cw = CheckpointWriter(runs_root=tmp_path)
    assert cw.list_phases("r_no_run") == []


def test_list_phases_returns_sorted(tmp_path: Path) -> None:
    cw = CheckpointWriter(runs_root=tmp_path)
    state = _state()
    for phase in ("testing", "planning", "building"):
        cw.save(state, phase)
    assert cw.list_phases(state.run_id) == ["building", "planning", "testing"]


# ---------- global_lock (Q19) ----------


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_global_lock_creates_lock_file(tmp_path: Path) -> None:
    cw = CheckpointWriter(runs_root=tmp_path)
    with cw.global_lock():
        assert (tmp_path / GLOBAL_LOCK_FILE).exists()


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_global_lock_blocks_concurrent_acquire(tmp_path: Path) -> None:
    """같은 process 안 다른 fd 로 LOCK_NB 시도 → GlobalLockHeldError."""
    cw1 = CheckpointWriter(runs_root=tmp_path)
    cw2 = CheckpointWriter(runs_root=tmp_path)
    with cw1.global_lock():
        with pytest.raises(GlobalLockHeldError):
            with cw2.global_lock():
                pass


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_global_lock_releases_on_exit(tmp_path: Path) -> None:
    """첫 lock 종료 후 두 번째 acquire 가능."""
    cw = CheckpointWriter(runs_root=tmp_path)
    with cw.global_lock():
        pass
    # 종료 후 다시 잡을 수 있어야.
    with cw.global_lock():
        pass


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_global_lock_releases_on_exception(tmp_path: Path) -> None:
    """예외 발생해도 finally 가 lock 해제."""
    cw = CheckpointWriter(runs_root=tmp_path)

    class _BoomError(Exception):
        pass

    with pytest.raises(_BoomError):
        with cw.global_lock():
            raise _BoomError("boom")
    # 예외 후에도 다시 잡을 수 있어야.
    with cw.global_lock():
        pass


def test_global_lock_runs_root_auto_created(tmp_path: Path) -> None:
    """runs_root 가 없어도 mkdir 후 lock 가능."""
    cw = CheckpointWriter(runs_root=tmp_path / "doesnotexist_yet")
    with cw.global_lock():
        assert (tmp_path / "doesnotexist_yet").is_dir()
