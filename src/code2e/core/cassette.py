"""Cassette store — LLM 호출 record/replay (v4 §3.10, §16.4).

Key: hash(agent + version + prompt_hash + canonical_input + model + temperature + repair_round).
저장 형식: cassettes/{name}/00001.{key8}.json (CassetteEntry).
DECISION: Q32 — secret 자동 redact.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from code2e.core.schemas import CassetteEntry, CassetteManifest

CassetteMode = Literal["off", "record", "replay", "auto"]


def compute_key(
    agent: str,
    agent_version: str,
    prompt_hash: str,
    canonical_input: str,
    model: str,
    temperature: float,
    repair_round: int = 0,
) -> str:
    raw = f"{agent}|{agent_version}|{prompt_hash}|{canonical_input}|{model}|{temperature}|{repair_round}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class CassetteStore:
    name: str
    dir: Path
    mode: CassetteMode = "auto"
    redact_secrets: bool = True

    def try_hit(self, key: str) -> CassetteEntry | None:
        raise NotImplementedError("CassetteStore.try_hit — phase 2 구현 예정")

    def record(self, entry: CassetteEntry) -> None:
        raise NotImplementedError("CassetteStore.record — phase 2 구현 예정")

    def manifest(self) -> CassetteManifest:
        raise NotImplementedError("CassetteStore.manifest — phase 2 구현 예정")

    @staticmethod
    def redact(payload: dict[str, object]) -> dict[str, object]:
        """secret 패턴 마스킹 (NFR-S-3)."""
        raise NotImplementedError("CassetteStore.redact — phase 2 구현 예정")


def canonicalize(obj: object) -> str:
    """비결정 필드(now/trace_id) 제외 + sort_keys 직렬화 (cassette 키 안정성)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
