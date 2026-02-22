"""Walk-forward utilities tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from src.backtest.models import BacktestConfig, DateRange, Metrics, WalkForwardConfig
from src.backtest.walkforward import build_walk_forward_windows, run_walk_forward


def _mk_metrics(run_id: str, ret: float, max_dd: float, excess: float, mode: str = "history_signal") -> Metrics:
    return Metrics(
        run_id=run_id,
        mode=mode,
        start="2026-01-01 00:00:00+00:00",
        end="2026-01-08 00:00:00+00:00",
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="1m",
        initial_equity=1000.0,
        final_equity=1000.0 * (1.0 + ret / 100.0),
        total_return_pct=ret,
        max_drawdown_pct=max_dd,
        sharpe=1.0,
        trade_count=10,
        win_rate_pct=50.0,
        profit_factor=1.1,
        avg_holding_minutes=5.0,
        signal_count=100,
        bar_count=1000,
        buy_hold_final_equity=1010.0,
        buy_hold_return_pct=1.0,
        excess_return_pct=excess,
        symbol_contributions=[],
    )


def test_build_walk_forward_windows_respects_cap() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 31, tzinfo=timezone.utc)

    out = build_walk_forward_windows(
        start,
        end,
        train_days=10,
        test_days=5,
        step_days=5,
        max_folds=3,
    )

    assert len(out) == 3
    assert out[0].fold == 1
    assert out[0].train_start == start
    assert out[0].test_start > out[0].train_start
    assert out[-1].fold == 3


def test_run_walk_forward_writes_summary(monkeypatch, tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 1, tzinfo=timezone.utc)

    monkeypatch.setattr("src.backtest.walkforward.REPO_ROOT", tmp_path)

    # Keep range deterministic and avoid loading external clocks.
    monkeypatch.setattr("src.backtest.walkforward.resolve_range", lambda _: (start, end))

    calls = []

    def _fake_run_backtest(cfg, *, mode, run_id, output_dir=None):
        calls.append((cfg.date_range.start, cfg.date_range.end, mode, run_id))
        idx = len(calls)
        return SimpleNamespace(
            metrics=_mk_metrics(
                run_id,
                ret=float(idx),
                max_dd=float(idx) * 2.0,
                excess=-0.5 * idx,
                mode=mode,
            )
        )

    monkeypatch.setattr("src.backtest.walkforward.run_backtest", _fake_run_backtest)

    cfg = BacktestConfig(
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="1m",
        date_range=DateRange(start="2026-01-01 00:00:00", end="2026-02-01 00:00:00"),
        walk_forward=WalkForwardConfig(train_days=7, test_days=5, step_days=5),
    )

    summary = run_walk_forward(cfg, mode="history_signal", run_id="wf-unit", max_folds=2)

    assert summary.fold_count == 2
    assert summary.avg_return_pct > 0
    assert len(calls) == 2

    output_dir = tmp_path / "artifacts" / "backtest" / "wf-unit"
    assert output_dir == summary.output_dir
    assert (output_dir / "walk_forward_folds.csv").exists()
    assert (output_dir / "metrics.json").exists()
    assert (output_dir / "equity_curve.csv").exists()

    payload = json.loads((output_dir / "walk_forward_summary.json").read_text(encoding="utf-8"))
    assert payload["fold_count"] == 2
    assert len(payload["folds"]) == 2
    assert payload["avg_excess_return_pct"] < 0

    metrics_payload = json.loads((output_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics_payload["mode"] == "walk_forward"
    assert metrics_payload["walk_forward_summary"]["fold_count"] == 2

    latest = tmp_path / "artifacts" / "backtest" / "latest"
    assert latest.exists()
    if latest.is_symlink():
        assert latest.resolve() == output_dir.resolve()
    else:
        assert (latest / "walk_forward_summary.json").exists()


def test_run_walk_forward_auto_fallback_to_offline(monkeypatch, tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 1, tzinfo=timezone.utc)

    monkeypatch.setattr("src.backtest.walkforward.REPO_ROOT", tmp_path)
    monkeypatch.setattr("src.backtest.walkforward.resolve_range", lambda _: (start, end))

    from types import SimpleNamespace

    monkeypatch.setattr(
        "src.backtest.walkforward.compute_coverage_report",
        lambda cfg: SimpleNamespace(signal_days=0, signal_count=0),
    )

    mode_calls = []

    def _fake_run_backtest(cfg, *, mode, run_id, output_dir=None):
        mode_calls.append(mode)
        idx = len(mode_calls)
        return SimpleNamespace(
            metrics=_mk_metrics(
                run_id,
                ret=-1.0 * idx,
                max_dd=2.0,
                excess=-0.2,
                mode=mode,
            )
        )

    monkeypatch.setattr("src.backtest.walkforward.run_backtest", _fake_run_backtest)

    cfg = BacktestConfig(
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="1m",
        date_range=DateRange(start="2026-01-01 00:00:00", end="2026-02-01 00:00:00"),
        walk_forward=WalkForwardConfig(train_days=7, test_days=5, step_days=5),
    )

    summary = run_walk_forward(
        cfg,
        mode="history_signal",
        run_id="wf-fallback",
        max_folds=2,
        auto_fallback=True,
        min_signal_days=1,
        min_signal_count=1,
    )

    assert mode_calls == ["offline_replay", "offline_replay"]
    assert summary.history_fold_count == 0
    assert summary.replay_fold_count == 2
    assert summary.fallback_fold_count == 2

    payload = json.loads(
        (tmp_path / "artifacts" / "backtest" / "wf-fallback" / "walk_forward_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["fallback_fold_count"] == 2
    assert payload["folds"][0]["fallback_reason"]
