# Code2E CLI

`reference/multi-agent-system-plan-v4.md` 가 명세하는 **Python 3.12+ / localhost 전용 멀티-에이전트 코드 생성 CLI 도구** 의 구현 저장소.

> 같은 이름의 학부 연구 프로젝트(`paran/code2e-agent`) 와는 **별개**. 본 저장소는 v4 plan 의 산출물(production CLI) 을 만든다.

---

## 핵심 문서

- `../code2e-agent/reference/multi-agent-system-plan-v4.md` — 종합 설계 문서 (1797줄). 모든 기술적 결정의 단일 출처. **코드 작업 전 반드시 참조.**
- `~/.claude/plans/reference-multi-agent-system-plan-v4-md-humming-ocean.md` — 본 저장소 스캐폴드 계획 + §19.2/§19.3 결정 + v4 보정 4건. 신규 모듈 추가 시 결정 추적성용으로 다시 참조.

---

## 기술 스택

- **Python 3.12+** (TaskGroup, ExceptionGroup 활용)
- 패키지: `pip install -e ".[dev]"` (uv 도 가능)
- **Pydantic v2** — 모든 공개 타입의 single source of truth (ADR-033)
- **Typer + Rich** — CLI / TTY 출력
- **structlog** — 구조화 로깅 + contextvar 자동 전파
- **httpx + anthropic** — LLM provider (1차 Anthropic, adapter 로 교체 가능)
- **Playwright (python)** — E2E 테스트 러너
- **Jinja2** — inspect HTML 리포트
- 검증: `ruff` + `pyright` + `pytest` (+ `pytest-asyncio`)

`python -m code2e --help` 로 CLI 진입점 확인 가능.

---

## 디렉토리

| 경로 | 책임 | v4 근거 |
|------|------|--------|
| `src/code2e/agents/` | Agent Protocol + Planner / Executor / Advisor / Evaluator (4 고정, ADR-040) | §3.4 |
| `src/code2e/core/schemas.py` | Pydantic 모델 모음 (Plan, CodeChange, SystemState, ...) | Part VII |
| `src/code2e/core/orchestrator.py` | 상태 머신 + Phase 1/2/L/3/Teardown | §3.3, §3.5, §6.1 |
| `src/code2e/core/llm_gateway.py` | cassette → budget → call → validate → repair → record 파이프라인 | §3.9, §16 |
| `src/code2e/core/process_manager.py` | Generated app subprocess (launch / health / teardown / restart) | §9.4, ADR-037 |
| `src/code2e/core/port_allocator.py` | localhost 포트 자동 할당 | §9.5 |
| `src/code2e/runners/` | TestRunner Protocol + Playwright 구현 | §3.12, §5.4 |
| `src/code2e/cli/` | Typer App + 13 서브커맨드 | §4 |
| `src/code2e/prompts/` | 프롬프트 파일 (frontmatter + system + user) | §13 |
| `config/default.yaml` | 기본 설정 (loops / agents / llm / budget / cassettes) | §3.14 |

---

## 커밋 / 브랜치 컨벤션

```
<type>: <한 줄 요약 (50자 이내)>

<본문 (선택)>
```

type: `feat` / `task` / `fix` / `docs` / `refactor` / `test` / `chore`

- 브랜치: `hykjun/<기능명>` (예: `hykjun/llm-gateway`, `hykjun/orchestrator-phase1`)
- main 직접 push 금지 (1인 개발이지만 PR 흐름 유지)
- 한국어 커밋 메시지 OK

---

## 개발 규칙

### Rule #0 — 작업 전 핵심 문서 정독
v4 plan + 본 파일을 먼저 읽고 컨텍스트를 잡은 뒤 진행한다. 결정의 정량 근거가 필요하면 §19.2/§19.3 결정 표를 참조.

