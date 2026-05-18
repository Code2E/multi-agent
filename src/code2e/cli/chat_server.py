"""Code2E Chat — 로컬 웹 인터페이스 (v0).

FastAPI + Server-Sent Events 로 단일 run 진행을 실시간 push.

DECISION:
- v0 는 단일 run per session. follow-up 은 v1.
- chat 모드는 Teardown 을 skip 해 산출 앱이 세션 동안 살아있음 → iframe preview.
- SSE 단방향 (WebSocket 보다 단순). 클라이언트는 EventSource API.
- v4 ADR-040/041/036 와 충돌 없음 — UI wrapper, agent 추가 없음, localhost.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from code2e.core.event_emitter import EventEmitter


def _load_index_html() -> str:
    """chat_ui/index.html 을 그대로 반환. 매 요청마다 디스크 read — dev 편의."""
    path = Path(__file__).parent / "chat_ui" / "index.html"
    return path.read_text(encoding="utf-8")


def build_chat_app(
    *,
    config: dict[str, Any],
    runs_dir: Path,
    cassettes_dir: Path,
    cassette_mode: str,
    cassette_name: str,
) -> FastAPI:
    """FastAPI app 생성. `code2e chat` 명령이 uvicorn 으로 띄움.

    Orchestrator 는 매 task 요청 마다 새로 빌드 (v0 단일 run). emitter 도 새로.
    state 는 `app.state.last_run` 에 저장 (preview 용).
    """
    from code2e.cli.commands.run import _build_orchestrator  # noqa: PLC0415

    state: dict[str, Any] = {
        "emitter": None,
        "task": None,
        "run_id": None,
        "active_process": None,  # (pm, launch_info, port_allocator) — shutdown 시 cleanup
    }

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        # shutdown — 산출 앱 process 가 살아있으면 정리.
        ap = state.get("active_process")
        if ap is not None:
            pm, info, alloc = ap
            try:
                await pm.teardown(info, grace_s=3)
            except Exception:  # noqa: BLE001
                pass
            if alloc is not None and info.port is not None:
                try:
                    await alloc.release(info.port)
                except Exception:  # noqa: BLE001
                    pass

    app = FastAPI(title="Code2E Chat", lifespan=lifespan)
    app.state.session = state

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _load_index_html()

    @app.post("/chat")
    async def chat(payload: dict[str, Any]) -> JSONResponse:
        """task 입력 → background 에서 Orchestrator.start 실행.

        반환: {run_id, status: 'started'}. 진행 상황은 /events 로.
        """
        task = (payload.get("task") or "").strip()
        if not task:
            return JSONResponse({"error": "task is required"}, status_code=400)

        if state["task"] is not None and not state["task"].done():
            return JSONResponse(
                {"error": "another run in progress"}, status_code=409
            )

        # 새 emitter + orchestrator. 기존 SSE 구독자는 새 emitter 로 재구독해야 함 (v1 에서 개선).
        emitter = EventEmitter()
        budget_usd = float(payload.get("budget_usd") or 5.0)
        orch = _build_orchestrator(
            config=config,
            cassette_name=cassette_name,
            cassette_mode=cassette_mode,
            cassettes_dir=cassettes_dir,
            runs_dir=runs_dir,
            budget_usd_override=budget_usd,
        )
        orch.emitter = emitter

        async def _run() -> None:
            try:
                # chat 모드: Teardown skip → 산출 앱 iframe 살아있음.
                result = await orch.start(task, run_id=None, skip_teardown=True)
                state["run_id"] = result.run_id
                # 다음 task 가 새 process 띄울 수 있도록 active process 추적.
                if result.launch is not None and orch.process_manager is not None:
                    state["active_process"] = (orch.process_manager, result.launch, orch.port_allocator)
            except Exception as e:  # noqa: BLE001
                emitter.emit("run.exception", {"error": str(e), "type": type(e).__name__})
            finally:
                emitter.close()

        state["emitter"] = emitter
        state["task"] = asyncio.create_task(_run())
        return JSONResponse({"status": "started"})

    @app.get("/events")
    async def events() -> StreamingResponse:
        """SSE stream. 현재 활성 emitter 의 모든 이벤트를 push.

        클라이언트 EventSource 자동 재연결 — 새 run 시작하면 재구독.
        """

        async def gen():
            # 활성 emitter 가 없으면 polling 으로 대기 (최대 30s).
            for _ in range(300):
                if state["emitter"] is not None:
                    break
                await asyncio.sleep(0.1)
            else:
                yield "event: idle\ndata: {}\n\n"
                return
            emitter: EventEmitter = state["emitter"]
            async for evt in emitter.subscribe():
                yield f"event: {evt.type}\ndata: {json.dumps(evt.to_dict(), ensure_ascii=False)}\n\n"
            yield "event: stream-end\ndata: {}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/runs/{run_id}/source")
    async def get_source(run_id: str) -> JSONResponse:
        """산출물 source code 반환 (v0 는 main.py 우선)."""
        run_dir = runs_dir / run_id
        if not run_dir.is_dir():
            return JSONResponse({"error": "run not found"}, status_code=404)
        files: dict[str, str] = {}
        for name in ("main.py", "requirements.txt", "index.html"):
            p = run_dir / name
            if p.is_file():
                files[name] = p.read_text(encoding="utf-8")
        return JSONResponse({"files": files})

    return app


async def serve(
    *,
    host: str,
    port: int,
    config_path: Path,
    runs_dir: Path,
    cassettes_dir: Path,
    cassette_mode: str,
    cassette_name: str,
) -> None:
    """uvicorn 으로 chat app 띄움. KeyboardInterrupt 시 우아하게 종료."""
    import uvicorn  # noqa: PLC0415

    if config_path.is_file():
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {}

    app = build_chat_app(
        config=cfg,
        runs_dir=runs_dir,
        cassettes_dir=cassettes_dir,
        cassette_mode=cassette_mode,
        cassette_name=cassette_name,
    )
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
