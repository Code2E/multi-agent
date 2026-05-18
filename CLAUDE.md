# Code2E CLI

`reference/multi-agent-system-plan-v4.md` 가 명세하는 **Python 3.12+ / localhost 전용 멀티-에이전트 코드 생성 CLI 도구** 의 구현 저장소.

> 같은 이름의 학부 연구 프로젝트(`paran/code2e-agent`) 와는 **별개**. 본 저장소는 v4 plan 의 산출물(production CLI) 을 만든다.
>
> **상태**: 27 commits / 342 tests / ~93% 완성도 / 4 phase + 9 CLI 명령 동작. `code2e run "..."` end-to-end 가능 (API key + `playwright install chromium` 전제).

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

- **본 repo (`Code2E/multi-agent`) 는 1인 개발 — `main` 에 직접 작업/push OK.**
- 큰 변경 / 실험 / WIP 은 feature branch 활용 (선택). 머지는 fast-forward 선호.
- 한국어 커밋 메시지 OK.

---

## 개발 규칙

### Rule #0 — 작업 전 핵심 문서 정독
v4 plan + 본 파일을 먼저 읽고 컨텍스트를 잡은 뒤 진행한다. 결정의 정량 근거가 필요하면 §19.2/§19.3 결정 표를 참조.

> **Tradeoff:** 아래 규칙들은 속도보다 신중함 쪽으로 편향. 본 프로젝트는 통제 영역(4 에이전트 고정 / localhost 전용 / cassette schema 안정성 / 결정값) 이 많아 `improve` 가 곧 회귀가 되기 쉬움. 사소한 작업은 융통성 있게.

### Rule #1 — Think Before Coding (가정 금지, 혼란 표면화)

구현 전:

- 가정은 **명시적으로** 말한다. 불확실하면 묻는다.
- 해석이 여러 갈래면 모두 제시한다 — 혼자 골라 가지 않는다.
- 더 단순한 접근이 있으면 말한다. 필요하면 push back.
- 모호하면 멈추고, 무엇이 헷갈리는지 명명하고 질문한다.

본 프로젝트 특수 케이스:

- v4 plan / §19.2 / §19.3 의 결정과 어긋나 보이는 요청이면 **plan 파일과의 차이를 표면화** 하고 어느 쪽이 의도인지 묻는다.
- ADR 번호가 걸린 결정(ADR-039 / 040 / 041 / 043) 은 절대 가정으로 우회하지 않는다.

### Rule #2 — Simplicity First (최소 코드)

- 요청 외 기능 / 추측성 추상화 / 요청되지 않은 유연성·설정 가능성 모두 금지.
- 단발 호출 코드를 위한 추상화 금지.
- 발생할 수 없는 시나리오의 에러 핸들링 금지.
- 200 줄로 쓴 게 50 줄로 가능하면 다시 쓴다.
- 자문: *"시니어 엔지니어가 보면 over-engineered 라고 할까?"* 그렇다면 단순화.

본 프로젝트 특수 케이스:

- v4 NG (비목표) 영역 — 컨테이너 / 배포 / CI / 5번째 에이전트 / 자율 협업 / 다중 사용자 — 제안하지도, 미리 hook 도 만들지 않는다.
- v1.1 로 미뤄진 항목 (worker kind / cheaper 강등 / flaky 다수결 / STDOUT_MATCH+FILE_EXISTS / 휴리스틱 LaunchSpec / stdin pipe) 은 v1 에서 손대지 않는다.
- `config/default.yaml` 에 새 옵션 추가 시 §19.2/19.3 같은 결정 근거 없이는 추가하지 않는다.

### Rule #3 — Surgical Changes (외과적 변경)

기존 코드 편집 시:

- 인접 코드 / 주석 / 포맷팅을 "improve" 하지 않는다. 깨지지 않은 것 리팩토링 금지.
- 기존 스타일 유지 (본인이라면 다르게 했더라도).
- 무관한 dead code 는 **언급만** 하고 삭제 금지.
- 본인 변경으로 고아가 된 import / 변수 / 함수만 제거.
- 기준: **변경된 모든 줄이 사용자 요청과 직접 연결**되어야 함.

⚠️ **본 프로젝트 통제 영역 — 절대 "improve" 대상 아님:**

