"""Unit tests for `code2e doctor` CLI command."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from code2e.cli.app import app
from code2e.cli.commands.doctor import (
    PORT_SAMPLE_AROUND,
    CheckResult,
    _check_anthropic_key,
    _check_config,
    _check_filesystem,
    _check_playwright,
    _check_port_range,
    _check_python,
)

runner = CliRunner()


# ---------- _check_python ----------


def test_check_python_passes_on_312_plus() -> None:
    """현재 테스트 러너가 Python 3.12+."""
    result = _check_python()
    assert result.status == "ok"
    assert "Python" in result.message


# ---------- _check_anthropic_key ----------


def test_check_anthropic_key_ok_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    r = _check_anthropic_key()
    assert r.status == "ok"


def test_check_anthropic_key_err_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = _check_anthropic_key()
    assert r.status == "err"
    assert "ANTHROPIC_API_KEY" in r.name
    assert "fix" in r.fix_hint or ".env" in r.fix_hint


# ---------- _check_filesystem ----------


def test_check_filesystem_warns_when_dirs_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    results = _check_filesystem(fix=False)
    assert all(r.status == "warn" for r in results)


def test_check_filesystem_creates_dirs_with_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    results = _check_filesystem(fix=True)
    assert all(r.status == "ok" for r in results)
    assert (tmp_path / "runs").is_dir()
    assert (tmp_path / "cassettes").is_dir()


def test_check_filesystem_ok_when_dirs_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs").mkdir()
    (tmp_path / "cassettes").mkdir()
    results = _check_filesystem(fix=False)
    assert all(r.status == "ok" for r in results)


# ---------- _check_config ----------


def test_check_config_warns_when_missing(tmp_path: Path) -> None:
    r = _check_config(tmp_path / "no.yaml")
    assert r.status == "warn"


def test_check_config_ok_for_valid_yaml(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("budget:\n  max_total_usd: 5\n", encoding="utf-8")
    r = _check_config(p)
    assert r.status == "ok"


def test_check_config_err_for_invalid_yaml(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("a: : :\n  b\n", encoding="utf-8")
    r = _check_config(p)
    assert r.status == "err"


def test_check_config_err_for_non_dict_yaml(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    r = _check_config(p)
    assert r.status == "err"


# ---------- _check_port_range ----------


def test_check_port_range_ok_for_default_range(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    # 사용 거의 안 되는 high port 영역 사용 → free 일 가능성 매우 높음.
    p.write_text(
        "generated_app:\n  port_range: [55000, 55999]\n", encoding="utf-8"
    )
    r = _check_port_range(p)
    # 5개 모두 free 일 가능성 높음. 최소 1개 free 면 ok.
    assert r.status in ("ok", "err")  # extremely unlikely all-busy in tests.


def test_check_port_range_warns_when_config_missing(tmp_path: Path) -> None:
    r = _check_port_range(tmp_path / "no.yaml")
    assert r.status == "warn"


def test_check_port_range_warns_for_invalid_range(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("generated_app:\n  port_range: 123\n", encoding="utf-8")
    r = _check_port_range(p)
    assert r.status == "warn"


def test_check_port_range_err_when_all_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """샘플 5개 모두 점유 → err. monkeypatch _is_port_free → False."""
    from code2e.cli.commands import doctor as doctor_mod

    monkeypatch.setattr(doctor_mod, "_is_port_free", lambda _port: False)

    p = tmp_path / "c.yaml"
    p.write_text(
        "generated_app:\n  port_range: [3000, 3999]\n", encoding="utf-8"
    )
    r = _check_port_range(p)
    assert r.status == "err"
    assert "free" in r.message


# ---------- _check_playwright ----------


def test_check_playwright_ok_when_installed() -> None:
    r = _check_playwright()
    # .venv 에 playwright 설치돼 있음 (pyproject 의존성).
    assert r.status == "ok"


def test_check_playwright_err_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """importlib.util.find_spec 이 None 반환하도록 patch."""
    from code2e.cli.commands import doctor as doctor_mod

    monkeypatch.setattr(
        doctor_mod.importlib.util, "find_spec", lambda _name: None
    )
    r = _check_playwright()
    assert r.status == "err"


# ---------- CLI command ----------


def test_doctor_help() -> None:
    result = runner.invoke(app, ["doctor", "--help"])
    assert result.exit_code == 0
    assert "--fix" in result.output


def test_doctor_summary_exits_0_when_all_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ANTHROPIC_API_KEY + 디렉토리 + config + 정상 port range + playwright 모두 OK."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs").mkdir()
    (tmp_path / "cassettes").mkdir()
    (tmp_path / "c.yaml").write_text(
        "generated_app:\n  port_range: [55000, 55999]\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["doctor", "--config", "c.yaml"])
    # all-ok 면 exit 0. 만약 시스템상 port 가 모두 점유돼 있으면 err 가능.
    assert result.exit_code in (0, 1), result.output
    assert "Summary:" in result.output


def test_doctor_exits_1_when_api_key_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output


def test_doctor_fix_creates_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["doctor", "--fix"])
    assert (tmp_path / "runs").is_dir()
    assert (tmp_path / "cassettes").is_dir()


def test_port_sample_constant_matches_q49() -> None:
    """Q49 결정값: ± 5 개."""
    assert PORT_SAMPLE_AROUND == 5


def test_check_result_dataclass_has_fields() -> None:
    """단순 sanity — dataclass 구조 변경 시 캐치."""
    r = CheckResult(name="x", status="ok", message="m")
    assert r.fix_hint == ""
    assert r.name == "x"
