"""BudgetTracker — 토큰/USD 누적 + 한도 검사 (v4 NFR-C-1/2).

80% 도달 시 경고, 100% 도달 시 즉시 차단 (BudgetExceededError).
DECISION: Q14 — cheaper 강등은 v1.1.
"""

from __future__ import annotations

from dataclasses import dataclass


class BudgetExceededError(Exception):
    """v1 budget hard ceiling. Orchestrator 가 BUDGET_EXCEEDED 로 변환."""


@dataclass
class BudgetTracker:
    limit_usd: float
    limit_tokens: int
    warn_threshold: float = 0.8
    usd_used: float = 0.0
    tokens_used: int = 0
    _warned: bool = False

    def check_headroom(self, est_cost_usd: float = 0.0, est_tokens: int = 0) -> None:
        """LLM 호출 직전 사전 검사. 한도 초과 시 즉시 raise."""
        raise NotImplementedError("BudgetTracker.check_headroom — phase 2 구현 예정")

    def add(self, tokens_in: int, tokens_out: int, cost_usd: float) -> None:
        raise NotImplementedError("BudgetTracker.add — phase 2 구현 예정")

    @property
    def usage_ratio(self) -> float:
        raise NotImplementedError("BudgetTracker.usage_ratio — phase 2 구현 예정")
