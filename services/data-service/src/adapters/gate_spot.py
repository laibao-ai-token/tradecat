"""Gate spot REST adapter (public, no API key).

Used as a fallback candle source when Binance endpoints are not reachable.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)


GATE_BASE_URL = "https://api.gateio.ws/api/v4"


@dataclass(frozen=True)
class GateSpotCandle:
    """1m candle data from Gate spot candlesticks API."""

    ts: int  # epoch seconds
    quote_volume: float
    close: float
    high: float
    low: float
    open: float
    volume: float
    is_closed: bool

    @property
    def bucket_ts(self) -> datetime:
        return datetime.fromtimestamp(self.ts, tz=timezone.utc)


def _http_get_json(url: str, timeout_s: float = 10.0) -> object:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TradeCat/data-service", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8"))


def fetch_spot_candles(
    currency_pair: str,
    interval: str = "1m",
    limit: int = 2,
    timeout_s: float = 10.0,
) -> List[GateSpotCandle]:
    """
    Fetch spot candlesticks from Gate.

    API:
      GET /spot/candlesticks?currency_pair=BTC_USDT&interval=1m&limit=2

    Response (list of lists):
      [
        [ts, quote_volume, close, high, low, open, volume, is_closed],
        ...
      ]
    """
    pair = (currency_pair or "").strip().upper()
    if not pair or "_" not in pair:
        return []

    qs = urllib.parse.urlencode({"currency_pair": pair, "interval": interval, "limit": str(int(limit))})
    url = f"{GATE_BASE_URL}/spot/candlesticks?{qs}"

    try:
        raw = _http_get_json(url, timeout_s=timeout_s)
        if not isinstance(raw, list):
            return []
        out: List[GateSpotCandle] = []
        for item in raw:
            if not isinstance(item, list) or len(item) < 8:
                continue
            try:
                out.append(
                    GateSpotCandle(
                        ts=int(item[0]),
                        quote_volume=float(item[1]),
                        close=float(item[2]),
                        high=float(item[3]),
                        low=float(item[4]),
                        open=float(item[5]),
                        volume=float(item[6]),
                        is_closed=str(item[7]).lower() == "true",
                    )
                )
            except Exception:
                continue
        return out
    except Exception as e:
        logger.debug("gate spot fetch failed %s %s: %s", pair, interval, e)
        return []


def to_candle_row(
    *,
    exchange: str,
    symbol: str,
    candle: GateSpotCandle,
    source: str = "gate_spot",
) -> dict:
    """Convert a Gate candle to a Timescale candles_1m row dict."""
    return {
        "exchange": exchange,
        "symbol": symbol.upper(),
        "bucket_ts": candle.bucket_ts,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
        "quote_volume": candle.quote_volume,
        "trade_count": None,
        "taker_buy_volume": None,
        "taker_buy_quote_volume": None,
        "is_closed": True,
        "source": source,
    }

