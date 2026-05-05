"""Cassette store — LLM 호출 record/replay (v4 §3.10, §16.4).

Key: hash(agent + version + prompt_hash + canonical_input + model + temperature + repair_round).
저장 형식: cassettes/{name}/{NNNNN}.{key8}.json (CassetteEntry).

- 비결정 필드(now/trace_id 등) 는 canonicalize 에서 제외 → 키 안정성 (NFR-R-5).
- record 시 secret 자동 redact (Q32, NFR-S-3, NFR-S-6).
- schema_version 불일치 entry 는 try_hit 에서 무시 (마이그레이션은 별도 명령).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from code2e.core.schemas import CassetteEntry, CassetteManifest

CassetteMode = Literal["off", "record", "replay", "auto"]

CASSETTE_SCHEMA_VERSION = 1

# v4 §3.10: 비결정 필드는 정규화에서 제외해 키 안정성을 확보.
_VOLATILE_KEYS = frozenset({"now", "trace_id", "request_id", "timestamp", "created_at"})

# Secret redaction patterns (NFR-S-3, NFR-S-6).
_SECRET_KEY_NAME = re.compile(
    r"^(api[_-]?key|apikey|authorization|password|secret|token)$",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)
_SK_PREFIX = re.compile(r"sk-[A-Za-z0-9_\-]{20,}")
REDACTED = "<redacted>"


def compute_key(
    agent: str,
    agent_version: str,
    prompt_hash: str,
    canonical_input: str,
    model: str,
    temperature: float,
    repair_round: int = 0,
) -> str:
    raw = (
        f"{agent}|{agent_version}|{prompt_hash}|{canonical_input}|"
        f"{model}|{temperature}|{repair_round}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _strip_volatile(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if k not in _VOLATILE_KEYS}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def canonicalize(obj: object) -> str:
    """비결정 필드 제외 + sort_keys 직렬화. cassette key 입력으로만 사용한다."""
    return json.dumps(_strip_volatile(obj), sort_keys=True, separators=(",", ":"), default=str)


def _redact_value(value: object, path: str) -> tuple[object, list[dict[str, str]]]:
    """value 안의 secret 패턴 마스킹. (redacted_value, [{path, type}, ...])."""
    if isinstance(value, str):
        out = value
        records: list[dict[str, str]] = []
        if _BEARER.search(out):
            out = _BEARER.sub(r"\1" + REDACTED, out)
            records.append({"path": path, "type": "bearer"})
        if _SK_PREFIX.search(out):
            out = _SK_PREFIX.sub(REDACTED, out)
            records.append({"path": path, "type": "sk-prefix"})
        return out, records
    if isinstance(value, dict):
        out_dict: dict[str, object] = {}
        records: list[dict[str, str]] = []
        for k, v in value.items():
            sub_path = f"{path}.{k}" if path else str(k)
            if _SECRET_KEY_NAME.match(str(k)):
                out_dict[k] = REDACTED
                records.append({"path": sub_path, "type": "key-name"})
            else:
                rv, sub = _redact_value(v, sub_path)
                out_dict[k] = rv
                records.extend(sub)
        return out_dict, records
    if isinstance(value, list):
        out_list: list[object] = []
        records: list[dict[str, str]] = []
        for i, v in enumerate(value):
            rv, sub = _redact_value(v, f"{path}[{i}]")
            out_list.append(rv)
            records.extend(sub)
        return out_list, records
    return value, []


def redact(payload: dict[str, object]) -> tuple[dict[str, object], list[dict[str, str]]]:
    """payload 의 secret 마스킹. (redacted_payload, redactions_log) 반환."""
    redacted, records = _redact_value(payload, "")
    assert isinstance(redacted, dict)
    return redacted, records


@dataclass
class CassetteStore:
    name: str
    dir: Path
    mode: CassetteMode = "auto"
    redact_secrets: bool = True

    @property
    def cassette_dir(self) -> Path:
        return self.dir / self.name

    def try_hit(self, key: str) -> CassetteEntry | None:
        """key 와 정확히 일치하는 entry 반환. 없거나 schema mismatch 면 None."""
        if not self.cassette_dir.exists():
            return None
        prefix = key[:8]
        for path in sorted(self.cassette_dir.glob(f"*.{prefix}.json")):
            try:
                data = json.loads(path.read_text())
                entry = CassetteEntry.model_validate(data)
            except (json.JSONDecodeError, ValueError):
                continue
            if entry.schema_version != CASSETTE_SCHEMA_VERSION:
                continue
            if entry.key == key:
                return entry
        return None

    def record(self, entry: CassetteEntry) -> Path:
        """다음 sequence 로 저장. redact_secrets=True 면 자동 마스킹."""
        if self.redact_secrets:
            req, log_a = redact(entry.request)
            resp, log_b = redact(entry.response)
            entry = entry.model_copy(
                update={
                    "request": req,
                    "response": resp,
                    "redactions": [*entry.redactions, *log_a, *log_b],
                }
            )
        self.cassette_dir.mkdir(parents=True, exist_ok=True)
        seq = self._next_sequence()
        path = self.cassette_dir / f"{seq:05d}.{entry.key[:8]}.json"
        path.write_text(json.dumps(entry.model_dump(mode="json"), indent=2, sort_keys=True))
        return path

    def _next_sequence(self) -> int:
        return sum(1 for _ in self.cassette_dir.glob("*.json")) + 1

    def manifest(self) -> CassetteManifest:
        raise NotImplementedError(
            "CassetteStore.manifest — phase 2 후반 (`code2e cassettes inspect`) 구현 예정"
        )
