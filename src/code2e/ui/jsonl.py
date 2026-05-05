"""JSONL 출력 (non-TTY / 파이프 / CI). 한 줄 = 한 이벤트 (v4 §4.3)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import TextIO


@dataclass
class JsonlRenderer:
    stream: TextIO = sys.stdout

    def emit(self, event: dict[str, object]) -> None:
        raise NotImplementedError("JsonlRenderer.emit — phase 2 구현 예정")

    @staticmethod
    def serialize(event: dict[str, object]) -> str:
        return json.dumps(event, separators=(",", ":"), default=str)
