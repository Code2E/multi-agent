"""Unit tests for code2e.core.workspace (NFR-S-1, NFR-S-2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from code2e.core.schemas import FileEdit
from code2e.core.workspace import PathTraversalError, Workspace

# ---------- apply: create / update / delete ----------


def test_apply_creates_file(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    ws.apply([FileEdit(path="hello.py", op="create", content="print('hi')")])
    assert (tmp_path / "hello.py").read_text() == "print('hi')"


def test_apply_creates_nested_directories(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    ws.apply([FileEdit(path="src/pkg/mod.py", op="create", content="x = 1")])
    assert (tmp_path / "src" / "pkg" / "mod.py").read_text() == "x = 1"


def test_apply_update_overwrites(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    ws.apply([FileEdit(path="a.txt", op="create", content="v1")])
    ws.apply([FileEdit(path="a.txt", op="update", content="v2")])
    assert (tmp_path / "a.txt").read_text() == "v2"


def test_apply_delete_removes_file(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    ws.apply([FileEdit(path="x.py", op="create", content="x")])
    ws.apply([FileEdit(path="x.py", op="delete")])
    assert not (tmp_path / "x.py").exists()


def test_apply_delete_nonexistent_is_noop(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    ws.apply([FileEdit(path="never.py", op="delete")])  # no raise


def test_apply_create_with_none_content_writes_empty(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    ws.apply([FileEdit(path="empty.txt", op="create", content=None)])
    assert (tmp_path / "empty.txt").read_text() == ""


def test_apply_multiple_edits_in_order(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    ws.apply(
        [
            FileEdit(path="a.txt", op="create", content="A"),
            FileEdit(path="b.txt", op="create", content="B"),
            FileEdit(path="a.txt", op="update", content="A2"),
        ]
    )
    assert (tmp_path / "a.txt").read_text() == "A2"
    assert (tmp_path / "b.txt").read_text() == "B"


# ---------- path traversal (NFR-S-2) ----------


def test_apply_rejects_absolute_path(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    with pytest.raises(PathTraversalError):
        ws.apply([FileEdit(path="/etc/passwd", op="create", content="x")])


def test_apply_rejects_home_path(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    with pytest.raises(PathTraversalError):
        ws.apply([FileEdit(path="~/secret", op="create", content="x")])


def test_apply_rejects_parent_traversal(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    with pytest.raises(PathTraversalError):
        ws.apply([FileEdit(path="../escape.txt", op="create", content="x")])


def test_apply_rejects_nested_parent_traversal(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    with pytest.raises(PathTraversalError):
        ws.apply([FileEdit(path="src/../../escape.txt", op="create", content="x")])


def test_apply_rejects_empty_path(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    with pytest.raises(PathTraversalError):
        ws.apply([FileEdit(path="", op="create", content="x")])


def test_apply_rejects_symlink_escape(tmp_path: Path) -> None:
    """workspace 안에서 symlink 가 외부를 가리키면 resolve 후 검증에서 차단."""
    outside = tmp_path.parent / "outside_target"
    outside.mkdir()
    (tmp_path).mkdir(exist_ok=True)
    link = tmp_path / "evil_link"
    link.symlink_to(outside)
    ws = Workspace(root=tmp_path)
    with pytest.raises(PathTraversalError):
        ws.apply([FileEdit(path="evil_link/escaped.txt", op="create", content="x")])


# ---------- snapshot ----------


def test_snapshot_empty_when_root_missing(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path / "doesnotexist")
    assert ws.snapshot() == []


def test_snapshot_returns_text_files(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    ws.apply(
        [
            FileEdit(path="a.txt", op="create", content="A"),
            FileEdit(path="b.py", op="create", content="print('hi')"),
        ]
    )
    snap = ws.snapshot()
    assert {f.path for f in snap} == {"a.txt", "b.py"}
    assert all(f.op == "update" for f in snap)
    assert any(f.content == "A" for f in snap)


def test_snapshot_skips_binary_files(tmp_path: Path) -> None:
    """UTF-8 디코딩 실패하면 스킵."""
    ws = Workspace(root=tmp_path)
    ws.apply([FileEdit(path="text.txt", op="create", content="ok")])
    # 바이너리 파일은 디스크에 직접.
    (tmp_path / "binary.bin").write_bytes(b"\x80\x81\x82\xff")
    snap = ws.snapshot()
    paths = {f.path for f in snap}
    assert "text.txt" in paths
    assert "binary.bin" not in paths


def test_snapshot_recurses_into_subdirs(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    ws.apply([FileEdit(path="src/pkg/m.py", op="create", content="x = 1")])
    snap = ws.snapshot()
    assert any(f.path == "src/pkg/m.py" for f in snap)


def test_snapshot_sorted_by_path(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    ws.apply(
        [
            FileEdit(path="z.txt", op="create", content="z"),
            FileEdit(path="a.txt", op="create", content="a"),
            FileEdit(path="m.txt", op="create", content="m"),
        ]
    )
    paths = [f.path for f in ws.snapshot()]
    assert paths == sorted(paths)
