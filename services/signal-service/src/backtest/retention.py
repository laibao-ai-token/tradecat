"""Artifact retention and comparable-history lookup utilities."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .models import Metrics


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
        target = os.path.relpath(run_dir, latest.parent)
        latest.symlink_to(target, target_is_directory=True)
    except Exception:
        shutil.copytree(run_dir, latest)

    return latest


def cleanup_old_runs(backtest_root: Path, keep_runs: int) -> list[str]:
    """Keep newest `keep_runs` run directories; return removed names."""

    keep = max(1, int(keep_runs))
    runs = [p for p in backtest_root.iterdir() if p.is_dir() and p.name != "latest"]
    runs_sorted = sorted(runs, key=lambda p: p.stat().st_mtime, reverse=True)

    removed: list[str] = []
    for old in runs_sorted[keep:]:
        removed.append(old.name)
        _remove_path(old)

    return removed


def _safe_load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_symbols(raw: object) -> tuple[str, ...]:
    if isinstance(raw, (list, tuple, set)):
        values = raw
    elif raw is None:
        values = []
    else:
        values = [raw]

    out: list[str] = []
    for item in values:
        text = str(item or "").upper().strip()
        if text:
            out.append(text)
    return tuple(sorted(dict.fromkeys(out)))


def _clean_text(raw: object) -> str:
    return str(raw or "").strip()


def _canonical_json(raw: object) -> str:
    try:
        return json.dumps(raw, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except Exception:
        return ""


def _is_same_context(payload: dict[str, Any], current_metrics: Metrics) -> bool:
    if _clean_text(payload.get("mode")) != _clean_text(current_metrics.mode):
        return False
    if _clean_text(payload.get("start")) != _clean_text(current_metrics.start):
        return False
    if _clean_text(payload.get("end")) != _clean_text(current_metrics.end):
        return False
    if _clean_text(payload.get("timeframe")) != _clean_text(current_metrics.timeframe):
        return False
    if _normalize_symbols(payload.get("symbols")) != _normalize_symbols(current_metrics.symbols):
        return False
    if _clean_text(payload.get("strategy_config_path")) != _clean_text(current_metrics.strategy_config_path):
        return False
    if _clean_text(payload.get("strategy_label")) != _clean_text(current_metrics.strategy_label):
        return False
    if _clean_text(payload.get("strategy_summary")) != _clean_text(current_metrics.strategy_summary):
        return False

    payload_context = payload.get("strategy_context")
    payload_context_dict = payload_context if isinstance(payload_context, dict) else None
    current_context = getattr(current_metrics, "strategy_context", {}) or {}
    has_payload_context = bool(payload_context_dict)
    has_current_context = bool(current_context)
    if has_payload_context or has_current_context:
        if not has_payload_context or not has_current_context:
            return False
        if _canonical_json(payload_context_dict) != _canonical_json(current_context):
            return False
    return True


def collect_recent_comparable_metrics(
    backtest_root: Path,
    *,
    current_metrics: Metrics,
    exclude_run_dir: Path | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Collect recent comparable metrics payloads across historical backtest artifacts."""

    root = Path(backtest_root)
    if not root.exists() or not root.is_dir():
        return []

    exclude_resolved = exclude_run_dir.resolve() if exclude_run_dir is not None and exclude_run_dir.exists() else None
    rows: list[tuple[float, dict[str, Any]]] = []

    for top in root.iterdir():
        if top.name == "latest":
            continue
        if not top.is_dir():
            continue
        for metrics_path in top.rglob("metrics.json"):
            run_dir = metrics_path.parent
            try:
                resolved_run_dir = run_dir.resolve()
            except Exception:
                resolved_run_dir = run_dir
            if exclude_resolved is not None and resolved_run_dir == exclude_resolved:
                continue

            payload = _safe_load_json(metrics_path)
            if payload is None or not _is_same_context(payload, current_metrics):
                continue

            try:
                sort_key = float(metrics_path.stat().st_mtime)
            except Exception:
                sort_key = 0.0
            payload["artifact_dir"] = os.path.relpath(run_dir, root)
            rows.append((sort_key, payload))

    rows.sort(key=lambda item: item[0], reverse=True)
    return [payload for _, payload in rows[: max(0, int(limit))]]
