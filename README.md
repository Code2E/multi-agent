# Code2E

Multi-agent code generation CLI — Planner / Executor / Advisor / Evaluator with a fixed 3-phase loop, running entirely on localhost.

> Status: **scaffold only.** All function bodies raise `NotImplementedError`. Module shape, Pydantic schemas, and Protocols are in place; phase-by-phase implementation lands next.

## Quickstart (target UX, not yet wired)

```bash
uv tool install code2e            # or pipx
code2e init my-app                # interactive
cd my-app && cp .env.example .env # add ANTHROPIC_API_KEY
code2e doctor
code2e run "build a todo CLI"
code2e inspect <run_id> --open
```

## Layout

See `src/code2e/` for the package and `config/default.yaml` for runtime configuration. The reference design lives in the parent project's `reference/multi-agent-system-plan-v4.md`.

## Platform support

macOS / Linux are first-class. Windows is best-effort (see Q45 in the plan).

## Development

```bash
python3.12 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pyright src/code2e   # basic in scaffold; switch to strict in phase 2
.venv/bin/python -m ruff check src/code2e tests
.venv/bin/python -m pytest -q
.venv/bin/python -m code2e --help
```

Pyright is set to `basic` while the bodies are stubbed (lots of `list[Unknown]` false positives from `Field(default_factory=list)`). Switch to `strict` in `pyproject.toml` once the phase-2 implementation lands.
