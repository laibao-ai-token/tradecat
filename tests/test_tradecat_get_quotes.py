from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "tradecat_get_quotes.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("tradecat_get_quotes_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeWatchlists:
    @staticmethod
    def normalize_us_symbols(raw: str) -> list[str]:
        token = raw.strip().upper()
        return [token] if token in {"NVDA", "META"} else []

    @staticmethod
    def normalize_hk_symbols(raw: str) -> list[str]:
        token = raw.strip()
        return [token.zfill(5)] if token.isdigit() and len(token) <= 5 else []

    @staticmethod
    def normalize_cn_symbols(raw: str) -> list[str]:
        token = raw.strip().upper()
        return [token] if token in {"SH600519"} else []

    @staticmethod
    def normalize_cn_fund_symbols(raw: str) -> list[str]:
        token = raw.strip().upper()
        return [token] if token in {"SH510300", "024389"} else []

    @staticmethod
    def normalize_crypto_symbols(raw: str) -> list[str]:
        token = raw.strip().upper().replace("-", "_")
        return [token] if token in {"BTC_USDT", "ETH_USDT"} else []

    @staticmethod
    def normalize_metals_symbols(raw: str) -> list[str]:
        token = raw.strip().upper()
        return [token] if token in {"XAUUSD", "XAGUSD"} else []


class TradecatGetQuotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module()

    def _runtime(self, *, batch_result=None, batch_exc=None, single_results=None):
        single_results = single_results or {}

        def fetch_quotes(provider, market, symbols, timeout_s=0.0):
            self.assertGreater(timeout_s, 0.0)
            if batch_exc is not None:
                raise batch_exc
            if callable(batch_result):
                return batch_result(provider, market, symbols, timeout_s)
            return batch_result or {}

        def fetch_quote(provider, market, symbol, timeout_s=0.0):
            self.assertGreater(timeout_s, 0.0)
            result = single_results.get(symbol)
            if isinstance(result, Exception):
                raise result
            return result

        quote_module = SimpleNamespace(fetch_quotes=fetch_quotes, fetch_quote=fetch_quote)
        return self.module.QuoteRuntime(quote_module=quote_module, watchlists_module=_FakeWatchlists)

    def test_single_symbol_success(self) -> None:
        quote = SimpleNamespace(
            symbol="NVDA",
            name="Nvidia",
            price=180.32,
            ts="2026-03-10 10:06:41",
            source="tencent",
            currency="USD",
            prev_close=179.0,
            open=180.0,
            high=181.0,
            low=178.5,
            volume=100.0,
            amount=18032.0,
        )
        runtime = self._runtime(batch_result={"NVDA": quote})

        exit_code, payload = self.module.execute(["--market", "us_stock", "NVDA"], runtime=runtime)

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["request"]["market"], "us_stock")
        self.assertEqual(payload["request"]["provider"], "tencent")
        self.assertEqual(payload["summary"]["succeeded"], 1)
        self.assertEqual(payload["data"][0]["symbol"], "NVDA")
        self.assertEqual(payload["data"][0]["provider"], "tencent")
        self.assertIsNone(payload["error"])

    def test_market_is_inferred_for_multiple_us_symbols(self) -> None:
        nvda = SimpleNamespace(
            symbol="NVDA",
            name="Nvidia",
            price=180.32,
            ts="2026-03-10 10:06:41",
            source="tencent",
            currency="USD",
            prev_close=179.0,
            open=180.0,
            high=181.0,
            low=178.5,
            volume=100.0,
            amount=18032.0,
        )
        meta = SimpleNamespace(
            symbol="META",
            name="Meta",
            price=520.1,
            ts="2026-03-10 10:06:42",
            source="tencent",
            currency="USD",
            prev_close=515.0,
            open=516.0,
            high=521.0,
            low=514.5,
            volume=200.0,
            amount=104020.0,
        )
        runtime = self._runtime(batch_result={"NVDA": nvda, "META": meta})

        exit_code, payload = self.module.execute(["NVDA", "META"], runtime=runtime)

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["request"]["market"], "us_stock")
        self.assertEqual(payload["request"]["normalized_symbols"], ["NVDA", "META"])
        self.assertEqual([item["symbol"] for item in payload["data"]], ["NVDA", "META"])

    def test_ambiguous_market_returns_structured_error(self) -> None:
        runtime = self._runtime(batch_result={})

        with self.assertRaises(self.module.CliError) as ctx:
            self.module.execute(["024389"], runtime=runtime)

        self.assertEqual(ctx.exception.code, "ambiguous_market")
        self.assertIn("pass --market explicitly", str(ctx.exception))

    def test_missing_quote_returns_structured_symbol_error(self) -> None:
        quote = SimpleNamespace(
            symbol="NVDA",
            name="Nvidia",
            price=180.32,
            ts="2026-03-10 10:06:41",
            source="tencent",
            currency="USD",
            prev_close=179.0,
            open=180.0,
            high=181.0,
            low=178.5,
            volume=100.0,
            amount=18032.0,
        )
        runtime = self._runtime(batch_result={"NVDA": quote, "META": None})

        exit_code, payload = self.module.execute(["--market", "us_stock", "NVDA", "META"], runtime=runtime)

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["failed"], 1)
        self.assertTrue(payload["data"][0]["ok"])
        self.assertFalse(payload["data"][1]["ok"])
        self.assertEqual(payload["data"][1]["error"]["code"], "quote_not_found")

    def test_batch_failure_falls_back_to_single_fetch(self) -> None:
        quote = SimpleNamespace(
            symbol="NVDA",
            name="Nvidia",
            price=180.32,
            ts="2026-03-10 10:06:41",
            source="tencent",
            currency="USD",
            prev_close=179.0,
            open=180.0,
            high=181.0,
            low=178.5,
            volume=100.0,
            amount=18032.0,
        )
        runtime = self._runtime(
            batch_exc=RuntimeError("batch down"),
            single_results={"NVDA": quote, "META": RuntimeError("single down")},
        )

        exit_code, payload = self.module.execute(["--market", "us_stock", "NVDA", "META"], runtime=runtime)

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["source"]["batch_fallback"])
        self.assertTrue(payload["data"][0]["ok"])
        self.assertEqual(payload["data"][0]["symbol"], "NVDA")
        self.assertFalse(payload["data"][1]["ok"])
        self.assertEqual(payload["data"][1]["error"]["code"], "quote_fetch_failed")


if __name__ == "__main__":
    unittest.main()
