#!/usr/bin/env python3
"""
ETF 自动驾驶选基策略 30天离线评估脚本

用法:
    python scripts/etf_backtest.py [--days 30] [--top-n 5]

输出:
    artifacts/analysis/etf_backtest_YYYYMMDD.json
    artifacts/analysis/etf_backtest_YYYYMMDD.md
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import logging
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Add src to path for imports
_REPO_ROOT = Path(__file__).parent.parent.resolve()
_TUI_ROOT = _REPO_ROOT / "services-preview" / "tui-service"
_TUI_SRC_ROOT = _TUI_ROOT / "src"
logger = logging.getLogger(__name__)


def _load_tui_modules():
    pkg_name = "tradecat_tui_src"
    if pkg_name not in sys.modules:
        init_file = _TUI_SRC_ROOT / "__init__.py"
        spec = importlib.util.spec_from_file_location(
            pkg_name,
            init_file,
            submodule_search_locations=[str(_TUI_SRC_ROOT)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载 TUI 包: {_TUI_SRC_ROOT}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[pkg_name] = module
        spec.loader.exec_module(module)

    etf_module = importlib.import_module(f"{pkg_name}.etf_profiles")
    quote_module = importlib.import_module(f"{pkg_name}.quote")
    return (
        getattr(etf_module, "ETFDomainProfile"),
        getattr(etf_module, "get_etf_domain_profile"),
        getattr(quote_module, "fetch_daily_curve_1d"),
    )


ETFDomainProfile, get_etf_domain_profile, fetch_daily_curve_1d = _load_tui_modules()


@dataclass(frozen=True)
class DailyCandle:
    """Simple candle for backtest."""
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class BacktestResult:
    """Backtest result container."""
    strategy_label: str = ""
    strategy_version: str = ""
    domain_key: str = ""
    start_date: str = ""
    end_date: str = ""
    days: int = 0
    total_candidates: int = 0
    valid_candidates: int = 0
    daily_results: list[dict[str, Any]] = field(default_factory=list)
    strategy_total_return: float = 0.0
    benchmark_total_return: float = 0.0
    strategy_avg_daily_return: float = 0.0
    benchmark_avg_daily_return: float = 0.0
    strategy_volatility: float = 0.0
    benchmark_volatility: float = 0.0
    strategy_sharpe: float = 0.0
    benchmark_sharpe: float = 0.0
    top_holdings: list[dict[str, Any]] = field(default_factory=list)


def _to_float(value: Any) -> float:
    """Safe float conversion."""
    try:
        return float(value)
    except Exception:
        return 0.0


def _safe_pct_change(base: float, current: float) -> float:
    """Calculate percentage change safely."""
    if abs(base) <= 1e-12:
        return 0.0
    return (current - base) / base


def _curve_volatility(closes: list[float]) -> float:
    """Calculate volatility from close prices."""
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
    return math.sqrt(max(0.0, sq)) * math.sqrt(252)  # Annualized


def _compound_total_return(returns: list[float]) -> float:
    """Compound periodic returns into a total return."""
    equity = 1.0
    for ret in returns:
        equity *= 1.0 + float(ret)
    return equity - 1.0


def _returns_std(returns: list[float]) -> float:
    """Population standard deviation of periodic returns."""
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((ret - mean) ** 2 for ret in returns) / len(returns)
    return math.sqrt(max(0.0, var))


def _normalize_scores(raw_map: dict[str, float]) -> dict[str, float]:
    """Normalize scores to 0-100 range."""
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


def fetch_fund_history(symbol: str, days: int = 35) -> list[DailyCandle]:
    """Fetch fund historical data for backtesting."""
    data = fetch_daily_curve_1d(
        provider="eastmoney",
        market="cn_fund",
        symbol=symbol,
        limit=days,
    )
    if not data:
        return []
    # data is list of (ts, open, high, low, close, volume)
    out = []
    for item in data:
        if len(item) >= 5:
            out.append(DailyCandle(
                ts=item[0],
                open=_to_float(item[1]),
                high=_to_float(item[2]),
                low=_to_float(item[3]),
                close=_to_float(item[4]),
                volume=_to_float(item[5]) if len(item) > 5 else 0.0,
            ))
    # Sort by timestamp
    out.sort(key=lambda x: x.ts)
    return out


def calculate_daily_features(candles: list[DailyCandle], as_of_idx: int) -> dict[str, float] | None:
    """Calculate features for a specific day (as_of_idx is the day to evaluate on)."""
    if as_of_idx < 5:
        return None
    
    # Use data up to as_of_idx (exclusive) for features, then evaluate on as_of_idx
    lookback_candles = candles[:as_of_idx + 1]
    if len(lookback_candles) < 5:
        return None
    
    closes = [c.close for c in lookback_candles if c.close > 0]
    if len(closes) < 5:
        return None
    
    # Trend: 30-day return
    first = closes[0]
    last = closes[-1]
    long_ret = _safe_pct_change(first, last)
    
    # Momentum: 5-day return
    short_base = closes[max(0, len(closes) - 5)]
    short_ret = _safe_pct_change(short_base, last)
    
    # Volume as liquidity proxy
    volumes = [c.volume for c in lookback_candles[-10:] if c.volume > 0]
    liquidity = math.log1p(sum(volumes) / max(1, len(volumes))) if volumes else 0.0
    
    # Risk: volatility
    vol = _curve_volatility(closes[-20:]) if len(closes) >= 20 else _curve_volatility(closes)
    
    return {
        "trend": long_ret,
        "momentum": short_ret,
        "liquidity": liquidity,
        "risk": -vol,  # Lower vol is better
    }


def rank_funds_by_strategy(
    features: dict[str, dict[str, float]],
    weights: dict[str, float],
) -> list[tuple[str, float]]:
    """Rank funds by strategy score."""
    if not features:
        return []
    
    # Normalize each feature
    trend_norm = _normalize_scores({s: f.get("trend", 0) for s, f in features.items()})
    momentum_norm = _normalize_scores({s: f.get("momentum", 0) for s, f in features.items()})
    liquidity_norm = _normalize_scores({s: f.get("liquidity", 0) for s, f in features.items()})
    risk_norm = _normalize_scores({s: f.get("risk", 0) for s, f in features.items()})
    
    scored = []
    for symbol, feat in features.items():
        trend = trend_norm.get(symbol, 50.0)
        momentum = momentum_norm.get(symbol, 50.0)
        liquidity = liquidity_norm.get(symbol, 50.0)
        risk = risk_norm.get(symbol, 50.0)
        
        total = (
            weights.get("trend", 0.35) * trend +
            weights.get("momentum", 0.25) * momentum +
            weights.get("liquidity", 0.20) * liquidity +
            weights.get("risk_adjusted", 0.20) * risk
        )
        scored.append((symbol, total))
    
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def run_backtest(
    symbols: list[str],
    days: int = 30,
    top_n: int = 5,
    profile: ETFDomainProfile | None = None,
) -> BacktestResult:
    """Run the backtest."""
    # Fetch history for all symbols
    logger.info("Fetching historical data for %d symbols...", len(symbols))
    history: dict[str, list[DailyCandle]] = {}
    for sym in symbols:
        hist = fetch_fund_history(sym, days=days + 10)  # Extra buffer
        if hist:
            history[sym] = hist
            logger.info("  %s: %d days", sym, len(hist))

    if not history:
        logger.error("No historical data fetched!")
        return BacktestResult()
    
    # Determine date range
    all_dates: set[int] = set()
    for hist in history.values():
        for c in hist:
            all_dates.add(c.ts)
    sorted_dates = sorted(all_dates)
    
    # Use last N trading days
    trading_days = sorted_dates[-days:] if len(sorted_dates) >= days else sorted_dates
    if len(trading_days) < 10:
        logger.error("Not enough trading days: %d", len(trading_days))
        return BacktestResult()
    
    start_date = datetime.fromtimestamp(trading_days[0]).strftime("%Y-%m-%d")
    end_date = datetime.fromtimestamp(trading_days[-1]).strftime("%Y-%m-%d")
    
    logger.info("Backtest period: %s to %s (%d days)", start_date, end_date, len(trading_days))
    
    active_profile = profile or get_etf_domain_profile("auto_driving_cn")

    # Weights from active profile
    weights = {
        "trend": active_profile.weights.trend,
        "momentum": active_profile.weights.momentum,
        "liquidity": active_profile.weights.liquidity,
        "risk_adjusted": active_profile.weights.risk_adjusted,
    }
    
    # Daily results
    daily_results: list[dict[str, Any]] = []
    strategy_returns: list[float] = []
    benchmark_returns: list[float] = []
    
    # Find common trading days with sufficient history
    valid_days = []
    for day_ts in trading_days:
        # Find index of this day in each fund's history
        features: dict[str, dict[str, float]] = {}
        for sym, hist in history.items():
            # Find the candle for this day
            for idx, c in enumerate(hist):
                if c.ts == day_ts:
                    feat = calculate_daily_features(hist, idx)
                    if feat:
                        features[sym] = feat
                    break
        
        if len(features) >= 3:  # Need at least 3 funds
            valid_days.append((day_ts, features))
    
    logger.info("Valid evaluation days: %d", len(valid_days))
    
    # Process each day
    for day_ts, features in valid_days:
        day_str = datetime.fromtimestamp(day_ts).strftime("%Y-%m-%d")
        
        # Get rankings
        ranked = rank_funds_by_strategy(features, weights)
        top_symbols = [s for s, _ in ranked[:top_n]]
        
        # Calculate next-day returns for strategy
        strategy_day_returns = []
        for sym in top_symbols:
            hist = history.get(sym)
            if not hist:
                continue
            # Find current day and next day
            for idx, c in enumerate(hist):
                if c.ts == day_ts:
                    if idx + 1 < len(hist):
                        ret = _safe_pct_change(c.close, hist[idx + 1].close)
                        strategy_day_returns.append(ret)
                    break
        
        # Calculate next-day returns for equal-weight benchmark (all valid funds)
        benchmark_day_returns = []
        for sym in features.keys():
            hist = history.get(sym)
            if not hist:
                continue
            for idx, c in enumerate(hist):
                if c.ts == day_ts:
                    if idx + 1 < len(hist):
                        ret = _safe_pct_change(c.close, hist[idx + 1].close)
                        benchmark_day_returns.append(ret)
                    break
        
        if not strategy_day_returns or not benchmark_day_returns:
            continue
        
        strat_ret = sum(strategy_day_returns) / len(strategy_day_returns)
        bench_ret = sum(benchmark_day_returns) / len(benchmark_day_returns)
        
        strategy_returns.append(strat_ret)
        benchmark_returns.append(bench_ret)
        
        daily_results.append({
            "date": day_str,
            "top_symbols": top_symbols,
            "strategy_return": round(strat_ret * 100, 4),
            "benchmark_return": round(bench_ret * 100, 4),
        })
    
    # Calculate metrics
    if not strategy_returns:
        logger.error("No valid returns calculated!")
        return BacktestResult()
    
    # Total returns (compound, not sum)
    strat_total = _compound_total_return(strategy_returns)
    bench_total = _compound_total_return(benchmark_returns)
    
    # Average daily returns
    strat_avg = sum(strategy_returns) / len(strategy_returns)
    bench_avg = sum(benchmark_returns) / len(benchmark_returns)
    
    # Volatility (annualized from return distribution)
    strat_daily_std = _returns_std(strategy_returns)
    bench_daily_std = _returns_std(benchmark_returns)
    strat_vol = strat_daily_std * math.sqrt(252)
    bench_vol = bench_daily_std * math.sqrt(252)

    # Sharpe (assuming 0% risk-free rate)
    strat_sharpe = (strat_avg / strat_daily_std) * math.sqrt(252) if strat_daily_std > 0 else 0.0
    bench_sharpe = (bench_avg / bench_daily_std) * math.sqrt(252) if bench_daily_std > 0 else 0.0
    
    # Top holdings count
    holding_counts: dict[str, int] = {}
    for dr in daily_results:
        for sym in dr.get("top_symbols", []):
            holding_counts[sym] = holding_counts.get(sym, 0) + 1
    
    top_holdings = sorted(holding_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    top_holdings_list = [{"symbol": s, "days_in_top": c} for s, c in top_holdings]
    
    result = BacktestResult(
        strategy_label="ETF-AUTO-V1",
        strategy_version="v1.0.0",
        domain_key=active_profile.key,
        start_date=start_date,
        end_date=end_date,
        days=len(valid_days),
        total_candidates=len(symbols),
        valid_candidates=len(history),
        daily_results=daily_results,
        strategy_total_return=strat_total,
        benchmark_total_return=bench_total,
        strategy_avg_daily_return=strat_avg,
        benchmark_avg_daily_return=bench_avg,
        strategy_volatility=strat_vol,
        benchmark_volatility=bench_vol,
        strategy_sharpe=strat_sharpe,
        benchmark_sharpe=bench_sharpe,
        top_holdings=top_holdings_list,
    )
    
    return result


def format_markdown(result: BacktestResult) -> str:
    """Format result as markdown report."""
    md = f"""# ETF 自动驾驶选基策略 30天离线评估报告

