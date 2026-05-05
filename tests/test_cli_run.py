"""Unit tests for `code2e run` CLI command.

Typer CliRunner 로 인자 검증 + 헬퍼 함수 단위 테스트. 실제 LLM 호출은 안 하고
asyncio.run 까지 가는 통합 테스트는 monkeypatch 로 Orchestrator 를 swap 하여 검증.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from code2e.cli.app import app
from code2e.cli.commands.run import (
    _get,
    _load_config,
    _resolve_task,
)

runner = CliRunner()


# ---------- typer help ----------


def test_run_help_shows_expected_options() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    out = result.output
    for option in ("--task-file", "--budget-usd", "--cassette", "--record", "--replay"):
        assert option in out


# ---------- _resolve_task ----------


def test_resolve_task_returns_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _resolve_task("  hello  ", None)
    assert out == "hello"


def test_resolve_task_normalizes_nfc() -> None:
    """NFD ('가' = 'ᄀ' + 'ᅡ') → NFC ('가' single codepoint)."""
    nfd = "가"  # NFD 의 '가'.
    out = _resolve_task(nfd, None)
    assert out == "가"  # NFC.


def test_resolve_task_reads_file(tmp_path: Path) -> None:
    p = tmp_path / "task.md"
    p.write_text("build a todo CLI\n", encoding="utf-8")
    out = _resolve_task(None, p)
    assert out == "build a todo CLI"


def test_resolve_task_rejects_both_inline_and_file(tmp_path: Path) -> None:
    p = tmp_path / "task.md"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(typer.Exit) as exc_info:
        _resolve_task("inline", p)
    assert exc_info.value.exit_code == 2


def test_resolve_task_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(typer.Exit) as exc_info:
        _resolve_task(None, tmp_path / "nope.md")
    assert exc_info.value.exit_code == 2


def test_resolve_task_rejects_empty_inputs() -> None:
    with pytest.raises(typer.Exit) as exc_info:
        _resolve_task(None, None)
    assert exc_info.value.exit_code == 2


# ---------- _load_config ----------


def test_load_config_returns_empty_when_file_missing(tmp_path: Path) -> None:
    out = _load_config(tmp_path / "no.yaml")
    assert out == {}


def test_load_config_parses_yaml(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("budget:\n  max_total_usd: 1.5\n", encoding="utf-8")
    out = _load_config(p)
    assert out == {"budget": {"max_total_usd": 1.5}}


def test_load_config_returns_empty_for_non_dict_yaml(tmp_path: Path) -> None:
    """예: 단일 list yaml — 우리는 dict 만 받음."""
    p = tmp_path / "c.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    out = _load_config(p)
    assert out == {}


# ---------- _get (dotted lookup) ----------


def test_get_returns_default_for_missing_path() -> None:
    assert _get({}, "a.b.c", default="X") == "X"


def test_get_returns_value_for_nested_path() -> None:
    cfg = {"budget": {"max_total_usd": 1.5}}
    assert _get(cfg, "budget.max_total_usd") == 1.5


def test_get_returns_default_when_intermediate_is_not_dict() -> None:
    cfg = {"budget": "not a dict"}
    assert _get(cfg, "budget.max_total_usd", default="Z") == "Z"


# ---------- CLI invariants (실제 실행은 안 함) ----------


def test_run_rejects_record_and_replay_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # API key 가 있어야 record path 진입.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    result = runner.invoke(app, ["run", "task", "--record", "--replay"])
    assert result.exit_code == 2
    assert "동시 사용" in result.output


def test_run_requires_task_or_file() -> None:
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 2
    assert "task" in result.output


def test_run_aborts_when_api_key_missing_in_non_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ANTHROPIC_API_KEY 없고 --replay 도 아니면 exit 1."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # config 파일이 cwd 에 있어 mode 가 'auto' (cassette 디폴트) 로 진입할 수 있으니
    # --record 명시로 cassette_mode='record' → API key 필요.
    result = runner.invoke(app, ["run", "build a CLI", "--record"])
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output


# ---------- 통합 (monkeypatch Orchestrator) ----------


def test_run_invokes_orchestrator_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Orchestrator 빌드를 그대로 두되 .start() 만 stub — replay 모드로 LLM/cassette 우회."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # noop, replay 라 미사용.

    captured: dict[str, object] = {}

    async def fake_start(self, user_input: str, run_id: str | None = None):
        from code2e.core.schemas import BudgetState, SystemState

        captured["user_input"] = user_input
        captured["run_id"] = run_id
        return SystemState(
            run_id=run_id or "r_fake_001",
            status="completed",
            user_input=user_input,
            budget=BudgetState(limit_usd=1.0, limit_tokens=1000),
        )

    monkeypatch.setattr(
        "code2e.core.orchestrator.Orchestrator.start", fake_start, raising=True
    )

    result = runner.invoke(
        app,
        [
            "run",
            "build a todo",
            "--replay",  # API key 검사 우회.
            "--runs-dir",
            str(tmp_path / "runs"),
            "--cassettes-dir",
            str(tmp_path / "cassettes"),
            "--run-id",
            "r_explicit_42",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["user_input"] == "build a todo"
    assert captured["run_id"] == "r_explicit_42"
    assert "Status:  completed" in result.output


def test_run_exits_1_when_state_aborted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """state.status='aborted' → exit code 1."""

    async def fake_start(self, user_input: str, run_id: str | None = None):
        from code2e.core.schemas import (
            BudgetState,
            SystemState,
            TerminationInfo,
        )

        return SystemState(
            run_id="r_x",
            status="aborted",
            user_input=user_input,
            budget=BudgetState(limit_usd=1.0, limit_tokens=1000),
            termination=TerminationInfo(
                reason="LAUNCH_SPEC_MISSING",
                phase="Phase L",
                details="no spec",
                suggested_next="prompt 점검",
            ),
        )

    monkeypatch.setattr(
        "code2e.core.orchestrator.Orchestrator.start", fake_start, raising=True
    )

    result = runner.invoke(
        app,
        [
            "run",
            "task",
            "--replay",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--cassettes-dir",
            str(tmp_path / "cassettes"),
        ],
    )
    assert result.exit_code == 1
    assert "LAUNCH_SPEC_MISSING" in result.output
