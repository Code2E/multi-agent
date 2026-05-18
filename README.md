# Code2E

Multi-agent code generation CLI — Planner / Executor / Advisor / Evaluator with a
fixed 3-phase loop, running entirely on localhost. 자연어 task 한 줄 → 동작하는 산출 앱.

> Status: **v1 동작 가능**. 5 phase (Planning → Build+Testgen → Launching → Testing → Teardown)
> end-to-end 검증됨. 342 tests passing. 대화형 인터페이스 (`code2e chat`, `code2e tui`) 추가.

---

## 1. 팀 setup (한 줄)

```bash
git clone <repo> && cd code2e
./scripts/setup-dev.sh
```

스크립트가 자동으로 처리하는 것:

- Python 3.12+ 검증
- `.venv` 생성
- `pip install -e ".[dev,demo]"` (code2e + 산출 앱 런타임 deps 포함)
- macOS provenance xattr 우회용 PYTHONPATH 처리
- `.env` 템플릿 생성 안내

이후 각자 본인 API 키 채우기:

1. <https://console.anthropic.com> → Settings → API Keys → Create Key
2. 발급된 키를 `.env` 의 `ANTHROPIC_API_KEY=` 뒤에 붙여넣기

Playwright 브라우저는 처음 한 번:

```bash
source .venv/bin/activate
playwright install chromium
```

환경 점검:

```bash
python -m code2e doctor   # 6 가지 항목 모두 ✓ 이어야 함
```

---

## 2. 세 가지 사용 모드

| 모드 | 명령 | 적합한 시나리오 |
| --- | --- | --- |
| **Batch** | `code2e run "..."` | 1 회성 실행, CI / 자동화, 결과 재현 |
| **Chat (Web)** | `code2e chat` | 학부 발표 / 시연, 실시간 모니터링 + iframe preview |
| **TUI** | `code2e tui` | SSH / 헤드리스 서버, GUI 없는 환경 |

세 모드 모두 동일한 4 에이전트 파이프라인 + 동일한 산출물 (`runs/r_<slug>_<unix>/`).

### 2.1 Batch — `code2e run`

```bash
python -m code2e run "Calculator REST API in FastAPI with GET /add and GET /sub" \
  --name calculator \
  --budget-usd 2
```

주요 옵션:

- `--name <slug>` — 식별 가능한 run id 접두사 (`runs/r_calculator_<unix>/`). 생략 시 task 에서 자동 추출.
- `--budget-usd <amount>` — USD 한도 (기본 config 의 값). 초과 시 `BUDGET_EXCEEDED` abort.
- `--cassette <name>` — 다른 cassette set 사용 (기본 `default`).
- `--replay` — LLM 호출 없이 cassette 만 재생 (cassette miss 시 raise).
- `--record` — 명시적 record 모드.

실행 결과 (예시):

```text
=== Run r_calculator_1779083812 ===
Status:  completed
Budget:  $0.1839 / $2.00
Tokens:  44,493 / 1,000,000
Plans:   3 round(s)
Units:   2/2 approved
Tests:   iter 1 — 6/6 passed
```

### 2.2 Chat (Web) — `code2e chat`

```bash
python -m code2e chat
# → http://127.0.0.1:9876 (브라우저 자동 open)
```

4 컬럼 레이아웃:

- **History** — 세션 안의 모든 past run, 클릭 시 preview / source 복원
- **Conversation** — 자연어 task 입력 + 결과 로그 (Claude Web 스타일)
- **Pipeline Monitor** — Phase 1/2/L/3/Teardown 실시간 진행 (spinner / 체크 / ×) + cost / tokens
- **Result Preview** — Live (산출 앱 iframe) / Source (산출 파일 모노스페이스 표시) 두 탭

기능:

- Follow-up task 지원 (한 세션에서 여러 run 누적)
- 실행 중 Cancel 버튼 (cancel_token 전파 → CANCELLED abort)
- 완료 / 중단 시 Toast 알림 (5s 자동 fade)
- chat 모드는 Teardown 을 skip — 산출 앱이 iframe 으로 살아있음. 다음 task 시작
  시 또는 서버 종료 (Ctrl+C) 시 자동 정리

옵션:

- `--port <n>` — 기본 9876
- `--no-browser` — 자동 open 비활성
- `--cassette / --record / --replay` — `run` 명령과 동일

### 2.3 TUI — `code2e tui`

```bash
python -m code2e tui
```

Rich Live 기반 터미널 UI. 2 패널 (Conversation / Pipeline). SSH 환경에서 iframe
대신 산출 앱 `base_url` 만 표시 (사용자가 별도 브라우저로 open). 다음 task 시작
시 이전 산출 process 자동 teardown.

---

## 3. 검증된 task 시나리오 (재현용)

전부 영어로 작성. 한국어 task 도 동작하나 토큰 비용이 ~2배 (한도 주의).

