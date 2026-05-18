# Code2E

Multi-agent code generation CLI — Planner / Executor / Advisor / Evaluator with a fixed 3-phase loop, running entirely on localhost.

> Status: **v1 동작 가능**. 4 phase (Planning → Build/Testgen → Launch → Test → Teardown) end-to-end 검증됨. 342 tests passing.

## 팀 setup (한 줄)

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

## 사용

```bash
source .venv/bin/activate
python -m code2e doctor                                # 환경 점검 (모두 ✓ 이어야 함)
python -m code2e run "Hello world FastAPI endpoint" --budget-usd 1
python -m code2e inspect <run_id> --open               # HTML 리포트
```

Playwright 브라우저는 처음 한 번:

```bash
playwright install chromium
```

## 의존성 관리

- **단일 출처**: [`pyproject.toml`](pyproject.toml) 의 `[project.dependencies]` + `[project.optional-dependencies]`
- 호환 진입점: [`requirements.txt`](requirements.txt) (`pip install -r requirements.txt` 동작)
- Extras:
  - `dev` — pytest / ruff / pyright / coverage
  - `demo` — fastapi / uvicorn[standard] (산출 앱 런타임)

새 패키지 추가는 항상 `pyproject.toml` 만 수정.

## 개발 검증

```bash
.venv/bin/python -m ruff check src/code2e tests
.venv/bin/python -m pyright src/code2e
.venv/bin/python -m pytest -q
.venv/bin/python -m code2e --help
```

## 레이아웃

- `src/code2e/` — 패키지 (agents / core / cli / runners / prompts / reports / ui)
- `config/default.yaml` — 런타임 설정
- `tests/` — pytest
- `scripts/setup-dev.sh` — 팀 setup 자동화
- 참조 설계: `../code2e-agent/reference/multi-agent-system-plan-v4.md`

## 플랫폼

macOS / Linux first-class. Windows best-effort (plan §Q45).
