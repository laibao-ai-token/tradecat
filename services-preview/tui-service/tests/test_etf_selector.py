import time
import unittest
from types import SimpleNamespace


class TestETFSelector(unittest.TestCase):
    def _quote(self, symbol: str, price: float, volume: float, amount: float):
        from src.quote import Quote

        return Quote(
            symbol=symbol,
            name=symbol,
            price=price,
            prev_close=max(0.01, price * 0.99),
            open=price,
            high=price,
            low=price,
            currency="CNY",
            volume=volume,
            amount=amount,
            ts="2026-02-19 12:00:00",
            source="tencent",
        )

    def _curve(self, start: float, step: float, size: int = 20):
        from src.micro import Candle

        out = []
        now = int(time.time())
        for idx in range(size):
            close = start + step * idx
            out.append(
                Candle(
                    ts_open=now + idx * 5,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume_est=1000.0 + idx,
                    notional_est=(1000.0 + idx) * close,
                )
            )
        return out

    def test_select_etf_candidates_returns_ranked_items(self) -> None:
        from src.etf_profiles import get_etf_domain_profile
        from src.etf_selector import select_etf_candidates

        profile = get_etf_domain_profile("auto_driving_cn")
        now_ts = time.time()
        symbols = list(profile.symbols[:6])

        quote_entries = {
            symbols[0]: SimpleNamespace(quote=self._quote(symbols[0], 1.20, 8_000_000, 60_000_000), last_fetch_at=now_ts),
            symbols[1]: SimpleNamespace(quote=self._quote(symbols[1], 2.10, 6_000_000, 45_000_000), last_fetch_at=now_ts),
            symbols[2]: SimpleNamespace(quote=self._quote(symbols[2], 0.88, 5_000_000, 30_000_000), last_fetch_at=now_ts),
            symbols[3]: SimpleNamespace(quote=self._quote(symbols[3], 1.55, 4_000_000, 26_000_000), last_fetch_at=now_ts),
            symbols[4]: SimpleNamespace(quote=self._quote(symbols[4], 3.40, 9_000_000, 90_000_000), last_fetch_at=now_ts),
            symbols[5]: SimpleNamespace(quote=self._quote(symbols[5], 1.05, 2_000_000, 12_000_000), last_fetch_at=now_ts),
        }
        curve_map = {
            symbols[0]: self._curve(1.00, 0.02),
            symbols[1]: self._curve(2.00, 0.01),
            symbols[2]: self._curve(1.20, -0.01),
            symbols[3]: self._curve(1.60, -0.002),
            symbols[4]: self._curve(3.00, 0.03),
            symbols[5]: self._curve(1.05, 0.0),
        }

        snapshot = select_etf_candidates(
            profile=profile,
            symbols=symbols,
            quote_entries=quote_entries,
            curve_map=curve_map,
            micro_snapshots={},
            now_ts=now_ts,
            stale_seconds=120,
        )

        self.assertEqual(snapshot.strategy_label, "ETF-AUTO-V1")
        self.assertGreaterEqual(snapshot.valid_candidates, 1)
        self.assertLessEqual(len(snapshot.items), profile.top_n)
        if snapshot.items:
            self.assertGreaterEqual(len(snapshot.items[0].reason_tags), 2)
        totals = [item.total_score for item in snapshot.items]
        self.assertEqual(totals, sorted(totals, reverse=True))

    def test_select_etf_candidates_skips_stale_entries(self) -> None:
        from src.etf_profiles import get_etf_domain_profile
        from src.etf_selector import select_etf_candidates

        profile = get_etf_domain_profile("auto_driving_cn")
        now_ts = time.time()
        sym = profile.symbols[0]

        quote_entries = {
            sym: SimpleNamespace(quote=self._quote(sym, 1.0, 1_000_000, 8_000_000), last_fetch_at=now_ts - 600),
        }
        curve_map = {sym: self._curve(1.0, 0.01)}

        snapshot = select_etf_candidates(
            profile=profile,
            symbols=[sym],
            quote_entries=quote_entries,
            curve_map=curve_map,
            micro_snapshots={},
            now_ts=now_ts,
            stale_seconds=120,
        )

        self.assertEqual(snapshot.valid_candidates, 0)
        self.assertEqual(snapshot.skipped_stale, 1)
        self.assertEqual(len(snapshot.items), 0)


if __name__ == "__main__":
    unittest.main()

