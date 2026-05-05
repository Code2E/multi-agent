"""SystemState 관리 (v4 §3.6).

immutable update 헬퍼 + run_id 생성. Pydantic v2 의 model_copy(update=...) 래핑.
DECISION: Q29 — run_id 형식은 r_<unix>_<rand4>.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from code2e.core.schemas import SystemState


def new_run_id() -> str:
    return f"r_{int(time.time())}_{secrets.token_hex(2)}"


def update_state(state: SystemState, **changes: Any) -> SystemState:  # noqa: ANN401
    """immutable update wrapper.

    Pydantic v2 의 model_copy(update=...) 는 deep merge 를 하지 않으므로,
    필드 단위 교체에만 사용한다.
    """
    return state.model_copy(update=changes)
