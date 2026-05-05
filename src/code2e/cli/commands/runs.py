"""`code2e runs ls / gc / rm` — run 메타 관리 (v4 §4.1).

기본 파일 시스템 조작만 — checkpoint 파일에서 status / phase 만 읽고,
디렉토리 크기 / mtime 으로 정렬.

DECISION: Q29 의 r_<unix>_<rand> 형식만 인식 (다른 디렉토리는 스킵).
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

DEFAULT_RUNS_DIR = Path("runs")
RUN_ID_PREFIX = "r_"

app = typer.Typer(help="Manage past runs.")


@dataclass
class RunRow:
    run_id: str
    last_phase: str
    status: str
    mtime: datetime
    size_bytes: int


# ---------- ls ----------


@app.command("ls")
def ls(
    runs_dir: Path = typer.Option(DEFAULT_RUNS_DIR, "--runs-dir", help="Runs directory."),
) -> None:
    """List past runs (sorted by mtime)."""
    if not runs_dir.exists():
        typer.echo(f"No runs directory: {runs_dir}")
        return

    rows = sorted(
        (collect_row(child) for child in runs_dir.iterdir() if _is_run_dir(child)),
        key=lambda r: r.mtime,
    )

    if not rows:
        typer.echo(f"No runs in {runs_dir}.")
        return

    typer.echo(
        f"{'run_id':<28} {'last_phase':<12} {'status':<11} {'mtime':<19} size"
    )
    for r in rows:
        typer.echo(
            f"{r.run_id:<28} {r.last_phase:<12} {r.status:<11} "
            f"{r.mtime.strftime('%Y-%m-%d %H:%M'):<19} {format_size(r.size_bytes)}"
        )


# ---------- gc ----------


@app.command("gc")
def gc(
    older_than: str = typer.Option(
        "30d", "--older-than", help="Delete runs older than DURATION (e.g. 30d, 24h)."
    ),
    runs_dir: Path = typer.Option(DEFAULT_RUNS_DIR, "--runs-dir"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print what would be deleted, don't actually delete."
    ),
) -> None:
    """Garbage-collect runs older than DURATION."""
    if not runs_dir.exists():
        typer.echo(f"No runs directory: {runs_dir}")
        return

    cutoff = _cutoff_from_duration(older_than)
    deleted: list[str] = []
    skipped: list[str] = []
    for child in sorted(runs_dir.iterdir()):
        if not _is_run_dir(child):
            continue
        if datetime.fromtimestamp(child.stat().st_mtime, tz=UTC) < cutoff:
            if not dry_run:
                shutil.rmtree(child)
            deleted.append(child.name)
        else:
            skipped.append(child.name)

    label = "Would delete" if dry_run else "Deleted"
    typer.echo(f"{label}: {len(deleted)}")
    for name in deleted:
        typer.echo(f"  - {name}")
    typer.echo(f"Skipped (newer than cutoff): {len(skipped)}")


# ---------- rm ----------


@app.command("rm")
def rm(
    run_id: str = typer.Argument(..., help="Run id to delete."),
    runs_dir: Path = typer.Option(DEFAULT_RUNS_DIR, "--runs-dir"),
) -> None:
    """Delete a single run directory."""
    if not run_id.startswith(RUN_ID_PREFIX):
        typer.echo(
            f"Error: run_id must start with '{RUN_ID_PREFIX}': {run_id}", err=True
        )
        raise typer.Exit(2)
    target = runs_dir / run_id
    if not target.exists():
        typer.echo(f"Error: run not found: {target}", err=True)
        raise typer.Exit(2)
    if not target.is_dir():
        typer.echo(f"Error: not a directory: {target}", err=True)
        raise typer.Exit(2)

    shutil.rmtree(target)
    typer.echo(f"Deleted {target}")


# ---------- helpers ----------


def _is_run_dir(p: Path) -> bool:
    return p.is_dir() and p.name.startswith(RUN_ID_PREFIX)


def collect_row(run_dir: Path) -> RunRow:
    """run 디렉토리에서 RunRow 추출."""
    cp_dir = run_dir / "checkpoints"
    last_phase = "—"
    status = "—"
    if cp_dir.exists():
        cps = list(cp_dir.glob("after_*.json"))
        if cps:
            # mtime 가장 최근 체크포인트.
            latest = max(cps, key=lambda p: p.stat().st_mtime)
            try:
                data = json.loads(latest.read_text(encoding="utf-8"))
                status = str(data.get("status", "—"))
                last_phase = latest.stem.removeprefix("after_")
            except (json.JSONDecodeError, ValueError):
                pass

    mtime = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=UTC)
    size_bytes = sum(f.stat().st_size for f in run_dir.rglob("*") if f.is_file())

    return RunRow(
        run_id=run_dir.name,
        last_phase=last_phase,
        status=status,
        mtime=mtime,
        size_bytes=size_bytes,
    )


_DURATION_RE = re.compile(r"^(\d+)([dh])$")


def _cutoff_from_duration(s: str) -> datetime:
    """`30d` / `24h` 형식 → 현재 시각 - duration."""
    m = _DURATION_RE.match(s)
    if m is None:
        raise typer.BadParameter(
            f"invalid duration: {s!r} (expected like '30d' or '24h')"
        )
    n = int(m.group(1))
    unit = m.group(2)
    seconds = n * (86400 if unit == "d" else 3600)
    return datetime.now(UTC) - timedelta(seconds=seconds)


def format_size(n: int) -> str:
    """바이트 → 사람 읽기 쉬운 단위."""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"
