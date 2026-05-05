"""Unit tests for code2e.core.orchestrator pure functions.

Covers v4 보정 #4 (DAG validate) + Q33 (위상 정렬) + Q39 (실패 리포트 컷) +
Q4 (parse_units_from_plan: frontmatter + fallback) + Q41 (extract_launch_spec_from_plan).
"""

from __future__ import annotations

from datetime import UTC, datetime

from code2e.core.orchestrator import (
    FAILURE_REPORT_HEAD_BYTES,
    FAILURE_REPORT_TAIL_BYTES,
    extract_launch_spec_from_plan,
    parse_units_from_plan,
    topological_sort,
    truncate_failure_report,
    validate_unit_dag,
)
from code2e.core.schemas import Plan, PlanMeta, PlanUnit


def _u(uid: str, deps: list[str] | None = None) -> PlanUnit:
    return PlanUnit(
        id=uid,
        title=f"unit {uid}",
        description="desc",
        acceptance_criteria=["x"],
        dependencies=deps or [],
    )


# ---------- validate_unit_dag ----------


def test_validate_dag_empty_passes() -> None:
    ok, reason = validate_unit_dag([])
    assert ok is True
    assert reason is None


def test_validate_dag_single_unit_no_deps() -> None:
    ok, reason = validate_unit_dag([_u("U-1")])
    assert ok is True
    assert reason is None


def test_validate_dag_linear_chain_passes() -> None:
    units = [_u("U-1"), _u("U-2", ["U-1"]), _u("U-3", ["U-2"])]
    ok, reason = validate_unit_dag(units)
    assert ok is True
    assert reason is None


def test_validate_dag_diamond_passes() -> None:
    """U-1 → U-2, U-3 → U-4 (양쪽 의존)."""
    units = [_u("U-1"), _u("U-2", ["U-1"]), _u("U-3", ["U-1"]), _u("U-4", ["U-2", "U-3"])]
    ok, reason = validate_unit_dag(units)
    assert ok is True


def test_validate_dag_self_loop_fails() -> None:
    units = [_u("U-1", ["U-1"])]
    ok, reason = validate_unit_dag(units)
    assert ok is False
    assert reason == "UNIT_DECOMPOSITION_FAILED"


def test_validate_dag_two_node_cycle_fails() -> None:
    units = [_u("U-1", ["U-2"]), _u("U-2", ["U-1"])]
    ok, reason = validate_unit_dag(units)
    assert ok is False
    assert reason == "UNIT_DECOMPOSITION_FAILED"


def test_validate_dag_three_node_cycle_fails() -> None:
    units = [_u("U-1", ["U-3"]), _u("U-2", ["U-1"]), _u("U-3", ["U-2"])]
    ok, reason = validate_unit_dag(units)
    assert ok is False


def test_validate_dag_dangling_reference_fails() -> None:
    units = [_u("U-1", ["U-NONEXISTENT"])]
    ok, reason = validate_unit_dag(units)
    assert ok is False
    assert reason == "UNIT_DECOMPOSITION_FAILED"


def test_validate_dag_duplicate_ids_fails() -> None:
    units = [_u("U-1"), _u("U-1")]
    ok, reason = validate_unit_dag(units)
    assert ok is False
    assert reason == "UNIT_DECOMPOSITION_FAILED"


def test_validate_dag_disconnected_components_pass() -> None:
    """A→B, C→D 두 별개 그래프."""
    units = [_u("U-A"), _u("U-B", ["U-A"]), _u("U-C"), _u("U-D", ["U-C"])]
    ok, reason = validate_unit_dag(units)
    assert ok is True


# ---------- topological_sort ----------


def test_topo_sort_linear_chain() -> None:
    units = [_u("U-1"), _u("U-2", ["U-1"]), _u("U-3", ["U-2"])]
    out = topological_sort(units)
    ids = [u.id for u in out]
    assert ids == ["U-1", "U-2", "U-3"]


