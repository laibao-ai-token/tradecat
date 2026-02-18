"""Backtest models for Phase A (M1 minimal closed loop)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ExecutionConfig:
    """Trade execution knobs."""

    entry: str = "next_open"
    slippage_bps: float = 3.0
    # Default aligned to Binance USD-M futures VIP0 taker (0.04% per side).
    # Override via config/CLI if you use a different venue/VIP tier or maker orders.
    fee_bps: float = 4.0
    # Enable/disable directions (useful for biasing a strategy to short-only / long-only).
    allow_long: bool = True
    allow_short: bool = True
    # Exit smoothing to reduce churn on noisy 1m signals.
    # - min_hold_minutes: do not allow neutral-close before holding for at least N minutes.
    # - neutral_confirm_minutes: require N consecutive "neutral" buckets before neutral-close.
    #   (>=1 keeps backward-compatible behavior; 1 means immediate neutral-close.)
    min_hold_minutes: int = 0
    neutral_confirm_minutes: int = 1


@dataclass
class RiskConfig:
    """Risk model knobs for M1."""

    leverage: float = 2.0
    initial_equity: float = 10_000.0
    position_size_pct: float = 0.25


@dataclass
class AggregationConfig:
    """Signal-score aggregation thresholds."""

    long_open_threshold: int = 70
    short_open_threshold: int = 70
    close_threshold: int = 20


@dataclass
class WalkForwardConfig:
    """Reserved for M2 walk-forward verification."""

    train_days: int = 45
    test_days: int = 15
    step_days: int = 15


@dataclass
class RetentionConfig:
    """Run retention policy."""

    keep_runs: int = 30


@dataclass
class DateRange:
    """Backtest date range."""

    start: str = ""
    end: str = ""


@dataclass
class BacktestConfig:
    """Top-level config passed into runner."""

    market: str = "crypto"
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"])
    timeframe: str = "1m"
    # Optional strategy metadata for reporting/TUI.
    strategy_label: str = ""
    strategy_config_path: str = ""
    date_range: DateRange = field(default_factory=DateRange)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    aggregation: AggregationConfig = field(default_factory=AggregationConfig)
    walk_forward: WalkForwardConfig = field(default_factory=WalkForwardConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)


@dataclass(frozen=True)
class SignalEvent:
    """Historical signal event used by the backtest runner."""

    event_id: int
    timestamp: datetime
    symbol: str
    direction: str
    strength: int
    signal_type: str
    timeframe: str
    source: str
    price: float | None


@dataclass(frozen=True)
class Bar:
    """Single OHLCV bar."""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Position:
    """Open position state."""

    symbol: str
    side: str  # LONG or SHORT
    qty: float
    entry_ts: datetime
    entry_price: float
    entry_fee: float
    entry_score: int


@dataclass
class Trade:
    """Closed trade details."""

    symbol: str
    side: str
    entry_ts: datetime
    exit_ts: datetime
    entry_price: float
    exit_price: float
    qty: float
    entry_fee: float
    exit_fee: float
    pnl_gross: float
    pnl_net: float
    entry_score: int
    exit_score: int
    reason: str


@dataclass(frozen=True)
class SymbolContribution:
    """Per-symbol contribution summary."""

    symbol: str
    pnl_net: float
    trade_count: int
    win_rate_pct: float
    avg_holding_minutes: float


@dataclass(frozen=True)
class EquityPoint:
    """Equity point for plotting/reporting."""

    timestamp: datetime
    equity: float


@dataclass
class Metrics:
    """Backtest metrics for JSON/report output."""

    run_id: str
    mode: str
    start: str
    end: str
    symbols: list[str]
    timeframe: str
    initial_equity: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    trade_count: int
    win_rate_pct: float
    profit_factor: float
    avg_holding_minutes: float
    signal_count: int
    bar_count: int
    buy_hold_final_equity: float = 0.0
    buy_hold_return_pct: float = 0.0
    excess_return_pct: float = 0.0
    symbol_contributions: list[SymbolContribution] = field(default_factory=list)
    signal_type_counts: dict[str, int] = field(default_factory=dict)
    direction_counts: dict[str, int] = field(default_factory=dict)
    timeframe_counts: dict[str, int] = field(default_factory=dict)
    strategy_label: str = ""
    strategy_config_path: str = ""
    strategy_summary: str = ""
