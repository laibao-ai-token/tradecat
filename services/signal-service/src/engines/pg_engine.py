"""
基于 TimescaleDB 的信号检测引擎
直接从 PostgreSQL 读取 candles_1m 和 binance_futures_metrics_5m 数据

解耦改进：
- 移除 from bot.app import I18N 依赖
- i18n 改为返回 key + params，由消费端翻译
"""

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

try:
    from ..config import COOLDOWN_SECONDS, DATA_MAX_AGE_SECONDS, get_database_url
    from ..events import SignalEvent, SignalPublisher
    from ..storage.cooldown import get_cooldown_storage
    from ..storage.history import get_history
except ImportError:
    from config import COOLDOWN_SECONDS, DATA_MAX_AGE_SECONDS, get_database_url
    from events import SignalEvent, SignalPublisher
    from storage.cooldown import get_cooldown_storage
    from storage.history import get_history

from .base import BaseEngine

logger = logging.getLogger(__name__)


@dataclass
class PGSignal:
    """基于 PG 数据的信号"""

    symbol: str
    signal_type: str
    direction: str  # BUY/SELL/ALERT
    strength: int  # 0-100
    message_key: str  # i18n key（由消费端翻译）
    message_params: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    timeframe: str = "5m"
    price: float = 0.0
    extra: dict = field(default_factory=dict)


# 符号白名单正则：仅允许大写字母、数字、下划线，长度2-20
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9_]{2,20}$")


def _safe_float(val, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _validate_symbols(symbols: list[str]) -> list[str]:
    """校验并过滤符号列表，防止SQL注入"""
    validated = []
    for s in symbols:
        if isinstance(s, str) and _SYMBOL_PATTERN.match(s):
            validated.append(s)
        else:
            logger.warning(f"Invalid symbol rejected: {s!r}")
    return validated


# 美股符号白名单（允许点/中横线，如 BRK.B / RDS-A）
_US_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,15}$")


def _normalize_us_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    # 兼容常见后缀写法：AAPL.US -> AAPL
    if s.endswith('.US') and len(s) > 3:
        s = s[:-3]
    return s


def _validate_us_symbols(symbols: list[str]) -> list[str]:
    validated: list[str] = []
    for raw in symbols:
        s = _normalize_us_symbol(raw)
        if s and _US_SYMBOL_PATTERN.match(s):
            validated.append(s)
        else:
            logger.warning(f"Invalid US symbol rejected: {raw!r}")
    return validated


def _get_us_symbols() -> list[str]:
    """
    从配置读取美股监控列表（可为空）

    读取顺序：
    - SIGNAL_US_SYMBOLS（环境变量）
    - config/.env 中 SIGNAL_US_SYMBOLS
    """
    env = _load_env_file()
    raw = os.environ.get("SIGNAL_US_SYMBOLS", env.get("SIGNAL_US_SYMBOLS", "")).strip()
    if not raw:
        return []
    syms = [x.strip() for x in raw.split(",") if x.strip()]
    return _validate_us_symbols(syms)


def _load_env_file() -> dict:
    """加载 config/.env 文件"""
    from pathlib import Path

    # 查找 config/.env
    current = Path(__file__).resolve()
    for _ in range(6):  # 最多向上查找 6 层
        current = current.parent
        env_file = current / "config" / ".env"
        if env_file.exists():
            result = {}
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    result[k.strip()] = v.strip().strip("\"'")
            return result
    return {}


def _get_default_symbols() -> list[str]:
    """
    从全局配置获取监控币种

    读取 config/.env 中的配置：
    - SIGNAL_SYMBOLS: 直接指定（优先级最高）
    - SYMBOLS_GROUPS + SYMBOLS_GROUP_*: 分组配置
    - SYMBOLS_EXTRA / SYMBOLS_EXCLUDE: 额外添加/排除
    """
    import os

    env = _load_env_file()

    # 优先读取 SIGNAL_SYMBOLS（signal-service 专用）
    direct = os.environ.get("SIGNAL_SYMBOLS", env.get("SIGNAL_SYMBOLS", "")).strip()
    if direct:
        symbols = [s.strip().upper() for s in direct.split(",") if s.strip()]
        if symbols:
            return _validate_symbols(symbols)

    # 将 .env 中的 SYMBOLS_* 注入环境，避免未 source 时读取不到
    for key, val in env.items():
        if key.startswith("SYMBOLS_") and key not in os.environ:
            os.environ[key] = val

    # 与全局符号选择逻辑保持一致（支持 all/auto）
    try:
        import sys
        from pathlib import Path

        libs_path = str(Path(__file__).parents[4] / "libs")
        if libs_path not in sys.path:
            sys.path.insert(0, libs_path)
        from common.symbols import get_configured_symbols
    except Exception:
        get_configured_symbols = None

    if get_configured_symbols:
        configured = get_configured_symbols()
        if configured:
            return _validate_symbols(configured)

    # 兜底：保持原默认
    return _DEFAULT_SYMBOLS


# 默认币种（main4）
_DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]


