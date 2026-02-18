"""Backtest run-state persistence tests."""

from __future__ import annotations

from pathlib import Path

from src.backtest.state import mark_done, mark_error, mark_running, read_state


def test_mark_running_then_done_keeps_started_at(tmp_path: Path) -> None:
    state_path = tmp_path / "run_state.json"

    s1 = mark_running(
        state_path,
        run_id="run-1",
        mode="history_signal",
        stage="loading_signals",
        message="loading",
    )
    assert s1.status == "running"
    assert s1.stage == "loading_signals"
    assert s1.started_at
    assert s1.finished_at is None

    s2 = mark_running(
        state_path,
        run_id="run-1",
        mode="history_signal",
        stage="executing",
        message="executing",
    )
    assert s2.status == "running"
    assert s2.stage == "executing"
    assert s2.started_at == s1.started_at

    s3 = mark_done(
        state_path,
        run_id="run-1",
        mode="history_signal",
        latest_run_id="run-1",
        message="done",
    )
    assert s3.status == "done"
    assert s3.stage == "done"
    assert s3.latest_run_id == "run-1"
    assert s3.finished_at

    loaded = read_state(state_path)
    assert loaded.status == "done"
    assert loaded.run_id == "run-1"
    assert loaded.error is None


def test_mark_error_sets_stage_and_error(tmp_path: Path) -> None:
    state_path = tmp_path / "run_state.json"

    mark_running(
        state_path,
        run_id="run-err",
        mode="history_signal",
        stage="loading_candles",
        message="loading candles",
    )

    s = mark_error(
        state_path,
        run_id="run-err",
        mode="history_signal",
        stage="loading_candles",
        error="RuntimeError: boom",
        message="failed",
    )
    assert s.status == "error"
    assert s.stage == "loading_candles"
    assert s.error == "RuntimeError: boom"
    assert s.finished_at

    loaded = read_state(state_path)
    assert loaded.status == "error"
    assert loaded.message == "failed"
