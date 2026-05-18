"""SystemState 관리 (v4 §3.6).

immutable update 헬퍼 + run_id 생성. Pydantic v2 의 model_copy(update=...) 래핑.
DECISION: Q29 — run_id 형식은 r_<unix>_<rand4>, slug 명시 시 r_<slug>_<unix>.
"""

from __future__ import annotations

import re
import secrets
import time
from typing import Any

from code2e.core.schemas import SystemState

_SLUG_WORD_RE = re.compile(r"[a-zA-Z]+")
_SLUG_MAX_WORDS = 4
_SLUG_MAX_LEN = 30


def slugify_task(task: str) -> str | None:
    """task 입력에서 첫 N 개 영어 단어를 추출해 slug 화.

    - 한국어/특수문자만 있는 입력 → None (호출자가 fallback 형식 사용)
    - kebab-case, 소문자, 최대 4 단어 / 30 자
    """
    words = _SLUG_WORD_RE.findall(task)
    meaningful = [w.lower() for w in words if len(w) >= 2]
    if not meaningful:
        return None
    selected = meaningful[:_SLUG_MAX_WORDS]
    slug = "-".join(selected)[:_SLUG_MAX_LEN].rstrip("-")
    return slug or None


def new_run_id(slug: str | None = None) -> str:
    """slug 있으면 r_<slug>_<unix>, 없으면 r_<unix>_<rand4>."""
    if slug:
        return f"r_{slug}_{int(time.time())}"
    return f"r_{int(time.time())}_{secrets.token_hex(2)}"


def update_state(state: SystemState, **changes: Any) -> SystemState:  # noqa: ANN401
    """immutable update wrapper.

    Pydantic v2 의 model_copy(update=...) 는 deep merge 를 하지 않으므로,
    필드 단위 교체에만 사용한다.
    """
    return state.model_copy(update=changes)
