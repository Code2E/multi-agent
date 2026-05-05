"""Unit tests for code2e.core.budget (v4 NFR-C-1/2)."""

from __future__ import annotations

import pytest

from code2e.core.budget import BudgetExceededError, BudgetTracker


def _tracker(usd: float = 1.0, tokens: int = 1000) -> BudgetTracker:
    return BudgetTracker(limit_usd=usd, limit_tokens=tokens)


# ---------- check_headroom ----------


def test_check_headroom_passes_within_limit() -> None:
    t = _tracker()
    t.check_headroom(est_cost_usd=0.5, est_tokens=500)  # no raise


def test_check_headroom_raises_on_usd_overrun() -> None:
    t = _tracker(usd=1.0)
    t.add(0, 0, 0.9)
    with pytest.raises(BudgetExceededError, match="USD"):
        t.check_headroom(est_cost_usd=0.2)


def test_check_headroom_raises_on_token_overrun() -> None:
    t = _tracker(tokens=100)
    t.add(50, 0, 0.0)
    with pytest.raises(BudgetExceededError, match="Token"):
        t.check_headroom(est_tokens=60)


def test_check_headroom_with_zero_estimate_uses_only_accumulated() -> None:
    t = _tracker(usd=1.0)
    t.add(0, 0, 0.99)
    t.check_headroom()  # 누적이 한도 미달이면 통과


def test_check_headroom_at_exact_limit_passes() -> None:
    """동등 시점은 허용. > 만 차단."""
    t = _tracker(usd=1.0)
    t.check_headroom(est_cost_usd=1.0)


# ---------- add ----------


def test_add_accumulates_tokens_and_cost() -> None:
    t = _tracker()
    t.add(100, 50, 0.123)
    assert t.tokens_used == 150
    assert t.usd_used == pytest.approx(0.123)
    t.add(10, 20, 0.001)
    assert t.tokens_used == 180
    assert t.usd_used == pytest.approx(0.124)


# ---------- usage_ratio ----------


def test_usage_ratio_picks_higher_of_usd_token() -> None:
    t = _tracker(usd=1.0, tokens=1000)
    t.add(100, 100, 0.1)  # 200/1000=0.2 vs 0.1/1.0=0.1 → 0.2
    assert t.usage_ratio == pytest.approx(0.2)


def test_usage_ratio_handles_zero_limits() -> None:
    """ZeroDivisionError 회피."""
    t = BudgetTracker(limit_usd=0.0, limit_tokens=0)
    t.add(100, 100, 1.0)
    assert t.usage_ratio == 0.0


# ---------- should_warn ----------


def test_should_warn_false_below_threshold() -> None:
    t = BudgetTracker(limit_usd=1.0, limit_tokens=1000, warn_threshold=0.8)
    t.add(0, 0, 0.5)
    assert t.should_warn() is False


def test_should_warn_true_once_at_threshold() -> None:
    t = BudgetTracker(limit_usd=1.0, limit_tokens=1000, warn_threshold=0.8)
    t.add(0, 0, 0.8)
    assert t.should_warn() is True
    # 두 번째 호출은 False (재경고 방지).
    assert t.should_warn() is False


def test_should_warn_uses_max_of_two_ratios() -> None:
    """USD ratio 0.0 / token ratio 0.85 → warn."""
    t = BudgetTracker(limit_usd=10.0, limit_tokens=1000, warn_threshold=0.8)
    t.add(850, 0, 0.0)
    assert t.should_warn() is True


def test_should_warn_stays_false_after_threshold_drop() -> None:
    """should_warn 한 번 True 후엔 다시 True 안 됨, 비록 ratio 가 다시 올라가도."""
    t = BudgetTracker(limit_usd=1.0, limit_tokens=1000, warn_threshold=0.8)
    t.add(0, 0, 0.85)
    assert t.should_warn() is True
    t.add(0, 0, 0.10)
    assert t.should_warn() is False
