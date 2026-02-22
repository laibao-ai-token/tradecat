"""
历史指标回填脚本
根据 RETENTION 配置，为每个币种每个周期计算并写入历史指标数据
"""
import os
import sys
import time
import sqlite3
from pathlib import Path
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import numpy as np
from src.db.reader import (
    apply_indicator_retention_overrides,
    get_indicator_retention_map,
    reader,
    writer,
)
from src.indicators.base import get_batch_indicators, get_all_indicators
from src.core.async_full_engine import get_high_priority_symbols_fast

INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d', '1w']
DEFAULT_BAR_LIMIT = 10_000


def _is_truthy_env(name: str) -> bool:
    return (os.environ.get(name, "") or "").strip().lower() in {"1", "true", "yes", "on"}


def _index_to_ts_strings(index: pd.Index) -> list[str]:
    """Convert a candle DataFrame index to ISO-ish timestamp strings for SQLite."""
    return [ts.isoformat() if hasattr(ts, "isoformat") else str(ts) for ts in index]


def _is_indicator_ready(
    sqlite_path: Path,
    table: str,
    symbol: str,
    interval: str,
    retention: int,
) -> bool:
    """Return True when table already has enough rows for symbol+interval."""
    try:
        conn = sqlite3.connect(str(sqlite_path))
        cur = conn.cursor()
        cur.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE upper("交易对")=? AND COALESCE("周期","")=?',
            (str(symbol).upper(), str(interval)),
        )
        row = cur.fetchone()
        conn.close()
        count = int(row[0]) if row and row[0] is not None else 0
        return count >= max(1, int(retention))
    except Exception:
        return False


def _backfill_k_pattern_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Fast historical backfill for KPattern using talib CDL arrays once."""
    if df is None or df.empty:
        return pd.DataFrame()

    try:
        from src.indicators.batch.k_pattern import CDL_NAMES, _get_talib_cdl_funcs
    except Exception:
        return pd.DataFrame()

    close = pd.to_numeric(df.get("close"), errors="coerce")
    open_ = pd.to_numeric(df.get("open"), errors="coerce")
    high = pd.to_numeric(df.get("high"), errors="coerce")
    low = pd.to_numeric(df.get("low"), errors="coerce")
    if close is None or open_ is None or high is None or low is None:
        return pd.DataFrame()

    n = len(df)
    pattern_lists: list[list[str]] = [[] for _ in range(n)]
    strengths = np.zeros(n, dtype=float)

    cdl_funcs = _get_talib_cdl_funcs() or []
    o = open_.to_numpy(dtype=float)
    h = high.to_numpy(dtype=float)
    l = low.to_numpy(dtype=float)
    c = close.to_numpy(dtype=float)

    for fname, fn in cdl_funcs:
        try:
            arr = np.asarray(fn(o, h, l, c), dtype=float)
        except Exception:
            continue
        nz = np.flatnonzero(arr != 0)
        if nz.size == 0:
            continue
        label = CDL_NAMES.get(fname, fname)
        vals = np.abs(arr[nz] / 100.0)
        strengths[nz] += vals
        for idx in nz.tolist():
            pattern_lists[idx].append(label)

    quote = df.get("quote_volume")
    if quote is None:
        quote = pd.to_numeric(df.get("volume"), errors="coerce") * pd.to_numeric(df.get("close"), errors="coerce")
    else:
        quote = pd.to_numeric(quote, errors="coerce")

    out = pd.DataFrame(
        {
            "交易对": symbol,
            "周期": interval,
            "数据时间": [ts.isoformat() if hasattr(ts, "isoformat") else str(ts) for ts in df.index],
            "形态类型": [",".join(items) if items else "无形态" for items in pattern_lists],
            "检测数量": [len(items) for items in pattern_lists],
            "强度": np.round(strengths, 2),
            "成交额（USDT）": quote.fillna(0.0).astype(float).to_numpy(),
            "当前价格": close.fillna(0.0).astype(float).to_numpy(),
        }
    )
    return out


def _to_float_series(df: pd.DataFrame, key: str, *, default: float | None = None) -> pd.Series:
    """Read numeric column as float series (NaN when missing unless default provided)."""
    col = df.get(key)
    if col is None:
        fill = np.nan if default is None else float(default)
        return pd.Series([fill] * len(df), index=df.index, dtype=float)
    out = pd.to_numeric(col, errors="coerce").astype(float)
    if default is not None:
        out = out.fillna(float(default))
    return out


def _to_int_series(df: pd.DataFrame, key: str, *, default: int = 0) -> pd.Series:
    col = df.get(key)
    if col is None:
        return pd.Series([int(default)] * len(df), index=df.index, dtype=int)
    out = pd.to_numeric(col, errors="coerce").fillna(int(default)).astype(int)
    return out


def _build_base_frame(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "交易对": symbol,
            "周期": interval,
            "数据时间": _index_to_ts_strings(df.index),
        }
    )


def _backfill_base_data_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for 基础数据同步器.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    o = _to_float_series(df, "open", default=0.0)
    h = _to_float_series(df, "high", default=0.0)
    low = _to_float_series(df, "low", default=0.0)
    c = _to_float_series(df, "close", default=0.0)
    vol = _to_float_series(df, "volume", default=0.0)

    quote_raw = _to_float_series(df, "quote_volume", default=None)
    quote = quote_raw.fillna(vol * c)

    trade_count = _to_int_series(df, "trade_count", default=0)
    tbv = _to_float_series(df, "taker_buy_volume", default=None)
    tbq = _to_float_series(df, "taker_buy_quote_volume", default=None)

    mask_tbq = tbq.isna() & tbv.notna() & (c != 0)
    tbq = tbq.where(~mask_tbq, tbv * c)
    mask_tbv = tbv.isna() & tbq.notna() & (c != 0)
    tbv = tbv.where(~mask_tbv, tbq / c.replace(0, np.nan))

    has_taker = tbv.notna() & tbq.notna()
    sell_vol = (vol - tbv).clip(lower=0.0).where(has_taker)
    sell_quote = (quote - tbq).clip(lower=0.0).where(has_taker)
    buy_ratio = (tbv / vol.replace(0, np.nan)).where(has_taker)
    net_flow = (tbq - sell_quote).where(has_taker)

    amp = np.where(low.to_numpy(dtype=float) != 0, (h - low) / low, 0.0)
    chg = np.where(o.to_numpy(dtype=float) != 0, (c - o) / o, 0.0)
    avg_trade = (quote / trade_count.replace(0, np.nan)).fillna(0.0)

    out = _build_base_frame(df, symbol, interval)
    out["开盘价"] = o.to_numpy(dtype=float)
    out["最高价"] = h.to_numpy(dtype=float)
    out["最低价"] = low.to_numpy(dtype=float)
    out["收盘价"] = c.to_numpy(dtype=float)
    out["当前价格"] = c.to_numpy(dtype=float)
    out["成交量"] = vol.to_numpy(dtype=float)
    out["成交额"] = quote.to_numpy(dtype=float)
    out["振幅"] = amp
    out["变化率"] = chg
    out["交易次数"] = trade_count.to_numpy(dtype=int)
    out["成交笔数"] = trade_count.to_numpy(dtype=int)
    out["主动买入量"] = tbv.to_numpy(dtype=float)
    out["主动买量"] = tbv.to_numpy(dtype=float)
    out["主动买额"] = tbq.to_numpy(dtype=float)
    out["主动卖出量"] = sell_vol.to_numpy(dtype=float)
    out["主动买卖比"] = buy_ratio.to_numpy(dtype=float)
    out["主动卖出额"] = sell_quote.to_numpy(dtype=float)
    out["资金流向"] = net_flow.to_numpy(dtype=float)
    out["平均每笔成交额"] = avg_trade.to_numpy(dtype=float)
    return out


def _backfill_buy_sell_ratio_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for 主动买卖比扫描器.py."""
    if df is None or df.empty:
        return pd.DataFrame()
    vol = _to_float_series(df, "volume", default=0.0)
    buy = _to_float_series(df, "taker_buy_volume", default=None).fillna(vol * 0.5)
    sell = (vol - buy).clip(lower=0.0)
    ratio = (buy / vol.replace(0, np.nan)).fillna(0.0)
    price = _to_float_series(df, "close", default=0.0)

    out = _build_base_frame(df, symbol, interval)
    out["主动买量"] = buy.to_numpy(dtype=float)
    out["主动卖量"] = sell.to_numpy(dtype=float)
    out["主动买卖比"] = ratio.to_numpy(dtype=float)
    out["价格"] = price.to_numpy(dtype=float)
    return out