| Task 명 | Prompt 예시 | 산출물 | Cost |
| --- | --- | --- | --- |
| Hello World | `"Hello world FastAPI endpoint"` | 단일 endpoint | $0.13 |
| Calculator | `"Calculator REST API in FastAPI with GET /add and GET /sub"` | 2 endpoint | $0.18 |
| TODO API | `"In-memory TODO REST API with FastAPI. POST /todos and GET /todos"` | CRUD 일부 | $0.19 |
| Health Check | `"Health check service: GET /health returns {status, uptime}"` | 단일 endpoint | $0.19 |
| TODO UI | `"TODO web app with FastAPI. GET / returns HTMLResponse with single-page TODO UI..."` | HTML + JS + JSON API | $1.57 |
| SaaS Landing | `"SaaS Landing Page for 'AcornFlow' with hero / features / pricing / contact form..."` | 정적 + 폼 | $0.79 |

### 산출 앱 띄우기

run 종료 후 산출 앱은 (chat 모드 외엔) 죽음. 다시 띄우려면:

```bash
cd runs/r_<slug>_<unix>/
python main.py    # 기본 PORT=8000
# 또는: PORT=4000 python main.py
```

브라우저 / curl 로 확인:

```bash
curl "http://localhost:8000/add?a=2&b=3"
# → {"result": 5.0}
```

FastAPI 자동 문서 (Swagger UI):

```text
http://localhost:8000/docs
```

---

## 4. 결과물 조회 / 디버깅

```bash
python -m code2e runs ls                       # 모든 run 목록
python -m code2e inspect <run_id>              # HTML 리포트 생성 (runs/<id>/report/index.html)
python -m code2e cost <run_id>                 # USD / tokens / ratio
python -m code2e logs <run_id>                 # events.jsonl (v1.1)
python -m code2e diff <run_id_a> <run_id_b>    # 두 run 비교
python -m code2e runs gc --older-than 30d      # 오래된 run 청소
```

생성된 파일 구조:

```text
runs/r_calculator_1779083812/
├── main.py            ← 산출 앱 코드
├── requirements.txt   ← 산출 앱 deps (참고용; 실제 install 은 code2e venv 사용)
├── checkpoints/
│   ├── after_planning.json
│   ├── after_building.json
│   ├── after_launching.json
│   ├── after_testing.json
│   ├── after_teardown.json
│   └── after_completed.json
└── report/index.html  ← inspect 명령으로 생성
```

---

## 5. 의존성 관리

- **단일 출처**: [`pyproject.toml`](pyproject.toml) 의 `[project.dependencies]` +
  `[project.optional-dependencies]`
- 호환 진입점: [`requirements.txt`](requirements.txt)
  (`pip install -r requirements.txt` 동작)
- Extras:
  - `dev` — pytest / ruff / pyright / coverage
  - `demo` — fastapi / uvicorn[standard] (산출 앱 런타임 + `code2e chat` 서버)

새 패키지 추가는 항상 `pyproject.toml` 만 수정.

---

## 6. 개발 검증 (모든 PR 통과 필수)

```bash
.venv/bin/python -m ruff check src/code2e tests
.venv/bin/python -m pyright src/code2e
.venv/bin/python -m pytest -q
.venv/bin/python -m code2e --help
```

---

## 7. 레이아웃

- `src/code2e/` — 패키지
  - `agents/` — Planner / Executor / Advisor / Evaluator
  - `core/` — orchestrator / event_emitter / llm_gateway / workspace / cassette /
    budget / port_allocator / process_manager / checkpoint / schemas
  - `cli/` — `commands/{run,chat,tui,doctor,inspect,runs,...}` + `chat_server.py` +
    `chat_ui/index.html`
  - `runners/` — Playwright runner
  - `prompts/` — planner_round_{1,2,3}.md / executor.md / advisor.md /
    evaluator_testgen.md / evaluator_testrun.md
  - `reports/` — HTML 템플릿
- `config/default.yaml` — 런타임 설정
- `tests/` — pytest (342 tests)
- `scripts/setup-dev.sh` — 팀 setup 자동화
- 참조 설계: `../code2e-agent/reference/multi-agent-system-plan-v4.md`

---

## 8. 트러블슈팅

| 증상 | 원인 / 해결 |
| --- | --- |
| `ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다` | `.env` 에 키 입력. `code2e chat / run` 은 `__main__.py` 가 자동으로 `.env` 로드함. |
| `No module named 'code2e'` | macOS Sequoia provenance xattr 이슈. `./scripts/setup-dev.sh` 다시 실행 (`.venv/bin/activate` 에 PYTHONPATH 박음). |
| `LAUNCH_TIMEOUT (Phase L)` | 산출 앱이 30 초 안에 응답 못함. abort 메시지의 stdout tail (자동 첨부) 로 원인 진단. PORT env 무시 / import error 등이 흔함. |
| `BUDGET_EXCEEDED` | USD 또는 tokens 한도 초과. `--budget-usd` 늘리거나, task 를 더 단순한 영어로 변경. |
| `UNIT_DECOMPOSITION_FAILED` | Planner round 3 가 units 없이 반환 또는 DAG 깨짐. task 표현 명확화. |
| 산출 앱 surrogate / UTF-8 에러 | `workspace.apply` 가 자동 sanitize. 그래도 발생하면 task 에 "ASCII only" 명시. |

자세한 v1.1 deferred 항목은 [`CLAUDE.md`](CLAUDE.md) 참조.

---

## 9. 플랫폼

macOS / Linux first-class. Windows best-effort (plan §Q45 — `setsid` /
`SIGTERM` 미지원, ProcessManager 가 부분 동작).