def test_topo_sort_respects_dependencies() -> None:
    """U-3 가 U-1 을 의존해도 U-1 이 먼저 와야 한다 (입력 순서가 뒤섞여 있어도)."""
    units = [_u("U-3", ["U-1"]), _u("U-1"), _u("U-2", ["U-1"])]
    out = topological_sort(units)
    ids = [u.id for u in out]
    assert ids.index("U-1") < ids.index("U-2")
    assert ids.index("U-1") < ids.index("U-3")


def test_topo_sort_diamond() -> None:
    """U-1 → {U-2, U-3} → U-4."""
    units = [_u("U-1"), _u("U-2", ["U-1"]), _u("U-3", ["U-1"]), _u("U-4", ["U-2", "U-3"])]
    out = topological_sort(units)
    ids = [u.id for u in out]
    assert ids[0] == "U-1"
    assert ids[-1] == "U-4"
    assert set(ids[1:3]) == {"U-2", "U-3"}


def test_topo_sort_independent_units_keep_input_order() -> None:
    """의존성이 없으면 입력 순서가 유지 (안정 정렬)."""
    units = [_u("U-A"), _u("U-B"), _u("U-C")]
    out = topological_sort(units)
    assert [u.id for u in out] == ["U-A", "U-B", "U-C"]


def test_topo_sort_returns_same_units() -> None:
    """입력의 모든 unit 이 출력에 정확히 한 번씩 포함."""
    units = [_u("U-1"), _u("U-2", ["U-1"]), _u("U-3", ["U-1"])]
    out = topological_sort(units)
    assert len(out) == 3
    assert {u.id for u in out} == {"U-1", "U-2", "U-3"}


# ---------- truncate_failure_report ----------


def test_truncate_short_text_unchanged() -> None:
    raw = "short failure"
    assert truncate_failure_report(raw) == raw


def test_truncate_at_exact_threshold_unchanged() -> None:
    raw = "x" * (FAILURE_REPORT_HEAD_BYTES + FAILURE_REPORT_TAIL_BYTES)
    assert truncate_failure_report(raw) == raw


def test_truncate_long_text_keeps_head_and_tail() -> None:
    head = "H" * FAILURE_REPORT_HEAD_BYTES
    middle = "M" * 1000
    tail = "T" * FAILURE_REPORT_TAIL_BYTES
    raw = head + middle + tail
    out = truncate_failure_report(raw)
    assert out.startswith(head[:100])  # 헤드 보존
    assert out.endswith(tail[-100:])  # 테일 보존
    assert "M" * 1000 not in out  # 중간 제거
    assert "truncated" in out


def test_truncate_marker_records_omitted_byte_count() -> None:
    raw = "x" * (FAILURE_REPORT_HEAD_BYTES + 2000 + FAILURE_REPORT_TAIL_BYTES)
    out = truncate_failure_report(raw)
    assert "[2000 bytes truncated]" in out


def test_truncate_safe_with_multibyte_utf8() -> None:
    """utf-8 multi-byte 가 잘리는 경계에서도 valid utf-8 출력."""
    # 한글 (3 bytes/char) 로 5KB+ 채우기.
    big = "한" * 4000  # ~12KB
    out = truncate_failure_report(big)
    # decode 시점에 valid utf-8 이어야 (UnicodeDecodeError 없어야).
    out.encode("utf-8").decode("utf-8")
    assert "truncated" in out


# ---------- parse_units_from_plan ----------


_PLAN_WITH_UNITS_FRONTMATTER = """\
---
agent: planner
round: 3
units:
  - id: U-001
    title: scaffold
    description: bootstrap project
    acceptance_criteria:
      - "compiles"
  - id: U-002
    title: api
    description: add REST endpoints
    acceptance_criteria:
      - "GET / returns 200"
    dependencies:
      - U-001
---

# Plan body
text body here.
"""


