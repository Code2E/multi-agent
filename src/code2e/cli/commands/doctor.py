"""`code2e doctor` — 환경/의존성/설정 진단 (v4 §4.5).

DECISION: Q49 — 포트 검사는 port_range 시작점 ± 5개 샘플 (전체 1000 검사 = NFR-P-4 위반).
"""

from __future__ import annotations

import typer

PORT_SAMPLE_AROUND = 5  # Q49


def doctor(
    fix: bool = typer.Option(False, "--fix", help="Apply safe automatic fixes."),
) -> None:
    """Diagnose runtime / config / filesystem / network / cassettes."""
    raise NotImplementedError("doctor — v4 §4.5 진단 항목 구현 예정")
