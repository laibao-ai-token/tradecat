from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .etf_profiles import ETFDomainProfile
from .micro import Candle, MicroSnapshot


@dataclass(frozen=True)
class ETFSelectionItem:
    symbol: str
    total_score: float
    trend_score: float
    momentum_score: float
    liquidity_score: float
    risk_adjusted_score: float
    risk_level: str
    reason_tags: tuple[str, ...]
    age_s: int


@dataclass(frozen=True)
class ETFSelectionSnapshot:
    strategy_label: str
    strategy_version: str
    domain_key: str
    domain_label: str
    rebalance: str
    risk_profile: str
    as_of: str
    total_candidates: int
    valid_candidates: int
    skipped_missing: int
    skipped_stale: int
    items: tuple[ETFSelectionItem, ...]


@dataclass(frozen=True)
class _RawFeature:
    symbol: str
    age_s: int
    trend_raw: float
    momentum_raw: float
    liquidity_raw: float
    risk_raw: float


def _safe_pct_change(base: float, current: float) -> float:
    if abs(base) <= 1e-12:
        return 0.0
    return (current - base) / base


def _curve_drawdown(closes: list[float]) -> float:
    if not closes:
        return 0.0
    peak = closes[0]
    max_dd = 0.0
    for value in closes:
        peak = max(peak, value)
        if peak <= 1e-12:
            continue
        dd = (peak - value) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _curve_volatility(closes: list[float]) -> float:
    if len(closes) < 3:
        return 0.0
    rets: list[float] = []
    for idx in range(1, len(closes)):
        prev = closes[idx - 1]
        cur = closes[idx]
        if prev <= 1e-12:
            continue
        rets.append((cur - prev) / prev)
    if not rets:
        return 0.0
    sq = sum(v * v for v in rets) / float(len(rets))
    return math.sqrt(max(0.0, sq))


def _normalize_scores(raw_map: dict[str, float]) -> dict[str, float]:
    if not raw_map:
        return {}
    values = list(raw_map.values())
    lo = min(values)
    hi = max(values)
    if abs(hi - lo) <= 1e-12:
        return {key: 50.0 for key in raw_map}
    out: dict[str, float] = {}
    for key, value in raw_map.items():
        out[key] = (value - lo) / (hi - lo) * 100.0
    return out


def _risk_level(score: float) -> str:
    if score >= 67.0:
        return "LOW"
    if score >= 40.0:
        return "MED"
    return "HIGH"


def _reason_tags(
    *,
    trend: float,
    momentum: float,
    liquidity: float,
    risk_adjusted: float,
    age_s: int,
) -> tuple[str, ...]:
    tags: list[str] = []
    if trend >= 60.0:
        tags.append("趋势向上")
    elif trend <= 40.0:
        tags.append("趋势偏弱")

    if momentum >= 60.0:
        tags.append("动量增强")
    elif momentum <= 40.0:
        tags.append("动量不足")

    if liquidity >= 60.0:
        tags.append("流动性良好")
    else:
        tags.append("流动性一般")

    if risk_adjusted >= 67.0:
        tags.append("波动可控")
    elif risk_adjusted <= 40.0:
        tags.append("波动偏高")
    else:
        tags.append("风险中性")

    if age_s > 60:
        tags.append("数据略旧")

    if len(tags) < 2:
        tags.append("观察为主")
    return tuple(tags[:3])


def _extract_curve_closes(curve: list[Candle], fallback_price: float) -> list[float]:
    closes = [float(item.close) for item in (curve or []) if float(item.close) > 0.0]
    if closes:
        return closes
    if fallback_price > 0.0:
        return [fallback_price]
    return []