def _backfill_cvd_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for CVD信号排行榜.py."""
    if df is None or df.empty or "taker_buy_volume" not in df.columns:
        return pd.DataFrame()

    vol = _to_float_series(df, "volume", default=0.0)
    buy = _to_float_series(df, "taker_buy_volume", default=None).fillna(vol * 0.5)
    sell = (vol - buy).clip(lower=0.0)
    delta = buy - sell
    cvd = delta.cumsum()

    window = min(360, max(1, len(cvd) - 1))
    base = cvd.shift(window)
    change = (cvd - base) / (base.abs() + 1e-9)

    out = _build_base_frame(df, symbol, interval)
    out["CVD值"] = cvd.to_numpy(dtype=float)
    out["变化率"] = change.to_numpy(dtype=float)
    return out


def _backfill_obv_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for OBV能量潮扫描器.py."""
    if df is None or df.empty:
        return pd.DataFrame()
    close = _to_float_series(df, "close", default=0.0)
    vol = _to_float_series(df, "volume", default=0.0)
    direction = np.sign(close.diff()).fillna(0.0)
    obv = (direction * vol).cumsum()
    window = min(30, max(1, len(obv) - 1))
    base = obv.shift(window)
    change = (obv - base) / (base.abs() + 1e-9)

    out = _build_base_frame(df, symbol, interval)
    out["OBV值"] = obv.to_numpy(dtype=float)
    out["OBV变化率"] = change.to_numpy(dtype=float)
    return out


def _backfill_macd_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for MACD柱状扫描器.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    close = _to_float_series(df, "close", default=0.0)
    ema12 = close.ewm(span=12, adjust=False, min_periods=1).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=1).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False, min_periods=1).mean()
    macd = 2 * (dif - dea)

    crossed_up = (macd.shift(1) <= 0) & (macd > 0)
    crossed_down = (macd.shift(1) >= 0) & (macd < 0)
    crossed = np.select([crossed_up, crossed_down], ["零轴上穿", "零轴下破"], default="")

    golden = (dif.shift(1) <= dea.shift(1)) & (dif > dea)
    dead = (dif.shift(1) >= dea.shift(1)) & (dif < dea)
    base = np.select([golden, dead], ["金叉", "死叉"], default="")
    signal = np.where(
        base != "",
        np.where(crossed != "", base + "/" + crossed, base),
        np.where(crossed != "", crossed, "延续"),
    )
    if len(signal) > 0:
        signal[0] = "数据不足"

    quote = df.get("quote_volume")
    if quote is None:
        quote = _to_float_series(df, "volume", default=0.0) * close
    else:
        quote = _to_float_series(df, "quote_volume", default=0.0)

    out = _build_base_frame(df, symbol, interval)
    out["信号概述"] = signal
    out["MACD"] = np.round(dif.to_numpy(dtype=float), 6)
    out["MACD信号线"] = np.round(dea.to_numpy(dtype=float), 6)
    out["MACD柱状图"] = np.round(macd.to_numpy(dtype=float), 6)
    out["DIF"] = np.round(dif.to_numpy(dtype=float), 6)
    out["DEA"] = np.round(dea.to_numpy(dtype=float), 6)
    out["成交额"] = quote.to_numpy(dtype=float)
    out["当前价格"] = close.to_numpy(dtype=float)
    return out


def _backfill_kdj_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for KDJ随机指标扫描器.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    high = _to_float_series(df, "high", default=0.0)
    low = _to_float_series(df, "low", default=0.0)
    close = _to_float_series(df, "close", default=0.0)

    low_n = low.rolling(9, min_periods=9).min()
    high_n = high.rolling(9, min_periods=9).max()
    rsv = (close - low_n) / (high_n - low_n + 1e-10) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False, min_periods=3).mean()
    d = k.ewm(alpha=1 / 3, adjust=False, min_periods=3).mean()
    j = 3 * k - 2 * d

    cross_up = (k.shift(1) <= d.shift(1)) & (k > d)
    cross_down = (k.shift(1) >= d.shift(1)) & (k < d)
    signal = np.select(
        [cross_up, cross_down, j > 100, j < 0],
        ["金叉", "死叉", "J>100 极值", "J<0 极值"],
        default="延续",
    )
    signal = np.where(k.isna() | d.isna() | j.isna(), "数据不足", signal)

    quote = df.get("quote_volume")
    if quote is None:
        quote = _to_float_series(df, "volume", default=0.0) * close
    else:
        quote = _to_float_series(df, "quote_volume", default=0.0)

    out = _build_base_frame(df, symbol, interval)
    out["J值"] = np.round(j.to_numpy(dtype=float), 3)
    out["K值"] = np.round(k.to_numpy(dtype=float), 3)
    out["D值"] = np.round(d.to_numpy(dtype=float), 3)
    out["信号概述"] = signal
    out["成交额"] = quote.to_numpy(dtype=float)
    out["当前价格"] = close.to_numpy(dtype=float)
    return out