## 基本信息

| 项目 | 值 |
|------|-----|
| 策略标签 | {result.strategy_label} |
| 策略版本 | {result.strategy_version} |
| 领域 | {result.domain_key} |
| 评估区间 | {result.start_date} ~ {result.end_date} |
| 评估天数 | {result.days} 天 |
| 候选池数量 | {result.total_candidates} |
| 有效数据 | {result.valid_candidates} |

## 收益对比

| 指标 | 策略 (ETF-AUTO-V1) | 等权基准 |
|------|-------------------|----------|
| 累计收益率 | {result.strategy_total_return * 100:.2f}% | {result.benchmark_total_return * 100:.2f}% |
| 日均收益率 | {result.strategy_avg_daily_return * 100:.4f}% | {result.benchmark_avg_daily_return * 100:.4f}% |
| 年化波动率 | {result.strategy_volatility * 100:.2f}% | {result.benchmark_volatility * 100:.2f}% |
| 夏普比率 | {result.strategy_sharpe:.4f} | {result.benchmark_sharpe:.4f} |

## 持仓统计

Top 10 持仓天数:

| 排名 | 代码 | 入选天数 |
|------|------|----------|
"""
    for i, h in enumerate(result.top_holdings, 1):
        md += f"| {i} | {h['symbol']} | {h['days_in_top']} |\n"
    
    md += """