def select_etf_candidates(
    *,
    profile: ETFDomainProfile,
    symbols: list[str],
    quote_entries: dict[str, Any],
    curve_map: dict[str, list[Candle]],
    micro_snapshots: dict[str, MicroSnapshot],
    now_ts: float,
    stale_seconds: int = 120,
) -> ETFSelectionSnapshot:
    total_candidates = len(symbols)
    skipped_missing = 0
    skipped_stale = 0

    features: list[_RawFeature] = []
    for symbol in symbols:
        normalized = (symbol or "").strip().upper()
        if not normalized:
            continue

        entry = quote_entries.get(normalized)
        quote = getattr(entry, "quote", None) if entry is not None else None
        if quote is None or float(getattr(quote, "price", 0.0) or 0.0) <= 0.0:
            skipped_missing += 1
            continue

        fetch_at = float(getattr(entry, "last_fetch_at", 0.0) or 0.0)
        age_s = int(max(0.0, now_ts - fetch_at)) if fetch_at > 0.0 else 10**9
        if age_s > int(stale_seconds):
            skipped_stale += 1
            continue

        closes = _extract_curve_closes(curve_map.get(normalized, []), float(quote.price))
        if not closes:
            skipped_missing += 1
            continue

        first = closes[0]
        last = closes[-1]
        short_base = closes[max(0, len(closes) - min(8, len(closes)))]
        long_ret = _safe_pct_change(first, last)
        short_ret = _safe_pct_change(short_base, last)
        vol = _curve_volatility(closes)
        drawdown = _curve_drawdown(closes)

        micro = micro_snapshots.get(normalized)
        ema = float(micro.signals.ema_crossover) if micro is not None else 0.0
        roc = float(micro.signals.roc) if micro is not None else 0.0
        mscore = float(micro.signals.score) if micro is not None else 0.0

        trend_raw = 0.70 * long_ret + 0.20 * ema + 0.10 * mscore
        momentum_raw = 0.60 * short_ret + 0.20 * roc + 0.20 * mscore

        volume_raw = max(0.0, float(getattr(quote, "volume", 0.0) or 0.0))
        amount_raw = max(0.0, float(getattr(quote, "amount", 0.0) or 0.0))
        if amount_raw <= 0.0:
            amount_raw = volume_raw * max(0.0, float(quote.price))
        liquidity_raw = math.log1p(volume_raw) + 0.35 * math.log1p(amount_raw)

        # Higher is better after negation (lower vol/drawdown -> higher risk_raw).
        risk_raw = -(120.0 * vol + 80.0 * drawdown)

        features.append(
            _RawFeature(
                symbol=normalized,
                age_s=age_s,
                trend_raw=trend_raw,
                momentum_raw=momentum_raw,
                liquidity_raw=liquidity_raw,
                risk_raw=risk_raw,
            )
        )

    if not features:
        return ETFSelectionSnapshot(
            strategy_label="ETF-AUTO-V1",
            strategy_version="v1.0.0",
            domain_key=profile.key,
            domain_label=profile.label,
            rebalance=profile.rebalance,
            risk_profile=profile.risk_profile,
            as_of=datetime.now().strftime("%H:%M:%S"),
            total_candidates=total_candidates,
            valid_candidates=0,
            skipped_missing=skipped_missing,
            skipped_stale=skipped_stale,
            items=tuple(),
        )

    trend_norm = _normalize_scores({item.symbol: item.trend_raw for item in features})
    momentum_norm = _normalize_scores({item.symbol: item.momentum_raw for item in features})
    liquidity_norm = _normalize_scores({item.symbol: item.liquidity_raw for item in features})
    risk_norm = _normalize_scores({item.symbol: item.risk_raw for item in features})

    ranked: list[ETFSelectionItem] = []
    for item in features:
        trend = trend_norm.get(item.symbol, 50.0)
        momentum = momentum_norm.get(item.symbol, 50.0)
        liquidity = liquidity_norm.get(item.symbol, 50.0)
        risk_adjusted = risk_norm.get(item.symbol, 50.0)

        total = (
            profile.weights.trend * trend
            + profile.weights.momentum * momentum
            + profile.weights.liquidity * liquidity
            + profile.weights.risk_adjusted * risk_adjusted
        )
        risk = _risk_level(risk_adjusted)
        reasons = _reason_tags(
            trend=trend,
            momentum=momentum,
            liquidity=liquidity,
            risk_adjusted=risk_adjusted,
            age_s=item.age_s,
        )
        ranked.append(
            ETFSelectionItem(
                symbol=item.symbol,
                total_score=total,
                trend_score=trend,
                momentum_score=momentum,
                liquidity_score=liquidity,
                risk_adjusted_score=risk_adjusted,
                risk_level=risk,
                reason_tags=reasons,
                age_s=item.age_s,
            )
        )

    ranked.sort(key=lambda row: row.total_score, reverse=True)
    top_n = max(1, int(profile.top_n))
    return ETFSelectionSnapshot(
        strategy_label="ETF-AUTO-V1",
        strategy_version="v1.0.0",
        domain_key=profile.key,
        domain_label=profile.label,
        rebalance=profile.rebalance,
        risk_profile=profile.risk_profile,
        as_of=datetime.now().strftime("%H:%M:%S"),
        total_candidates=total_candidates,
        valid_candidates=len(features),
        skipped_missing=skipped_missing,
        skipped_stale=skipped_stale,
        items=tuple(ranked[:top_n]),
    )