def _backfill_atr_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for ATR波幅扫描器.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    high = _to_float_series(df, "high", default=0.0)
    low = _to_float_series(df, "low", default=0.0)
    close = _to_float_series(df, "close", default=0.0)

    prev_close = close.shift(1)
    tr1 = (high - low).abs()
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    atr_pct = (atr / close.replace(0, np.nan) * 100).fillna(0.0)
    mid = close.rolling(20, min_periods=20).mean()
    upper = mid + 2 * atr
    lower = mid - 2 * atr

    med = atr.rolling(30, min_periods=1).median()
    category = np.where(
        med.isna(),
        "未知",
        np.where(atr > med * 1.1, "升温", np.where(atr < med * 0.9, "降温", "稳定")),
    )

    quote = df.get("quote_volume")
    if quote is None:
        quote = _to_float_series(df, "volume", default=0.0) * close
    else:
        quote = _to_float_series(df, "quote_volume", default=0.0)

    out = _build_base_frame(df, symbol, interval)
    out["波动分类"] = category
    out["ATR百分比"] = np.round(atr_pct.to_numpy(dtype=float), 4)
    out["上轨"] = np.round(upper.to_numpy(dtype=float), 6)
    out["中轨"] = np.round(mid.to_numpy(dtype=float), 6)
    out["下轨"] = np.round(lower.to_numpy(dtype=float), 6)
    out["成交额"] = quote.to_numpy(dtype=float)
    out["当前价格"] = close.to_numpy(dtype=float)
    return out


def _trend_bias_series(e7: pd.Series, e25: pd.Series, e99: pd.Series, price: pd.Series) -> np.ndarray:
    e7v = e7.to_numpy(dtype=float)
    e25v = e25.to_numpy(dtype=float)
    e99v = e99.to_numpy(dtype=float)
    pv = price.to_numpy(dtype=float)

    out = np.full(len(price), "震荡", dtype=object)

    bull = (e7v > e25v) & (e25v > e99v)
    out[bull & (pv >= e7v)] = "多头排列"
    out[bull & (pv < e7v)] = "偏多"

    bear = (e7v < e25v) & (e25v < e99v)
    out[bear & (pv <= e7v)] = "空头排列"
    out[bear & (pv > e7v)] = "偏空"

    other = ~(bull | bear)
    out[other & (pv > e99v)] = "偏多"
    out[other & (pv < e99v)] = "偏空"
    return out


def _backfill_ema_gc_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for G，C点扫描器.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    close = _to_float_series(df, "close", default=0.0)
    ema7 = close.ewm(span=7, adjust=False, min_periods=1).mean()
    ema25 = close.ewm(span=25, adjust=False, min_periods=1).mean()
    ema99 = close.ewm(span=99, adjust=False, min_periods=1).mean()

    trend = _trend_bias_series(ema7, ema25, ema99, close)

    vals_max = np.maximum.reduce([ema7.to_numpy(dtype=float), ema25.to_numpy(dtype=float), ema99.to_numpy(dtype=float)])
    vals_min = np.minimum.reduce([ema7.to_numpy(dtype=float), ema25.to_numpy(dtype=float), ema99.to_numpy(dtype=float)])
    price = close.to_numpy(dtype=float)
    bw = np.where(price != 0, (vals_max - vals_min) / np.abs(price), 0.0)
    tau = 0.03
    score = 100.0 * (1.0 - np.exp(-bw / max(tau, 1e-6)))
    score = np.clip(score, 0.0, 100.0)

    out = _build_base_frame(df, symbol, interval)
    out["EMA7"] = np.round(ema7.to_numpy(dtype=float), 6)
    out["EMA25"] = np.round(ema25.to_numpy(dtype=float), 6)
    out["EMA99"] = np.round(ema99.to_numpy(dtype=float), 6)
    out["价格"] = close.to_numpy(dtype=float)
    out["趋势方向"] = trend
    out["带宽评分"] = np.round(score, 2)
    return out


def _backfill_bollinger_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for 布林带扫描器.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    from src.indicators.safe_calc import safe_bollinger

    close = _to_float_series(df, "close", default=0.0)
    upper, mid, lower, _ = safe_bollinger(close, 20, 2.0, min_period=5)

    bandwidth = (upper - lower) / mid.replace(0, np.nan) * 100
    pct_b = (close - lower) / (upper - lower).replace(0, np.nan)
    half = 10
    slope = (mid - mid.shift(half)) / float(half)

    quote = df.get("quote_volume")
    if quote is None:
        quote = _to_float_series(df, "volume", default=0.0) * close
    else:
        quote = _to_float_series(df, "quote_volume", default=0.0)

    out = _build_base_frame(df, symbol, interval)
    out["带宽"] = np.round(bandwidth.to_numpy(dtype=float), 4)
    out["中轨斜率"] = np.round(slope.to_numpy(dtype=float), 6)
    out["中轨价格"] = np.round(mid.to_numpy(dtype=float), 6)
    out["上轨价格"] = np.round(upper.to_numpy(dtype=float), 6)
    out["下轨价格"] = np.round(lower.to_numpy(dtype=float), 6)
    out["百分比b"] = np.round(pct_b.fillna(0.0).to_numpy(dtype=float), 4)
    out["价格"] = close.to_numpy(dtype=float)
    out["成交额"] = quote.to_numpy(dtype=float)
    return out


