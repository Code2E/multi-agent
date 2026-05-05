"""BudgetTracker — 토큰/USD 누적 + 한도 검사 (v4 NFR-C-1/2).

- check_headroom: LLM 호출 직전 (누적 + est) 가 한도 초과면 BudgetExceededError raise.
- add: 호출 완료 후 누적.
- usage_ratio: USD / 토큰 비율 중 더 높은 값.
- should_warn: warn_threshold (기본 0.8) 도달 시 처음 1회만 True.

DECISION: Q14 — cheaper 강등은 v1.1. 강등 = 모델 카탈로그 + 라우팅 + 비용 비교 →
새 추상화 3개. v1 은 단일 한도 정책으로 단순화.
순수 계산 객체로 유지: 부수 효과는 호출자(LlmGateway) 가 should_warn() 으로 결정.
"""

from __future__ import annotations

from dataclasses import dataclass


class BudgetExceededError(Exception):
    """Orchestrator 가 TerminationReason.BUDGET_EXCEEDED 로 변환."""


@dataclass
class BudgetTracker:
    limit_usd: float
    limit_tokens: int
    warn_threshold: float = 0.8
    usd_used: float = 0.0
    tokens_used: int = 0
    _warned: bool = False

    def check_headroom(self, est_cost_usd: float = 0.0, est_tokens: int = 0) -> None:
        """LLM 호출 직전 사전 검사. (현재 누적 + est) 가 한도 초과면 raise."""
        projected_usd = self.usd_used + est_cost_usd
        if projected_usd > self.limit_usd:
            raise BudgetExceededError(
                f"USD budget exceeded: {projected_usd:.4f} > {self.limit_usd:.4f}"
            )
        projected_tokens = self.tokens_used + est_tokens
        if projected_tokens > self.limit_tokens:
            raise BudgetExceededError(
                f"Token budget exceeded: {projected_tokens} > {self.limit_tokens}"
            )

    def add(self, tokens_in: int, tokens_out: int, cost_usd: float) -> None:
        """LLM 호출 완료 후 누적."""
        self.tokens_used += tokens_in + tokens_out
        self.usd_used += cost_usd

    @property
    def usage_ratio(self) -> float:
        """USD / 토큰 한도 중 더 높은 비율."""
        usd_ratio = self.usd_used / self.limit_usd if self.limit_usd > 0 else 0.0
        token_ratio = self.tokens_used / self.limit_tokens if self.limit_tokens > 0 else 0.0
        return max(usd_ratio, token_ratio)

    def should_warn(self) -> bool:
        """warn_threshold 도달 시 처음 1회만 True (재경고 방지)."""
        if self._warned:
            return False
        if self.usage_ratio >= self.warn_threshold:
            self._warned = True
            return True
        return False
