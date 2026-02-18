"""Artifact retention utilities."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def _remove_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    shutil.rmtree(path, ignore_errors=True)


def update_latest_link(backtest_root: Path, run_dir: Path) -> Path:
    """Update `latest` pointer to run_dir (symlink preferred, copy fallback)."""

    latest = backtest_root / "latest"
    _remove_path(latest)

    try:
        # Relative link makes artifact folder movable.
        target = os.path.relpath(run_dir, latest.parent)
        latest.symlink_to(target, target_is_directory=True)
    except Exception:
        shutil.copytree(run_dir, latest)

    return latest


def cleanup_old_runs(backtest_root: Path, keep_runs: int) -> list[str]:
    """Keep newest `keep_runs` run directories; return removed names."""

    keep = max(1, int(keep_runs))
    runs = [
        p
        for p in backtest_root.iterdir()
        if p.is_dir() and p.name != "latest"
    ]
    runs_sorted = sorted(runs, key=lambda p: p.stat().st_mtime, reverse=True)

    removed: list[str] = []
    for old in runs_sorted[keep:]:
        removed.append(old.name)
        _remove_path(old)

    return removed
