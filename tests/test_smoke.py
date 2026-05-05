"""Smoke test — 스캐폴드 import 정합성 검증.

이 테스트가 통과한다 = 모든 모듈이 import 가능 + Pydantic / Protocol 정의가 일관됨.
"""

from __future__ import annotations


def test_package_imports() -> None:
    import code2e
    from code2e.agents import advisor, evaluator, executor, planner
    from code2e.agents import base as agents_base
    from code2e.cli import app as cli_app
    from code2e.core import (
        budget,
        cassette,
        checkpoint,
        hooks,
        llm_gateway,
        logger,
        orchestrator,
        port_allocator,
        process_manager,
        schemas,
        state,
        termination,
    )
    from code2e.runners import base as runners_base
    from code2e.runners import playwright_runner
    from code2e.ui import jsonl, pretty

    # 임포트만으로 충분 — 단순히 reachable 한지 확인.
    assert code2e.__version__
    assert agents_base.Agent is not None
    assert planner.PlannerAgent.name == "planner"
    assert executor.ExecutorAgent.name == "executor"
    assert advisor.AdvisorAgent.name == "advisor"
    assert evaluator.EvaluatorTestgenAgent.name == "evaluator.testgen"
    assert evaluator.EvaluatorTestrunAgent.name == "evaluator.testrun"
    assert runners_base.TestRunner is not None
    assert playwright_runner.PlaywrightRunner.name == "playwright"
    assert cli_app.app is not None
    assert hasattr(jsonl, "JsonlRenderer")
    assert hasattr(pretty, "PrettyRenderer")
    # core 모듈은 import 가능성만 확인.
    assert all(
        m is not None
        for m in (
            budget,
            cassette,
            checkpoint,
            hooks,
            llm_gateway,
            logger,
            orchestrator,
            port_allocator,
            process_manager,
            schemas,
            state,
            termination,
        )
    )


def test_run_id_format() -> None:
    from code2e.core.state import new_run_id

    rid = new_run_id()
    assert rid.startswith("r_")
    parts = rid.split("_")
    assert len(parts) == 3
    assert parts[1].isdigit()
    assert len(parts[2]) == 4  # secrets.token_hex(2) → 4 hex chars


def test_termination_reason_includes_launch_spec_missing() -> None:
    """v4 보정 #1: LAUNCH_SPEC_MISSING 이 enum 에 포함."""
    from typing import get_args

    from code2e.core.schemas import TerminationReason

    assert "LAUNCH_SPEC_MISSING" in get_args(TerminationReason)


def test_executor_input_has_regression_context() -> None:
    """v4 보정 #3: ExecutorInput.regression_context 필드 존재."""
    from code2e.core.schemas import ExecutorInput

    assert "regression_context" in ExecutorInput.model_fields


def test_process_manager_has_restart() -> None:
    """v4 보정 #2: ProcessManager.restart 메서드 존재."""
    from code2e.core.process_manager import ProcessManager

    assert hasattr(ProcessManager, "restart")


def test_is_stagnant_basic() -> None:
    """문서 §8.2 라인 1220-1227 의 함수 의미 검증."""
    from code2e.core.schemas import AdvisorFeedback
    from code2e.core.termination import is_stagnant

    def fb(sig: str) -> AdvisorFeedback:
        return AdvisorFeedback(unit_id="U-1", decision="revise", signature=sig)

    # 짧은 history → False.
    assert is_stagnant([fb("a"), fb("b")]) is False
    # 같은 signature 가 3 회 연속 → True (window=2).
    assert is_stagnant([fb("a"), fb("x"), fb("x"), fb("x")]) is True
    # 다른 signature → False.
    assert is_stagnant([fb("a"), fb("b"), fb("c"), fb("d")]) is False


def test_decide_force_stop_on_empty_revise() -> None:
    """Q11: revise + 빈 코멘트 → force-stop."""
    from code2e.core.schemas import AdvisorFeedback, FeedbackComment
    from code2e.core.termination import decide_force_stop_on_empty_revise

    empty_revise = AdvisorFeedback(unit_id="U-1", decision="revise", signature="x", comments=[])
    nonempty_revise = AdvisorFeedback(
        unit_id="U-1",
        decision="revise",
        signature="x",
        comments=[FeedbackComment(message="fix")],
    )
    approve = AdvisorFeedback(unit_id="U-1", decision="approve", signature="x", comments=[])

    assert decide_force_stop_on_empty_revise(empty_revise) is True
    assert decide_force_stop_on_empty_revise(nonempty_revise) is False
    assert decide_force_stop_on_empty_revise(approve) is False