def _backfill_volume_ratio_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for 成交量比率扫描器.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    vol = _to_float_series(df, "volume", default=0.0)
    avg = vol.rolling(20, min_periods=20).mean()
    ratio = (vol / avg.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)

    cur = ratio.fillna(0.0)
    signal = np.where(
        cur > 5,
        "极值放量",
        np.where(cur > 2, "异常放量", np.where(cur > 1, "放量", np.where(cur < 0.7, "缩量", "正常"))),
    )

    close = _to_float_series(df, "close", default=0.0)
    quote = df.get("quote_volume")
    if quote is None:
        quote = vol * close
    else:
        quote = _to_float_series(df, "quote_volume", default=0.0)

    out = _build_base_frame(df, symbol, interval)
    out["量比"] = np.round(cur.to_numpy(dtype=float), 4)
    out["信号概述"] = signal
    out["成交额"] = quote.to_numpy(dtype=float)
    out["当前价格"] = close.to_numpy(dtype=float)
    return out


def _backfill_mfi_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for MFI资金流量扫描器.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    high = _to_float_series(df, "high", default=0.0)
    low = _to_float_series(df, "low", default=0.0)
    close = _to_float_series(df, "close", default=0.0)
    vol = _to_float_series(df, "volume", default=0.0)

    tp = (high + low + close) / 3.0
    mf = tp * vol
    direction = np.sign(tp.diff()).fillna(0.0)
    pos = mf.where(direction > 0, 0.0).rolling(14, min_periods=14).sum()
    neg = mf.where(direction < 0, 0.0).rolling(14, min_periods=14).sum().abs()
    mfr = pos / neg.replace(0, np.nan)
    mfi = 100 - (100 / (1 + mfr))

    out = _build_base_frame(df, symbol, interval)
    out["MFI值"] = np.round(mfi.to_numpy(dtype=float), 2)
    return out


def _wilder_smooth(arr: np.ndarray, period: int) -> np.ndarray:
    result = np.zeros_like(arr, dtype=float)
    if len(arr) == 0:
        return result
    result[0] = float(arr[0])
    alpha = 1.0 / float(max(1, int(period)))
    for i in range(1, len(arr)):
        result[i] = result[i - 1] * (1 - alpha) + float(arr[i]) * alpha
    return result


