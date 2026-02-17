import unittest


class TestMicroEngine(unittest.TestCase):
    def _quote(
        self,
        *,
        price: float,
        ts: str,
        volume: float,
        amount: float,
        symbol: str = "BTC_USDT",
        source: str = "htx",
    ):
        from src.quote import Quote

        return Quote(
            symbol=symbol,
            name=symbol,
            price=price,
            prev_close=price,
            open=price,
            high=price,
            low=price,
            currency="USDT",
            volume=volume,
            amount=amount,
            ts=ts,
            source=source,
        )

    def test_candle_aggregation_across_buckets(self) -> None:
        from src.micro import MicroConfig, MicroEngine

        engine = MicroEngine(MicroConfig(symbol="BTC_USDT", interval_s=5, window=20, flow_rows=20, refresh_s=0.5))

        engine.ingest_quote(self._quote(price=100.0, ts="2026-02-08 10:00:01", volume=10, amount=1000))
        engine.ingest_quote(self._quote(price=102.0, ts="2026-02-08 10:00:03", volume=11, amount=1102))
        engine.ingest_quote(self._quote(price=101.0, ts="2026-02-08 10:00:04", volume=12, amount=1203))
        engine.ingest_quote(self._quote(price=103.0, ts="2026-02-08 10:00:06", volume=13, amount=1339))

        snap = engine.snapshot()
        self.assertEqual(len(snap.candles), 2)

        first = snap.candles[0]
        second = snap.candles[1]
        self.assertAlmostEqual(first.open, 100.0, places=6)
        self.assertAlmostEqual(first.high, 102.0, places=6)
        self.assertAlmostEqual(first.low, 100.0, places=6)
        self.assertAlmostEqual(first.close, 101.0, places=6)
        self.assertAlmostEqual(second.open, 103.0, places=6)
        self.assertAlmostEqual(second.close, 103.0, places=6)

    def test_flow_side_classification(self) -> None:
        from src.micro import MicroConfig, MicroEngine

        engine = MicroEngine(MicroConfig(symbol="BTC_USDT", interval_s=5, window=20, flow_rows=20, refresh_s=0.5))
        engine.ingest_quote(self._quote(price=100.0, ts="2026-02-08 10:00:01", volume=10, amount=1000))
        engine.ingest_quote(self._quote(price=101.0, ts="2026-02-08 10:00:02", volume=11, amount=1111))
        engine.ingest_quote(self._quote(price=99.0, ts="2026-02-08 10:00:03", volume=12, amount=1188))
        engine.ingest_quote(self._quote(price=99.0, ts="2026-02-08 10:00:04", volume=13, amount=1287))

        flow = engine.snapshot().flow
        self.assertGreaterEqual(len(flow), 3)
        self.assertEqual(flow[-3].side, "BUY")
        self.assertEqual(flow[-2].side, "SELL")
        self.assertEqual(flow[-1].side, "NEUTRAL")

    def test_signal_bias_uptrend(self) -> None:
        from src.micro import MicroConfig, MicroEngine

        engine = MicroEngine(MicroConfig(symbol="BTC_USDT", interval_s=5, window=80, flow_rows=80, refresh_s=0.5))
        base = 100.0
        for idx in range(40):
            price = base + idx * 0.8
            volume = 100.0 + idx
            amount = volume * price
            sec = idx % 60
            minute = idx // 60
            ts = f"2026-02-08 10:{minute:02d}:{sec:02d}"
            engine.ingest_quote(self._quote(price=price, ts=ts, volume=volume, amount=amount))

        signals = engine.snapshot().signals
        self.assertGreater(signals.ema_crossover, 0.0)
        self.assertGreater(signals.rsi, 0.0)
        self.assertIn(signals.bias, {"BUY", "NEUTRAL"})

    def test_signal_bias_downtrend(self) -> None:
        from src.micro import MicroConfig, MicroEngine

        engine = MicroEngine(MicroConfig(symbol="BTC_USDT", interval_s=5, window=80, flow_rows=80, refresh_s=0.5))
        base = 200.0
        for idx in range(40):
            price = base - idx * 1.1
            volume = 100.0 + idx
            amount = volume * max(1.0, price)
            sec = idx % 60
            minute = idx // 60
            ts = f"2026-02-08 11:{minute:02d}:{sec:02d}"
            engine.ingest_quote(self._quote(price=price, ts=ts, volume=volume, amount=amount))

        signals = engine.snapshot().signals
        self.assertLess(signals.ema_crossover, 0.0)
        self.assertLess(signals.rsi, 0.0)
        self.assertIn(signals.bias, {"SELL", "NEUTRAL"})


if __name__ == "__main__":
    unittest.main()