class PGSignalRules:
    """基于 PG 数据的信号规则集（解耦版：返回 i18n key）"""

    def check_price_surge(self, curr: dict, prev: dict, threshold_pct: float = 3.0) -> PGSignal | None:
        """价格急涨信号"""
        if not prev or not curr:
            return None
        try:
            curr_close = _safe_float(curr.get("close", 0))
            prev_close = _safe_float(prev.get("close", 0))
            if prev_close == 0:
                return None
            change_pct = (curr_close - prev_close) / prev_close * 100
            if change_pct >= threshold_pct:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="price_surge",
                    direction="BUY",
                    strength=min(90, int(50 + change_pct * 10)),
                    message_key="signal.pg.msg.price_surge",
                    message_params={"pct": f"{change_pct:.2f}"},
                    price=curr_close,
                    extra={"change_pct": change_pct},
                )
        except Exception as e:
            logger.warning(f"check_price_surge error: {e}")
        return None

    def check_price_dump(self, curr: dict, prev: dict, threshold_pct: float = 3.0) -> PGSignal | None:
        """价格急跌信号"""
        if not prev or not curr:
            return None
        try:
            curr_close = _safe_float(curr.get("close", 0))
            prev_close = _safe_float(prev.get("close", 0))
            if prev_close == 0:
                return None
            change_pct = (curr_close - prev_close) / prev_close * 100
            if change_pct <= -threshold_pct:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="price_dump",
                    direction="SELL",
                    strength=min(90, int(50 + abs(change_pct) * 10)),
                    message_key="signal.pg.msg.price_dump",
                    message_params={"pct": f"{abs(change_pct):.2f}"},
                    price=curr_close,
                    extra={"change_pct": change_pct},
                )
        except Exception as e:
            logger.warning(f"check_price_dump error: {e}")
        return None

    def check_volume_spike(self, curr: dict, prev: dict, multiplier: float = 5.0) -> PGSignal | None:
        """成交量异常放大信号"""
        if not prev or not curr:
            return None
        try:
            curr_vol = _safe_float(curr.get("quote_volume", 0))
            prev_vol = _safe_float(prev.get("quote_volume", 0))
            if prev_vol == 0:
                return None
            vol_ratio = curr_vol / prev_vol
            if vol_ratio >= multiplier:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="volume_spike",
                    direction="ALERT",
                    strength=min(85, int(50 + vol_ratio * 5)),
                    message_key="signal.pg.msg.volume_spike",
                    message_params={"ratio": f"{vol_ratio:.1f}", "vol": f"{curr_vol / 1e6:.2f}"},
                    price=_safe_float(curr.get("close", 0)),
                    extra={"vol_ratio": vol_ratio, "quote_volume": curr_vol},
                )
        except Exception as e:
            logger.warning(f"check_volume_spike error: {e}")
        return None

    def check_breakout_up(self, curr: dict, prev: dict, threshold_pct: float = 0.15) -> PGSignal | None:
        """向上突破前一根K线高点"""
        if not prev or not curr:
            return None
        try:
            prev_high = _safe_float(prev.get("high", 0))
            curr_close = _safe_float(curr.get("close", 0))
            if prev_high <= 0:
                return None
            breakout_pct = (curr_close - prev_high) / prev_high * 100
            if breakout_pct >= threshold_pct:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="breakout_up",
                    direction="BUY",
                    strength=min(88, int(60 + breakout_pct * 12)),
                    message_key="signal.pg.msg.breakout_up",
                    message_params={"pct": f"{breakout_pct:.2f}", "base": f"{prev_high:.2f}"},
                    timeframe="1m",
                    price=curr_close,
                    extra={"breakout_pct": breakout_pct, "prev_high": prev_high},
                )
        except Exception as e:
            logger.warning(f"check_breakout_up error: {e}")
        return None

    def check_breakout_down(self, curr: dict, prev: dict, threshold_pct: float = 0.15) -> PGSignal | None:
        """向下跌破前一根K线低点"""
        if not prev or not curr:
            return None
        try:
            prev_low = _safe_float(prev.get("low", 0))
            curr_close = _safe_float(curr.get("close", 0))
            if prev_low <= 0:
                return None
            breakdown_pct = (prev_low - curr_close) / prev_low * 100
            if breakdown_pct >= threshold_pct:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="breakout_down",
                    direction="SELL",
                    strength=min(88, int(60 + breakdown_pct * 12)),
                    message_key="signal.pg.msg.breakout_down",
                    message_params={"pct": f"{breakdown_pct:.2f}", "base": f"{prev_low:.2f}"},
                    timeframe="1m",
                    price=curr_close,
                    extra={"breakdown_pct": breakdown_pct, "prev_low": prev_low},
                )
        except Exception as e:
            logger.warning(f"check_breakout_down error: {e}")
        return None

    def check_gap_up(self, curr: dict, prev: dict, threshold_pct: float = 0.35) -> PGSignal | None:
        """跳空高开"""
        if not prev or not curr:
            return None
        try:
            prev_close = _safe_float(prev.get("close", 0))
            curr_open = _safe_float(curr.get("open", 0))
            if prev_close <= 0:
                return None
            gap_pct = (curr_open - prev_close) / prev_close * 100
            if gap_pct >= threshold_pct:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="gap_up",
                    direction="BUY",
                    strength=min(82, int(58 + gap_pct * 10)),
                    message_key="signal.pg.msg.gap_up",
                    message_params={"pct": f"{gap_pct:.2f}"},
                    timeframe="1m",
                    price=_safe_float(curr.get("close", 0)),
                    extra={"gap_pct": gap_pct},
                )
        except Exception as e:
            logger.warning(f"check_gap_up error: {e}")
        return None

    def check_gap_down(self, curr: dict, prev: dict, threshold_pct: float = 0.35) -> PGSignal | None:
        """跳空低开"""
        if not prev or not curr:
            return None
        try:
            prev_close = _safe_float(prev.get("close", 0))
            curr_open = _safe_float(curr.get("open", 0))
            if prev_close <= 0:
                return None
            gap_pct = (prev_close - curr_open) / prev_close * 100
            if gap_pct >= threshold_pct:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="gap_down",
                    direction="SELL",
                    strength=min(82, int(58 + gap_pct * 10)),
                    message_key="signal.pg.msg.gap_down",
                    message_params={"pct": f"{gap_pct:.2f}"},
                    timeframe="1m",
                    price=_safe_float(curr.get("close", 0)),
                    extra={"gap_pct": gap_pct},
                )
        except Exception as e:
            logger.warning(f"check_gap_down error: {e}")
        return None

    def check_bullish_engulfing(self, curr: dict, prev: dict) -> PGSignal | None:
        """看涨吞没（两根K线形态）"""
        if not prev or not curr:
            return None
        try:
            prev_open = _safe_float(prev.get("open", 0))
            prev_close = _safe_float(prev.get("close", 0))
            curr_open = _safe_float(curr.get("open", 0))
            curr_close = _safe_float(curr.get("close", 0))
            prev_body = abs(prev_close - prev_open)
            curr_body = abs(curr_close - curr_open)
            if prev_body == 0 or curr_body == 0:
                return None
            is_bullish_engulfing = (
                prev_close < prev_open
                and curr_close > curr_open
                and curr_open <= prev_close
                and curr_close >= prev_open
                and curr_body >= prev_body * 1.05
            )
            if is_bullish_engulfing:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="bullish_engulfing",
                    direction="BUY",
                    strength=74,
                    message_key="signal.pg.msg.bullish_engulfing",
                    timeframe="1m",
                    price=curr_close,
                    extra={"body_ratio": curr_body / prev_body},
                )
        except Exception as e:
            logger.warning(f"check_bullish_engulfing error: {e}")
        return None

    def check_bearish_engulfing(self, curr: dict, prev: dict) -> PGSignal | None:
        """看跌吞没（两根K线形态）"""
        if not prev or not curr:
            return None
        try:
            prev_open = _safe_float(prev.get("open", 0))
            prev_close = _safe_float(prev.get("close", 0))
            curr_open = _safe_float(curr.get("open", 0))
            curr_close = _safe_float(curr.get("close", 0))
            prev_body = abs(prev_close - prev_open)
            curr_body = abs(curr_close - curr_open)
            if prev_body == 0 or curr_body == 0:
                return None
            is_bearish_engulfing = (
                prev_close > prev_open
                and curr_close < curr_open
                and curr_open >= prev_close
                and curr_close <= prev_open
                and curr_body >= prev_body * 1.05
            )
            if is_bearish_engulfing:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="bearish_engulfing",
                    direction="SELL",
                    strength=74,
                    message_key="signal.pg.msg.bearish_engulfing",
                    timeframe="1m",
                    price=curr_close,
                    extra={"body_ratio": curr_body / prev_body},
                )
        except Exception as e:
            logger.warning(f"check_bearish_engulfing error: {e}")
        return None

    def check_doji(self, curr: dict, max_body_ratio: float = 0.12) -> PGSignal | None:
        """十字星（实体占比很小）"""
        if not curr:
            return None
        try:
            high = _safe_float(curr.get("high", 0))
            low = _safe_float(curr.get("low", 0))
            open_px = _safe_float(curr.get("open", 0))
            close_px = _safe_float(curr.get("close", 0))
            candle_range = high - low
            if candle_range <= 0:
                return None
            body_ratio = abs(close_px - open_px) / candle_range
            if body_ratio <= max_body_ratio:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="doji",
                    direction="ALERT",
                    strength=58,
                    message_key="signal.pg.msg.doji",
                    message_params={"ratio": f"{body_ratio:.2f}"},
                    timeframe="1m",
                    price=close_px,
                    extra={"body_ratio": body_ratio},
                )
        except Exception as e:
            logger.warning(f"check_doji error: {e}")
        return None

    def check_range_expansion(self, curr: dict, prev: dict, multiplier: float = 1.8) -> PGSignal | None:
        """波动区间放大"""
        if not prev or not curr:
            return None
        try:
            curr_high = _safe_float(curr.get("high", 0))
            curr_low = _safe_float(curr.get("low", 0))
            prev_high = _safe_float(prev.get("high", 0))
            prev_low = _safe_float(prev.get("low", 0))
            curr_close = _safe_float(curr.get("close", 0))
            curr_open = _safe_float(curr.get("open", 0))
            curr_range = curr_high - curr_low
            prev_range = prev_high - prev_low
            if curr_range <= 0 or prev_range <= 0:
                return None
            ratio = curr_range / prev_range
            if ratio >= multiplier:
                direction = "BUY" if curr_close >= curr_open else "SELL"
                signal_type = "range_expansion_up" if direction == "BUY" else "range_expansion_down"
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type=signal_type,
                    direction=direction,
                    strength=min(84, int(58 + ratio * 10)),
                    message_key="signal.pg.msg.range_expansion",
                    message_params={"ratio": f"{ratio:.2f}"},
                    timeframe="1m",
                    price=curr_close,
                    extra={"range_ratio": ratio},
                )
        except Exception as e:
            logger.warning(f"check_range_expansion error: {e}")
        return None

    @staticmethod
    def _ema_series(values: list[float], period: int) -> list[float]:
        """计算 EMA 序列。"""
        if period <= 0 or not values:
            return []
        alpha = 2.0 / (period + 1)
        ema: list[float] = [values[0]]
        for val in values[1:]:
            ema.append(alpha * val + (1.0 - alpha) * ema[-1])
        return ema

    @staticmethod
    def _compute_rsi(closes: list[float], period: int = 14) -> list[float]:
        """计算 RSI 序列（Wilder 平滑），返回长度与 closes 一致。"""
        if period <= 0 or len(closes) <= period:
            return []

        rsi_values = [50.0] * len(closes)
        gains: list[float] = []
        losses: list[float] = []
        for idx in range(1, period + 1):
            delta = closes[idx] - closes[idx - 1]
            gains.append(max(delta, 0.0))
            losses.append(max(-delta, 0.0))

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            rsi_values[period] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_values[period] = 100.0 - 100.0 / (1.0 + rs)

        for idx in range(period + 1, len(closes)):
            delta = closes[idx] - closes[idx - 1]
            gain = max(delta, 0.0)
            loss = max(-delta, 0.0)
            avg_gain = ((avg_gain * (period - 1)) + gain) / period
            avg_loss = ((avg_loss * (period - 1)) + loss) / period
            if avg_loss == 0:
                rsi_values[idx] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi_values[idx] = 100.0 - 100.0 / (1.0 + rs)

        return rsi_values

    def check_us_ema_cross(
        self, recent: list[dict], fast: int = 9, slow: int = 21, min_spread_pct: float = 0.04
    ) -> PGSignal | None:
        """美股 EMA 金叉/死叉。"""
        if not recent or len(recent) < slow + 2:
            return None
        try:
            closes = [_safe_float(c.get("close", 0.0)) for c in recent]
            if any(v <= 0 for v in closes[-(slow + 2) :]):
                return None

            ema_fast = self._ema_series(closes, fast)
            ema_slow = self._ema_series(closes, slow)
            if len(ema_fast) < 2 or len(ema_slow) < 2:
                return None

            prev_fast, curr_fast = ema_fast[-2], ema_fast[-1]
            prev_slow, curr_slow = ema_slow[-2], ema_slow[-1]
            curr_close = closes[-1]
            base = curr_slow if curr_slow > 0 else curr_close
            if base <= 0:
                return None

            if prev_fast <= prev_slow and curr_fast > curr_slow:
                spread_pct = (curr_fast - curr_slow) / base * 100.0
                if spread_pct >= min_spread_pct:
                    return PGSignal(
                        symbol=recent[-1].get("symbol", ""),
                        signal_type="us_ema_cross_up",
                        direction="BUY",
                        strength=min(86, int(62 + spread_pct * 90)),
                        message_key="signal.pg.msg.us_ema_cross_up",
                        message_params={"fast": str(fast), "slow": str(slow), "spread": f"{spread_pct:.2f}"},
                        timeframe="1m",
                        price=curr_close,
                        extra={"ema_fast": curr_fast, "ema_slow": curr_slow, "spread_pct": spread_pct},
                    )

            if prev_fast >= prev_slow and curr_fast < curr_slow:
                spread_pct = (curr_slow - curr_fast) / base * 100.0
                if spread_pct >= min_spread_pct:
                    return PGSignal(
                        symbol=recent[-1].get("symbol", ""),
                        signal_type="us_ema_cross_down",
                        direction="SELL",
                        strength=min(86, int(62 + spread_pct * 90)),
                        message_key="signal.pg.msg.us_ema_cross_down",
                        message_params={"fast": str(fast), "slow": str(slow), "spread": f"{spread_pct:.2f}"},
                        timeframe="1m",
                        price=curr_close,
                        extra={"ema_fast": curr_fast, "ema_slow": curr_slow, "spread_pct": spread_pct},
                    )
        except Exception as e:
            logger.warning(f"check_us_ema_cross error: {e}")
        return None

    def check_us_rsi_reversal(
        self, recent: list[dict], period: int = 14, oversold: float = 30.0, overbought: float = 70.0
    ) -> PGSignal | None:
        """美股 RSI 超卖反弹/超买回落。"""
        if not recent or len(recent) < period + 2:
            return None
        try:
            closes = [_safe_float(c.get("close", 0.0)) for c in recent]
            if any(v <= 0 for v in closes[-(period + 2) :]):
                return None

            rsi_values = self._compute_rsi(closes, period=period)
            if len(rsi_values) < 2:
                return None

            prev_rsi = rsi_values[-2]
            curr_rsi = rsi_values[-1]
            curr_close = closes[-1]

            if prev_rsi <= oversold < curr_rsi:
                strength = min(82, int(60 + (curr_rsi - oversold) * 1.6))
                return PGSignal(
                    symbol=recent[-1].get("symbol", ""),
                    signal_type="us_rsi_rebound",
                    direction="BUY",
                    strength=strength,
                    message_key="signal.pg.msg.us_rsi_rebound",
                    message_params={"rsi": f"{curr_rsi:.1f}", "th": f"{oversold:.0f}"},
                    timeframe="1m",
                    price=curr_close,
                    extra={"rsi": curr_rsi, "period": period},
                )

            if prev_rsi >= overbought > curr_rsi:
                strength = min(82, int(60 + (overbought - curr_rsi) * 1.6))
                return PGSignal(
                    symbol=recent[-1].get("symbol", ""),
                    signal_type="us_rsi_fade",
                    direction="SELL",
                    strength=strength,
                    message_key="signal.pg.msg.us_rsi_fade",
                    message_params={"rsi": f"{curr_rsi:.1f}", "th": f"{overbought:.0f}"},
                    timeframe="1m",
                    price=curr_close,
                    extra={"rsi": curr_rsi, "period": period},
                )
        except Exception as e:
            logger.warning(f"check_us_rsi_reversal error: {e}")
        return None

    def check_us_range_breakout(
        self,
        recent: list[dict],
        lookback: int = 20,
        vol_multiplier: float = 1.8,
        breakout_buffer_pct: float = 0.12,
    ) -> PGSignal | None:
        """美股区间突破（含量能确认）。"""
        if not recent or len(recent) < lookback + 1:
            return None
        try:
            history = recent[-(lookback + 1) : -1]
            curr = recent[-1]
            curr_close = _safe_float(curr.get("close", 0.0))
            if curr_close <= 0:
                return None

            highs = [_safe_float(c.get("high", 0.0)) for c in history]
            lows = [_safe_float(c.get("low", 0.0)) for c in history]
            vols = [_safe_float(c.get("volume", 0.0)) for c in history]
            prev_high = max((v for v in highs if v > 0), default=0.0)
            prev_low = min((v for v in lows if v > 0), default=0.0)
            avg_vol = sum(v for v in vols if v > 0) / max(1, len([v for v in vols if v > 0]))
            curr_vol = _safe_float(curr.get("volume", 0.0))
            if avg_vol <= 0 or curr_vol <= 0 or prev_high <= 0 or prev_low <= 0:
                return None

            vol_ratio = curr_vol / avg_vol
            up_pct = (curr_close - prev_high) / prev_high * 100.0
            down_pct = (prev_low - curr_close) / prev_low * 100.0

            if up_pct >= breakout_buffer_pct and vol_ratio >= vol_multiplier:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="us_range_breakout_up",
                    direction="BUY",
                    strength=min(88, int(64 + up_pct * 20 + vol_ratio * 4)),
                    message_key="signal.pg.msg.us_range_breakout_up",
                    message_params={"pct": f"{up_pct:.2f}", "vol": f"{vol_ratio:.1f}"},
                    timeframe="1m",
                    price=curr_close,
                    extra={"breakout_pct": up_pct, "vol_ratio": vol_ratio, "lookback": lookback},
                )

            if down_pct >= breakout_buffer_pct and vol_ratio >= vol_multiplier:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="us_range_breakout_down",
                    direction="SELL",
                    strength=min(88, int(64 + down_pct * 20 + vol_ratio * 4)),
                    message_key="signal.pg.msg.us_range_breakout_down",
                    message_params={"pct": f"{down_pct:.2f}", "vol": f"{vol_ratio:.1f}"},
                    timeframe="1m",
                    price=curr_close,
                    extra={"breakout_pct": down_pct, "vol_ratio": vol_ratio, "lookback": lookback},
                )
        except Exception as e:
            logger.warning(f"check_us_range_breakout error: {e}")
        return None

    def check_us_wick_reversal(
        self,
        recent: list[dict],
        wick_ratio: float = 0.55,
        min_range_pct: float = 0.35,
        vol_multiplier: float = 1.5,
    ) -> PGSignal | None:
        """美股长影线反转（锤子线/流星线，带量能过滤）。"""
        if not recent or len(recent) < 12:
            return None
        try:
            curr = recent[-1]
            prev_part = recent[-11:-1]

            high = _safe_float(curr.get("high", 0.0))
            low = _safe_float(curr.get("low", 0.0))
            open_px = _safe_float(curr.get("open", 0.0))
            close_px = _safe_float(curr.get("close", 0.0))
            curr_vol = _safe_float(curr.get("volume", 0.0))
            candle_range = high - low
            if candle_range <= 0 or close_px <= 0:
                return None

            range_pct = candle_range / close_px * 100.0
            prev_vols = [_safe_float(c.get("volume", 0.0)) for c in prev_part]
            pos_prev_vols = [v for v in prev_vols if v > 0]
            if not pos_prev_vols or curr_vol <= 0:
                return None
            avg_vol = sum(pos_prev_vols) / len(pos_prev_vols)
            if avg_vol <= 0:
                return None
            vol_ratio = curr_vol / avg_vol
            if range_pct < min_range_pct or vol_ratio < vol_multiplier:
                return None

            lower_wick = min(open_px, close_px) - low
            upper_wick = high - max(open_px, close_px)

            if close_px > open_px and lower_wick / candle_range >= wick_ratio:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="us_hammer_reversal",
                    direction="BUY",
                    strength=min(84, int(62 + vol_ratio * 6 + range_pct * 3)),
                    message_key="signal.pg.msg.us_hammer_reversal",
                    message_params={"vol": f"{vol_ratio:.1f}", "range": f"{range_pct:.2f}"},
                    timeframe="1m",
                    price=close_px,
                    extra={"vol_ratio": vol_ratio, "range_pct": range_pct},
                )

            if close_px < open_px and upper_wick / candle_range >= wick_ratio:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="us_shooting_star",
                    direction="SELL",
                    strength=min(84, int(62 + vol_ratio * 6 + range_pct * 3)),
                    message_key="signal.pg.msg.us_shooting_star",
                    message_params={"vol": f"{vol_ratio:.1f}", "range": f"{range_pct:.2f}"},
                    timeframe="1m",
                    price=close_px,
                    extra={"vol_ratio": vol_ratio, "range_pct": range_pct},
                )
        except Exception as e:
            logger.warning(f"check_us_wick_reversal error: {e}")
        return None

    def check_taker_buy_dominance(self, curr: dict, threshold: float = 0.7) -> PGSignal | None:
        """主动买入占比异常高"""
        if not curr:
            return None
        try:
            # When candle source doesn't provide taker fields (e.g. spot REST), skip this rule to avoid false signals.
            taker_raw = curr.get("taker_buy_quote_volume", None)
            total_raw = curr.get("quote_volume", None)
            if taker_raw is None or total_raw is None:
                return None

            taker_buy = _safe_float(taker_raw, 0.0)
            total_vol = _safe_float(total_raw, 0.0)
            if total_vol == 0:
                return None
            buy_ratio = taker_buy / total_vol
            if buy_ratio >= threshold:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="taker_buy_dominance",
                    direction="BUY",
                    strength=int(60 + buy_ratio * 30),
                    message_key="signal.pg.msg.taker_buy",
                    message_params={"pct": f"{buy_ratio * 100:.1f}", "threshold": f"{threshold * 100:.0f}"},
                    timeframe="1m",
                    price=_safe_float(curr.get("close", 0)),
                    extra={"buy_ratio": buy_ratio},
                )
        except Exception as e:
            logger.warning(f"check_taker_buy_dominance error: {e}")
        return None

    def check_taker_sell_dominance(self, curr: dict, threshold: float = 0.7) -> PGSignal | None:
        """主动卖出占比异常高"""
        if not curr:
            return None
        try:
            # When candle source doesn't provide taker fields (e.g. spot REST), skip this rule to avoid false signals.
            taker_raw = curr.get("taker_buy_quote_volume", None)
            total_raw = curr.get("quote_volume", None)
            if taker_raw is None or total_raw is None:
                return None

            taker_buy = _safe_float(taker_raw, 0.0)
            total_vol = _safe_float(total_raw, 0.0)
            if total_vol == 0:
                return None
            sell_ratio = 1 - taker_buy / total_vol
            if sell_ratio >= threshold:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="taker_sell_dominance",
                    direction="SELL",
                    strength=int(60 + sell_ratio * 30),
                    message_key="signal.pg.msg.taker_sell",
                    message_params={"pct": f"{sell_ratio * 100:.1f}", "threshold": f"{threshold * 100:.0f}"},
                    timeframe="1m",
                    price=_safe_float(curr.get("close", 0)),
                    extra={"sell_ratio": sell_ratio},
                )
        except Exception as e:
            logger.warning(f"check_taker_sell_dominance error: {e}")
        return None

    def check_oi_surge(self, curr: dict, prev: dict, threshold_pct: float = 5.0) -> PGSignal | None:
        """持仓量急增信号"""
        if not prev or not curr:
            return None
        try:
            curr_oi = _safe_float(curr.get("sum_open_interest_value", 0))
            prev_oi = _safe_float(prev.get("sum_open_interest_value", 0))
            if prev_oi == 0:
                return None
            change_pct = (curr_oi - prev_oi) / prev_oi * 100
            if change_pct >= threshold_pct:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="oi_surge",
                    direction="ALERT",
                    strength=min(80, int(55 + change_pct * 3)),
                    message_key="signal.pg.msg.oi_surge",
                    message_params={"pct": f"{change_pct:.2f}", "oi": f"{curr_oi / 1e9:.2f}"},
                    timeframe="5m",
                    extra={"oi_change_pct": change_pct, "oi_value": curr_oi},
                )
        except Exception as e:
            logger.warning(f"check_oi_surge error: {e}")
        return None

    def check_oi_dump(self, curr: dict, prev: dict, threshold_pct: float = 5.0) -> PGSignal | None:
        """持仓量急减信号"""
        if not prev or not curr:
            return None
        try:
            curr_oi = _safe_float(curr.get("sum_open_interest_value", 0))
            prev_oi = _safe_float(prev.get("sum_open_interest_value", 0))
            if prev_oi == 0:
                return None
            change_pct = (curr_oi - prev_oi) / prev_oi * 100
            if change_pct <= -threshold_pct:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="oi_dump",
                    direction="ALERT",
                    strength=min(80, int(55 + abs(change_pct) * 3)),
                    message_key="signal.pg.msg.oi_dump",
                    message_params={"pct": f"{abs(change_pct):.2f}", "oi": f"{curr_oi / 1e9:.2f}"},
                    timeframe="5m",
                    extra={"oi_change_pct": change_pct, "oi_value": curr_oi},
                )
        except Exception as e:
            logger.warning(f"check_oi_dump error: {e}")
        return None

    def check_top_trader_extreme_long(self, curr: dict, threshold: float = 3.0) -> PGSignal | None:
        """大户极度看多"""
        if not curr:
            return None
        try:
            ratio = _safe_float(curr.get("count_toptrader_long_short_ratio", 1), 1.0)
            if ratio >= threshold:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="top_trader_extreme_long",
                    direction="ALERT",
                    strength=min(85, int(60 + ratio * 8)),
                    message_key="signal.pg.msg.top_long",
                    message_params={"ratio": f"{ratio:.2f}", "threshold": f"{threshold}"},
                    timeframe="5m",
                    extra={"top_trader_ratio": ratio},
                )
        except Exception as e:
            logger.warning(f"check_top_trader_extreme_long error: {e}")
        return None

    def check_top_trader_extreme_short(self, curr: dict, threshold: float = 0.5) -> PGSignal | None:
        """大户极度看空"""
        if not curr:
            return None
        try:
            ratio = _safe_float(curr.get("count_toptrader_long_short_ratio", 1), 1.0)
            if ratio <= threshold:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="top_trader_extreme_short",
                    direction="ALERT",
                    strength=min(85, int(60 + (1 / ratio) * 5)),
                    message_key="signal.pg.msg.top_short",
                    message_params={"ratio": f"{ratio:.2f}", "threshold": f"{threshold}"},
                    timeframe="5m",
                    extra={"top_trader_ratio": ratio},
                )
        except Exception as e:
            logger.warning(f"check_top_trader_extreme_short error: {e}")
        return None

    def check_taker_ratio_flip_long(self, curr: dict, prev: dict) -> PGSignal | None:
        """主动成交多空比翻多"""
        if not prev or not curr:
            return None
        try:
            curr_ratio = _safe_float(curr.get("sum_taker_long_short_vol_ratio", 1), 1.0)
            prev_ratio = _safe_float(prev.get("sum_taker_long_short_vol_ratio", 1), 1.0)
            if prev_ratio < 1.0 and curr_ratio >= 1.2:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="taker_ratio_flip_long",
                    direction="BUY",
                    strength=70,
                    message_key="signal.pg.msg.taker_flip_long",
                    message_params={"prev": f"{prev_ratio:.2f}", "curr": f"{curr_ratio:.2f}"},
                    timeframe="5m",
                    extra={"prev_ratio": prev_ratio, "curr_ratio": curr_ratio},
                )
        except Exception as e:
            logger.warning(f"check_taker_ratio_flip_long error: {e}")
        return None

    def check_taker_ratio_flip_short(self, curr: dict, prev: dict) -> PGSignal | None:
        """主动成交多空比翻空"""
        if not prev or not curr:
            return None
        try:
            curr_ratio = _safe_float(curr.get("sum_taker_long_short_vol_ratio", 1), 1.0)
            prev_ratio = _safe_float(prev.get("sum_taker_long_short_vol_ratio", 1), 1.0)
            if prev_ratio > 1.0 and curr_ratio <= 0.8:
                return PGSignal(
                    symbol=curr.get("symbol", ""),
                    signal_type="taker_ratio_flip_short",
                    direction="SELL",
                    strength=70,
                    message_key="signal.pg.msg.taker_flip_short",
                    message_params={"prev": f"{prev_ratio:.2f}", "curr": f"{curr_ratio:.2f}"},
                    timeframe="5m",
                    extra={"prev_ratio": prev_ratio, "curr_ratio": curr_ratio},
                )
        except Exception as e:
            logger.warning(f"check_taker_ratio_flip_short error: {e}")
        return None


