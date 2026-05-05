"""Pydantic v2 데이터 모델 (v4 Part VII).

모든 에이전트 I/O 와 시스템 상태의 single source of truth (ADR-033).

v4 문서 대비 보정 (병렬 검토에서 발견된 결함):
1. TerminationReason 에 LAUNCH_SPEC_MISSING 추가 (v4 §9.2 라인 1267 언급, Part VII enum 누락).
2. Executor InputModel 에 RegressionContext 경로 신설 (v4 §3.4 회귀 정보 전달 필드 부재).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ---------- Termination reasons ----------
# DECISION: Q41 — LAUNCH_SPEC_MISSING 추가 (v4 §9.2 라인 1267, Part VII 누락 보정).
# 다른 모델들이 forward reference 없이 직접 참조할 수 있도록 파일 최상단에 둔다.
TerminationReason = Literal[
    "COMPLETED",
    "MAX_ITERATIONS",
    "STAGNATION",
    "BUDGET_EXCEEDED",
    "CANCELLED",
    "LLM_FAILURE",
    "VALIDATION_FAILURE",
    "SECURITY_VIOLATION",
    "UNIT_DECOMPOSITION_FAILED",
    "LAUNCH_TIMEOUT",
    "LAUNCH_SPEC_MISSING",  # v4 보정 #1
    "PORT_UNAVAILABLE",
    "APP_CRASHED",
    "TEST_ENV_FAILURE",
    "INTERNAL_ERROR",
]


# ---------- Plan ----------


class PlanUnit(BaseModel):
    id: str  # e.g., "U-001"
    title: str
    description: str
    acceptance_criteria: list[str]
    dependencies: list[str] = Field(default_factory=list)  # Q33: id 리스트 (DAG 검증은 orchestrator)
    estimated_complexity: Literal["low", "med", "high"] = "med"


class PlanMeta(BaseModel):
    created_at: datetime
    tokens_in: int
    tokens_out: int


class Plan(BaseModel):
    version: Literal[1, 2, 3, "final"]
    content: str
    units: list[PlanUnit] = Field(default_factory=list)
    meta: PlanMeta


# ---------- Build ----------


class FileEdit(BaseModel):
    path: str  # workspace 상대 경로
    op: Literal["create", "update", "delete"]
    content: str | None = None


class CodeChange(BaseModel):
    unit_id: str
    files: list[FileEdit]
    rationale: str


class FeedbackComment(BaseModel):
    file: str | None = None
    line: int | None = None
    message: str
    suggestion: str | None = None


class AdvisorFeedback(BaseModel):
    unit_id: str
    decision: Literal["approve", "revise"]
    severity: Literal["low", "med", "high"] = "low"
    comments: list[FeedbackComment] = Field(default_factory=list)
    signature: str  # 정체 감지용 해시 (Q12: 문자열 동일성 비교)


class CodeSnapshotRef(BaseModel):
    iteration: int
    tree_hash: str


class UnitState(BaseModel):
    unit_id: str
    status: Literal["pending", "in_progress", "approved", "force_stopped"] = "pending"
    iteration: int = 0
    feedback_history: list[AdvisorFeedback] = Field(default_factory=list)
    code_snapshots: list[CodeSnapshotRef] = Field(default_factory=list)
    force_stop_reason: TerminationReason | None = None


# ---------- Regression context (v4 보정 #3) ----------


class RegressionContext(BaseModel):
    """Phase 3 에서 회귀 감지 시 Executor 에 전달되는 컨텍스트.

    v4 §8.3 라인 1238: "Executor 에 회귀가 발생했음 + 이전에 통과하던 케이스 X 가
    깨졌음을 명시 전달" 의 구조화. v4 §3.4 의 Executor InputModel 에 회귀 필드가
    누락되어 있어 신설.
    """

    previously_passing_case_ids: list[str]
    note: str = ""


# ---------- Generated App Launch (v4 §9, NEW in v4) ----------


class HealthCheckSpec(BaseModel):
    method: Literal["HTTP_GET", "TCP_CONNECT", "STDOUT_MATCH", "FILE_EXISTS"]
    target: str = "/"  # path / "host:port" / 정규식 / 파일 경로
    expected_status: list[int] = Field(default_factory=lambda: [200, 301, 302, 404])
    interval_ms: int = 500


class LaunchSpec(BaseModel):
    """Plan frontmatter 또는 워크스페이스 YAML 에서 추출.

    Q41 결정: v1 은 휴리스틱 추론 미포함. plan / yaml 모두 없으면 LAUNCH_SPEC_MISSING.
    Q47 결정: HealthCheckSpec.method 4 종 정의는 유지하되 v1 구현은 HTTP_GET / TCP_CONNECT 만.
    """

    kind: Literal["http", "cli", "worker"]  # Q9: worker 는 v1.1
    command: list[str]
    cwd: str = "."
    env: dict[str, str] = Field(default_factory=dict)
    port_hint: int | None = None
    health_check: HealthCheckSpec
    startup_timeout_s: int = 30
    teardown_grace_s: int = 5  # Q43: setsid 와 함께 process group 정리


class LaunchInfo(BaseModel):
    pid: int
    port: int | None
    base_url: str | None
    started_at: datetime
    healthy_at: datetime | None = None
    log_path: str


# ---------- Test ----------


class TestCase(BaseModel):
    id: str  # e.g., "T-001"
    scenario: str
    given: str
    when: str
    then: str
    runner_script: str  # Playwright 스크립트
    plan_unit_refs: list[str] = Field(default_factory=list)


class TestResult(BaseModel):
    case_id: str
    status: Literal["passed", "failed", "errored", "skipped"]
    duration_ms: int
    failure_reason: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)  # screenshot 경로 등


class TestSummary(BaseModel):
    passed: int
    failed: int
    errored: int
    total: int


class TestRun(BaseModel):
    iteration: int
    results: list[TestResult]
    summary: TestSummary
    signature: str  # Q34: v1 은 1회 실행, signature 는 실패 케이스 id+reason 해시


# ---------- Termination ----------


class TerminationInfo(BaseModel):
    reason: TerminationReason
    phase: str
    details: str
    suggested_next: str  # DX-7


# ---------- Budget / Timing ----------


class BudgetState(BaseModel):
    tokens_used: int = 0
    usd_used: float = 0.0
    limit_usd: float
    limit_tokens: int


class PhaseTiming(BaseModel):
    started_at: datetime
    ended_at: datetime | None = None


# ---------- System State ----------


class PlanState(BaseModel):
    iterations: list[Plan] = Field(default_factory=list)
    final: Plan | None = None
    launch_spec: LaunchSpec | None = None


class BuildState(BaseModel):
    units: list[UnitState] = Field(default_factory=list)


class TestState(BaseModel):
    suite: list[TestCase] | None = None
    runs: list[TestRun] = Field(default_factory=list)
    status: Literal["pending", "passed", "failed", "force_stopped"] = "pending"


class SystemState(BaseModel):
    schema_version: int = 1  # v4 §3.6: 마이그레이션 호환성

    run_id: str
    status: Literal[
        "idle",
        "planning",
        "building",
        "launching",
        "testing",
        "completed",
        "aborted",
    ] = "idle"
    user_input: str

    plan: PlanState = Field(default_factory=PlanState)
    build: BuildState = Field(default_factory=BuildState)
    launch: LaunchInfo | None = None
    test: TestState = Field(default_factory=TestState)

    termination: TerminationInfo | None = None
    budget: BudgetState
    timings: dict[str, PhaseTiming] = Field(default_factory=dict)


# ---------- Cassette ----------


class CassetteEntry(BaseModel):
    schema_version: int = 1
    key: str
    agent: str
    agent_version: str
    model_id: str
    request: dict[str, object]  # canonical
    response: dict[str, object]
    tokens_in: int
    tokens_out: int
    cost_usd: float
    recorded_at: datetime
    redactions: list[dict[str, object]] = Field(default_factory=list)


class CassetteManifest(BaseModel):
    schema_version: int = 1
    name: str
    created_at: datetime
    entry_count: int
    llm_provider: str


# ---------- Agent input/output models (v4 §3.4 표) ----------


class PlannerInput(BaseModel):
    user_input: str
    prev_plan: Plan | None = None
    round: Literal[1, 2, 3]


class ExecutorInput(BaseModel):
    """v4 보정 #3: regression_context 신설 (회귀 정보 전달 경로)."""

    unit: PlanUnit
    files: list[FileEdit] = Field(default_factory=list)  # 현재 워크스페이스 스냅샷
    feedback: AdvisorFeedback | None = None
    test_failure: TestRun | None = None
    regression_context: RegressionContext | None = None  # v4 보정 #3


class AdvisorInput(BaseModel):
    unit: PlanUnit
    code: list[FileEdit]
    prior_feedback: list[AdvisorFeedback] = Field(default_factory=list)


class EvaluatorTestgenInput(BaseModel):
    final_plan: Plan


class EvaluatorTestrunInput(BaseModel):
    workspace: str  # 워크스페이스 디렉토리 절대경로
    suite: list[TestCase]
    base_url: str | None = None  # http kind 인 경우
