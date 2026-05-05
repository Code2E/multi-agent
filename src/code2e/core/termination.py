"""TerminationStrategy + is_stagnant + signature_fn (v4 §3.3, §8.2-3).

DECISION: Q12 — signature 자체가 해시이므로 문자열 동일성 비교. 임계값 0.92 는
hash collision 안전 마진 (실질적으로 정확 일치만 카운트).
DECISION: Q11 — Advisor 의 revise + 빈 코멘트는 force-stop.
"""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from code2e.core.schemas import AdvisorFeedback, TerminationReason, TestRun

DEFAULT_STAGNATION_WINDOW = 2
DEFAULT_SIMILARITY_THRESHOLD = 0.92


@runtime_checkable
class TerminationStrategy(Protocol):
    def should_terminate(
        self, history: list[AdvisorFeedback]
    ) -> tuple[bool, TerminationReason | None]: ...


def signature_fn(payload: str) -> str:
    """SHA256 짧은 해시 (signature 필드 채우기용)."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _similarity(a: str, b: str) -> float:
    """v1 은 정확 일치만 1.0, 그 외 0.0 (Q12: signature 가 해시이므로 fuzzy 불필요)."""
    return 1.0 if a == b else 0.0


def is_stagnant(
    history: list[AdvisorFeedback],
    window: int = DEFAULT_STAGNATION_WINDOW,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> bool:
    """v4 §8.2 라인 1220-1227 그대로."""
    if len(history) < window + 1:
        return False
    recent = history[-(window + 1) :]
    return all(
        i == 0 or _similarity(f.signature, recent[i - 1].signature) >= threshold
        for i, f in enumerate(recent)
    )


def is_test_stagnant(runs: list[TestRun], window: int = DEFAULT_STAGNATION_WINDOW) -> bool:
    """Phase 3: 직전 라운드와 동일 실패 signature 가 window 회 이상."""
    if len(runs) < window + 1:
        return False
    recent = runs[-(window + 1) :]
    return all(i == 0 or r.signature == recent[i - 1].signature for i, r in enumerate(recent))


def decide_force_stop_on_empty_revise(feedback: AdvisorFeedback) -> bool:
    """Q11: revise 인데 코멘트가 비어있으면 force-stop (정보 없는 revise = 무한 루프)."""
    return feedback.decision == "revise" and len(feedback.comments) == 0