### Rule #1 — Surgical / Simplicity / Goal-Driven (hykjun_claude.md Rule #1 와 동일 정신)
- 가정은 명시화. 모호하면 멈추고 질문.
- 요청 외 기능 / 추측성 추상화 / 발생 불가 시나리오의 에러 핸들링 모두 금지.
- 통제 영역(아래) 은 절대 임의 변경 금지.

### 통제 영역 (변경 시 ADR 또는 결정 갱신 필요)
- 4 에이전트 고정 (ADR-040). 5번째 에이전트 추가 비목표.
- localhost 전용 (ADR-041). 컨테이너 / 배포 / CI 비목표.
- Repair 2회 (ADR-043), auto-rollback OFF (ADR-039), Planner 3회 (FR-002).
- §19.2 / §19.3 의 결정값 (stagnation 0.92, max iter 5, port range 3000-3999, teardown grace 5s, ...).
- Pydantic 모델 (Part VII) 의 필드 추가 시 `schema_version` bump + 마이그레이션.

### 검증 골든 4종
구현 후 다음이 모두 통과해야 머지:
```bash
.venv/bin/python -m ruff check src/code2e tests
.venv/bin/python -m pyright src/code2e         # phase 2 부터 strict 격상
.venv/bin/python -m pytest -q
.venv/bin/python -m code2e --help
```

---

## §19.2 / §19.3 결정 (요약)

| Q | 결정 | 한 줄 근거 |
|---|------|----------|
| Q4 | Planner 명시 + Orchestrator 후처리 fallback | 이중 안전망, 후처리 비용 0 |
| Q9 | http+cli v1 / worker v1.1 | http=90% 사례, worker=정규식 모니터링 복잡 |
| Q10 | Stateless | replay 결정성 |
| Q11 | revise + 빈 코멘트 = force-stop | 무한 루프 방지 |
| Q12 | similarity 0.92, signature 동일성 비교 | 해시 = fuzzy 불필요 |
| Q14 | 80% cheaper 강등 v1.1 | 모델 카탈로그 + 라우팅 = 새 추상화 3개 |
| Q17 | 입력 언어 따름, 미명시 시 Python | 프롬프트로 강제 |
| Q19 | runs/.global.lock 단일 run | 동시 cassette 충돌 방지 |
| Q20 | Planner only infeasible 권한 | Executor 발견 시 stagnation 흡수 |
| Q33 | DAG 위상 정렬, 순환 시 UNIT_DECOMPOSITION_FAILED | v4 보정 #4 |
| Q34 | v1: 1회 실행, flaky 다수결 v1.1 | 비용 3배 회피 |
| Q39 | raw + 10KB 컷 (헤드/테일 5KB) | 정보 손실 최소 |
| Q41 | 휴리스틱 v1 미포함, abort LAUNCH_SPEC_MISSING | ADR-038 명시 우선 |
| Q42 | 항상 재기동 | 부분 재기동 = 트리 분석 필요 |
| Q43 | macOS/Linux 항상 setsid | 좀비 방지 > 자식 영향 |
| Q47 | HTTP_GET + TCP_CONNECT v1 | 90% 사례 + 단순 fallback |
| Q49 | port_range 시작 ± 5개 샘플 | doctor NFR-P-4 ≤5s |

전체 표 (Q4-Q50, 결정 + 정량 근거 + 반영 위치) 는 plan 파일 참조.

---

## v4 문서 결함 보정 (병렬 검토에서 식별, 코드에 반영 완료)

| # | 결함 | 보정 위치 | 검증 테스트 |
|---|------|---------|----------|
| 1 | `LAUNCH_SPEC_MISSING` 이 §9.2 에만 있고 Part VII enum 누락 | `schemas.py` TerminationReason | `test_termination_reason_includes_launch_spec_missing` |
| 2 | `ProcessManager.restart()` 가 §6.1 에 등장하나 §9.4 클래스에 미정의 | `process_manager.py.restart` | `test_process_manager_has_restart` |
| 3 | 회귀 정보 → Executor InputModel 경로 부재 | `RegressionContext` + `ExecutorInput.regression_context` | `test_executor_input_has_regression_context` |
| 4 | `PlanUnit.dependencies` 위상 정렬 / 순환 검증 부재 | `orchestrator.validate_unit_dag` (스텁) | (phase 2 에서 단위 테스트 추가) |

