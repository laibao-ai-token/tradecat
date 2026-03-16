from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "tradecat_get_backtest_summary.py"


def _run_tool(*args: str) -> tuple[int, dict[str, object]]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.stdout, proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def test_reads_metrics_summary_by_run_id(tmp_path: Path) -> None:
    root = tmp_path / "artifacts" / "backtest"
    run_dir = root / "20260310-120000"
    run_dir.mkdir(parents=True)
    (run_dir / "metrics.json").write_text(
        json.dumps(
            {
                "run_id": "unit-run",
                "mode": "history_signal",
                "start": "2026-01-01 00:00:00+00:00",
                "end": "2026-01-07 00:00:00+00:00",
                "symbols": ["BTCUSDT", "ETHUSDT"],
                "timeframe": "1m",
                "total_return_pct": 12.5,
                "max_drawdown_pct": 4.2,
                "sharpe": 1.9,
                "trade_count": 14,
                "win_rate_pct": 57.1,
                "profit_factor": 1.4,
                "avg_holding_minutes": 16.5,
                "signal_count": 120,
                "bar_count": 4200,
                "excess_return_pct": 6.3,
                "strategy_label": "dual-v3",
                "strategy_config_path": "src/backtest/strategies/default.crypto.yaml",
                "strategy_summary": "L/S/C=130/130/20",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "report.md").write_text("# report\n", encoding="utf-8")

    rc, payload = _run_tool(
        "--artifacts-root",
        str(root),
        "--run-id",
        "unit-run",
        "--strategy",
        "dual-v3",
        "--symbols",
        "BTCUSDT",
    )

    assert rc == 0
    assert payload["ok"] is True
    assert payload["data"]["run_id"] == "unit-run"
    assert payload["data"]["metrics"]["mode"] == "history_signal"
    assert payload["data"]["strategy"]["label"] == "dual-v3"
    assert payload["data"]["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert payload["data"]["artifacts"]["kind"] == "metrics"
    assert payload["source"]["matched_by"] == ["run_id", "strategy", "symbols"]


def test_reads_compare_summary_by_base_run_id(tmp_path: Path) -> None:
    root = tmp_path / "artifacts" / "backtest"
    session_dir = root / "20260310-121500"
    history_dir = session_dir / "cmp-001-history"
    rule_dir = session_dir / "cmp-001-rules"
    compare_dir = session_dir / "cmp-001-compare"
    history_dir.mkdir(parents=True)
    rule_dir.mkdir(parents=True)
    compare_dir.mkdir(parents=True)

    shared_metrics = {
        "start": "2026-01-01 00:00:00+00:00",
        "end": "2026-01-10 00:00:00+00:00",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "timeframe": "1m",
        "strategy_label": "cmp-safe",
        "strategy_config_path": "src/backtest/strategies/default.crypto.btc_eth.safe.yaml",
        "strategy_summary": "safe profile",
    }
    (history_dir / "metrics.json").write_text(
        json.dumps(
            {
                **shared_metrics,
                "run_id": "cmp-001-history",
                "mode": "history_signal",
                "total_return_pct": 2.5,
                "max_drawdown_pct": 5.1,
                "trade_count": 11,
                "win_rate_pct": 54.5,
                "signal_count": 80,
                "bar_count": 800,
                "excess_return_pct": 1.0,
            }
        ),
        encoding="utf-8",
    )
    (rule_dir / "metrics.json").write_text(
        json.dumps(
            {
                **shared_metrics,
                "run_id": "cmp-001-rules",
                "mode": "offline_rule_replay",
                "total_return_pct": 4.0,
                "max_drawdown_pct": 4.2,
                "trade_count": 9,
                "win_rate_pct": 55.5,
                "signal_count": 96,
                "bar_count": 800,
                "excess_return_pct": 2.3,
            }
        ),
        encoding="utf-8",
    )
    (compare_dir / "comparison.json").write_text(
        json.dumps(
            {
                "run_id": "cmp-001",
                "history_run_id": "cmp-001-history",
                "rule_run_id": "cmp-001-rules",
                "delta_return_pct": 1.5,
                "delta_max_drawdown_pct": -0.9,
                "delta_trade_count": -2,
                "delta_excess_return_pct": 1.3,
                "delta_signal_count": 16,
                "delta_buy_ratio_pct": -3.0,
                "alignment_score": 78.5,
                "alignment_status": "warn",
                "alignment_risk_level": "medium",
                "alignment_risk_summary": "review needed",
                "alignment_warnings": [{"kind": "rule_gap", "subject": "MACD"}],
                "rule_overlap": {"shared_rule_types": 8, "jaccard_pct": 72.0},
                "signal_type_delta_top": [{"key": "MACD", "delta": -4}],
                "timeframe_delta_top": [{"key": "1m", "delta": 16}],
            }
        ),
        encoding="utf-8",
    )

    rc, payload = _run_tool(
        "--artifacts-root",
        str(root),
        "--run-id",
        "cmp-001",
        "--strategy",
        "safe.yaml",
        "--symbols",
        "BTCUSDT,ETHUSDT",
    )

    assert rc == 0
    assert payload["ok"] is True
    assert payload["data"]["run_id"] == "cmp-001"
    assert payload["data"]["metrics"]["mode"] == "compare_history_rule"
    assert payload["data"]["metrics"]["rule"]["mode"] == "offline_rule_replay"
    assert payload["data"]["window"]["timeframe"] == "1m"
    assert payload["data"]["artifacts"]["kind"] == "comparison"
    assert payload["source"]["matched_by"] == ["run_id", "strategy", "symbols"]


def test_returns_structured_error_when_run_is_missing(tmp_path: Path) -> None:
    root = tmp_path / "artifacts" / "backtest"
    root.mkdir(parents=True)

    rc, payload = _run_tool(
        "--artifacts-root",
        str(root),
        "--run-id",
        "missing-run",
    )

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error"]["code"] == "artifact_not_found"
    assert payload["data"] is None