def test_parse_units_frontmatter_single() -> None:
    plan = """\
---
units:
  - id: U-1
    title: only one
    description: d
    acceptance_criteria: [a]
---
"""
    out = parse_units_from_plan(plan)
    assert len(out) == 1
    assert out[0].id == "U-1"
    assert out[0].title == "only one"


def test_parse_units_frontmatter_multiple_with_deps() -> None:
    out = parse_units_from_plan(_PLAN_WITH_UNITS_FRONTMATTER)
    assert [u.id for u in out] == ["U-001", "U-002"]
    assert out[1].dependencies == ["U-001"]


def test_parse_units_no_frontmatter_no_fallback_returns_empty() -> None:
    out = parse_units_from_plan("plain markdown with no unit headers.")
    assert out == []


def test_parse_units_fallback_via_markdown_headers() -> None:
    """frontmatter 가 없으면 본문에서 `## U-NNN: title` 형식 추출."""
    plan = """\
# Plan

## U-001: scaffold project
some text

## U-002: implement api
more text
"""
    out = parse_units_from_plan(plan)
    assert [u.id for u in out] == ["U-001", "U-002"]
    assert out[0].title == "scaffold project"
    assert out[1].title == "implement api"


def test_parse_units_fallback_via_bullet_list() -> None:
    """`- U-NNN: title` 형식도 fallback 패턴에 포함."""
    plan = """\
- U-1: first
- U-2: second
"""
    out = parse_units_from_plan(plan)
    assert {u.id for u in out} == {"U-1", "U-2"}


def test_parse_units_invalid_yaml_falls_back_to_regex() -> None:
    """frontmatter 가 broken YAML 이면 fallback 으로 본문 정규식 시도."""
    plan = """\
---
units: [{not: valid yaml syntax: here
---

## U-1: recovered
"""
    out = parse_units_from_plan(plan)
    # YAML 깨짐 → fallback 정규식이 본문에서 U-1 추출.
    assert any(u.id == "U-1" for u in out)


def test_parse_units_skips_invalid_items_in_units_list() -> None:
    """units 배열 안 invalid 항목은 건너뛰고 valid 만 반환."""
    plan = """\
---
units:
  - id: U-1
    title: ok
    description: d
    acceptance_criteria: [a]
  - not_a_dict
  - id: U-2
    title: also ok
    description: d
    acceptance_criteria: [a]
---
"""
    out = parse_units_from_plan(plan)
    assert {u.id for u in out} == {"U-1", "U-2"}


# ---------- extract_launch_spec_from_plan ----------


def _plan(content: str) -> Plan:
    return Plan(
        version=3,
        content=content,
        meta=PlanMeta(
            created_at=datetime(2026, 5, 5, tzinfo=UTC),
            tokens_in=0,
            tokens_out=0,
        ),
    )


def test_extract_launch_spec_http_kind() -> None:
    plan = _plan(
        """\
---
launch:
  kind: http
  command: ["python", "-m", "my_app"]
  health_check:
    method: HTTP_GET
    target: /
---
"""
    )
    spec = extract_launch_spec_from_plan(plan)
    assert spec is not None
    assert spec.kind == "http"
    assert spec.command == ["python", "-m", "my_app"]
    assert spec.health_check.method == "HTTP_GET"


def test_extract_launch_spec_returns_none_without_frontmatter() -> None:
    spec = extract_launch_spec_from_plan(_plan("just plain text"))
    assert spec is None


def test_extract_launch_spec_returns_none_when_launch_key_absent() -> None:
    plan = _plan(
        """\
---
units: []
---
"""
    )
    assert extract_launch_spec_from_plan(plan) is None


def test_extract_launch_spec_returns_none_on_invalid_spec() -> None:
    """launch 가 dict 이지만 LaunchSpec 검증 실패 (필수 필드 누락)."""
    plan = _plan(
        """\
---
launch:
  kind: http
  # command / health_check 누락 → ValidationError → None.
---
"""
    )
    assert extract_launch_spec_from_plan(plan) is None