class PGSignalEngine(BaseEngine):
    """基于 TimescaleDB 的信号检测引擎（解耦版）"""

    def __init__(self, db_url: str = None, symbols: list[str] = None):
        super().__init__()
        self.db_url = db_url or get_database_url()
        raw_symbols = symbols or _get_default_symbols()
        self.symbols = _validate_symbols(raw_symbols) if symbols else raw_symbols
        # 美股分钟信号（独立配置，不影响 crypto 主链路）
        self.us_symbols = _get_us_symbols()
        self.us_price_threshold_pct = _safe_float(os.environ.get("SIGNAL_US_PRICE_THRESHOLD_PCT", "0.8"), 0.8)
        self.us_volume_spike_multiplier = _safe_float(os.environ.get("SIGNAL_US_VOL_SPIKE_MULTIPLIER", "2.5"), 2.5)

        # 状态
        self.baseline_candles: dict[str, dict] = {}
        self.baseline_metrics: dict[str, dict] = {}
        self.baseline_us_candles: dict[str, dict] = {}
        self.cooldowns: dict[str, float] = {}
        self.cooldown_seconds = COOLDOWN_SECONDS
        self._conn = None
        self._conn_last_check = 0.0
        self._cooldown_storage = get_cooldown_storage()
        self._history = get_history()
        # 只加载 PG 前缀的冷却记录，避免与 SQLite 互相干扰
        self.cooldowns = {
            k: v for k, v in self._cooldown_storage.load_all().items() if k.startswith("pg:")
        }
        if self.cooldowns:
            logger.info("PG 冷却记录已加载: %d", len(self.cooldowns))
        self.persistence_failures = 0

        # 统计
        self.stats = {"checks": 0, "signals": 0, "errors": 0, "stale": 0}
        if self.us_symbols:
            logger.info("PG 美股信号已启用: symbols=%s", self.us_symbols)

    def _get_conn(self):
        """获取数据库连接"""
        if self._conn is None or self._conn.closed:
            try:
                import psycopg

                self._conn = psycopg.connect(self.db_url, connect_timeout=3)
                self._conn.autocommit = True
            except ImportError:
                logger.error("psycopg not installed")
                return None
            except Exception as e:
                logger.error(f"Database connection failed: {e}")
                return None
        return self._conn

    def _ensure_conn(self):
        """确保连接可用，必要时重连"""
        conn = self._get_conn()
        if not conn:
            return None

        now = time.time()
        if now - self._conn_last_check < 30:
            return conn

        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            self._conn_last_check = now
            return conn
        except Exception as e:
            logger.warning("PG 连接失效，准备重连: %s", e)
            try:
                conn.close()
            except Exception:
                pass
            self._conn = None
            return self._get_conn()

    @staticmethod
    def _tf_seconds(timeframe: str) -> float:
        """将 1m/5m/1h/4h/1d 转为秒，未知返回 0"""
        try:
            unit = timeframe[-1].lower()
            val = float(timeframe[:-1])
            if unit == "m":
                return val * 60
            if unit == "h":
                return val * 3600
            if unit == "d":
                return val * 86400
        except Exception:
            return 0
        return 0

    def _is_cooled_down(self, signal_key: str, cooldown_seconds: float) -> bool:
        last = self.cooldowns.get(signal_key, 0)
        return time.time() - last > cooldown_seconds

    def _fetch_latest_candles(self) -> dict[str, dict]:
        """获取最新K线数据"""
        conn = self._ensure_conn()
        if not conn:
            return {}

        result = {}
        try:
            query = """
                WITH ranked AS (
                    SELECT symbol, bucket_ts, open, high, low, close, volume,
                           quote_volume, trade_count, taker_buy_volume, taker_buy_quote_volume,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY bucket_ts DESC) as rn
                    FROM market_data.candles_1m
                    WHERE symbol = ANY(%s)
                )
                SELECT symbol, bucket_ts, open, high, low, close, volume,
                       quote_volume, trade_count, taker_buy_volume, taker_buy_quote_volume
                FROM ranked WHERE rn = 1
            """
            with conn.cursor() as cur:
                cur.execute(query, (self.symbols,))
                for row in cur.fetchall():
                    result[row[0]] = {
                        "symbol": row[0],
                        "bucket_ts": row[1],
                        "open": row[2],
                        "high": row[3],
                        "low": row[4],
                        "close": row[5],
                        "volume": row[6],
                        "quote_volume": row[7],
                        "trade_count": row[8],
                        "taker_buy_volume": row[9],
                        "taker_buy_quote_volume": row[10],
                    }
        except Exception as e:
            logger.error(f"Fetch candles error: {e}")
            try:
                conn.close()
            except Exception:
                pass
            self._conn = None
            self.stats["errors"] += 1
        return result

    def _is_fresh(self, ts: datetime | None, timeframe: str, fallback_seconds: float) -> bool:
        """数据是否新鲜，按周期动态阈值"""
        if ts is None:
            return False
        # DB里多为 UTC 的 naive 时间，需要用 UTC now 计算
        now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.utcnow()
        age = (now - ts).total_seconds()
        tf_secs = self._tf_seconds(timeframe) or fallback_seconds
        allowed = max(DATA_MAX_AGE_SECONDS, tf_secs * 1.5 if tf_secs else 0)
        if allowed <= 0:
            allowed = DATA_MAX_AGE_SECONDS
        return age <= allowed

    def _fetch_latest_metrics(self) -> dict[str, dict]:
        """获取最新期货指标数据"""
        conn = self._ensure_conn()
        if not conn:
            return {}

        result = {}
        try:
            query = """
                WITH ranked AS (
                    SELECT symbol, create_time, sum_open_interest, sum_open_interest_value,
                           count_toptrader_long_short_ratio, sum_toptrader_long_short_ratio,
                           count_long_short_ratio, sum_taker_long_short_vol_ratio,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY create_time DESC) as rn
                    FROM market_data.binance_futures_metrics_5m
                    WHERE symbol = ANY(%s)
                )
                SELECT symbol, create_time, sum_open_interest, sum_open_interest_value,
                       count_toptrader_long_short_ratio, sum_toptrader_long_short_ratio,
                       count_long_short_ratio, sum_taker_long_short_vol_ratio
                FROM ranked WHERE rn = 1
            """
            with conn.cursor() as cur:
                cur.execute(query, (self.symbols,))
                for row in cur.fetchall():
                    result[row[0]] = {
                        "symbol": row[0],
                        "create_time": row[1],
                        "sum_open_interest": row[2],
                        "sum_open_interest_value": row[3],
                        "count_toptrader_long_short_ratio": row[4],
                        "sum_toptrader_long_short_ratio": row[5],
                        "count_long_short_ratio": row[6],
                        "sum_taker_long_short_vol_ratio": row[7],
                    }
        except Exception as e:
            logger.error(f"Fetch metrics error: {e}")
            try:
                conn.close()
            except Exception:
                pass
            self._conn = None
            self.stats["errors"] += 1
        return result

    def _fetch_latest_us_equity_candles(self) -> dict[str, dict]:
        """获取美股最新 1m K 线（raw.us_equity_1m）"""
        if not self.us_symbols:
            return {}
        conn = self._ensure_conn()
        if not conn:
            return {}

        result: dict[str, dict] = {}
        try:
            query = """
                WITH ranked AS (
                    SELECT symbol, open_time, open, high, low, close, volume, amount,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY open_time DESC) as rn
                    FROM raw.us_equity_1m
                    WHERE symbol = ANY(%s)
                )
                SELECT symbol, open_time, open, high, low, close, volume, amount
                FROM ranked WHERE rn = 1
            """
            with conn.cursor() as cur:
                cur.execute(query, (self.us_symbols,))
                for row in cur.fetchall():
                    result[row[0]] = {
                        "symbol": row[0],
                        "bucket_ts": row[1],
                        "open": row[2],
                        "high": row[3],
                        "low": row[4],
                        "close": row[5],
                        "volume": row[6],
                        # 复用 volume_spike 规则读取的 quote_volume 字段
                        "quote_volume": row[7],
                    }
        except Exception as e:
            logger.error(f"Fetch US candles error: {e}")
            self.stats["errors"] += 1
        return result

    def _fetch_recent_us_equity_candles(self, limit: int = 48) -> dict[str, list[dict]]:
        """获取美股最近 N 根 1m K 线（按时间升序）。"""
        if not self.us_symbols:
            return {}
        conn = self._ensure_conn()
        if not conn:
            return {}

        safe_limit = max(5, min(int(limit), 240))
        result: dict[str, list[dict]] = {sym: [] for sym in self.us_symbols}
        try:
            query = """
                WITH ranked AS (
                    SELECT symbol, open_time, open, high, low, close, volume, amount,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY open_time DESC) as rn
                    FROM raw.us_equity_1m
                    WHERE symbol = ANY(%s)
                )
                SELECT symbol, open_time, open, high, low, close, volume, amount
                FROM ranked
                WHERE rn <= %s
                ORDER BY symbol ASC, open_time ASC
            """
            with conn.cursor() as cur:
                cur.execute(query, (self.us_symbols, safe_limit))
                for row in cur.fetchall():
                    result.setdefault(row[0], []).append(
                        {
                            "symbol": row[0],
                            "bucket_ts": row[1],
                            "open": row[2],
                            "high": row[3],
                            "low": row[4],
                            "close": row[5],
                            "volume": row[6],
                            "quote_volume": row[7],
                        }
                    )
        except Exception as e:
            logger.error(f"Fetch US recent candles error: {e}")
            self.stats["errors"] += 1
        return result

    def check_signals(self) -> list[PGSignal]:
        """检查所有信号"""
        signals = []
        self.stats["checks"] += 1

        candles = self._fetch_latest_candles()
        metrics = self._fetch_latest_metrics()
        us_candles = self._fetch_latest_us_equity_candles()
        us_recent_candles = self._fetch_recent_us_equity_candles(limit=48)
        rules = PGSignalRules()

        # ===== Crypto (market_data.candles_1m + futures metrics) =====
        for symbol in self.symbols:
            curr_candle = candles.get(symbol)
            prev_candle = self.baseline_candles.get(symbol)
            curr_metric = metrics.get(symbol)
            prev_metric = self.baseline_metrics.get(symbol)

            if not curr_candle:
                continue

            # 数据新鲜度检查
            ts_candle = curr_candle.get("bucket_ts")
            if not self._is_fresh(ts_candle, "1m", 60):
                self.stats["stale"] += 1
                logger.warning("跳过陈旧K线数据 %s ts=%s", symbol, ts_candle)
                continue
            if curr_metric:
                ts_metric = curr_metric.get("create_time")
                if not self._is_fresh(ts_metric, "5m", 300):
                    self.stats["stale"] += 1
                    logger.warning("跳过陈旧期货指标 %s ts=%s", symbol, ts_metric)
                    curr_metric = None

            checkers = [
                (rules.check_price_surge, [curr_candle, prev_candle, 2.0]),
                (rules.check_price_dump, [curr_candle, prev_candle, 2.0]),
                (rules.check_volume_spike, [curr_candle, prev_candle, 5.0]),
                (rules.check_taker_buy_dominance, [curr_candle, 0.7]),
                (rules.check_taker_sell_dominance, [curr_candle, 0.7]),
            ]

            if curr_metric:
                checkers.extend(
                    [
                        (rules.check_oi_surge, [curr_metric, prev_metric, 3.0]),
                        (rules.check_oi_dump, [curr_metric, prev_metric, 3.0]),
                        (rules.check_top_trader_extreme_long, [curr_metric, 3.0]),
                        (rules.check_top_trader_extreme_short, [curr_metric, 0.5]),
                        (rules.check_taker_ratio_flip_long, [curr_metric, prev_metric]),
                        (rules.check_taker_ratio_flip_short, [curr_metric, prev_metric]),
                    ]
                )

            for checker, args in checkers:
                try:
                    signal = checker(*args)
                    if signal:
                        signal.extra.setdefault("market", "crypto")
                        signal_key = f"pg:crypto:{signal.symbol}_{signal.signal_type}"
                        cooldown_seconds = self.cooldown_seconds
                        if self._is_cooled_down(signal_key, cooldown_seconds):
                            if self._set_cooldown(signal_key):
                                signals.append(signal)
                                self.stats["signals"] += 1
                                try:
                                    if self._history.save(signal, source="pg") < 0:
                                        logger.warning("信号历史写入失败: %s", signal_key)
                                except Exception as e:
                                    logger.warning("信号历史写入异常: %s", e)
                                logger.info(f"PG Signal: {signal.symbol} - {signal.signal_type}")
                                # 发布事件
                                self._publish_event(signal)
                            else:
                                self.stats["errors"] += 1
                                logger.error("冷却持久化失败，跳过信号推送: %s", signal_key)
                except Exception as e:
                    logger.warning(f"Check error: {e}")
                    self.stats["errors"] += 1

            self.baseline_candles[symbol] = curr_candle
            if curr_metric:
                self.baseline_metrics[symbol] = curr_metric

        # ===== US Equities (raw.us_equity_1m) =====
        for symbol in self.us_symbols:
            recent_candles = us_recent_candles.get(symbol) or []
            curr_candle = recent_candles[-1] if recent_candles else us_candles.get(symbol)
            prev_candle = self.baseline_us_candles.get(symbol)
            if not curr_candle:
                continue

            ts_candle = curr_candle.get("bucket_ts")
            if not self._is_fresh(ts_candle, "1m", 60):
                self.stats["stale"] += 1
                logger.warning("跳过陈旧美股K线数据 %s ts=%s", symbol, ts_candle)
                continue

            checkers = [
                (rules.check_price_surge, [curr_candle, prev_candle, self.us_price_threshold_pct]),
                (rules.check_price_dump, [curr_candle, prev_candle, self.us_price_threshold_pct]),
                (rules.check_volume_spike, [curr_candle, prev_candle, self.us_volume_spike_multiplier]),
                (rules.check_breakout_up, [curr_candle, prev_candle, 0.15]),
                (rules.check_breakout_down, [curr_candle, prev_candle, 0.15]),
                (rules.check_gap_up, [curr_candle, prev_candle, 0.35]),
                (rules.check_gap_down, [curr_candle, prev_candle, 0.35]),
                (rules.check_bullish_engulfing, [curr_candle, prev_candle]),
                (rules.check_bearish_engulfing, [curr_candle, prev_candle]),
                (rules.check_doji, [curr_candle, 0.12]),
                (rules.check_range_expansion, [curr_candle, prev_candle, 1.8]),
                (rules.check_us_ema_cross, [recent_candles, 9, 21, 0.04]),
                (rules.check_us_rsi_reversal, [recent_candles, 14, 30.0, 70.0]),
                (rules.check_us_range_breakout, [recent_candles, 20, 1.8, 0.12]),
                (rules.check_us_wick_reversal, [recent_candles, 0.55, 0.35, 1.5]),
            ]

            for checker, args in checkers:
                try:
                    signal = checker(*args)
                    if signal:
                        signal.timeframe = "1m"
                        signal.extra.setdefault("market", "us_stock")
                        signal_key = f"pg:us:{signal.symbol}_{signal.signal_type}"
                        if self._is_cooled_down(signal_key, self.cooldown_seconds):
                            if self._set_cooldown(signal_key):
                                signals.append(signal)
                                self.stats["signals"] += 1
                                try:
                                    if self._history.save(signal, source="pg") < 0:
                                        logger.warning("信号历史写入失败: %s", signal_key)
                                except Exception as e:
                                    logger.warning("信号历史写入异常: %s", e)
                                logger.info("PG US Signal: %s - %s", signal.symbol, signal.signal_type)
                                self._publish_event(signal)
                            else:
                                self.stats["errors"] += 1
                                logger.error("冷却持久化失败，跳过美股信号推送: %s", signal_key)
                except Exception as e:
                    logger.warning("US check error: %s", e)
                    self.stats["errors"] += 1

            self.baseline_us_candles[symbol] = curr_candle

        return signals

    def _publish_event(self, signal: PGSignal):
        """发布信号事件"""
        event = SignalEvent(
            symbol=signal.symbol,
            signal_type=signal.signal_type,
            direction=signal.direction,
            strength=signal.strength,
            message_key=signal.message_key,
            message_params=signal.message_params,
            timestamp=signal.timestamp,
            timeframe=signal.timeframe,
            price=signal.price,
            source="pg",
            extra=signal.extra,
        )
        SignalPublisher.publish(event)

    def _set_cooldown(self, signal_key: str) -> bool:
        """设置冷却并持久化。失败则返回 False，调用方应跳过推送。"""
        ts = time.time()
        try:
            self._cooldown_storage.set(signal_key, ts)
            self.cooldowns[signal_key] = ts
            return True
        except Exception as e:
            self.persistence_failures += 1
            logger.error("写入冷却存储失败: %s", e, exc_info=True)
            return False

    def run_loop(self, interval: int = 60):
        """持续运行"""
        self._running = True
        logger.info(f"PG Signal Engine started, interval: {interval}s, symbols: {self.symbols}")

        while self._running:
            try:
                signals = self.check_signals()
                if signals:
                    for signal in signals:
                        self._emit_signal(signal)
                    logger.info(f"Found {len(signals)} PG signals")
            except Exception as e:
                logger.error(f"Run loop error: {e}")
            time.sleep(interval)

    def get_stats(self) -> dict:
        return {
            **self.stats,
            "symbols": len(self.symbols),
            "us_symbols": len(self.us_symbols),
            "cooldowns": len(self.cooldowns),
        }


# 单例
_pg_engine: PGSignalEngine | None = None
_pg_engine_lock = threading.Lock()


def get_pg_engine(symbols: list[str] = None) -> PGSignalEngine:
    """获取 PG 信号引擎单例"""
    global _pg_engine
    if _pg_engine is None:
        with _pg_engine_lock:
            if _pg_engine is None:
                _pg_engine = PGSignalEngine(symbols=symbols)
    return _pg_engine


def start_pg_signal_loop(interval: int = 60, symbols: list[str] = None):
    """在后台线程启动 PG 信号检测循环"""

    def run():
        engine = get_pg_engine(symbols)
        engine.run_loop(interval=interval)

    thread = threading.Thread(target=run, daemon=True, name="PGSignalEngine")
    thread.start()
    return thread