---

## 현재 작업 현황 (2026-05-05)

### 완료 (스캐폴드)
- [x] 디렉토리 레이아웃 (`src/code2e/{cli,ui,agents,runners,core,prompts,reports}` + `tests/` + 빈 `runs/`/`cassettes/`/`hooks/`/`goldens/`)
- [x] 루트 메타 (`pyproject.toml`, `.python-version`, `.gitignore`, `.env.example`, `README.md`, `config/default.yaml`)
- [x] `core/schemas.py` — Part VII 모든 Pydantic 모델 + 보정 4건
- [x] `agents/base.py` — Agent Protocol + InvocationContext
- [x] 4 에이전트 스켈레톤 (`planner` / `executor` / `advisor` / `evaluator`)
- [x] 코어 11종 (`orchestrator` / `state` / `termination` / `llm_gateway` / `cassette` / `budget` / `logger` / `checkpoint` / `hooks` / `process_manager` / `port_allocator`)
- [x] `runners/` (Protocol + Playwright stub)
- [x] CLI Typer App + 13 서브커맨드 + `python -m code2e` 진입점
- [x] UI renderer 2종 (`pretty` + `jsonl`)
- [x] 프롬프트 파일 6종 (frontmatter + 본문 placeholder)
- [x] HTML 리포트 템플릿 (Jinja2 7섹션 placeholder)
- [x] 검증 4종 PASS (ruff / pyright basic / pytest 7 passed / `--help`)

### 다음 단계 (TO DO, 우선순위 순)
- [ ] **GitHub repo 생성 + remote 등록 + push** (사용자 작업)
- [ ] `core/cassette.py` 구현 — record/replay/redact + key 정규화 (Phase 2 다른 모듈의 의존성)
- [ ] `core/budget.py` 구현 — `check_headroom` / `add` / `usage_ratio`
- [ ] `core/logger.py` 마무리 — contextvar 매핑 + `get_logger` 검증
- [ ] `core/llm_gateway.py` 구현 — Anthropic provider adapter + 6단계 파이프라인
- [ ] **Planner 프롬프트 3종 본문 작성** — round 1/2/3 의 system + user 템플릿
- [ ] `agents/planner.py.invoke` 구현 + 단위 테스트 (cassette 사용)
- [ ] `core/orchestrator.py._run_planning` 구현 + Phase 1 통합 테스트
- [ ] `core/orchestrator.py.parse_units_from_plan` + `validate_unit_dag` + `topological_sort` 구현 + 단위 테스트
- [ ] Executor / Advisor / Evaluator 프롬프트 + invoke 구현
- [ ] `core/process_manager.py` 구현 (HTTP_GET / TCP_CONNECT health, setsid, restart)
- [ ] `core/port_allocator.py` 구현
- [ ] CLI `run` / `doctor` / `init` / `inspect` 본문 구현
- [ ] pyright `strict` 격상 + 모든 type 경고 해소

### 결정 / 확인 필요 (open)
- [ ] LLM provider config: `claude-sonnet-4-6` 디폴트 OK 인가? `config/default.yaml` 에 박혀있음. 변경 시 cassette 키 invalidate.
- [ ] 새 GitHub repo 이름 (`code2e` ? `code2e-cli` ?) — 같은 이름의 paran/code2e-agent 와 헷갈릴 수 있음.
- [ ] `paran/code2e-agent/reference/` 가 본 저장소에서 참조되는데, repo 분리 시 reference 사본을 본 저장소에 둘지 / symlink 할지 / 외부 링크로 둘지.

---

## 변경 로그

- **2026-05-05** — 저장소 신설. v4 plan 기반 스캐폴드 완료. 검증 4종 통과. §19.2 / §19.3 결정 + v4 보정 4건 반영.