def _backfill_supertrend_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for SuperTrend.py (LEAN version)."""
    if df is None or df.empty:
        return pd.DataFrame()

    high = _to_float_series(df, "high", default=0.0).to_numpy(dtype=float)
    low = _to_float_series(df, "low", default=0.0).to_numpy(dtype=float)
    close = _to_float_series(df, "close", default=0.0).to_numpy(dtype=float)
    n = len(close)
    if n < 2:
        return pd.DataFrame()

    period = 10
    mult = 3.0

    tr = np.zeros(n, dtype=float)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr = _wilder_smooth(tr, period)

    hl2 = (high + low) / 2.0
    upper = hl2 + mult * atr
    lower_band = hl2 - mult * atr

    final_upper = np.copy(upper)
    final_lower = np.copy(lower_band)
    supertrend = np.zeros(n, dtype=float)
    direction = np.ones(n, dtype=int)  # 1=空, -1=多

    for i in range(1, n):
        if close[i - 1] > final_upper[i - 1]:
            final_upper[i] = upper[i]
        else:
            final_upper[i] = min(upper[i], final_upper[i - 1])

        if close[i - 1] < final_lower[i - 1]:
            final_lower[i] = lower_band[i]
        else:
            final_lower[i] = max(lower_band[i], final_lower[i - 1])

        if supertrend[i - 1] == final_upper[i - 1]:
            direction[i] = -1 if close[i] > final_upper[i] else 1
        else:
            direction[i] = 1 if close[i] < final_lower[i] else -1

        supertrend[i] = final_upper[i] if direction[i] == 1 else final_lower[i]

    out = _build_base_frame(df, symbol, interval)
    out["SuperTrend"] = supertrend
    out["方向"] = np.where(direction == 1, "空", "多")
    out["上轨"] = final_upper
    out["下轨"] = final_lower
    return out


def _backfill_adx_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for ADX.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    high = _to_float_series(df, "high", default=0.0).to_numpy(dtype=float)
    low = _to_float_series(df, "low", default=0.0).to_numpy(dtype=float)
    close = _to_float_series(df, "close", default=0.0).to_numpy(dtype=float)
    n = len(close)
    if n < 2:
        return pd.DataFrame()

    period = 14
    tr = np.zeros(n, dtype=float)
    plus_dm = np.zeros(n, dtype=float)
    minus_dm = np.zeros(n, dtype=float)

    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0

    smooth_tr = _wilder_smooth(tr, period)
    smooth_plus = _wilder_smooth(plus_dm, period)
    smooth_minus = _wilder_smooth(minus_dm, period)

    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = np.where(smooth_tr > 0, 100.0 * smooth_plus / smooth_tr, 0.0)
        minus_di = np.where(smooth_tr > 0, 100.0 * smooth_minus / smooth_tr, 0.0)
        di_sum = plus_di + minus_di
        dx = np.where(di_sum > 0, 100.0 * np.abs(plus_di - minus_di) / di_sum, 0.0)
    adx = _wilder_smooth(dx, period)

    out = _build_base_frame(df, symbol, interval)
    out["ADX"] = adx
    out["正向DI"] = plus_di
    out["负向DI"] = minus_di
    return out


def _backfill_cci_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for CCI.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    high = _to_float_series(df, "high", default=0.0).to_numpy(dtype=float)
    low = _to_float_series(df, "low", default=0.0).to_numpy(dtype=float)
    close = _to_float_series(df, "close", default=0.0).to_numpy(dtype=float)
    tp = (high + low + close) / 3.0

    period = 20
    n = len(tp)
    out_arr = np.full(n, np.nan, dtype=float)
    if n < period:
        out = _build_base_frame(df, symbol, interval)
        out["CCI"] = out_arr
        return out

    sma = np.convolve(tp, np.ones(period) / period, mode="valid")  # n-period+1
    mad = np.zeros_like(sma, dtype=float)
    for i in range(len(sma)):
        window = tp[i : i + period]
        mad[i] = float(np.mean(np.abs(window - sma[i])))
    cci_valid = (tp[period - 1 :] - sma) / (0.015 * mad + 1e-10)
    out_arr[period - 1 :] = cci_valid

    out = _build_base_frame(df, symbol, interval)
    out["CCI"] = out_arr
    return out


def _backfill_williams_r_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for WilliamsR.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    high = _to_float_series(df, "high", default=0.0)
    low = _to_float_series(df, "low", default=0.0)
    close = _to_float_series(df, "close", default=0.0)
    period = 14
    hh = high.rolling(period, min_periods=period).max()
    ll = low.rolling(period, min_periods=period).min()
    wr = -100.0 * (hh - close) / (hh - ll + 1e-10)

    out = _build_base_frame(df, symbol, interval)
    out["WilliamsR"] = wr.to_numpy(dtype=float)
    return out


def _backfill_donchian_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for Donchian.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    high = _to_float_series(df, "high", default=0.0)
    low = _to_float_series(df, "low", default=0.0)
    period = 20
    upper = high.rolling(period, min_periods=period).max()
    lower = low.rolling(period, min_periods=period).min()
    mid = (upper + lower) / 2.0

    out = _build_base_frame(df, symbol, interval)
    out["上轨"] = upper.to_numpy(dtype=float)
    out["中轨"] = mid.to_numpy(dtype=float)
    out["下轨"] = lower.to_numpy(dtype=float)
    return out


def _backfill_keltner_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for Keltner.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    high = _to_float_series(df, "high", default=0.0)
    low = _to_float_series(df, "low", default=0.0)
    close = _to_float_series(df, "close", default=0.0)
    ema_period = 20
    atr_period = 10
    mult = 2.0

    mid = close.ewm(span=ema_period, adjust=False, min_periods=1).mean()
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / atr_period, adjust=False, min_periods=1).mean()

    out = _build_base_frame(df, symbol, interval)
    out["上轨"] = (mid + mult * atr).to_numpy(dtype=float)
    out["中轨"] = mid.to_numpy(dtype=float)
    out["下轨"] = (mid - mult * atr).to_numpy(dtype=float)
    out["ATR"] = atr.to_numpy(dtype=float)
    return out


def _backfill_ichimoku_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for Ichimoku.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    high = _to_float_series(df, "high", default=0.0)
    low = _to_float_series(df, "low", default=0.0)
    close = _to_float_series(df, "close", default=0.0)

    tenkan = (high.rolling(9, min_periods=9).max() + low.rolling(9, min_periods=9).min()) / 2.0
    kijun = (high.rolling(26, min_periods=26).max() + low.rolling(26, min_periods=26).min()) / 2.0
    senkou_a = (tenkan + kijun) / 2.0
    senkou_b = (high.rolling(52, min_periods=52).max() + low.rolling(52, min_periods=52).min()) / 2.0

    cloud_top = np.maximum(senkou_a.to_numpy(dtype=float), senkou_b.to_numpy(dtype=float))
    cloud_bot = np.minimum(senkou_a.to_numpy(dtype=float), senkou_b.to_numpy(dtype=float))
    price = close.to_numpy(dtype=float)

    buy = (price > cloud_top) & (tenkan.to_numpy(dtype=float) > kijun.to_numpy(dtype=float))
    sell = (price < cloud_bot) & (tenkan.to_numpy(dtype=float) < kijun.to_numpy(dtype=float))
    signal = np.where(buy, "BUY", np.where(sell, "SELL", "NEUTRAL"))
    direction = np.where(buy, "多", np.where(sell, "空", "震荡"))

    denom = cloud_top - cloud_bot + 1e-10
    strength = np.where(
        price > cloud_top,
        np.minimum(1.0, (price - cloud_top) / denom),
        np.where(price < cloud_bot, np.minimum(1.0, (cloud_bot - price) / denom), 0.5),
    )

    out = _build_base_frame(df, symbol, interval)
    out["转换线"] = tenkan.to_numpy(dtype=float)
    out["基准线"] = kijun.to_numpy(dtype=float)
    out["先行带A"] = senkou_a.to_numpy(dtype=float)
    out["先行带B"] = senkou_b.to_numpy(dtype=float)
    out["迟行带"] = price
    out["当前价格"] = price
    out["信号"] = signal
    out["方向"] = direction
    out["强度"] = np.round(strength, 3)
    return out


def _backfill_support_resistance_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for 全量支撑阻力扫描器.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    high = _to_float_series(df, "high", default=0.0)
    low = _to_float_series(df, "low", default=0.0)
    close = _to_float_series(df, "close", default=0.0)

    support = low.rolling(20, min_periods=20).min()
    resistance = high.rolling(20, min_periods=20).max()

    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=1).mean()

    dist_support = (close - support) / close.replace(0, np.nan) * 100
    dist_resistance = (resistance - close) / close.replace(0, np.nan) * 100
    dist_key = np.minimum(dist_support.abs().to_numpy(dtype=float), dist_resistance.abs().to_numpy(dtype=float))

    out = _build_base_frame(df, symbol, interval)
    out["支撑位"] = np.round(support.to_numpy(dtype=float), 6)
    out["阻力位"] = np.round(resistance.to_numpy(dtype=float), 6)
    out["当前价格"] = close.to_numpy(dtype=float)
    out["ATR"] = np.round(atr.to_numpy(dtype=float), 6)
    out["距支撑百分比"] = np.round(dist_support.fillna(0.0).to_numpy(dtype=float), 4)
    out["距阻力百分比"] = np.round(dist_resistance.fillna(0.0).to_numpy(dtype=float), 4)
    out["距关键位百分比"] = np.round(dist_key, 4)
    return out


def _backfill_scalping_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for 剥头皮信号扫描器.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    close = _to_float_series(df, "close", default=0.0)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()

    price = close.to_numpy(dtype=float)
    rsi_v = rsi.fillna(0.0).to_numpy(dtype=float)
    e9 = ema9.to_numpy(dtype=float)
    e21 = ema21.to_numpy(dtype=float)

    signal = np.full(len(close), "观望", dtype=object)
    signal[(rsi_v < 30) & (price > e9) & (e9 > e21)] = "超卖反弹"
    signal[(rsi_v > 70) & (price < e9) & (e9 < e21)] = "超买回落"
    signal[(e9 > e21) & (rsi_v > 50)] = "多头"
    signal[(e9 < e21) & (rsi_v < 50)] = "空头"

    out = _build_base_frame(df, symbol, interval)
    out["剥头皮信号"] = signal
    out["RSI"] = np.round(rsi.to_numpy(dtype=float), 2)
    out["EMA9"] = np.round(e9, 6)
    out["EMA21"] = np.round(e21, 6)
    out["当前价格"] = price
    return out


def _backfill_vwap_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for VWAP离线信号扫描.py."""
    if df is None or df.empty:
        return pd.DataFrame()

    window = 300
    high = _to_float_series(df, "high", default=0.0)
    low = _to_float_series(df, "low", default=0.0)
    close = _to_float_series(df, "close", default=0.0)
    vol = _to_float_series(df, "volume", default=0.0).replace(0, 1e-9)

    price = (high + low + close) / 3.0
    tpv = price * vol
    sum_tpv = tpv.rolling(window, min_periods=1).sum()
    sum_vol = vol.rolling(window, min_periods=1).sum().replace(0, np.nan)
    vwap = (sum_tpv / sum_vol).fillna(close)
    price_std = price.rolling(window, min_periods=1).std(ddof=0).fillna(0.0)
    upper = vwap + price_std
    lower = vwap - price_std

    deviation = close - vwap
    dev_pct = deviation / vwap.replace(0, np.nan) * 100

    bandwidth = (upper - lower).clip(lower=0.0)
    bw_pct = bandwidth / vwap.replace(0, np.nan) * 100

    quote = df.get("quote_volume")
    if quote is None:
        quote = vol * close
    else:
        quote = _to_float_series(df, "quote_volume", default=0.0)

    out = _build_base_frame(df, symbol, interval)
    out["VWAP价格"] = np.round(vwap.to_numpy(dtype=float), 6)
    out["偏离度"] = np.round(deviation.to_numpy(dtype=float), 6)
    out["偏离百分比"] = np.round(dev_pct.fillna(0.0).to_numpy(dtype=float), 4)
    out["成交量加权"] = vol.to_numpy(dtype=float)
    out["当前价格"] = close.to_numpy(dtype=float)
    out["成交额（USDT）"] = quote.to_numpy(dtype=float)
    out["VWAP上轨"] = np.round(upper.to_numpy(dtype=float), 6)
    out["VWAP下轨"] = np.round(lower.to_numpy(dtype=float), 6)
    out["VWAP带宽"] = np.round(bandwidth.to_numpy(dtype=float), 6)
    out["VWAP带宽百分比"] = np.round(bw_pct.fillna(0.0).to_numpy(dtype=float), 4)
    return out


