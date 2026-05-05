"""Orchestrator — 전체 파이프라인 조율 + 상태 머신 (v4 §3.3, §3.5, §6.1).

상태 전이:
  IDLE → PLANNING → BUILDING → LAUNCHING → TESTING → TEARDOWN → COMPLETED
  실패 시 ABORTED (TerminationReason 기록).

Phase 1 종료 직후 asyncio.TaskGroup 으로 Build (Branch A) 와 Evaluator.testgen
(Branch B) 가 병렬 (ADR-036).

v4 보정 #4: PlanUnit.dependencies 위상 정렬 검증 (validate_unit_dag) — 순환 시
UNIT_DECOMPOSITION_FAILED.

DECISION: Q4 — Planner round 3 명시 출력 + Orchestrator 후처리 정규식 fallback.
DECISION: Q33 — DAG 위상 정렬, 순환 시 UNIT_DECOMPOSITION_FAILED.
DECISION: Q39 — 실패 리포트 raw + 길면 헤드/테일 5KB 씩 컷 (10KB 컷).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml
from pydantic import ValidationError

from code2e.core.schemas import (
    LaunchSpec,
    Plan,
    PlanUnit,
    SystemState,
    TerminationReason,
)

FAILURE_REPORT_HEAD_BYTES = 5 * 1024  # Q39
FAILURE_REPORT_TAIL_BYTES = 5 * 1024


@dataclass
class Orchestrator:
    """파이프라인 진입점. start() 호출 1회 = 1 run."""

    async def start(self, user_input: str) -> SystemState:
        raise NotImplementedError("Orchestrator.start — phase 2 구현 예정 (v4 §6.1)")

    async def _run_planning(self, state: SystemState) -> SystemState:
        """Phase 1: 3-round Planner."""
        raise NotImplementedError("Orchestrator._run_planning — phase 2 구현 예정")

    async def _run_building_and_testgen(self, state: SystemState) -> SystemState:
        """Phase 2: TaskGroup(Build, Evaluator.testgen) (v4 §3.8)."""
        raise NotImplementedError("Orchestrator._run_building_and_testgen — phase 2 구현 예정")

    async def _run_launching(self, state: SystemState) -> SystemState:
        """Phase L: ProcessManager.launch + health-check."""
        raise NotImplementedError("Orchestrator._run_launching — phase 2 구현 예정")

    async def _run_testing(self, state: SystemState) -> SystemState:
        """Phase 3: Evaluator.testrun ↔ Executor revise loop."""
        raise NotImplementedError("Orchestrator._run_testing — phase 2 구현 예정")

    async def _teardown(self, state: SystemState) -> SystemState:
        """SIGTERM (grace 5s) → SIGKILL → port release (v4 §18)."""
        raise NotImplementedError("Orchestrator._teardown — phase 2 구현 예정")


# --- frontmatter 파싱 utilities ---

# 문서 시작의 `---\n ... \n---\n` 블록만 인식. 본문 중간의 `---` 는 무시.
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)

# Fallback 정규식: 본문 헤더 형식 `## U-NNN: title` / `* U-NNN: title` / `- U-NNN: title`.
_UNIT_HEADER_RE = re.compile(r"^[#*\-]+\s*(U-\d+)\s*[:\-]\s*(.+?)\s*$", re.MULTILINE)


def _extract_frontmatter(text: str) -> str | None:
    """문서 처음의 YAML frontmatter 본문 (`---` 사이) 반환. 없으면 None."""
    m = _FRONTMATTER_RE.match(text)
    return m.group(1) if m else None


def _parse_yaml_dict(yaml_text: str) -> dict[str, object] | None:
    """YAML 텍스트를 dict 로 파싱. 파싱 실패 또는 dict 가 아니면 None."""
    try:
        data: object = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def parse_units_from_plan(plan_text: str) -> list[PlanUnit]:
    """Q4: Planner round 3 의 `units:` 파싱.

    1차: frontmatter YAML 의 `units:` 키 → PlanUnit 검증.
    2차 fallback: 본문에서 `## U-NNN: title` 형식 정규식 추출 (id/title 만 채움).

    둘 다 실패면 빈 리스트 반환 — 호출자가 UNIT_DECOMPOSITION_FAILED 로 abort.
    """
    fm = _extract_frontmatter(plan_text)
    if fm is not None:
        data = _parse_yaml_dict(fm)
        if data is not None and isinstance(data.get("units"), list):
            parsed: list[PlanUnit] = []
            for item in data["units"]:  # type: ignore[union-attr]
                if not isinstance(item, dict):
                    continue
                try:
                    parsed.append(PlanUnit.model_validate(item))
                except ValidationError:
                    continue
            if parsed:
                return parsed

    # Fallback: 본문 헤더 정규식. id 와 title 만 채우고 나머지는 빈값.
    fallback: list[PlanUnit] = []
    for match in _UNIT_HEADER_RE.finditer(plan_text):
        uid, title = match.group(1), match.group(2).strip()
        try:
            fallback.append(
                PlanUnit(
                    id=uid,
                    title=title,
                    description="",
                    acceptance_criteria=[],
                )
            )
        except ValidationError:
            continue
    return fallback


def extract_launch_spec_from_plan(plan: Plan) -> LaunchSpec | None:
    """v4 §9.2: plan frontmatter `launch:` 블록 파싱.

    Q41: workspace YAML / heuristic 추론은 v1 미포함. 이 함수가 None 반환 +
    workspace `code2e.launch.yaml` 도 없으면 호출자가 LAUNCH_SPEC_MISSING 로 abort.
    """
    fm = _extract_frontmatter(plan.content)
    if fm is None:
        return None
    data = _parse_yaml_dict(fm)
    if data is None:
        return None
    launch_data = data.get("launch")
    if not isinstance(launch_data, dict):
        return None
    try:
        return LaunchSpec.model_validate(launch_data)
    except ValidationError:
        return None


def validate_unit_dag(units: list[PlanUnit]) -> tuple[bool, TerminationReason | None]:
    """v4 보정 #4 + Q33: dependencies 가 valid id 인지 + 순환 검사.

    검사 1: 모든 unit 의 dependencies id 가 다른 unit.id 에 존재 (dangling reference).
    검사 2: 의존성 그래프에 순환이 없음 (DFS 3-color cycle detection).
    검사 3: unit.id 중복 없음.

    위반 시 (False, "UNIT_DECOMPOSITION_FAILED") 반환. 통과 시 (True, None).
    """
    ids = [u.id for u in units]
    if len(set(ids)) != len(ids):
        return False, "UNIT_DECOMPOSITION_FAILED"  # 중복 id

    id_set = set(ids)
    by_id = {u.id: u for u in units}

    # 검사 1: dangling reference.
    for u in units:
        for dep in u.dependencies:
            if dep not in id_set:
                return False, "UNIT_DECOMPOSITION_FAILED"

    # 검사 2: 3-color DFS cycle detection. white=0 / gray=1 / black=2.
    color = dict.fromkeys(ids, 0)

    def has_cycle(node: str) -> bool:
        color[node] = 1  # gray (진입)
        for dep in by_id[node].dependencies:
            if color[dep] == 1:
                return True  # back edge → 순환
            if color[dep] == 0 and has_cycle(dep):
                return True
        color[node] = 2  # black (완료)
        return False

    for u in units:
        if color[u.id] == 0 and has_cycle(u.id):
            return False, "UNIT_DECOMPOSITION_FAILED"

    return True, None


def topological_sort(units: list[PlanUnit]) -> list[PlanUnit]:
    """Q33 + Q5 (ADR-003): unit 처리 순서를 위상 정렬로 결정 (Kahn's algorithm).

    선행 조건: `validate_unit_dag(units) == (True, None)` 일 때만 호출. 호출자가
    DAG 검증을 먼저 통과시켜야 함. 위반 시 동작은 미정 (caller 책임).

    동률은 입력 순서를 따른다 (안정 정렬).
    """
    by_id = {u.id: u for u in units}
    in_degree = {u.id: len(u.dependencies) for u in units}

    # 입력 순서 유지를 위해 list 로 처리 (heapq 대신).
    queue: list[str] = [u.id for u in units if in_degree[u.id] == 0]
    sorted_ids: list[str] = []

    while queue:
        node = queue.pop(0)
        sorted_ids.append(node)
        # node 의 out-edge 들: node 를 dependency 로 갖는 unit.
        for v in units:
            if node in v.dependencies:
                in_degree[v.id] -= 1
                if in_degree[v.id] == 0:
                    queue.append(v.id)

    return [by_id[i] for i in sorted_ids]


def truncate_failure_report(raw: str) -> str:
    """Q39: 10KB 초과 시 헤드 5KB + 중간 마커 + 테일 5KB.

    바이트 기준 (utf-8 인코딩 후) 으로 컷한다. 마커는 잘린 바이트 수 포함.
    경계가 utf-8 multi-byte 중간이면 안전한 위치까지 줄여 자른다.
    """
    encoded = raw.encode("utf-8")
    total = len(encoded)
    if total <= FAILURE_REPORT_HEAD_BYTES + FAILURE_REPORT_TAIL_BYTES:
        return raw

    head = _safe_decode(encoded[:FAILURE_REPORT_HEAD_BYTES])
    tail = _safe_decode_from_end(encoded[-FAILURE_REPORT_TAIL_BYTES:])
    omitted = total - FAILURE_REPORT_HEAD_BYTES - FAILURE_REPORT_TAIL_BYTES
    return f"{head}\n\n... [{omitted} bytes truncated] ...\n\n{tail}"


def _safe_decode(buf: bytes) -> str:
    """utf-8 multi-byte 끝부분이 잘린 경우 마지막 valid byte 까지만."""
    for i in range(len(buf), max(0, len(buf) - 4) - 1, -1):
        try:
            return buf[:i].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return ""


def _safe_decode_from_end(buf: bytes) -> str:
    """utf-8 multi-byte 시작부분이 잘린 경우 첫 valid byte 부터."""
    for i in range(0, min(4, len(buf)) + 1):
        try:
            return buf[i:].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return ""
