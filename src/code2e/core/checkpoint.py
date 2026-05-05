"""Phase 경계 SystemState 저장/복구 (v4 §3.13, FR-011).

저장 위치: runs/{run_id}/checkpoints/after_{phase}.json
DECISION: Q19 — runs/.global.lock (fcntl) 으로 동시 run 단일화.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from code2e.core.schemas import SystemState


@dataclass
class CheckpointWriter:
    runs_root: Path

    def save(self, state: SystemState, phase: str) -> Path:
        raise NotImplementedError("CheckpointWriter.save — phase 2 구현 예정")

    def load(self, run_id: str, phase: str) -> SystemState:
        raise NotImplementedError("CheckpointWriter.load — phase 2 구현 예정")

    def acquire_global_lock(self) -> None:
        """Q19: 동시 run 단일화 (fcntl.flock on runs/.global.lock)."""
        raise NotImplementedError("CheckpointWriter.acquire_global_lock — phase 2 구현 예정")
