"""Unit tests for code2e.core.cassette (v4 §3.10)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from code2e.core.cassette import (
    CASSETTE_SCHEMA_VERSION,
    REDACTED,
    CassetteStore,
    canonicalize,
    compute_key,
    redact,
)
from code2e.core.schemas import CassetteEntry

# ---------- compute_key ----------


def test_compute_key_deterministic() -> None:
    a = compute_key("planner", "1.0", "ph", "ci", "claude-x", 0.7, 0)
    b = compute_key("planner", "1.0", "ph", "ci", "claude-x", 0.7, 0)
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_compute_key_diverges_per_field() -> None:
    base = compute_key("planner", "1.0", "ph", "ci", "claude-x", 0.7, 0)
    assert base != compute_key("executor", "1.0", "ph", "ci", "claude-x", 0.7, 0)
    assert base != compute_key("planner", "2.0", "ph", "ci", "claude-x", 0.7, 0)
    assert base != compute_key("planner", "1.0", "ph2", "ci", "claude-x", 0.7, 0)
    assert base != compute_key("planner", "1.0", "ph", "ci2", "claude-x", 0.7, 0)
    assert base != compute_key("planner", "1.0", "ph", "ci", "claude-y", 0.7, 0)
    assert base != compute_key("planner", "1.0", "ph", "ci", "claude-x", 0.3, 0)
    assert base != compute_key("planner", "1.0", "ph", "ci", "claude-x", 0.7, 1)


# ---------- canonicalize ----------


def test_canonicalize_strips_volatile_fields() -> None:
    """v4 §3.10: now / trace_id 등 비결정 필드는 키 정규화에서 제외."""
    a = canonicalize({"x": 1, "now": "2026-05-05", "trace_id": "abc"})
    b = canonicalize({"x": 1, "now": "different", "trace_id": "xyz"})
    assert a == b


def test_canonicalize_sorts_keys() -> None:
    a = canonicalize({"b": 2, "a": 1})
    b = canonicalize({"a": 1, "b": 2})
    assert a == b


def test_canonicalize_strips_volatile_in_nested() -> None:
    a = canonicalize({"req": {"x": 1, "trace_id": "t1"}})
    b = canonicalize({"req": {"x": 1, "trace_id": "t2"}})
    assert a == b


# ---------- redact ----------


def test_redact_secret_key_names() -> None:
    payload, log = redact({"api_key": "sk-abcd", "data": "ok"})
    assert payload["api_key"] == REDACTED
    assert payload["data"] == "ok"
    assert any(r["type"] == "key-name" for r in log)


def test_redact_bearer_token_in_string() -> None:
    payload, log = redact({"headers": {"Authorization": "Bearer eyJabc.def_ghi-jkl"}})
    # `authorization` 키 자체가 secret 키 이름이라 값이 통째로 redact 됨.
    assert payload["headers"]["Authorization"] == REDACTED  # type: ignore[index]
    assert any(r["type"] == "key-name" for r in log)


def test_redact_bearer_inside_arbitrary_string() -> None:
    payload, log = redact({"note": "received Bearer abc.def-123 from upstream"})
    assert REDACTED in payload["note"]  # type: ignore[operator]
    assert "abc.def-123" not in payload["note"]  # type: ignore[operator]
    assert any(r["type"] == "bearer" for r in log)


def test_redact_sk_prefix() -> None:
    payload, log = redact({"note": "key=sk-1234567890abcdef1234567890"})
    assert "sk-1234567890" not in payload["note"]  # type: ignore[operator]
    assert any(r["type"] == "sk-prefix" for r in log)


def test_redact_recurses_into_lists() -> None:
    payload, log = redact({"messages": [{"password": "p"}, {"text": "ok"}]})
    assert payload["messages"][0]["password"] == REDACTED  # type: ignore[index]
    assert payload["messages"][1]["text"] == "ok"  # type: ignore[index]
    assert any("messages[0].password" in r["path"] for r in log)


def test_redact_no_op_on_clean_payload() -> None:
    payload = {"q": "What is the weather?", "model": "claude"}
    out, log = redact(payload)
    assert out == payload
    assert log == []


# ---------- CassetteStore round-trip ----------


def _entry(key: str = "k" * 64, agent: str = "planner") -> CassetteEntry:
    return CassetteEntry(
        schema_version=CASSETTE_SCHEMA_VERSION,
        key=key,
        agent=agent,
        agent_version="1.0",
        model_id="claude-sonnet-4-6",
        request={"messages": [{"role": "user", "content": "hi"}]},
        response={"content": "hello"},
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.001,
        recorded_at=datetime(2026, 5, 5, tzinfo=UTC),
    )


def test_record_then_try_hit_round_trip(tmp_path: Path) -> None:
    store = CassetteStore(name="cs1", dir=tmp_path)
    entry = _entry()
    path = store.record(entry)
    assert path.exists()
    assert path.parent == tmp_path / "cs1"
    assert path.name.endswith(f".{entry.key[:8]}.json")

    hit = store.try_hit(entry.key)
    assert hit is not None
    assert hit.key == entry.key


def test_try_hit_returns_none_when_missing(tmp_path: Path) -> None:
    store = CassetteStore(name="empty", dir=tmp_path)
    assert store.try_hit("z" * 64) is None


def test_try_hit_skips_schema_mismatch(tmp_path: Path) -> None:
    store = CassetteStore(name="cs", dir=tmp_path, redact_secrets=False)
    e = _entry()
    store.record(e)
    # 디스크 파일을 직접 변조해서 schema_version 만 바꾼다.
    files = list((tmp_path / "cs").glob("*.json"))
    import json as _json

    data = _json.loads(files[0].read_text())
    data["schema_version"] = 99
    files[0].write_text(_json.dumps(data))
    assert store.try_hit(e.key) is None


def test_record_redacts_secrets_by_default(tmp_path: Path) -> None:
    store = CassetteStore(name="cs", dir=tmp_path)
    e = CassetteEntry(
        schema_version=CASSETTE_SCHEMA_VERSION,
        key="k" * 64,
        agent="planner",
        agent_version="1.0",
        model_id="claude",
        request={"api_key": "sk-livekey1234567890abcd"},
        response={"text": "ok"},
        tokens_in=1,
        tokens_out=1,
        cost_usd=0.0,
        recorded_at=datetime(2026, 5, 5, tzinfo=UTC),
    )
    store.record(e)
    hit = store.try_hit(e.key)
    assert hit is not None
    assert hit.request["api_key"] == REDACTED
    assert any(r["type"] == "key-name" for r in hit.redactions)


def test_record_keeps_secrets_when_redact_disabled(tmp_path: Path) -> None:
    store = CassetteStore(name="cs", dir=tmp_path, redact_secrets=False)
    e = CassetteEntry(
        schema_version=CASSETTE_SCHEMA_VERSION,
        key="k" * 64,
        agent="planner",
        agent_version="1.0",
        model_id="claude",
        request={"password": "p"},
        response={"text": "ok"},
        tokens_in=1,
        tokens_out=1,
        cost_usd=0.0,
        recorded_at=datetime(2026, 5, 5, tzinfo=UTC),
    )
    store.record(e)
    hit = store.try_hit(e.key)
    assert hit is not None
    assert hit.request["password"] == "p"


def test_sequence_increments(tmp_path: Path) -> None:
    store = CassetteStore(name="cs", dir=tmp_path)
    p1 = store.record(_entry(key="a" * 64))
    p2 = store.record(_entry(key="b" * 64))
    assert p1.name.startswith("00001.")
    assert p2.name.startswith("00002.")