- 4 에이전트 고정 (ADR-040). 5번째 추가 / Hook 으로 우회 모두 금지.
- localhost 전용 (ADR-041). 컨테이너화 / 원격 호출 / CI 통합 시도 금지.
- Repair 2회 (ADR-043), auto-rollback OFF (ADR-039), Planner 3회 (FR-002), Phase 2/3 max 5 회.
- §19.2 / §19.3 결정값 (stagnation 0.92, port_range 3000-3999, teardown_grace 5s, signature 동일성 비교 등).
- Pydantic 모델 (Part VII) 의 필드 추가 / 변경 시 `schema_version` bump + 마이그레이션 필수.
- 프롬프트 frontmatter `version` 변경 시 cassette 키가 자동 invalidate (§13.5) — 의도한 경우에만.
- Agent 의 InputModel / OutputModel / ClassVar (name / version / temperature) 는 cassette 키 직결. 변경은 새 ADR 동반.

### Rule #4 — Goal-Driven Execution (목표 기반 실행)

작업을 검증 가능한 형태로 변환한다:

- "validation 추가" → "잘못된 입력 테스트 작성 → 통과시키기"
- "버그 수정" → "재현 테스트 작성 → 통과시키기"
- "X 리팩토링" → "전·후 동일하게 테스트 통과"

다단계 작업은 짧은 plan 을 명시한다 (예: `1. X → 검증: Y / 2. A → 검증: B`). TodoWrite 도 같은 목적.

본 프로젝트 검증 골든 4종 — 모든 PR 은 다음이 통과해야 머지:
```bash
.venv/bin/python -m ruff check src/code2e tests
.venv/bin/python -m pyright src/code2e         # phase 2 부터 strict 격상
.venv/bin/python -m pytest -q
.venv/bin/python -m code2e --help
```

본 프로젝트 추가 검증:

- 프롬프트 변경 → `code2e prompt test <agent> --replay --assert-snapshot` 골든 비교 (§13.4, phase 2 이후).
- `core/schemas.py` 변경 → `schema_version` bump + 기존 cassette / state.json 마이그레이션 함수.
- `core/llm_gateway.py` / `cassette.py` 변경 → cassette key 정규화 식 회귀 테스트.

---

### 적용 신호 (가이드라인이 작동 중인지)

- diff 에 불필요한 변경이 줄어든다.
- 과설계로 인한 재작성이 줄어든다.
- 실수 후가 아니라 구현 전에 명확화 질문이 나온다.
- 통제 영역 위반이 PR 단계가 아니라 사고 단계에서 잡힌다.

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
| 4 | `PlanUnit.dependencies` 위상 정렬 / 순환 검증 부재 | `orchestrator.validate_unit_dag` + `topological_sort` | `tests/test_orchestrator_pure.py` (10 케이스: linear/diamond/self-loop/2-node-cycle/3-node-cycle/dangling/duplicate/disconnected) |

---

## 현재 작업 현황 (2026-05-18)

**누적**: 39 commits / 342 tests / 검증 4종 모두 PASS / 약 95% 완성도.
**상태**: v1 critical path + 핵심 운영 도구 모두 동작. 4 task 종류 end-to-end 검증
(Hello World / Calculator / TODO / Health check / SaaS Landing / TODO UI).

### 팀원 시작 가이드 (한 줄)

```bash
git clone <repo> && cd code2e
./scripts/setup-dev.sh    # venv + 의존성 + PYTHONPATH + .env 안내
# .env 에 본인 ANTHROPIC_API_KEY 채우기
source .venv/bin/activate
python -m code2e doctor   # 0 errors 확인
python -m code2e run "..." --name <slug> --budget-usd 2
```

### 완료 — 스캐폴드 (`2e8437a`, `6f00b61`, `19bbab0`)

- 디렉토리 레이아웃 (`src/code2e/{cli,ui,agents,runners,core,prompts,reports}` + `tests/` + 빈 `runs/`/`cassettes/`/`hooks/`/`goldens/`)
- 루트 메타 (`pyproject.toml`, `.python-version`, `.gitignore`, `.env.example`, `README.md`, `config/default.yaml`)
- `core/schemas.py` — Part VII 모든 Pydantic 모델 + v4 보정 4건 (LAUNCH_SPEC_MISSING / RegressionContext / restart / DAG validate)
- 4 에이전트 + 코어 11종 + runners + CLI 13 서브커맨드 + UI 2종 + 프롬프트 6종 + HTML 리포트 스켈레톤
- CLAUDE.md 4원칙 가이드라인 + 브랜치 정책 (`main` 직접 작업)
- GitHub `Code2E/multi-agent` 연결

### 완료 — 코어 인프라 (`2e37c26`, `4e5ff93`, `8b5dfeb`, `62ccf05`, `da1efd0`)