## 每日明细

| 日期 | TopN 持仓 | 策略收益 | 基准收益 |
|------|-----------|----------|----------|
"""
    for dr in result.daily_results[-15:]:  # Last 15 days
        symbols_str = ", ".join(dr["top_symbols"][:3])
        md += f"| {dr['date']} | {symbols_str}... | {dr['strategy_return']:.2f}% | {dr['benchmark_return']:.2f}% |\n"
    
    md += f"""
---
*报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""
    return md


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    parser = argparse.ArgumentParser(description="ETF 自动驾驶选基 30天离线评估")
    parser.add_argument("--domain", type=str, default="auto_driving_cn", help="领域key（默认 auto_driving_cn）")
    parser.add_argument("--days", type=int, default=30, help="评估天数")
    parser.add_argument("--top-n", type=int, default=0, help="选基数量（0=使用领域配置）")
    args = parser.parse_args()

    profile = get_etf_domain_profile(str(args.domain).strip().lower())
    symbols = list(profile.symbols)
    top_n = args.top_n if int(args.top_n) > 0 else max(1, int(profile.top_n))

    logger.info("领域: %s (%s)", profile.key, profile.label)
    logger.info("候选池: %s", symbols)
    logger.info("TopN: %d", top_n)

    # Run backtest
    result = run_backtest(symbols, days=args.days, top_n=top_n, profile=profile)
    
    if not result.daily_results:
        logger.error("评估失败：无有效数据")
        sys.exit(1)
    
    # Output paths
    ts = datetime.now().strftime("%Y%m%d")
    json_path = _REPO_ROOT / "artifacts" / "analysis" / f"etf_backtest_{ts}.json"
    md_path = _REPO_ROOT / "artifacts" / "analysis" / f"etf_backtest_{ts}.md"
    
    # Ensure directory exists
    json_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Save JSON
    json_data = {
        "strategy_label": result.strategy_label,
        "strategy_version": result.strategy_version,
        "domain_key": result.domain_key,
        "start_date": result.start_date,
        "end_date": result.end_date,
        "days": result.days,
        "total_candidates": result.total_candidates,
        "valid_candidates": result.valid_candidates,
        "metrics": {
            "strategy_total_return": result.strategy_total_return,
            "benchmark_total_return": result.benchmark_total_return,
            "strategy_avg_daily_return": result.strategy_avg_daily_return,
            "benchmark_avg_daily_return": result.benchmark_avg_daily_return,
            "strategy_volatility": result.strategy_volatility,
            "benchmark_volatility": result.benchmark_volatility,
            "strategy_sharpe": result.strategy_sharpe,
            "benchmark_sharpe": result.benchmark_sharpe,
        },
        "top_holdings": result.top_holdings,
        "daily_results": result.daily_results,
    }
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    logger.info("JSON 报告: %s", json_path)
    
    # Save Markdown
    md_content = format_markdown(result)
    with md_path.open("w", encoding="utf-8") as f:
        f.write(md_content)
    logger.info("Markdown 报告: %s", md_path)
    
    # Print summary
    logger.info("=" * 50)
    logger.info("评估结果摘要")
    logger.info("=" * 50)
    logger.info("策略累计收益: %.2f%%", result.strategy_total_return * 100)
    logger.info("基准累计收益: %.2f%%", result.benchmark_total_return * 100)
    logger.info("策略夏普比率: %.4f", result.strategy_sharpe)
    logger.info("基准夏普比率: %.4f", result.benchmark_sharpe)
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
