"""Workspace — Executor 의 FileEdit 들을 디스크에 적용 (v4 NFR-S-1, NFR-S-2).

DECISION:
- Q15: localhost 신뢰 환경 가정. workspace 격리는 디렉토리 + 경로 검증으로 충분.
- NFR-S-1: 모든 파일 쓰기는 workspace 디렉토리 내부로 강제.
- NFR-S-2: path traversal (`..`, 절대경로, `~`) 차단.

apply() 는 in-place — 실패 시 부분 적용된 파일이 디스크에 남을 수 있다 (rollback 없음).
snapshot() 은 텍스트 파일만 (UTF-8 디코딩 가능한 것만). 바이너리는 스킵.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from code2e.core.schemas import FileEdit

# LLM 이 이모지를 ASCII surrogate pair escape (\\uD83C\\uDFF7) 로 출력한 경우,
# 디스크에 그대로 저장되면 Python import 시 string literal 평가 단계에서 raw
# surrogate 두 개로 메모리에 올라가고, 이후 starlette / json 등 UTF-8 인코딩에서
# "surrogates not allowed" 로 실패한다. 디스크 저장 전 high+low pair 를 결합한
# 실제 Astral plane 코드 포인트로 치환해 둔다 (실제 case: AcornFlow landing run).
_SURROGATE_PAIR_LITERAL_RE = re.compile(
    r"\\u(?P<high>[dD][89aAbB][0-9a-fA-F]{2})"
    r"\\u(?P<low>[dD][cCdDeEfF][0-9a-fA-F]{2})"
)


def _join_surrogate_pair(match: re.Match[str]) -> str:
    high = int(match.group("high"), 16)
    low = int(match.group("low"), 16)
    codepoint = 0x10000 + (high - 0xD800) * 0x400 + (low - 0xDC00)
    return chr(codepoint)


def _sanitize_surrogates(text: str) -> str:
    """LLM 출력 의 ASCII surrogate pair literal + raw surrogate 모두 정상화.

    1) `\\uD83C\\uDFF7` 같은 escape sequence → 실제 이모지 character.
    2) raw surrogate (UTF-16) → utf-16 round-trip 으로 합치기.
    둘 다 실패해도 원본 그대로 반환 (안전 fallback).
    """
    fixed = _SURROGATE_PAIR_LITERAL_RE.sub(_join_surrogate_pair, text)
    try:
        return fixed.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeDecodeError:
        return fixed


class WorkspaceError(Exception):
    """workspace 파일 적용 실패 base."""


class PathTraversalError(WorkspaceError):
    """`..` / 절대경로 / `~` 등 path traversal 시도 (NFR-S-2)."""


@dataclass
class Workspace:
    root: Path

    def apply(self, files: list[FileEdit]) -> None:
        """FileEdit 들을 root 안에 적용. path traversal 시 PathTraversalError raise.

        content 는 디스크 저장 직전 _sanitize_surrogates 거침 — LLM 의 잘못된
        surrogate pair 출력이 production 산출물에 누출되는 것을 차단.
        """
        for edit in files:
            target = self._resolve_safe(edit.path)
            if edit.op in ("create", "update"):
                target.parent.mkdir(parents=True, exist_ok=True)
                content = _sanitize_surrogates(edit.content or "")
                target.write_text(content, encoding="utf-8")
            elif edit.op == "delete":
                if target.exists():
                    target.unlink()

    def snapshot(self) -> list[FileEdit]:
        """현재 root 의 모든 텍스트 파일을 FileEdit(op="update") 로 반환.

        Executor 의 입력 (현재 워크스페이스 스냅샷) 으로 사용. 정렬은 path 기준.
        """
        if not self.root.exists():
            return []
        out: list[FileEdit] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root)
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # 바이너리 스킵.
                continue
            out.append(FileEdit(path=str(rel), op="update", content=content))
        return out

    def _resolve_safe(self, rel_path: str) -> Path:
        """상대경로 검증 후 절대경로 반환. 위반 시 PathTraversalError."""
        if not rel_path:
            raise PathTraversalError("empty path")
        # 1) 즉시 거부 패턴.
        if rel_path.startswith(("/", "~")):
            raise PathTraversalError(f"absolute / home path not allowed: {rel_path}")
        # 2) 명시 `..` 컴포넌트 거부.
        if ".." in Path(rel_path).parts:
            raise PathTraversalError(f"parent traversal not allowed: {rel_path}")
        # 3) 최종 안전망: 해석된 경로가 root 안인지 확인 (symlink 우회 방지).
        target = (self.root / rel_path).resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError as e:
            raise PathTraversalError(f"resolved path escapes workspace: {rel_path}") from e
        return target
