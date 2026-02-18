"""Backtest run-state helpers for TUI visibility."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

_VALID_STATUS = {"idle", "running", "done", "error"}


@dataclass(frozen=True)
class BacktestRunState:
    """Current backtest run state persisted to artifacts/backtest/run_state.json."""

    status: str = "idle"
    stage: str = "idle"
    run_id: str = ""
    mode: str = "history_signal"
    started_at: str = ""
    updated_at: str = ""
    finished_at: str | None = None
    latest_run_id: str | None = None
    message: str = ""
    error: str | None = None


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(sep=" ")


def _clean_text(value: object, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _normalize_status(value: object) -> str:
    status = _clean_text(value, "idle").lower()
    if status in _VALID_STATUS:
        return status
    return "idle"


def read_state(path: Path) -> BacktestRunState:
    """Load state file with safe defaults when file is missing/corrupt."""

    if not path.exists():
        return BacktestRunState()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return BacktestRunState()

    if not isinstance(payload, dict):
        return BacktestRunState()

    return BacktestRunState(
        status=_normalize_status(payload.get("status")),
        stage=_clean_text(payload.get("stage"), "idle"),
        run_id=_clean_text(payload.get("run_id")),
        mode=_clean_text(payload.get("mode"), "history_signal"),
        started_at=_clean_text(payload.get("started_at")),
        updated_at=_clean_text(payload.get("updated_at")),
        finished_at=_clean_text(payload.get("finished_at")) or None,
        latest_run_id=_clean_text(payload.get("latest_run_id")) or None,
        message=_clean_text(payload.get("message")),
        error=_clean_text(payload.get("error")) or None,
    )


def write_state_atomic(path: Path, state: BacktestRunState) -> None:
    """Write state file atomically to avoid torn reads in TUI."""

    path.parent.mkdir(parents=True, exist_ok=True)

    payload = asdict(state)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def mark_running(path: Path, *, run_id: str, mode: str, stage: str, message: str = "") -> BacktestRunState:
    """Persist running stage state."""

    now_txt = _utc_now_iso()
    prev = read_state(path)
    started_at = prev.started_at if prev.run_id == run_id and prev.started_at else now_txt

    state = BacktestRunState(
        status="running",
        stage=_clean_text(stage, "running"),
        run_id=_clean_text(run_id),
        mode=_clean_text(mode, "history_signal"),
        started_at=started_at,
        updated_at=now_txt,
        finished_at=None,
        latest_run_id=prev.latest_run_id,
        message=_clean_text(message),
        error=None,
    )
    write_state_atomic(path, state)
    return state


def mark_done(
    path: Path,
    *,
    run_id: str,
    mode: str,
    latest_run_id: str | None = None,
    message: str = "",
) -> BacktestRunState:
    """Persist successful completion state."""

    now_txt = _utc_now_iso()
    prev = read_state(path)
    started_at = prev.started_at if prev.run_id == run_id and prev.started_at else now_txt

    state = BacktestRunState(
        status="done",
        stage="done",
        run_id=_clean_text(run_id),
        mode=_clean_text(mode, "history_signal"),
        started_at=started_at,
        updated_at=now_txt,
        finished_at=now_txt,
        latest_run_id=_clean_text(latest_run_id or run_id) or None,
        message=_clean_text(message),
        error=None,
    )
    write_state_atomic(path, state)
    return state


def mark_error(
    path: Path,
    *,
    run_id: str,
    mode: str,
    stage: str,
    error: str,
    message: str = "",
) -> BacktestRunState:
    """Persist error state and the stage that failed."""

    now_txt = _utc_now_iso()
    prev = read_state(path)
    started_at = prev.started_at if prev.run_id == run_id and prev.started_at else now_txt
    err_txt = _clean_text(error, "unknown error")

    state = BacktestRunState(
        status="error",
        stage=_clean_text(stage, "error"),
        run_id=_clean_text(run_id),
        mode=_clean_text(mode, "history_signal"),
        started_at=started_at,
        updated_at=now_txt,
        finished_at=now_txt,
        latest_run_id=prev.latest_run_id,
        message=_clean_text(message),
        error=err_txt,
    )
    write_state_atomic(path, state)
    return state
