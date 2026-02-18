import json
import tempfile
from datetime import datetime
from pathlib import Path
import time
import unittest


class TestTencentQuoteParse(unittest.TestCase):
    def test_parse_single_line(self) -> None:
        # NOTE: name field can be non-ASCII; parser should still work.
        from src.quote import _parse_tencent_quote_line

        parts = ["0"] * 71
        parts[0] = "200"
        parts[1] = "Nvidia"
        parts[2] = "NVDA.OQ"
        parts[3] = "180.32"
        parts[4] = "185.61"
        parts[5] = "186.24"
        parts[33] = "186.24"
        parts[34] = "179.39"
        parts[35] = "USD"
        parts[36] = "76135567"
        parts[37] = "13847068894"
        parts[30] = "2026-02-03 11:03:00"
        line = f'v_usNVDA="{"~".join(parts)}";'
        q = _parse_tencent_quote_line(line)
        self.assertIsNotNone(q)
        assert q is not None
        self.assertEqual(q.symbol, "NVDA")
        self.assertAlmostEqual(q.price, 180.32, places=6)
        self.assertAlmostEqual(q.prev_close, 185.61, places=6)
        self.assertAlmostEqual(q.open, 186.24, places=6)
        self.assertAlmostEqual(q.high, 186.24, places=6)
        self.assertAlmostEqual(q.low, 179.39, places=6)
        self.assertEqual(q.currency, "USD")
        self.assertAlmostEqual(q.volume, 76135567.0, places=6)
        self.assertAlmostEqual(q.amount, 13847068894.0, places=6)
        from zoneinfo import ZoneInfo

        ny_dt = datetime(2026, 2, 3, 11, 3, 0, tzinfo=ZoneInfo("America/New_York"))
        expected_local = ny_dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(q.ts, expected_local)
        self.assertEqual(q.source, "tencent")

    def test_normalize_market_ts_us_converts_to_local(self) -> None:
        from src.quote import _normalize_market_ts
        from zoneinfo import ZoneInfo

        raw = "2026-02-17 10:06:41"
        ny_dt = datetime(2026, 2, 17, 10, 6, 41, tzinfo=ZoneInfo("America/New_York"))
        expected_local = ny_dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(_normalize_market_ts(raw, "us"), expected_local)

    def test_parse_ts_offset_keeps_naive_datetime(self) -> None:
        from src.db import parse_ts

        dt = parse_ts("2026-02-17T10:06:41-05:00")
        self.assertNotEqual(dt, datetime.min)
        self.assertIsNone(dt.tzinfo)
        _ = datetime.now() - dt

    def test_decode_gbk(self) -> None:
        # Ensure our decode fallback can handle common GBK bytes.
        # Don't hit network here; just validate decode path behavior by mimicking bytes.
        # GBK for "腾讯" is: \xcc\xda\xd1\xb6
        data = b'\xcc\xda\xd1\xb6'
        try:
            txt = data.decode("utf-8")
            self.assertEqual(txt, "腾讯")  # pragma: no cover
        except UnicodeDecodeError:
            self.assertEqual(data.decode("gb18030", errors="replace"), "腾讯")

    def test_normalize_us_symbols_filters_garbage(self) -> None:
        from src.watchlists import normalize_us_symbols

        # contains ESC / brackets / control-like content, should be filtered out
        raw = "NVDA,\x1b[,[,META,^C^D,ORCL"
        self.assertEqual(normalize_us_symbols(raw), ["NVDA", "META", "ORCL"])

    def test_normalize_cn_symbols_infer_exchange(self) -> None:
        from src.watchlists import normalize_cn_symbols

        self.assertEqual(normalize_cn_symbols("688041"), ["SH688041"])
        self.assertEqual(normalize_cn_symbols("000001"), ["SZ000001"])
        self.assertEqual(normalize_cn_symbols("300750"), ["SZ300750"])

    def test_normalize_crypto_symbols(self) -> None:
        from src.watchlists import normalize_crypto_symbols

        self.assertEqual(normalize_crypto_symbols("btc_usdt,ETH-USDT"), ["BTC_USDT", "ETH_USDT"])
        self.assertEqual(normalize_crypto_symbols("BTCUSDT"), ["BTC_USDT"])
        self.assertEqual(normalize_crypto_symbols("DOGE"), ["DOGE_USDT"])
        self.assertEqual(normalize_crypto_symbols("bad$,^^,BTC_USDT"), ["BTC_USDT"])

    def test_normalize_metals_symbols(self) -> None:
        from src.watchlists import normalize_metals_symbols

        self.assertEqual(normalize_metals_symbols("XAUUSD,XAGUSD"), ["XAUUSD", "XAGUSD"])
        self.assertEqual(normalize_metals_symbols("XAUUSD=X,XAGUSD=X"), ["XAUUSD", "XAGUSD"])
        self.assertEqual(normalize_metals_symbols("bad$,XAUUSD=X"), ["XAUUSD"])

    def test_parse_hk_line(self) -> None:
        from src.quote import _parse_tencent_quote_line

        parts = ["0"] * 78
        parts[0] = "100"
        parts[1] = "TENCENT"
        parts[2] = "00700"
        parts[3] = "572.00"
        parts[4] = "581.00"
        parts[5] = "572.00"
        parts[33] = "574.50"
        parts[34] = "568.00"
        parts[36] = "4636127.0"
        parts[37] = "2646878349.660"
        parts[30] = "2026/02/04 09:32:26"
        parts[75] = "HKD"
        line = f'v_hk00700="{"~".join(parts)}";'

        q = _parse_tencent_quote_line(line)
        assert q is not None
        self.assertEqual(q.symbol, "00700")
        self.assertEqual(q.currency, "HKD")
        self.assertEqual(q.ts, "2026-02-04 09:32:26")
        self.assertAlmostEqual(q.high, 574.50, places=6)
        self.assertAlmostEqual(q.low, 568.00, places=6)

    def test_parse_cn_line(self) -> None:
        from src.quote import _parse_tencent_quote_line

        parts = ["0"] * 88
        parts[0] = "1"
        parts[1] = "MOUTAI"
        parts[2] = "600519"
        parts[3] = "1495.11"
        parts[4] = "1474.92"
        parts[5] = "1485.00"
        parts[33] = "1499.98"
        parts[34] = "1474.00"
        parts[35] = "1495.11/22466/3344578582"
        parts[36] = "22466"
        parts[37] = "334458"
        parts[30] = "20260204094729"
        parts[82] = "CNY"
        line = f'v_sh600519="{"~".join(parts)}";'

        q = _parse_tencent_quote_line(line)
        assert q is not None
        self.assertEqual(q.symbol, "SH600519")
        self.assertEqual(q.currency, "CNY")
        self.assertEqual(q.ts, "2026-02-04 09:47:29")
        # amount should prefer idx35 exact value
        self.assertAlmostEqual(q.amount, 3344578582.0, places=3)

    def test_crypto_pair_split_rejects_invalid(self) -> None:
        from src.quote import _split_crypto_pair

        self.assertEqual(_split_crypto_pair(""), (None, None))
        self.assertEqual(_split_crypto_pair("BTCUSDT"), (None, None))
        self.assertEqual(_split_crypto_pair("bad$"), (None, None))

    def test_crypto_auto_rejects_invalid(self) -> None:
        from src.quote import fetch_crypto_spot_quote_auto

        self.assertIsNone(fetch_crypto_spot_quote_auto("bad$"))

    def test_parse_yahoo_row(self) -> None:
        from src.quote import _parse_yahoo_quote_row

        row = {
            "symbol": "XAUUSD=X",
            "shortName": "Gold",
            "currency": "USD",
            "regularMarketPrice": 2040.25,
            "regularMarketPreviousClose": 2031.0,
            "regularMarketOpen": 2034.0,
            "regularMarketDayHigh": 2052.0,
            "regularMarketDayLow": 2020.0,
            "regularMarketVolume": 0,
            "regularMarketTime": 1700000000,
        }
        q = _parse_yahoo_quote_row(row)
        self.assertIsNotNone(q)
        assert q is not None
        self.assertEqual(q.symbol, "XAUUSD=X")
        self.assertEqual(q.name, "Gold")
        self.assertAlmostEqual(q.price, 2040.25, places=6)
        self.assertEqual(q.currency, "USD")
        self.assertEqual(q.source, "yahoo")

    def test_parse_stooq_csv_line(self) -> None:
        from src.quote import _parse_stooq_csv_line

        line = "XAUUSD,2026-02-04,16:59:12,4946.66,5091.82,4888.07,4921.14,"
        q = _parse_stooq_csv_line(line)
        self.assertIsNotNone(q)
        assert q is not None
        self.assertEqual(q.symbol, "XAUUSD")
        self.assertEqual(q.currency, "USD")
        self.assertEqual(q.source, "stooq")
        self.assertEqual(q.ts, "2026-02-04 16:59:12")

    def test_parse_sina_hf_line(self) -> None:
        from src.quote import _parse_sina_hf_line

        line = 'var hq_str_hf_GC="4949.272,,4951.800,4952.900,5045.000,4911.000,10:28:40,4950.800,4986.600,0,2,1,2026-02-05,纽约黄金,0";'
        sym, parts = _parse_sina_hf_line(line)
        self.assertEqual(sym, "GC")
        assert parts is not None
        self.assertGreaterEqual(len(parts), 14)

    def test_fmt_quote_ts_compact_year(self) -> None:
        from src.tui import _fmt_quote_ts, _fmt_quote_ts_date8

        self.assertEqual(_fmt_quote_ts("2026-02-07 11:35:16"), "26-02-07 11:35:16")
        self.assertEqual(_fmt_quote_ts_date8("2026-02-07 11:35:16"), "26-02-07")

    def test_truncate_handles_wide_chars(self) -> None:
        from src.tui import _text_display_width, _truncate

        raw = "状态: 运行中 | run=abc123"
        out = _truncate(raw, 16)
        self.assertTrue(out.endswith(">"))
        self.assertTrue(out.startswith("状态"))
        self.assertLessEqual(_text_display_width(out), 16)

    def test_truncate_width_one_returns_ascii_marker(self) -> None:
        from src.tui import _truncate

        self.assertEqual(_truncate("状态", 1), ">")
        self.assertEqual(_truncate("abc", 1), ">")

    def test_fmt_quote_ts_fallback_keeps_short_year(self) -> None:
        from src.tui import _fmt_quote_ts

        # Fallback path: non-ISO input still strips YYYY -> YY when possible.
        self.assertEqual(_fmt_quote_ts("2026/02/07 11:35:16"), "26/02/07 11:35:16")
        self.assertEqual(_fmt_quote_ts(""), "--")

    def test_format_service_status_bar_all_up(self) -> None:
        from src.tui import ServiceStatus, _format_service_status_bar

        bar = _format_service_status_bar(
            ServiceStatus(data_running=4, data_total=4, signal_up=True, trading_up=True, checked_at=0.0)
        )
        self.assertEqual(bar, "svc data up | sig up | trd up")

    def test_format_service_status_bar_partial(self) -> None:
        from src.tui import ServiceStatus, _format_service_status_bar

        bar = _format_service_status_bar(
            ServiceStatus(data_running=2, data_total=4, signal_up=False, trading_up=True, checked_at=0.0)
        )
        self.assertEqual(bar, "svc data 2/4 | sig dn | trd up")

    def test_build_header_line_keeps_service_tail(self) -> None:
        from src.tui import _build_header_line

        svc = "svc data up | sig up | trd dn"
        line = _build_header_line("26-02-10 12:00:00", "market_micro", "refresh=1.0s", svc, 64)
        self.assertTrue(line.endswith(svc))

    def test_build_header_line_narrow_falls_back_to_service(self) -> None:
        from src.tui import _build_header_line

        svc = "svc data up | sig up | trd dn"
        line = _build_header_line("26-02-10 12:00:00", "market_micro", "refresh=1.0s", svc, 20)
        self.assertTrue(line.startswith("svc"))

    def test_hot_reload_watcher_detects_py_change(self) -> None:
        from src.tui import _HotReloadWatcher

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mod = root / "demo.py"
            mod.write_text("x = 1\n", encoding="utf-8")
            watcher = _HotReloadWatcher([root], poll_s=0.2)

            self.assertFalse(watcher.should_reload(now_ts=10.0))
            time.sleep(0.01)
            mod.write_text("x = 2\n", encoding="utf-8")
            self.assertTrue(watcher.should_reload(now_ts=10.3))
            self.assertFalse(watcher.should_reload(now_ts=10.4))

    def test_hot_reload_watcher_disabled(self) -> None:
        from src.tui import _build_hot_reload_watcher

        self.assertIsNone(_build_hot_reload_watcher(False, poll_s=1.0))

    def test_load_backtest_snapshot_empty(self) -> None:
        from src.tui import _load_backtest_snapshot

        with tempfile.TemporaryDirectory() as td:
            snap = _load_backtest_snapshot(Path(td))
            self.assertFalse(snap.available)
            self.assertIn("no backtest", snap.status)

    def test_load_backtest_snapshot_parses_latest(self) -> None:
        from src.tui import _load_backtest_snapshot

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "metrics.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-001",
                        "start": "2026-02-01 00:00:00",
                        "end": "2026-02-10 00:00:00",
                        "total_return": 0.123,
                        "max_drawdown": 0.045,
                        "sharpe": 1.80,
                        "win_rate": 0.58,
                        "trade_count": 12,
                        "avg_holding_minutes": 14.2,
                        "symbol_contributions": [
                            {
                                "symbol": "BTCUSDT",
                                "pnl_net": 23.4,
                                "trade_count": 8,
                                "win_rate_pct": 62.5,
                                "avg_holding_minutes": 11.3,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (base / "equity_curve.csv").write_text(
                "ts,equity\n2026-02-01 00:00:00,10000\n2026-02-02 00:00:00,10100\n",
                encoding="utf-8",
            )
            (base / "trades.csv").write_text(
                "timestamp,symbol,side,pnl\n2026-02-02 00:00:00,BTCUSDT,BUY,12.5\n",
                encoding="utf-8",
            )

            snap = _load_backtest_snapshot(base)
            self.assertTrue(snap.available)
            self.assertEqual(snap.run_id, "run-001")
            self.assertEqual(snap.trade_count, 12)
            self.assertEqual(len(snap.equity_points), 2)
            self.assertGreaterEqual(len(snap.recent_trades), 1)
            self.assertAlmostEqual(snap.total_return_pct or 0.0, 12.3, places=2)
            self.assertAlmostEqual(snap.max_drawdown_pct or 0.0, 4.5, places=2)
            self.assertAlmostEqual(snap.win_rate_pct or 0.0, 58.0, places=2)
            self.assertAlmostEqual(snap.avg_holding_minutes or 0.0, 14.2, places=2)
            self.assertGreaterEqual(len(snap.symbol_contributions), 1)
            self.assertEqual(snap.symbol_contributions[0].symbol, "BTCUSDT")
            self.assertAlmostEqual(snap.symbol_contributions[0].pnl_net or 0.0, 23.4, places=2)

    def test_load_backtest_compare_snapshot_with_compare_run_state(self) -> None:
        from src.tui import BacktestRunStateSnapshot, _load_backtest_compare_snapshot

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "artifacts" / "backtest"
            latest = root / "latest"
            latest.mkdir(parents=True)
            compare_dir = root / "cmp-001-compare"
            compare_dir.mkdir(parents=True)
            (compare_dir / "comparison.json").write_text(
                json.dumps(
                    {
                        "run_id": "cmp-001",
                        "history_run_id": "cmp-001-history",
                        "rule_run_id": "cmp-001-rules",
                        "delta_return_pct": 1.25,
                        "delta_trade_count": -3,
                        "delta_excess_return_pct": 2.2,
                        "delta_signal_count": -15,
                        "delta_buy_ratio_pct": -4.5,
                        "rule_overlap": {
                            "history_rule_types": 12,
                            "rule_rule_types": 9,
                            "shared_rule_types": 6,
                            "jaccard_pct": 40.0,
                        },
                        "signal_type_delta_top": [
                            {
                                "key": "KDJ金叉",
                                "history_count": 30,
                                "rule_count": 12,
                                "delta": -18,
                            }
                        ],
                        "missing_history_rules_diagnostics": [
                            {
                                "key": "MACD死叉",
                                "primary_block_reason": "condition_failed",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            state = BacktestRunStateSnapshot(
                status="done",
                stage="done",
                run_id="cmp-001",
                mode="compare_history_rule",
            )
            snap = _load_backtest_compare_snapshot(latest, run_state=state, current_run_id="cmp-001-rules")

            self.assertTrue(snap.available)
            self.assertEqual(snap.run_id, "cmp-001")
            self.assertAlmostEqual(snap.delta_return_pct or 0.0, 1.25, places=6)
            self.assertEqual(snap.delta_trade_count, -3)
            self.assertEqual(snap.delta_signal_count, -15)
            self.assertEqual(snap.rule_history_types, 12)
            self.assertEqual(snap.rule_rule_types, 9)
            self.assertEqual(snap.rule_shared_types, 6)
            self.assertAlmostEqual(snap.rule_jaccard_pct or 0.0, 40.0, places=6)
            self.assertEqual(snap.missing_rule_reason, "MACD死叉: condition_failed")
            self.assertEqual(len(snap.signal_type_delta_top), 1)
            self.assertEqual(snap.signal_type_delta_top[0].key, "KDJ金叉")
            self.assertEqual(snap.signal_type_delta_top[0].delta, -18)

    def test_load_backtest_compare_snapshot_from_current_run_id(self) -> None:
        from src.tui import _load_backtest_compare_snapshot

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "artifacts" / "backtest"
            latest = root / "latest"
            latest.mkdir(parents=True)
            compare_dir = root / "demo-cmp-compare"
            compare_dir.mkdir(parents=True)
            (compare_dir / "comparison.json").write_text(
                json.dumps(
                    {
                        "run_id": "demo-cmp",
                        "delta_return_pct": -0.5,
                        "delta_trade_count": 2,
                        "signal_type_delta_top": [],
                    }
                ),
                encoding="utf-8",
            )

            snap = _load_backtest_compare_snapshot(latest, current_run_id="demo-cmp-rules")
            self.assertTrue(snap.available)
            self.assertEqual(snap.run_id, "demo-cmp")
            self.assertAlmostEqual(snap.delta_return_pct or 0.0, -0.5, places=6)
            self.assertEqual(snap.delta_trade_count, 2)
            self.assertIsNone(snap.rule_jaccard_pct)
            self.assertEqual(snap.missing_rule_reason, "")

    def test_load_backtest_run_state_defaults(self) -> None:
        from src.tui import _load_backtest_run_state

        with tempfile.TemporaryDirectory() as td:
            snap = _load_backtest_run_state(Path(td) / "run_state.json")
            self.assertEqual(snap.status, "idle")
            self.assertEqual(snap.stage, "idle")
            self.assertEqual(snap.run_id, "--")

    def test_load_backtest_run_state_parses_payload(self) -> None:
        from src.tui import _format_backtest_state_line, _load_backtest_run_state

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "run_state.json"
            path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "stage": "executing",
                        "run_id": "run-xyz",
                        "mode": "history_signal",
                        "updated_at": "2026-02-13 05:00:00+00:00",
                        "message": "executing with bars=100",
                    }
                ),
                encoding="utf-8",
            )

            snap = _load_backtest_run_state(path)
            self.assertEqual(snap.status, "running")
            self.assertEqual(snap.stage, "executing")
            self.assertEqual(snap.run_id, "run-xyz")
            line = _format_backtest_state_line(snap, width=120)
            self.assertIn("状态: 运行中", line)
            self.assertIn("阶段: 回测执行", line)

    def test_load_backtest_run_state_bad_json(self) -> None:
        from src.tui import _load_backtest_run_state

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "run_state.json"
            path.write_text("{bad-json", encoding="utf-8")
            snap = _load_backtest_run_state(path)
            self.assertEqual(snap.status, "unknown")
            self.assertIn("parse failed", snap.message)

    def test_format_symbol_contrib_lines(self) -> None:
        from src.tui import BacktestSymbolContribution, _format_symbol_contrib_lines

        rows = [
            BacktestSymbolContribution(
                symbol="BTCUSDT",
                pnl_net=120.0,
                trade_count=6,
                win_rate_pct=50.0,
                avg_holding_minutes=2.3,
            ),
            BacktestSymbolContribution(
                symbol="ETHUSDT",
                pnl_net=-60.0,
                trade_count=4,
                win_rate_pct=25.0,
                avg_holding_minutes=1.1,
            ),
        ]
        lines = _format_symbol_contrib_lines(rows, width=80)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0][1], 1)
        self.assertEqual(lines[1][1], -1)
        self.assertIn("BTCUSDT", lines[0][0])
        self.assertIn("ETHUSDT", lines[1][0])

    def test_format_backtest_curve_summary(self) -> None:
        from src.tui import _format_backtest_curve_summary

        line = _format_backtest_curve_summary([10000.0, 10050.0, 9980.0, 10020.0])
        self.assertIn("净值: 10020.00", line)
        self.assertIn("最高: 10050.00", line)
        self.assertIn("变化:", line)

    def test_format_backtest_trade_line_compact(self) -> None:
        from src.tui import _format_backtest_trade_line

        raw = "2026-02-13 04:39:00 BTCUSDT BUY pnl=-12.34"
        out = _format_backtest_trade_line(raw, width=48)
        self.assertIn("02-13", out)
        self.assertIn("BTCUSDT", out)
        self.assertIn("pnl -12.34", out)


    def test_compute_drawdown_series(self) -> None:
        from src.tui import _compute_drawdown_series

        out = _compute_drawdown_series([100.0, 120.0, 110.0, 90.0, 95.0])
        self.assertEqual(len(out), 5)
        self.assertAlmostEqual(out[0], 0.0, places=6)
        self.assertAlmostEqual(out[1], 0.0, places=6)
        self.assertLess(out[3], -24.0)

    def test_format_backtest_drawdown_summary(self) -> None:
        from src.tui import _format_backtest_drawdown_summary

        line = _format_backtest_drawdown_summary([100.0, 120.0, 110.0, 90.0, 95.0])
        self.assertIn("回撤: 当前", line)
        self.assertIn("最大", line)



    def test_build_signal_radar_rows_crypto(self) -> None:
        from src.db import SignalRow
        from src.tui import _build_signal_radar_rows

        now = datetime(2026, 2, 10, 12, 0, 0)
        rows = [
            SignalRow(1, "2026-02-10 11:59:30", "BTCUSDT", "macd", "BUY", 70, None, "1m", 70000.0, "sqlite"),
            SignalRow(2, "2026-02-10 11:40:00", "BTCUSDT", "macd", "BUY", 80, None, "1m", 69900.0, "sqlite"),
            SignalRow(3, "2026-02-10 11:59:40", "ETHUSDT", "kdj", "SELL", 75, None, "1m", 2200.0, "sqlite"),
            SignalRow(4, "2026-02-10 11:59:50", "BTCUSDT", "rsi", "ALERT", 50, None, "1m", 70010.0, "sqlite"),
        ]
        out = _build_signal_radar_rows(rows, "BTC_USDT", "crypto_spot", now, window_minutes=15, min_strength=65)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0].symbol, "BTCUSDT")
        self.assertEqual(out[0][1], 30)

    def test_build_signal_radar_rows_us(self) -> None:
        from src.db import SignalRow
        from src.tui import _build_signal_radar_rows

        now = datetime(2026, 2, 10, 12, 0, 0)
        rows = [
            SignalRow(1, "2026-02-10 11:59:50", "NVDA", "price_surge", "BUY", 80, None, "1m", 180.0, "pg"),
            SignalRow(2, "2026-02-10 11:59:20", "META", "price_dump", "SELL", 85, None, "1m", 500.0, "pg"),
        ]
        out = _build_signal_radar_rows(rows, "NVDA", "us_stock", now, window_minutes=15, min_strength=65)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0].symbol, "NVDA")
        self.assertEqual(out[0][1], 10)

    def test_curve_update_ts_uses_quote_ts_for_stale_market(self) -> None:
        from src.quote import Quote
        from src.tui import _curve_update_ts

        quote = Quote(
            symbol="NVDA",
            name="NVDA",
            price=180.0,
            prev_close=181.0,
            open=182.0,
            high=183.0,
            low=179.0,
            currency="USD",
            volume=1.0,
            amount=1.0,
            ts="2026-02-10 16:00:00",
            source="tencent",
        )
        stale_now = datetime(2026, 2, 11, 10, 0, 0).timestamp()
        out = _curve_update_ts(quote, fetched_at=stale_now, now_ts=stale_now)
        self.assertEqual(out, datetime(2026, 2, 10, 16, 0, 0).timestamp())

    def test_curve_update_ts_keeps_fetch_time_for_live_market(self) -> None:
        from src.quote import Quote
        from src.tui import _curve_update_ts

        now_ts = datetime(2026, 2, 10, 16, 0, 10).timestamp()
        quote = Quote(
            symbol="NVDA",
            name="NVDA",
            price=180.0,
            prev_close=181.0,
            open=182.0,
            high=183.0,
            low=179.0,
            currency="USD",
            volume=1.0,
            amount=1.0,
            ts="2026-02-10 16:00:00",
            source="tencent",
        )
        out = _curve_update_ts(quote, fetched_at=now_ts, now_ts=now_ts)
        self.assertEqual(out, now_ts)

    def test_parse_tencent_minute_payload_cn(self) -> None:
        from src.quote import _parse_tencent_minute_payload

        payload = json.dumps(
            {
                "data": {
                    "sh600519": {
                        "data": {
                            "date": "20260211",
                            "data": [
                                "1458 1500.00 100 1000.00",
                                "1459 1501.00 150 1500.00",
                                "1500 1502.00 210 2100.00",
                            ],
                        },
                        "qt": {"sh600519": ["0"] * 31},
                    }
                }
            }
        )
        out = _parse_tencent_minute_payload(payload, code="sh600519", market="cn_stock", limit=60)
        self.assertEqual(len(out), 3)
        self.assertLess(out[0][0], out[-1][0])
        self.assertAlmostEqual(out[-1][1], 1502.0, places=6)

    def test_parse_nasdaq_intraday_payload(self) -> None:
        from src.quote import _parse_nasdaq_intraday_payload

        payload = json.dumps(
            {
                "marketData": [
                    {"Date": "2026-02-10 15:58:00", "Value": 188.10, "Volume": 1000},
                    {"Date": "2026-02-10 15:59:00", "Value": 188.30, "Volume": 1400},
                    {"Date": "2026-02-10 16:00:00", "Value": 188.54, "Volume": 2000},
                ]
            }
        )
        out = _parse_nasdaq_intraday_payload(payload, symbol="NVDA", limit=60)
        self.assertEqual(len(out), 3)
        self.assertLess(out[0][0], out[-1][0])
        self.assertAlmostEqual(out[-1][1], 188.54, places=6)

    def test_fetch_intraday_curve_1m_prefers_nasdaq_for_us(self) -> None:
        from src import quote as quote_mod

        orig_nasdaq = quote_mod.fetch_nasdaq_us_minute_series
        orig_tencent = quote_mod.fetch_tencent_equity_minute_series
        try:
            quote_mod.fetch_nasdaq_us_minute_series = lambda *args, **kwargs: [(1, 101.0, 10.0)]
            quote_mod.fetch_tencent_equity_minute_series = lambda *args, **kwargs: [(2, 102.0, 20.0)]
            out = quote_mod.fetch_intraday_curve_1m("tencent", "us_stock", "NVDA", limit=60)
            self.assertEqual(out, [(1, 101.0, 10.0)])
        finally:
            quote_mod.fetch_nasdaq_us_minute_series = orig_nasdaq
            quote_mod.fetch_tencent_equity_minute_series = orig_tencent


    def test_request_with_policy_retries_retryable_error(self) -> None:
        import urllib.error

        from src.quote import _RequestPolicy, _request_with_policy

        calls = {"n": 0}

        def _fetch() -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.URLError("temporary")
            return "ok"

        out = _request_with_policy(
            _fetch,
            url="https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BTC_USDT",
            timeout_s=0.5,
            policy=_RequestPolicy(key="unit-retry", rate_per_s=1000.0, burst=1000, attempts=2, backoff_base_s=0.01),
        )
        self.assertEqual(out, "ok")
        self.assertEqual(calls["n"], 2)

    def test_request_with_policy_skips_retry_on_non_retryable_error(self) -> None:
        from src.quote import _RequestPolicy, _request_with_policy

        calls = {"n": 0}

        def _fetch() -> str:
            calls["n"] += 1
            raise ValueError("bad payload")

        with self.assertRaises(ValueError):
            _request_with_policy(
                _fetch,
                url="https://example.com/data",
                timeout_s=0.5,
                policy=_RequestPolicy(key="unit-no-retry", rate_per_s=1000.0, burst=1000, attempts=3, backoff_base_s=0.01),
            )
        self.assertEqual(calls["n"], 1)

    def test_load_backtest_snapshot_walk_forward_summary(self) -> None:
        from src.tui import _load_backtest_snapshot

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            payload = {
                "run_id": "wf-unit",
                "fold_count": 3,
                "avg_return_pct": 1.25,
                "avg_max_drawdown_pct": 2.5,
                "avg_excess_return_pct": 8.0,
                "positive_fold_rate_pct": 66.67,
                "history_fold_count": 2,
                "replay_fold_count": 1,
                "fallback_fold_count": 1,
                "folds": [
                    {
                        "fold": 1,
                        "mode": "history_signal",
                        "test_start": "2026-01-01 00:00:00+00:00",
                        "test_end": "2026-01-04 00:00:00+00:00",
                        "total_return_pct": 0.5,
                        "max_drawdown_pct": 0.3,
                        "trade_count": 5,
                    },
                    {
                        "fold": 2,
                        "mode": "offline_replay",
                        "test_start": "2026-01-04 00:00:00+00:00",
                        "test_end": "2026-01-07 00:00:00+00:00",
                        "total_return_pct": -0.2,
                        "max_drawdown_pct": 0.6,
                        "trade_count": 4,
                    },
                    {
                        "fold": 3,
                        "mode": "history_signal",
                        "test_start": "2026-01-07 00:00:00+00:00",
                        "test_end": "2026-01-10 00:00:00+00:00",
                        "total_return_pct": 0.9,
                        "max_drawdown_pct": 0.4,
                        "trade_count": 6,
                    },
                ],
            }
            (base / "walk_forward_summary.json").write_text(json.dumps(payload), encoding="utf-8")

            snap = _load_backtest_snapshot(base)
            self.assertTrue(snap.available)
            self.assertTrue(snap.is_walk_forward)
            self.assertEqual(snap.wf_fold_count, 3)
            self.assertAlmostEqual(snap.total_return_pct or 0.0, 1.25, places=6)
            self.assertAlmostEqual(snap.max_drawdown_pct or 0.0, 2.5, places=6)
            self.assertAlmostEqual(snap.excess_return_pct or 0.0, 8.0, places=6)
            self.assertIn("2026-01-01", snap.date_range)
            self.assertIn("2026-01-10", snap.date_range)
            self.assertGreaterEqual(len(snap.equity_points), 2)
            self.assertGreaterEqual(len(snap.recent_trades), 1)


if __name__ == "__main__":
    unittest.main()
