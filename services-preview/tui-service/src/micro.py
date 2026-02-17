from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .db import parse_ts
from .quote import Quote


def _clamp(value: float, lower: float, upper: float) -> float:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def _safe_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / float(len(values))


def _ema_last(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    if period <= 1:
        return values[-1]

    alpha = 2.0 / (float(period) + 1.0)
    ema = values[0]
    for current in values[1:]:
        ema = alpha * current + (1.0 - alpha) * ema
    return ema


def _rsi_last(values: list[float], period: int = 14) -> float:
    if len(values) < 2:
        return 50.0

    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
    tail = deltas[-period:] if len(deltas) > period else deltas
    gains = [change for change in tail if change > 0]
    losses = [-change for change in tail if change < 0]
    avg_gain = _safe_mean(gains)
    avg_loss = _safe_mean(losses)
    if avg_loss <= 1e-12:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _roc_last(values: list[float], period: int = 6) -> float:
    if len(values) <= period:
        return 0.0
    base = values[-1 - period]
    if abs(base) <= 1e-12:
        return 0.0
    return (values[-1] - base) / base


@dataclass(frozen=True)
class MicroConfig:
    symbol: str = "BTC_USDT"
    interval_s: int = 5
    window: int = 60
    flow_rows: int = 30
    refresh_s: float = 0.5


@dataclass(frozen=True)
class Candle:
    ts_open: int
    open: float
    high: float
    low: float
    close: float
    volume_est: float
    notional_est: float


@dataclass(frozen=True)
class FlowEvent:
    ts: float
    source: str
    side: str
    price: float
    qty_est: float
    notional_est: float


@dataclass(frozen=True)
class MicroSignals:
    ema_crossover: float = 0.0
    rsi: float = 0.0
    roc: float = 0.0
    vwap: float = 0.0
    whale: float = 0.0
    score: float = 0.0
    bias: str = "NEUTRAL"


@dataclass(frozen=True)
class MicroSnapshot:
    symbol: str
    interval_s: int
    last_price: float
    last_source: str
    last_quote_ts: str
    error: str
    candles: list[Candle]
    flow: list[FlowEvent]
    signals: MicroSignals


class MicroEngine:
    def __init__(self, cfg: MicroConfig) -> None:
        self._cfg = MicroConfig(
            symbol=(cfg.symbol or "BTC_USDT").strip().upper() or "BTC_USDT",
            interval_s=max(5, int(cfg.interval_s)),
            window=max(10, int(cfg.window)),
            flow_rows=max(10, int(cfg.flow_rows)),
            refresh_s=max(0.2, float(cfg.refresh_s)),
        )
        self._candles: deque[Candle] = deque(maxlen=self._cfg.window)
        self._flow: deque[FlowEvent] = deque(maxlen=self._cfg.flow_rows)
        self._signals = MicroSignals()

        self._last_price: Optional[float] = None
        self._last_volume_raw: Optional[float] = None
        self._last_amount_raw: Optional[float] = None
        self._last_source: str = ""
        self._last_quote_ts: str = ""

    @property
    def config(self) -> MicroConfig:
        return self._cfg

    def ingest_quote(self, quote: Quote | None, *, fetched_at: float | None = None) -> None:
        if quote is None:
            return

        symbol = (quote.symbol or "").strip().upper()
        if symbol != self._cfg.symbol:
            return

        price = float(quote.price)
        if price <= 0:
            return

        now_ts = fetched_at if fetched_at is not None and fetched_at > 0 else self._parse_quote_ts(quote.ts)
        volume_raw = max(0.0, float(quote.volume))
        amount_raw = max(0.0, float(quote.amount))
        if amount_raw <= 0.0 and volume_raw > 0.0:
            amount_raw = volume_raw * price

        qty_delta = self._positive_delta(self._last_volume_raw, volume_raw)
        amount_delta = self._positive_delta(self._last_amount_raw, amount_raw)
        if amount_delta <= 0.0 and qty_delta > 0.0:
            amount_delta = qty_delta * price

        if qty_delta <= 0.0:
            qty_delta = max(0.0001, abs(price - (self._last_price or price)) * 0.1)
        if amount_delta <= 0.0:
            amount_delta = qty_delta * price

        side = "NEUTRAL"
        if self._last_price is not None:
            if price > self._last_price:
                side = "BUY"
            elif price < self._last_price:
                side = "SELL"

        self._append_flow(
            FlowEvent(
                ts=now_ts,
                source=(quote.source or "--").strip() or "--",
                side=side,
                price=price,
                qty_est=qty_delta,
                notional_est=amount_delta,
            )
        )
        self._append_candle(now_ts, price, qty_delta, amount_delta)
        self._recompute_signals()

        self._last_price = price
        self._last_volume_raw = volume_raw
        self._last_amount_raw = amount_raw
        self._last_source = (quote.source or "").strip()
        self._last_quote_ts = (quote.ts or "").strip()

    def snapshot(self, *, error: str = "") -> MicroSnapshot:
        return MicroSnapshot(
            symbol=self._cfg.symbol,
            interval_s=self._cfg.interval_s,
            last_price=float(self._last_price or 0.0),
            last_source=self._last_source,
            last_quote_ts=self._last_quote_ts,
            error=(error or "").strip(),
            candles=list(self._candles),
            flow=list(self._flow),
            signals=self._signals,
        )

    def _append_flow(self, flow_event: FlowEvent) -> None:
        self._flow.append(flow_event)

    def _append_candle(self, now_ts: float, price: float, qty_delta: float, amount_delta: float) -> None:
        bucket = int(now_ts // self._cfg.interval_s) * self._cfg.interval_s
        if not self._candles or self._candles[-1].ts_open != bucket:
            self._candles.append(
                Candle(
                    ts_open=bucket,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume_est=max(0.0, qty_delta),
                    notional_est=max(0.0, amount_delta),
                )
            )
            return

        previous = self._candles[-1]
        self._candles[-1] = Candle(
            ts_open=previous.ts_open,
            open=previous.open,
            high=max(previous.high, price),
            low=min(previous.low, price),
            close=price,
            volume_est=previous.volume_est + max(0.0, qty_delta),
            notional_est=previous.notional_est + max(0.0, amount_delta),
        )

    def _recompute_signals(self) -> None:
        closes = [candle.close for candle in self._candles]
        if len(closes) < 2:
            self._signals = MicroSignals()
            return

        ema_fast = _ema_last(closes, 9)
        ema_slow = _ema_last(closes, 21)
        deltas = [abs(closes[index] - closes[index - 1]) for index in range(1, len(closes))]
        volatility = max(_safe_mean(deltas[-21:]), max(1e-9, closes[-1] * 0.001))
        ema_score = _clamp((ema_fast - ema_slow) / volatility, -2.0, 2.0)

        rsi_raw = _rsi_last(closes, 14)
        rsi_score = _clamp((rsi_raw - 50.0) / 25.0, -2.0, 2.0)

        roc_raw = _roc_last(closes, 6)
        roc_score = _clamp(roc_raw * 50.0, -2.0, 2.0)

        total_volume = sum(max(0.0, candle.volume_est) for candle in self._candles)
        total_notional = sum(max(0.0, candle.notional_est) for candle in self._candles)
        vwap_value = closes[-1] if total_volume <= 1e-12 else (total_notional / total_volume)
        vwap_score = _clamp(((closes[-1] - vwap_value) / max(vwap_value, 1e-9)) * 100.0 / 1.5, -2.0, 2.0)

        whale_score = self._calc_whale_score()

        score = (
            0.30 * ema_score
            + 0.20 * rsi_score
            + 0.15 * roc_score
            + 0.20 * vwap_score
            + 0.15 * whale_score
        )
        bias = "NEUTRAL"
        if score >= 0.8:
            bias = "BUY"
        elif score <= -0.8:
            bias = "SELL"

        self._signals = MicroSignals(
            ema_crossover=ema_score,
            rsi=rsi_score,
            roc=roc_score,
            vwap=vwap_score,
            whale=whale_score,
            score=score,
            bias=bias,
        )

    def _calc_whale_score(self) -> float:
        flow_list = list(self._flow)
        notionals = [entry.notional_est for entry in flow_list if entry.notional_est > 0.0]
        if not notionals:
            return 0.0

        sorted_notional = sorted(notionals)
        percentile_index = max(0, int(len(sorted_notional) * 0.75) - 1)
        threshold = sorted_notional[percentile_index]

        buy_total = sum(
            entry.notional_est for entry in flow_list if entry.notional_est >= threshold and entry.side == "BUY"
        )
        sell_total = sum(
            entry.notional_est for entry in flow_list if entry.notional_est >= threshold and entry.side == "SELL"
        )
        total = buy_total + sell_total
        if total <= 1e-12:
            return 0.0

        return _clamp(((buy_total - sell_total) / total) * 2.0, -2.0, 2.0)

    @staticmethod
    def _positive_delta(previous: float | None, current: float) -> float:
        if previous is None:
            return 0.0
        if current >= previous:
            return current - previous
        return 0.0

    @staticmethod
    def _parse_quote_ts(raw_ts: str) -> float:
        dt = parse_ts(raw_ts)
        if dt == datetime.min:
            return datetime.now().timestamp()
        return dt.timestamp()

