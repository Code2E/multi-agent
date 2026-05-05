"""`code2e doctor` — 환경/의존성/설정 진단 (v4 §4.5).

DECISION:
- Q49 — 포트 검사는 port_range 시작점 ± 5개 샘플 (NFR-P-4 ≤5s).
- v1 의 cassette schema 검사는 미포함 (cassette 자체 schema_version 으로 처리).

`--fix` 는 안전한 자동 수정만: ./runs / ./cassettes 디렉토리 mkdir.
"""

from __future__ import annotations

import importlib.util
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

import typer
import yaml

PORT_SAMPLE_AROUND = 5  # Q49.

DEFAULT_CONFIG_PATH = Path("config/default.yaml")


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "err"
    message: str
    fix_hint: str = ""


def doctor(
    fix: bool = typer.Option(False, "--fix", help="Apply safe automatic fixes."),
    config_path: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", help="Path to config YAML."
    ),
) -> None:
    """Diagnose runtime / config / filesystem / network / browser."""
    results: list[CheckResult] = []
    results.append(_check_python())
    results.append(_check_anthropic_key())
    results.extend(_check_filesystem(fix=fix))
    results.append(_check_config(config_path))
    results.append(_check_port_range(config_path))
    results.append(_check_playwright())

    _print_results(results)

    n_err = sum(1 for r in results if r.status == "err")
    n_warn = sum(1 for r in results if r.status == "warn")
    typer.echo("")
    typer.echo(f"Summary: {n_err} errors, {n_warn} warnings.")
    if n_err > 0:
        raise typer.Exit(1)


# ---------- individual checks ----------


def _check_python() -> CheckResult:
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 12):
        return CheckResult("Python", "ok", f"Python {major}.{minor}")
    return CheckResult(
        "Python",
        "err",
        f"Python {major}.{minor} < 3.12",
        "install Python 3.12+",
    )


def _check_anthropic_key() -> CheckResult:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return CheckResult("ANTHROPIC_API_KEY", "ok", "set in env")
    return CheckResult(
        "ANTHROPIC_API_KEY",
        "err",
        "not set",
        "cp .env.example .env 후 키 입력 (또는 --replay 로 cassette 재생).",
    )


def _check_filesystem(*, fix: bool) -> list[CheckResult]:
    out: list[CheckResult] = []
    for name in ("runs", "cassettes"):
        p = Path(name)
        if p.exists():
            out.append(CheckResult(f"./{name}", "ok", "exists"))
        elif fix:
            p.mkdir(parents=True, exist_ok=True)
            out.append(CheckResult(f"./{name}", "ok", "created (--fix)"))
        else:
            out.append(
                CheckResult(f"./{name}", "warn", "not found", f"mkdir {name}")
            )
    return out


def _check_config(path: Path) -> CheckResult:
    if not path.exists():
        return CheckResult(
            f"config: {path}", "warn", "not found", "code2e init 또는 직접 생성"
        )
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return CheckResult(
            f"config: {path}", "err", f"invalid YAML: {e}", "syntax 수정"
        )
    if not isinstance(parsed, dict):
        return CheckResult(
            f"config: {path}", "err", "not a YAML mapping", "dict 형태로 작성"
        )
    return CheckResult(f"config: {path}", "ok", "loaded")


def _check_port_range(config_path: Path) -> CheckResult:
    """Q49: port_range 시작점 ± 5개 샘플 (NFR-P-4 ≤5s 보장)."""
    if not config_path.exists():
        return CheckResult("ports", "warn", "config 없음 — port_range 확인 불가")
    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return CheckResult("ports", "warn", "config invalid")
    port_range = cfg.get("generated_app", {}).get("port_range", [3000, 3999])
    if not (isinstance(port_range, list) and len(port_range) == 2):
        return CheckResult("ports", "warn", f"invalid port_range: {port_range}")

    start = int(port_range[0])
    samples = list(range(start, start + PORT_SAMPLE_AROUND))
    free = sum(1 for p in samples if _is_port_free(p))
    if free == 0:
        return CheckResult(
            "ports",
            "err",
            f"none of {samples} free",
            "config.generated_app.port_range 변경 또는 점유 프로세스 종료",
        )
    return CheckResult("ports", "ok", f"{free}/{len(samples)} free in {samples}")


def _is_port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        return False
    else:
        return True
    finally:
        s.close()


def _check_playwright() -> CheckResult:
    if importlib.util.find_spec("playwright") is None:
        return CheckResult(
            "playwright",
            "err",
            "package not installed",
            "uv pip install playwright (or pip install playwright)",
        )
    return CheckResult(
        "playwright",
        "ok",
        "package installed (browser 별도: 'playwright install chromium')",
    )


# ---------- output ----------


_ICONS = {"ok": "✓", "warn": "⚠", "err": "✗"}


def _print_results(results: list[CheckResult]) -> None:
    for r in results:
        icon = _ICONS.get(r.status, "?")
        typer.echo(f"  {icon} {r.name:<32} {r.message}")
        if r.fix_hint and r.status != "ok":
            typer.echo(f"      → fix: {r.fix_hint}")
