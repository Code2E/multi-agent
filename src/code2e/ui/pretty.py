"""Rich 기반 TTY pretty 출력 (v4 §4.3)."""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console


@dataclass
class PrettyRenderer:
    console: Console

    def phase_header(self, phase: str, subtitle: str = "") -> None:
        raise NotImplementedError("PrettyRenderer.phase_header — phase 2 구현 예정")

    def step_line(self, status: str, label: str, duration_ms: int, cost_usd: float) -> None:
        raise NotImplementedError("PrettyRenderer.step_line — phase 2 구현 예정")

    def termination(self, reason: str, details: str, suggested_next: str) -> None:
        """DX-7: WHAT/WHERE/WHY/NEXT 4-요소."""
        raise NotImplementedError("PrettyRenderer.termination — phase 2 구현 예정")
