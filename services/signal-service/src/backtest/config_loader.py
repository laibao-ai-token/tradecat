"""Backtest config loading with stdlib-first parsing."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import (
    AggregationConfig,
    BacktestConfig,
    DateRange,
    ExecutionConfig,
    RetentionConfig,
    RiskConfig,
    WalkForwardConfig,
)


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    for key, val in src.items():
        if isinstance(val, dict) and isinstance(dst.get(key), dict):
            dst[key] = _deep_merge(dict(dst[key]), val)
        else:
            dst[key] = val
    return dst


def _parse_text(text: str, source: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        return {}

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if parsed is None:
        try:
            import yaml  # type: ignore
        except Exception as exc:  # pragma: no cover - fallback depends on environment
            raise ValueError(f"Cannot parse config file {source}: JSON failed and PyYAML not available") from exc

        parsed = yaml.safe_load(text)

    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError(f"Config root must be a mapping object: {source}")
    return parsed


def _load_payload_with_redirect(cfg_path: Path, *, max_hops: int = 5) -> dict[str, Any]:
    """Load config payload, following optional `_moved_to` redirects.

    We keep old config paths as small stubs:
      {"_moved_to": "strategies/default.crypto.yaml"}
    so docs/scripts don't break when templates are reorganized.
    """

    cur = cfg_path
    visited: set[Path] = set()

    for _ in range(max(1, int(max_hops))):
        resolved = cur.expanduser().resolve()
        if resolved in visited:
            raise ValueError(f"Config redirect loop detected: {resolved}")
        visited.add(resolved)

        payload = _parse_text(resolved.read_text(encoding="utf-8"), str(resolved))
        moved_to = payload.get("_moved_to")
        if isinstance(moved_to, str) and moved_to.strip():
            cur = (resolved.parent / moved_to.strip()).expanduser()
            continue

        # Ignore redirect key if present in a real config.
        payload.pop("_moved_to", None)
        return payload

    raise ValueError(f"Config redirect too deep (> {max_hops}): {cfg_path}")


def _normalize_symbols(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [x.strip() for x in raw.split(",")]
    elif isinstance(raw, (list, tuple)):
        parts = [str(x).strip() for x in raw]
    else:
        parts = [str(raw).strip()]

    out: list[str] = []
    for part in parts:
        if not part:
            continue
        norm = "".join(ch for ch in part.upper() if ch.isalnum())
        if not norm:
            continue
        out.append(norm)
    return list(dict.fromkeys(out))


def _strategy_label_from_path(cfg_path: Path) -> str:
    name = cfg_path.name
    for suffix in (".yaml", ".yml", ".json"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def load_config(
    path: str | Path | None,
    *,
    start: str | None = None,
    end: str | None = None,
    symbols: str | None = None,
    fee_bps: float | None = None,
    slippage_bps: float | None = None,
    allow_long: bool | None = None,
    allow_short: bool | None = None,
    min_hold_minutes: int | None = None,
    neutral_confirm_minutes: int | None = None,
    initial_equity: float | None = None,
    leverage: float | None = None,
    position_size_pct: float | None = None,
    wf_train_days: int | None = None,
    wf_test_days: int | None = None,
    wf_step_days: int | None = None,
    long_open_threshold: int | None = None,
    short_open_threshold: int | None = None,
    close_threshold: int | None = None,
) -> BacktestConfig:
    """Load config and apply CLI overrides."""

    merged = asdict(BacktestConfig())

    cfg_path: Path | None = None
    if path:
        cfg_path = Path(path).expanduser().resolve()
        payload = _load_payload_with_redirect(cfg_path)
        merged = _deep_merge(merged, payload)

    symbol_override = _normalize_symbols(symbols)
    if symbol_override:
        merged["symbols"] = symbol_override

    date_range = merged.get("date_range") or {}
    if not isinstance(date_range, dict):
        date_range = {}
    if start:
        date_range["start"] = str(start).strip()
    if end:
        date_range["end"] = str(end).strip()
    merged["date_range"] = date_range

    execution_cfg = merged.get("execution") or {}
    if not isinstance(execution_cfg, dict):
        execution_cfg = {}
    if fee_bps is not None:
        execution_cfg["fee_bps"] = float(fee_bps)
    if slippage_bps is not None:
        execution_cfg["slippage_bps"] = float(slippage_bps)
    if allow_long is not None:
        execution_cfg["allow_long"] = bool(allow_long)
    if allow_short is not None:
        execution_cfg["allow_short"] = bool(allow_short)
    if min_hold_minutes is not None:
        execution_cfg["min_hold_minutes"] = int(min_hold_minutes)
    if neutral_confirm_minutes is not None:
        execution_cfg["neutral_confirm_minutes"] = int(neutral_confirm_minutes)
    merged["execution"] = execution_cfg

    risk_cfg = merged.get("risk") or {}
    if not isinstance(risk_cfg, dict):
        risk_cfg = {}
    if initial_equity is not None:
        risk_cfg["initial_equity"] = float(initial_equity)
    if leverage is not None:
        risk_cfg["leverage"] = float(leverage)
    if position_size_pct is not None:
        risk_cfg["position_size_pct"] = float(position_size_pct)
    merged["risk"] = risk_cfg

    wf_cfg = merged.get("walk_forward") or {}
    if not isinstance(wf_cfg, dict):
        wf_cfg = {}
    if wf_train_days is not None:
        wf_cfg["train_days"] = int(wf_train_days)
    if wf_test_days is not None:
        wf_cfg["test_days"] = int(wf_test_days)
    if wf_step_days is not None:
        wf_cfg["step_days"] = int(wf_step_days)
    merged["walk_forward"] = wf_cfg

    aggregation_cfg = merged.get("aggregation") or {}
    if not isinstance(aggregation_cfg, dict):
        aggregation_cfg = {}
    if long_open_threshold is not None:
        aggregation_cfg["long_open_threshold"] = int(long_open_threshold)
    if short_open_threshold is not None:
        aggregation_cfg["short_open_threshold"] = int(short_open_threshold)
    if close_threshold is not None:
        aggregation_cfg["close_threshold"] = int(close_threshold)
    merged["aggregation"] = aggregation_cfg
    cfg = BacktestConfig(
        market=str(merged.get("market", "crypto")),
        symbols=_normalize_symbols(merged.get("symbols")) or BacktestConfig().symbols,
        timeframe=str(merged.get("timeframe", "1m")),
        strategy_label=str(merged.get("strategy_label", "")),
        strategy_config_path=str(merged.get("strategy_config_path", "")),
        date_range=DateRange(**(merged.get("date_range") or {})),
        execution=ExecutionConfig(**(merged.get("execution") or {})),
        risk=RiskConfig(**(merged.get("risk") or {})),
        aggregation=AggregationConfig(**(merged.get("aggregation") or {})),
        walk_forward=WalkForwardConfig(**(merged.get("walk_forward") or {})),
        retention=RetentionConfig(**(merged.get("retention") or {})),
    )

    if cfg_path is not None:
        if not cfg.strategy_config_path:
            cfg.strategy_config_path = str(cfg_path)
        if not cfg.strategy_label:
            cfg.strategy_label = _strategy_label_from_path(cfg_path)

    if cfg.risk.initial_equity <= 0:
        raise ValueError("risk.initial_equity must be > 0")
    if not (0 < cfg.risk.position_size_pct <= 1.0):
        raise ValueError("risk.position_size_pct must be in (0, 1]")
    if cfg.execution.fee_bps < 0 or cfg.execution.slippage_bps < 0:
        raise ValueError("execution fee/slippage must be >= 0")
    if cfg.aggregation.long_open_threshold <= 0 or cfg.aggregation.short_open_threshold <= 0:
        raise ValueError("aggregation thresholds must be > 0")
    if cfg.retention.keep_runs < 1:
        raise ValueError("retention.keep_runs must be >= 1")
    if cfg.walk_forward.train_days < 1 or cfg.walk_forward.test_days < 1 or cfg.walk_forward.step_days < 1:
        raise ValueError("walk_forward train/test/step days must be >= 1")

    return cfg