def _backfill_tv_rsi_fast(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Vectorized backfill for 智能RSI扫描器.py (simplified, divergence=无背离)."""
    if df is None or df.empty:
        return pd.DataFrame()

    from src.indicators.safe_calc import safe_atr, safe_rsi

    high = _to_float_series(df, "high", default=0.0)
    low = _to_float_series(df, "low", default=0.0)
    close = _to_float_series(df, "close", default=0.0)

    atr, atr_status = safe_atr(high, low, close, 14, min_period=3)
    if atr_status == "数据不足":
        atr_norm = pd.Series([0.5] * len(df), index=df.index)
    else:
        atr_norm = (atr - atr.min()) / (atr.max() - atr.min() + 1e-10)
        atr_norm = atr_norm.fillna(0.5)

    volatility_factor = 1.0 + (atr_norm - 0.5) * 0.2
    overbought = np.minimum(80.0, 70.0 * volatility_factor.to_numpy(dtype=float))
    oversold = np.maximum(20.0, 30.0 / volatility_factor.to_numpy(dtype=float))

    rsi7, status7 = safe_rsi(close, 7, min_period=3)
    rsi14, status14 = safe_rsi(close, 14, min_period=3)
    rsi21, status21 = safe_rsi(close, 21, min_period=3)

    rsi_avg = pd.concat([rsi7, rsi14, rsi21], axis=1).mean(axis=1, skipna=True).fillna(0.0)
    ema = close.ewm(span=34, adjust=False).mean()
    bullish = close > ema

    in_oversold = (rsi7 < oversold) + (rsi14 < oversold) + (rsi21 < oversold)
    in_overbought = (rsi7 > overbought) + (rsi14 > overbought) + (rsi21 > overbought)

    position = np.where(
        bullish.to_numpy(dtype=bool),
        np.where(in_oversold >= 2, "超卖区", np.where(in_overbought >= 2, "超买区", "中性区")),
        np.where(in_overbought >= 2, "超买区", np.where(in_oversold >= 2, "超卖区", "中性区")),
    )

    signal = np.where(
        bullish.to_numpy(dtype=bool),
        np.where(in_oversold >= 2, "买入", "观望"),
        np.where(in_overbought >= 2, "卖出", "观望"),
    )

    direction = np.where(
        bullish.to_numpy(dtype=bool),
        np.where(in_oversold >= 2, "多头", "震荡"),
        np.where(in_overbought >= 2, "空头", "震荡"),
    )

    rsi_avg_np = rsi_avg.to_numpy(dtype=float)
    strength = np.where(
        signal == "买入",
        (oversold - rsi_avg_np) / np.maximum(oversold, 1e-9) * 100.0,
        np.where(
            signal == "卖出",
            (rsi_avg_np - overbought) / np.maximum(100.0 - overbought, 1e-9) * 100.0,
            np.abs(50.0 - rsi_avg_np) / 50.0 * 100.0,
        ),
    )
    strength = np.clip(np.abs(strength), 0.0, 100.0)

    data_status = "参考值" if any(s == "参考值" for s in (status7, status14, status21)) else ""
    signal_out = np.where(data_status, signal + f"({data_status})", signal)

    out = _build_base_frame(df, symbol, interval)
    out["信号"] = signal_out
    out["方向"] = direction
    out["强度"] = np.round(strength, 2)
    out["RSI均值"] = np.round(rsi_avg_np, 2)
    out["RSI7"] = np.round(rsi7.to_numpy(dtype=float), 2)
    out["RSI14"] = np.round(rsi14.to_numpy(dtype=float), 2)
    out["RSI21"] = np.round(rsi21.to_numpy(dtype=float), 2)
    out["位置"] = position
    out["背离"] = "无背离"
    out["超买阈值"] = np.round(overbought, 2)
    out["超卖阈值"] = np.round(oversold, 2)
    return out


FAST_BACKFILLERS: dict[str, Callable[[pd.DataFrame, str, str], pd.DataFrame]] = {
    "基础数据同步器.py": _backfill_base_data_fast,
    "主动买卖比扫描器.py": _backfill_buy_sell_ratio_fast,
    "CVD信号排行榜.py": _backfill_cvd_fast,
    "OBV能量潮扫描器.py": _backfill_obv_fast,
    "MACD柱状扫描器.py": _backfill_macd_fast,
    "KDJ随机指标扫描器.py": _backfill_kdj_fast,
    "ATR波幅扫描器.py": _backfill_atr_fast,
    "G，C点扫描器.py": _backfill_ema_gc_fast,
    "布林带扫描器.py": _backfill_bollinger_fast,
    "成交量比率扫描器.py": _backfill_volume_ratio_fast,
    "MFI资金流量扫描器.py": _backfill_mfi_fast,
    "SuperTrend.py": _backfill_supertrend_fast,
    "ADX.py": _backfill_adx_fast,
    "CCI.py": _backfill_cci_fast,
    "WilliamsR.py": _backfill_williams_r_fast,
    "Donchian.py": _backfill_donchian_fast,
    "Keltner.py": _backfill_keltner_fast,
    "Ichimoku.py": _backfill_ichimoku_fast,
    "全量支撑阻力扫描器.py": _backfill_support_resistance_fast,
    "剥头皮信号扫描器.py": _backfill_scalping_fast,
    "VWAP离线信号扫描.py": _backfill_vwap_fast,
    "智能RSI扫描器.py": _backfill_tv_rsi_fast,
    "K线形态扫描器.py": _backfill_k_pattern_fast,
}


def _broadcast_last_row(indicator, df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Fallback: compute last-row once and broadcast across timestamps (keeps schema)."""
    if df is None or df.empty:
        return pd.DataFrame()

    window = df.tail(max(10, int(getattr(indicator.meta, "lookback", 50) or 50)))
    try:
        tpl = indicator.compute(window.copy(), symbol, interval)
    except Exception:
        tpl = pd.DataFrame()

    if tpl is None or tpl.empty:
        return pd.DataFrame()

    row = tpl.iloc[-1].to_dict()
    out = _build_base_frame(df, symbol, interval)
    for col in tpl.columns:
        if col in ("交易对", "周期", "数据时间"):
            continue
        out[col] = row.get(col)
    return out

def _normalize_list(raw_items: list[str] | None) -> list[str]:
    out: list[str] = []
    for raw in raw_items or []:
        for item in str(raw).split(","):
            text = item.strip()
            if text:
                out.append(text)
    return list(dict.fromkeys(out))


def _parse_int_overrides(raw: str, allowed_keys: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    txt = str(raw or "").strip()
    if not txt:
        return out

    allowed = set(allowed_keys)
    for item in txt.replace(";", ",").split(","):
        part = item.strip()
        if not part or "=" not in part:
            continue
        key, value = [x.strip() for x in part.split("=", 1)]
        if key not in allowed:
            continue
        try:
            parsed = int(value)
        except Exception:
            continue
        if parsed > 0:
            out[key] = parsed
    return out


def _build_bar_limit_map(
    intervals: list[str],
    retention_map: dict[str, int],
    *,
    base_limit: int,
    overrides: dict[str, int] | None = None,
) -> dict[str, int]:
    out: dict[str, int] = {}
    base = max(1, int(base_limit))
    extra_padding = max(100, int(os.environ.get("BACKFILL_BAR_PADDING", "200")))

    for interval in intervals:
        retention = max(1, int(retention_map.get(interval, 60)))
        dynamic_limit = max(base, retention + extra_padding)
        out[interval] = dynamic_limit

    for interval, value in (overrides or {}).items():
        out[interval] = max(out.get(interval, base), int(value))
    return out


def backfill_symbol_interval(
    symbol: str,
    interval: str,
    indicators: dict,
    retention: int,
    *,
    bar_limit: int,
):
    """为单个币种单个周期回填历史指标"""
    max(ind.meta.lookback for ind in indicators.values())

    # 获取尽可能多的K线数据
    klines = reader.get_klines([symbol], interval, max(1, int(bar_limit)))
    df = klines.get(symbol)
    if df is None or len(df) < 10:
        return 0

    total_bars = len(df)
    computed = 0

    for name, ind_cls in indicators.items():
        if _is_indicator_ready(writer.sqlite_path, name, symbol, interval, retention):
            continue

        if name == "K线形态扫描器.py" and _is_truthy_env("K_PATTERN_BACKFILL_FAST"):
            fast_rows = _backfill_k_pattern_fast(df, symbol, interval)
            if not fast_rows.empty:
                fast_rows = fast_rows.drop_duplicates(
                    subset=['交易对', '周期', '数据时间'],
                    keep='last'
                ).tail(retention)
                writer.write(name, fast_rows, interval)
                computed += len(fast_rows)
                continue

        indicator = ind_cls()
        lookback = indicator.meta.lookback
        # 增量指标允许单条计算（如基础数据同步器）
        if indicator.meta.is_incremental:
            min_data = 1
        else:
            min_data = getattr(indicator.meta, 'min_data', 5)

        results = []
        # 从能计算的最早位置开始
        for end_idx in range(min_data, total_bars + 1):
            window_df = df.iloc[max(0, end_idx - lookback):end_idx].copy()

            if len(window_df) < min_data:
                continue

            try:
                result = indicator.compute(window_df, symbol, interval)
                if result is not None and not result.empty:
                    results.append(result)
            except Exception:
                continue

        if results:
            all_results = pd.concat(results, ignore_index=True)
            all_results = all_results.drop_duplicates(
                subset=['交易对', '周期', '数据时间'],
                keep='last'
            ).tail(retention)

            writer.write(indicator.meta.name, all_results, interval)
            computed += len(all_results)

    return computed


def backfill_symbol_interval_fast(
    symbol: str,
    interval: str,
    indicators: dict,
    retention: int,
    *,
    bar_limit: int,
    workers: int = 1,
    fallback: str = "skip",
) -> int:
    """Fast backfill: compute full-history rows per indicator and bulk-write once."""
    # 获取尽可能多的K线数据
    klines = reader.get_klines([symbol], interval, max(1, int(bar_limit)))
    df = klines.get(symbol)
    if df is None or len(df) < 10:
        return 0

    workers = max(1, int(workers or 1))
    fallback = str(fallback or "broadcast").strip().lower()

    def compute_one(name: str, ind_cls):
        if _is_indicator_ready(writer.sqlite_path, name, symbol, interval, retention):
            return name, None, 0

        if name in FAST_BACKFILLERS:
            try:
                rows = FAST_BACKFILLERS[name](df, symbol, interval)
            except Exception:
                rows = pd.DataFrame()
        else:
            indicator = ind_cls()
            if fallback == "broadcast":
                rows = _broadcast_last_row(indicator, df, symbol, interval)
            else:
                rows = pd.DataFrame()

        if rows is None or rows.empty:
            return name, None, 0

        rows = rows.drop_duplicates(subset=["交易对", "周期", "数据时间"], keep="last").tail(retention)
        return name, rows, len(rows)

    data: dict[str, pd.DataFrame] = {}
    total = 0

    if workers <= 1:
        for name, ind_cls in indicators.items():
            table, rows, n = compute_one(name, ind_cls)
            if rows is not None and not rows.empty:
                data[table] = rows
                total += n
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(compute_one, name, ind_cls) for name, ind_cls in indicators.items()]
            for future in as_completed(futures):
                table, rows, n = future.result()
                if rows is not None and not rows.empty:
                    data[table] = rows
                    total += n

    if data:
        # 单次事务写入所有表，避免每表 commit
        writer.write_batch(data, interval)
    return total


def backfill_all(
    symbols: list[str] | None = None,
    intervals: list[str] | None = None,
    indicator_names: list[str] | None = None,
    *,
    use_all_indicators: bool = False,
    retention_map: dict[str, int] | None = None,
    bar_limit_map: dict[str, int] | None = None,
    fast: bool = False,
    fast_workers: int = 1,
    fast_fallback: str = "skip",
):
    """回填所有历史指标"""
    if symbols is None:
        symbols = get_high_priority_symbols_fast(top_n=50) or []
        if not symbols:
            print("无法获取币种列表")
            return
    symbols = [s.strip().upper() for s in symbols if str(s).strip()]

    if intervals is None:
        intervals = INTERVALS
    intervals = [iv.strip() for iv in intervals if str(iv).strip()]
    retention_map = retention_map or get_indicator_retention_map()
    bar_limit_map = bar_limit_map or _build_bar_limit_map(
        intervals,
        retention_map,
        base_limit=DEFAULT_BAR_LIMIT,
        overrides=None,
    )

    if indicator_names:
        # 允许显式指定增量指标进行回填
        indicators = get_all_indicators()
        indicators = {k: v for k, v in indicators.items() if k in indicator_names}
    elif use_all_indicators:
        indicators = get_all_indicators()
    else:
        indicators = get_batch_indicators()

    mode = "FAST" if fast else "SLOW"
    print(f"开始回填({mode}): {len(symbols)} 币种, {len(intervals)} 周期, {len(indicators)} 指标")
    print(f"保留配置: {retention_map}")
    print(f"K线读取上限: {bar_limit_map}")
    if fast and str(fast_fallback).strip().lower() == "broadcast":
        print("WARNING: --fast-fallback=broadcast 会把最后一行结果广播到历史K线，可能产生非真实历史值。")
    print("-" * 60)

    total_start = time.time()
    total_computed = 0

    for interval in intervals:
        retention = retention_map.get(interval, 60)
        bar_limit = bar_limit_map.get(interval, DEFAULT_BAR_LIMIT)
        print(f"\n[{interval}] 保留 {retention} 条 | 读取K线 {bar_limit} 条")

        for i, symbol in enumerate(symbols):
            t0 = time.time()
            if fast:
                computed = backfill_symbol_interval_fast(
                    symbol,
                    interval,
                    indicators,
                    retention,
                    bar_limit=bar_limit,
                    workers=fast_workers,
                    fallback=fast_fallback,
                )
            else:
                computed = backfill_symbol_interval(
                    symbol,
                    interval,
                    indicators,
                    retention,
                    bar_limit=bar_limit,
                )
            total_computed += computed

            if computed > 0:
                print(f"  {symbol}: {computed} 条, {time.time()-t0:.1f}s")

            # 进度
            if (i + 1) % 10 == 0:
                print(f"  进度: {i+1}/{len(symbols)}")

    print("-" * 60)
    print(f"完成! 总计 {total_computed} 条, 耗时 {time.time()-total_start:.1f}s")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="历史指标回填")
    parser.add_argument("-s", "--symbols", nargs="+", help="指定币种")
    parser.add_argument("-i", "--intervals", nargs="+", help="指定周期")
    parser.add_argument("-n", "--indicators", nargs="+", help="指定指标")
    parser.add_argument("--all-indicators", action="store_true", help="使用全部指标（含增量指标）")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="快速回填：尽量使用向量化计算 + 单次批量写入（未支持指标默认 broadcast 最后一行）",
    )
    parser.add_argument(
        "--fast-workers",
        type=int,
        default=1,
        help="fast 模式计算并行度（线程数，默认 1）",
    )
    parser.add_argument(
        "--fast-fallback",
        choices=["broadcast", "skip"],
        default="skip",
        help="fast 模式未支持指标的处理方式：broadcast 最后一行 / skip 跳过（默认 skip）",
    )
    parser.add_argument(
        "--retention-overrides",
        default="",
        help="覆盖保留条数，如: 1m=43200,5m=9000",
    )
    parser.add_argument(
        "--bar-limit",
        type=int,
        default=DEFAULT_BAR_LIMIT,
        help=f"每周期读取K线条数下限（默认 {DEFAULT_BAR_LIMIT}）",
    )
    parser.add_argument(
        "--bar-limit-overrides",
        default="",
        help="覆盖每周期读取K线上限，如: 1m=45000,5m=12000",
    )
    parser.add_argument("--top", type=int, default=50, help="高优先级币种数量")
    args = parser.parse_args()

    symbols = _normalize_list(args.symbols)
    if not symbols:
        symbols = get_high_priority_symbols_fast(top_n=args.top)

    intervals = _normalize_list(args.intervals) or INTERVALS

    retention_overrides = _parse_int_overrides(args.retention_overrides, INTERVALS)
    apply_indicator_retention_overrides(retention_overrides)
    retention_map = get_indicator_retention_map()

    bar_limit_overrides = _parse_int_overrides(args.bar_limit_overrides, intervals)
    bar_limit_map = _build_bar_limit_map(
        intervals,
        retention_map,
        base_limit=max(1, int(args.bar_limit)),
        overrides=bar_limit_overrides,
    )

    backfill_all(
        symbols=symbols,
        intervals=intervals,
        indicator_names=_normalize_list(args.indicators),
        use_all_indicators=bool(args.all_indicators),
        retention_map=retention_map,
        bar_limit_map=bar_limit_map,
        fast=bool(args.fast),
        fast_workers=max(1, int(args.fast_workers or 1)),
        fast_fallback=str(args.fast_fallback or "skip"),
    )
