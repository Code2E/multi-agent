"""Phase 경계 SystemState 저장/복구 + 동시 run 단일화 (v4 §3.13, FR-011, Q19).

저장 위치:
  runs/{run_id}/checkpoints/after_{phase}.json   # SystemState 직렬화
  runs/.global.lock                              # 단일 run lock (Q19)

DECISION:
- Q19: runs/.global.lock 으로 단일 run 강제 (fcntl.flock LOCK_EX | LOCK_NB).
       Windows 는 fcntl 미지원 → no-op (NG-7 best-effort).
- Q16: phase 경계 resume — list_phases() 로 어디까지 저장됐는지 확인.
- Schema 호환성: SystemState.schema_version 검증은 호출자 책임 (load 는 raw 역직렬화만).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from code2e.core.schemas import SystemState

try:
    import fcntl  # POSIX only.

    _HAS_FCNTL = True
except ImportError:  # pragma: no cover — Windows fallback (NG-7).
    _HAS_FCNTL = False


GLOBAL_LOCK_FILE = ".global.lock"


class CheckpointError(Exception):
    """checkpoint 저장/복구 실패 base."""


class GlobalLockHeldError(CheckpointError):
    """다른 run 이 이미 runs/.global.lock 을 잡고 있음."""


@dataclass
class CheckpointWriter:
    runs_root: Path

    def run_dir(self, run_id: str) -> Path:
        return self.runs_root / run_id

    def checkpoint_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "checkpoints"

    def save(self, state: SystemState, phase: str) -> Path:
        """state 를 after_{phase}.json 으로 저장. 디렉토리 자동 생성."""
        d = self.checkpoint_dir(state.run_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"after_{phase}.json"
        path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load(self, run_id: str, phase: str) -> SystemState:
        """after_{phase}.json 에서 SystemState 복구. 없으면 CheckpointError."""
        path = self.checkpoint_dir(run_id) / f"after_{phase}.json"
        if not path.exists():
            raise CheckpointError(f"checkpoint not found: {path}")
        return SystemState.model_validate_json(path.read_text(encoding="utf-8"))

    def list_phases(self, run_id: str) -> list[str]:
        """저장된 phase 이름 목록 (정렬)."""
        d = self.checkpoint_dir(run_id)
        if not d.exists():
            return []
        return sorted(p.stem.removeprefix("after_") for p in d.glob("after_*.json"))

    @contextmanager
    def global_lock(self) -> Iterator[None]:
        """동시 run 단일화 (Q19). 다른 프로세스가 이미 잡고 있으면 GlobalLockHeldError.

        Windows / fcntl 미지원 환경에서는 no-op (NG-7 best-effort).
        """
        self.runs_root.mkdir(parents=True, exist_ok=True)
        lock_path = self.runs_root / GLOBAL_LOCK_FILE

        if not _HAS_FCNTL:
            # Windows fallback: lock 파일만 만들고 진짜 잠금은 안 함.
            lock_path.touch(exist_ok=True)
            try:
                yield
            finally:
                pass
            return

        # POSIX: open + flock (LOCK_EX | LOCK_NB).
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise GlobalLockHeldError(
                    f"another run holds {lock_path}; concurrent runs disabled (Q19)"
                ) from e
            try:
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass  # cleanup best-effort.
        finally:
            os.close(fd)
