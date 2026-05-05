"""structlog 설정 (v4 §3.15, ADR-035).

TTY → ConsoleRenderer (Rich), non-TTY → JSONRenderer.
contextvar 로 trace_id / run_id / phase 자동 첨부.
"""

from __future__ import annotations

import sys

import structlog


def configure(json_output: bool | None = None) -> None:
    """JSON vs pretty 분기. json_output=None 이면 isatty 기반 자동 감지."""
    if json_output is None:
        json_output = not sys.stdout.isatty()

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "code2e") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