- `core/cassette.py` (17 tests) — compute_key / canonicalize (volatile 제외) / redact (Bearer/sk-/key-name) / try_hit / record (`NNNNN.{key8}.json` + schema_version 검사) — Q32 적용.
- `core/budget.py` (12 tests) — check_headroom / add / usage_ratio (limit=0 안전) / should_warn (1회) — NFR-C-2, Q14.
- `core/orchestrator.py` 순수 함수 5종 (31 tests):
  - `parse_units_from_plan` (Q4 frontmatter + 본문 정규식 fallback)
  - `validate_unit_dag` (보정 #4 + Q33: dangling/duplicate/3-color cycle)
  - `topological_sort` (Q5 Kahn's, 안정 정렬)
  - `truncate_failure_report` (Q39: 10KB 컷, utf-8 안전)
  - `extract_launch_spec_from_plan` (Q41 frontmatter `launch:` → LaunchSpec)
- `core/llm_gateway.py` (19 tests) — AnthropicProvider + 6단계 파이프라인 (cache → budget → call → validate → repair × 2 → record). NFR-R-1 retry, ADR-043 repair, RepairExhaustedError 신설.
- `core/checkpoint.py` (13 tests) — save/load/list_phases + Q19 `runs/.global.lock` (fcntl).

### 완료 — 4 에이전트 (`14e89b8`, `c62ba6f`, `4958257`, `acaafc5`)

- `agents/planner.py` (14 tests) — 3 round + temperature 변화 (0.7 → 0.3 → 0.3). 프롬프트 3종 본문 작성. PlannerLlmOutput 부분 모델.
- `agents/executor.py` (7 tests) — Q17 언어 정책. RegressionContext 흐름 (보정 #3).
- `agents/advisor.py` (12 tests) — signature 자동 생성 (Q11/Q12). prior_feedback 누적.
- `agents/evaluator.py` testgen (8 tests) — EvaluatorTestgenLlmOutput 래퍼 + list[TestCase].
- `agents/evaluator.py` testrun (7 tests) — Runner Protocol 위임 + signature.
- 모두 동일 패턴: 프롬프트 파일 → LlmGateway.call → Pydantic output 합성.

### 완료 — Phase 통합 (`2a3c240`, `a1a1535`, `6fd8fd7`, `4286b76`, `b311f85`, `ff4beee`, `e221dcb`, `df60cbb`, `a67667f`)

- `_run_planning` (Phase 1, 10 tests) — 3 round 순차 + DAG 검증 + launch_spec 추출.
- `core/workspace.py` (18 tests) — apply / snapshot + path traversal 3중 방어 (NFR-S-1/2).
- `_run_building_and_testgen` (Phase 2, 9 tests) — `asyncio.TaskGroup` 으로 build/testgen 병렬 (ADR-036). max 5 iter + Q11/Q12 + SECURITY_VIOLATION.
- `Orchestrator.start` (8 tests) — phase 순차 + checkpoint 매 phase 저장 + `_safe_phase` (NotImplementedError → INTERNAL_ERROR).
- `core/port_allocator.py` (12 tests) — socket.bind 검증 + asyncio.Lock + Q41.
- `core/process_manager.py` (12 tests, 실제 subprocess) — Q43 setsid / Q46 stdin DEVNULL / Q47 HTTP_GET+TCP_CONNECT / Q42 restart (보정 #2).
- `_run_launching` + `_teardown` (Phase L, 8 tests) — port acquire → launch → health → healthy_at / SIGTERM grace → port release.
- `_run_testing` (Phase 3, 10 tests) — testrun ↔ Executor revise loop, 회귀 감지 (ADR-039), Q42 항상 재기동.
- `runners/playwright_runner.py` (14 tests) — wrap_script / compile_script (exec) / fake browser fixture.

### 완료 — CLI (`d94b9ed`, `b29027a`, `2aaee23`, `e2717ab`, `f7dcd8d`)

- `run` (18 tests) — config / .env / Orchestrator 빌드 / asyncio.run / 결과 출력. Q30/Q38 적용.
- `doctor` (22 tests) — 6 가지 체크 (Python / API key / FS / config / ports Q49 / playwright) + `--fix`.
- `inspect` (14 tests) — Jinja2 HTML 7섹션 (Overview/Termination/Plan/Build/Launch/Tests/Metadata). Q24 단일 HTML.
- `runs ls/gc/rm` (25 tests) — r_ prefix 검증 / mtime 정렬 / `--older-than 30d`/`24h` 파싱 / `--dry-run`.
- `logs / cost / diff` (15 tests) — events.jsonl 출력 / budget 요약 / 두 run state 비교.

### 완료 — 2026-05-18 세션 (12 commits)

#### 팀 협업 / 사용성

- `__main__.py` (`a1ffac9`) — `.env` 자동 로드 (stdlib 만, dotenv 의존성 없음). venv 활성화 없이도 `ANTHROPIC_API_KEY` 가 host env 로 들어감.
- `scripts/setup-dev.sh` (`4dfd264`) — 팀원 한 줄 setup. macOS Sequoia provenance xattr 우회 PYTHONPATH 자동 박음 (`_editable_impl_*.pth` hidden 처리 회피).
- `pyproject.toml` `[demo]` extra (`4dfd264`) — fastapi / uvicorn 동봉 (v1 은 산출물 deps install 단계가 없음 → code2e venv 가 런타임 환경).
- `requirements.txt` (`4dfd264`) — pyproject 위임 진입점 (`-e .[dev,demo]` 한 줄).
- `run_id` 자동 slug + `--name` 옵션 (`d4ac59d`) — `runs/r_calculator-rest-api_<unix>/` 같이 식별 가능한 디렉토리.

#### Phase 격리 / 안정성 fix

- Phase L launch override (`11572d3`, `fdff5f6`) — `cwd` workspace 주입, `command[0]` python → `sys.executable`, env 의 `VIRTUAL_ENV / PYTHONHOME / PYTHONPATH` 호스트 누출 차단, `PORT` 주입. Phase 3 restart 도 동일 override (`_build_launch_spec` 공통 헬퍼).
- Cost tracking sync (`11572d3`) — `BudgetTracker` (실시간) ↔ `BudgetState` (state) 매 checkpoint 직전 sync. cassette hit 도 누적해 inspect/cost 가 LLM 가치 metric 유지.
- Phase 3 multi-unit revise (`e5b8a07`) — `_pick_target_unit` 가 실패 case 의 `plan_unit_refs` 카운트 → 가장 자주 실패한 unit fix. 종전 `units[0]` only 한계 해소.
- Phase 1 launch_spec 조기 감지 (`e5c10e3`) — HTTP 키워드 + launch_spec=None → Phase 2 비용 소진 전 abort. (`_looks_like_http_task`)
- Phase L / 3 abort 메시지에 `_tail_log` 자동 첨부 (`2048e23`) — stdout 마지막 15줄로 80% 케이스 자동 진단. deque 스트리밍 (메모리 일정).
- `workspace.apply` surrogate sanitize (`540e421`) — LLM 이 이모지를 `🏷` 형식으로 출력한 경우 UTF-8 안전 형태로 정규화. 실제 사례에서 발현 (`/`endpoint 가 UnicodeEncodeError 로 500 응답).

#### 한도 / 가이드

- max_tokens 4096 → 8192 (`d0b9794`) — 복잡 task / 한국어 응답 시 testgen 한도 도달 회피.
- planner_round_1/2 strict (`a1ffac9`) — `units: []` 강제. round 3 만 unit 분해.
- planner_round_3 units ≤ 8 + PORT env 가이드 (`a1ffac9`, `567f14c`).
- evaluator_testgen cases ≤ 20 (`567f14c`).
- max_tokens 도달 시 자동 경고 로그 (`567f14c`).

#### 객관 점검 (영역 1/2/4)

- `13ee133` — 새 헬퍼 6 종 (workspace / state / orchestrator) 의 8 건 edge case fix: sanitize fallback UTF-8 안전망, slugify stop word, tail_log OOM, target_unit skipped 제외, HTTP 메서드 case-sensitive 분리, python 변형 basename 비교, PATH 명시.
- `716e16a` — phase 분기 예외 처리 6 건: LlmPermanentError catch, OSError catch, port leak 방지, teardown 예외 삼킴, runner.setup 명확 abort.
- `4cc26f2` — 프롬프트 outdated 값 (4096) 갱신.

### 다음 단계 (우선순위 순, 영향 작아짐)

- [ ] `init` 대화형 명령 — Q→템플릿 5 옵션 + scaffold 파일 생성 (큰 작업).
- [ ] structlog contextvar 통합 — events.jsonl 실제 작성. logs / cost / inspect 의 timeline 섹션이 의미를 가짐.
- [ ] Cancellation (Part XVIII) — SIGINT/SIGTERM → root TaskGroup cancel + emergency teardown + state.json 마지막 저장.
- [ ] `prompt list/edit/diff/test/lint` — 프롬프트 회귀 방지 (§13.3, §13.4).
- [ ] `cassettes ls/inspect/redact` — cassette 메타 / re-redact.
- [ ] `core/hooks.py` discover + dispatch 동작 (read-only 강제, 5s 타임아웃).
- [ ] pyright `strict` 격상 + 모든 type 경고 해소.

### v1.1 로 의도적으로 미룬 항목 (2026-05-18 점검 결과)

팀원이 또 시도하지 않도록 명시 — 학부 일정 외에서 다룰 ADR/구조 변경 동반:

- **Testgen unit별 분할 호출** — v4 ADR-036 (TaskGroup 병렬 + testgen 단일 호출)
  변경 동반. 의견 있었으나 v1 에선 프롬프트 가드 (`cases ≤ 20`) + max_tokens
  8192 + max_tokens 경고 로그로 우회. 자세한 토론 정리는 `docs/code-review-2026-05-18.md` (추후 작성 시).
- **Interactive / HITL 모드** — v4 는 batch 결정성 전제. ADR 다수 재검토 필요.
- **cost tracking phase / agent 별 분해** — `events.jsonl` 통합 필요 (v1.1 의 다른 작업과 묶음).
- **flaky test 다수결** — Q34, v1 은 1 회 실행.
- **휴리스틱 LaunchSpec 생성** — Q41, v1 미포함 (현재는 *진단* 휴리스틱 `_looks_like_http_task` 만 사용 — 충돌 없음).
- **`runs/app-logs/stdout.log` per-run 격리** — 모든 run 공유 (장기 누적). `_tail_log`
  의 deque 스트리밍으로 OOM 회피했지만, 격리는 별도 작업.

### 결정 / 확인 필요 (open)

- [ ] LLM provider config: `claude-sonnet-4-6` 디폴트 (변경 시 cassette 키 invalidate).
- [ ] `paran/code2e-agent/reference/` 가 본 저장소에서 (현재) 상대경로로 참조됨 — 저장소 분리 진행 시 reference 사본을 본 repo 에 둘지 / symlink / 외부 링크로 둘지 결정.
- [ ] 프롬프트 frontmatter `version` 변경 → cassette 자동 invalidate (§13.5 의도) 가
  현재 코드는 동작 안 함 — agent ClassVar `version` 만 cassette key 에 반영. 의도와
  코드 일치시킬지, plan 문서 수정할지 결정 필요.

---

## 변경 로그

- **2026-05-05** — 저장소 신설. v4 plan 기반 스캐폴드 완료. 검증 4종 통과. §19.2 / §19.3 결정 + v4 보정 4건 반영. CLAUDE.md 4원칙 가이드라인.
- **2026-05-05** — GitHub `Code2E/multi-agent` 연결, `main` 직접 작업 정책.
- **2026-05-05** — 코어 인프라 5종: cassette / budget / orchestrator pure functions / llm_gateway / checkpoint (총 92 tests).
- **2026-05-05** — 4 에이전트 LLM 호출 경로 완성: Planner / Executor / Advisor / Evaluator-testgen / Evaluator-testrun (Runner 위임). 동일 패턴으로 일관 (총 48 tests).
- **2026-05-05** — Phase 1 (planning) + Phase 2 (building + testgen 병렬) + Orchestrator.start 통합. workspace + checkpoint 디스크 저장 통합 (총 27 tests).
- **2026-05-05** — Phase L 인프라 (port_allocator + process_manager) + Phase L 통합 + Teardown (총 32 tests, 실제 subprocess).
- **2026-05-05** — Phase 3 (testing) 통합 + PlaywrightRunner 실제 구현. **4 phase 모두 동작 가능** (총 24 tests).
- **2026-05-05** — CLI 9 명령 완성: run / doctor / inspect / runs (ls/gc/rm) / logs / cost / diff (총 94 tests). 사용자 가시 인터페이스 거의 완성.
- **2026-05-18** — 팀 협업 setup 자동화 (`scripts/setup-dev.sh`, `pyproject.toml [demo]` extra, `requirements.txt`, `README.md`). `.env` 자동 로드 (`__main__.py`). `run_id` 자동 slug + `--name` 옵션.
- **2026-05-18** — Phase 격리 / 안정성 fix: launch override 헬퍼 (cwd/sys.executable/env/PORT), Phase 3 restart 동일 override, multi-unit revise (`_pick_target_unit`), Phase 1 launch_spec 조기 감지, abort 메시지에 stdout tail 자동 첨부, workspace.apply surrogate sanitize. max_tokens 4096→8192. Cost tracking sync (BudgetTracker ↔ BudgetState).
- **2026-05-18** — 객관 line-by-line 점검 (영역 1/2/4): 새 헬퍼 6 종 edge case 8 건, phase 분기 예외 처리 6 건, 프롬프트 outdated 값 2 건 fix. 342 tests passing 유지.
- **2026-05-18** — End-to-end 검증 task 6 종 통과: Hello World / Calculator / TODO / Health check / SaaS Landing / TODO UI. cost ~$0.13–1.57 per run.
