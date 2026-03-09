"""Helpers for history vs rule-replay comparison artifacts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .runner import RunnerResult

_ALIGNMENT_TOP_N = 5
_ALIGNMENT_WEIGHTS = {
    "rule_overlap_score": 0.35,
    "top_rule_score": 0.35,
    "signal_count_score": 0.15,
    "direction_score": 0.10,
    "timeframe_score": 0.05,
}
_ALIGNMENT_THRESHOLDS = {
    "min_jaccard_pct": 60.0,
    "min_history_coverage_pct": 70.0,
    "max_signal_count_delta_pct": 20.0,
    "max_buy_ratio_delta_pct": 20.0,
    "max_top_rule_delta_pct": 35.0,
    "max_new_rule_share_pct": 15.0,
}


@dataclass(frozen=True)
class ComparisonSummary:
    run_id: str
    history_run_id: str
    rule_run_id: str
    history_return_pct: float
    rule_return_pct: float
    history_max_drawdown_pct: float
    rule_max_drawdown_pct: float
    history_trade_count: int
    rule_trade_count: int
    history_excess_return_pct: float
    rule_excess_return_pct: float
    history_signal_count: int
    rule_signal_count: int
    history_bar_count: int
    rule_bar_count: int
    history_signal_type_counts: dict[str, int]
    rule_signal_type_counts: dict[str, int]
    history_direction_counts: dict[str, int]
    rule_direction_counts: dict[str, int]
    history_timeframe_counts: dict[str, int]
    rule_timeframe_counts: dict[str, int]


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(sep=" ")


def _safe_pct(numerator: int, denominator: int) -> float:
    den = max(0, int(denominator))
    num = max(0, int(numerator))
    if den <= 0:
        return 0.0
    return float(num / den * 100.0)


def _normalize_counter(raw: dict[str, int] | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, value in (raw or {}).items():
        norm_key = str(key).strip()
        if not norm_key:
            continue
        out[norm_key] = out.get(norm_key, 0) + int(value)
    return dict(sorted(out.items(), key=lambda item: (-item[1], item[0])))


def _counter_delta(history: dict[str, int], rule: dict[str, int], *, top_n: int) -> list[dict[str, int | str]]:
    rows: list[dict[str, int | str]] = []
    keys = set(history.keys()) | set(rule.keys())
    for key in keys:
        history_count = int(history.get(key, 0))
        rule_count = int(rule.get(key, 0))
        delta = rule_count - history_count
        rows.append(
            {
                "key": key,
                "history_count": history_count,
                "rule_count": rule_count,
                "delta": delta,
                "abs_delta": abs(delta),
            }
        )

    rows.sort(key=lambda item: (-int(item["abs_delta"]), str(item["key"])))
    trimmed = rows[: max(0, int(top_n))]
    for row in trimmed:
        row.pop("abs_delta", None)
    return trimmed


def _top_missing_history_rules(
    history: dict[str, int],
    rule: dict[str, int],
    *,
    top_n: int,
) -> list[dict[str, int | str]]:
    rows: list[dict[str, int | str]] = []
    for key, history_count in history.items():
        if int(history_count) <= 0:
            continue
        if int(rule.get(key, 0)) != 0:
            continue
        rows.append(
            {
                "key": key,
                "history_count": int(history_count),
                "rule_count": 0,
                "delta": -int(history_count),
            }
        )

    rows.sort(key=lambda item: (-int(item["history_count"]), str(item["key"])))
    return rows[: max(0, int(top_n))]


def _top_new_rule_types(
    history: dict[str, int],
    rule: dict[str, int],
    *,
    top_n: int,
) -> list[dict[str, int | str]]:
    rows: list[dict[str, int | str]] = []
    for key, rule_count in rule.items():
        if int(rule_count) <= 0:
            continue
        if int(history.get(key, 0)) != 0:
            continue
        rows.append(
            {
                "key": key,
                "history_count": 0,
                "rule_count": int(rule_count),
                "delta": int(rule_count),
            }
        )

    rows.sort(key=lambda item: (-int(item["rule_count"]), str(item["key"])))
    return rows[: max(0, int(top_n))]


def _direction_mix(counter: dict[str, int]) -> dict[str, float | int]:
    buy = int(counter.get("BUY", 0))
    sell = int(counter.get("SELL", 0))
    total = int(sum(counter.values()))
    other = max(0, total - buy - sell)
    actionable_total = buy + sell
    buy_ratio_pct = (buy / actionable_total * 100.0) if actionable_total > 0 else 0.0
    return {
        "buy": buy,
        "sell": sell,
        "other": other,
        "total": total,
        "buy_ratio_pct": float(buy_ratio_pct),
    }


def _rule_overlap(history: dict[str, int], rule: dict[str, int]) -> dict[str, int | float]:
    history_set = {key for key, count in history.items() if int(count) > 0}
    rule_set = {key for key, count in rule.items() if int(count) > 0}
    shared = history_set & rule_set
    union = history_set | rule_set
    return {
        "history_rule_types": len(history_set),
        "rule_rule_types": len(rule_set),
        "shared_rule_types": len(shared),
        "jaccard_pct": _safe_pct(len(shared), len(union)),
        "history_coverage_pct": _safe_pct(len(shared), len(history_set)),
        "rule_overlap_pct": _safe_pct(len(shared), len(rule_set)),
    }


def _timeframe_profile(counter: dict[str, int]) -> dict[str, int | float | str]:
    total = int(sum(counter.values()))
    unique = len(counter)
    if total <= 0 or unique <= 0:
        return {
            "dominant": "--",
            "dominant_count": 0,
            "dominant_ratio_pct": 0.0,
            "total": 0,
            "unique": 0,
        }

    dominant, dominant_count = max(counter.items(), key=lambda item: (int(item[1]), item[0]))
    ratio = _safe_pct(int(dominant_count), total)
    return {
        "dominant": str(dominant),
        "dominant_count": int(dominant_count),
        "dominant_ratio_pct": float(ratio),
        "total": total,
        "unique": unique,
    }


def _normalize_tf_list(raw: object) -> list[str]:
    out: list[str] = []
    if isinstance(raw, (list, tuple, set)):
        values = raw
    elif raw is None:
        values = []
    else:
        values = [raw]

    for item in values:
        text = str(item or "").strip().lower()
        if text:
            out.append(text)
    return sorted(set(out))


def _load_rule_replay_diagnostics(
    rule_run_dir: Path | None,
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, list[str] | bool]]]:
    if rule_run_dir is None:
        return {}, {}

    payload_path = Path(rule_run_dir) / "rule_replay_diagnostics.json"
    if not payload_path.exists():
        return {}, {}

    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except Exception:
        return {}, {}

    if not isinstance(payload, dict):
        return {}, {}

    counters_raw = payload.get("rule_counters")
    counters_map = counters_raw if isinstance(counters_raw, dict) else {}

    out_counters: dict[str, dict[str, int]] = {}
    for key, row in counters_map.items():
        if not isinstance(row, dict):
            continue
        name = str(key).strip()
        if not name:
            continue
        out_counters[name] = {
            "evaluated": int(row.get("evaluated") or 0),
            "timeframe_filtered": int(row.get("timeframe_filtered") or 0),
            "volume_filtered": int(row.get("volume_filtered") or 0),
            "condition_failed": int(row.get("condition_failed") or 0),
            "cooldown_blocked": int(row.get("cooldown_blocked") or 0),
            "triggered": int(row.get("triggered") or 0),
        }

    profiles_raw = payload.get("rule_timeframe_profiles")
    profiles_map = profiles_raw if isinstance(profiles_raw, dict) else {}

    out_profiles: dict[str, dict[str, list[str] | bool]] = {}
    for key, row in profiles_map.items():
        if not isinstance(row, dict):
            continue
        name = str(key).strip()
        if not name:
            continue

        configured = _normalize_tf_list(row.get("configured_timeframes"))
        observed = _normalize_tf_list(row.get("observed_timeframes"))
        overlap = _normalize_tf_list(row.get("overlap_timeframes"))
        if not overlap:
            overlap = sorted(set(configured) & set(observed))

        out_profiles[name] = {
            "configured_timeframes": configured,
            "observed_timeframes": observed,
            "overlap_timeframes": overlap,
            "has_overlap": bool(overlap),
        }

    return out_counters, out_profiles


def _resolve_primary_block_reason(
    diag: dict[str, int],
    profile: dict[str, list[str] | bool] | None = None,
) -> str:
    timeframe_filtered = int(diag.get("timeframe_filtered") or 0)
    triggered = int(diag.get("triggered") or 0)
    if timeframe_filtered > 0 and triggered <= 0 and profile:
        configured = _normalize_tf_list(profile.get("configured_timeframes"))
        observed = _normalize_tf_list(profile.get("observed_timeframes"))
        overlap = _normalize_tf_list(profile.get("overlap_timeframes"))
        if configured and observed and not overlap:
            return "timeframe_no_data"

    reason_pairs = [
        ("condition_failed", int(diag.get("condition_failed") or 0)),
        ("timeframe_filtered", timeframe_filtered),
        ("volume_filtered", int(diag.get("volume_filtered") or 0)),
        ("cooldown_blocked", int(diag.get("cooldown_blocked") or 0)),
    ]
    reason_pairs.sort(key=lambda item: (-item[1], item[0]))
    top_reason, top_value = reason_pairs[0]
    if top_value <= 0:
        evaluated = int(diag.get("evaluated") or 0)
        return "not_evaluated" if evaluated <= 0 else "unknown"
    return top_reason


def _build_missing_rule_diagnostics(
    missing_rows: list[dict[str, int | str]],
    rule_diagnostics: dict[str, dict[str, int]],
    rule_timeframe_profiles: dict[str, dict[str, list[str] | bool]],
) -> list[dict[str, int | float | str | list[str]]]:
    out: list[dict[str, int | float | str | list[str]]] = []
    for row in missing_rows:
        key = str(row.get("key") or "").strip()
        if not key:
            continue

        diag = rule_diagnostics.get(key, {})
        profile = rule_timeframe_profiles.get(key, {})
        configured_tfs = _normalize_tf_list(profile.get("configured_timeframes"))
        observed_tfs = _normalize_tf_list(profile.get("observed_timeframes"))
        overlap_tfs = _normalize_tf_list(profile.get("overlap_timeframes"))

        evaluated = int(diag.get("evaluated") or 0)
        timeframe_filtered = int(diag.get("timeframe_filtered") or 0)
        volume_filtered = int(diag.get("volume_filtered") or 0)
        condition_failed = int(diag.get("condition_failed") or 0)
        cooldown_blocked = int(diag.get("cooldown_blocked") or 0)
        triggered = int(diag.get("triggered") or 0)

        out.append(
            {
                "key": key,
                "history_count": int(row.get("history_count") or 0),
                "rule_count": int(row.get("rule_count") or 0),
                "evaluated": evaluated,
                "timeframe_filtered": timeframe_filtered,
                "volume_filtered": volume_filtered,
                "condition_failed": condition_failed,
                "cooldown_blocked": cooldown_blocked,
                "triggered": triggered,
                "configured_timeframes": configured_tfs,
                "observed_timeframes": observed_tfs,
                "overlap_timeframes": overlap_tfs,
                "trigger_rate_pct": _safe_pct(triggered, evaluated),
                "primary_block_reason": _resolve_primary_block_reason(diag, profile),
            }
        )

    return out


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _round2(value: float) -> float:
    return round(float(value), 2)


def _relative_delta_pct(left: int | float, right: int | float, *, baseline: int | float | None = None) -> float:
    lhs = float(left)
    rhs = float(right)
    denominator = float(baseline) if baseline is not None else max(abs(lhs), abs(rhs), 1.0)
    if denominator <= 0:
        denominator = 1.0
    return float(abs(rhs - lhs) / denominator * 100.0)


def _timeframe_overlap_pct(history: dict[str, int], rule: dict[str, int]) -> float:
    history_set = {key for key, count in history.items() if int(count) > 0}
    rule_set = {key for key, count in rule.items() if int(count) > 0}
    union = history_set | rule_set
    if not union:
        return 100.0
    return _safe_pct(len(history_set & rule_set), len(union))


def _build_top_rule_alignment(
    history: dict[str, int],
    rule: dict[str, int],
    *,
    top_n: int,
) -> list[dict[str, int | float | str]]:
    ranked_history = sorted(
        ((str(key), int(value)) for key, value in history.items() if int(value) > 0),
        key=lambda item: (-item[1], item[0]),
    )
    top_rows = ranked_history[: max(0, int(top_n))]
    total_history = sum(count for _, count in top_rows)
    out: list[dict[str, int | float | str]] = []

    for key, history_count in top_rows:
        rule_count = int(rule.get(key, 0))
        delta = rule_count - history_count
        delta_pct = _relative_delta_pct(history_count, rule_count)
        weight = (history_count / total_history * 100.0) if total_history > 0 else 0.0
        out.append(
            {
                "key": key,
                "history_count": history_count,
                "rule_count": rule_count,
                "delta": delta,
                "delta_pct": _round2(delta_pct),
                "weight_pct": _round2(weight),
                "score": _round2(_clamp_score(100.0 - delta_pct)),
            }
        )

    return out


def _weighted_top_rule_score(rows: list[dict[str, int | float | str]]) -> float:
    if not rows:
        return 100.0

    total_weight = sum(float(row.get("weight_pct") or 0.0) for row in rows)
    if total_weight <= 0:
        return 100.0

    weighted = sum((float(row.get("weight_pct") or 0.0) / total_weight) * float(row.get("score") or 0.0) for row in rows)
    return _clamp_score(weighted)


def _append_alignment_warning(
    warnings: list[dict[str, object]],
    seen: set[tuple[str, str]],
    *,
    kind: str,
    severity: str,
    subject: str,
    actual: float | None = None,
    threshold: float | None = None,
    message: str,
) -> None:
    dedupe_key = (kind, subject)
    if dedupe_key in seen:
        return
    seen.add(dedupe_key)

    row: dict[str, object] = {
        "kind": kind,
        "severity": severity,
        "subject": subject,
        "message": message,
    }
    if actual is not None:
        row["actual"] = _round2(actual)
    if threshold is not None:
        row["threshold"] = _round2(threshold)
    warnings.append(row)


def _summarize_alignment_risk(
    *,
    alignment_score: float,
    alignment_status: str,
    warnings: list[dict[str, object]],
) -> tuple[str, dict[str, int], str]:
    warning_counts = {"error": 0, "warn": 0, "info": 0}
    for row in warnings:
        severity = str(row.get("severity") or "warn").strip().lower() or "warn"
        if severity not in warning_counts:
            warning_counts[severity] = 0
        warning_counts[severity] += 1

    score = float(alignment_score)
    status = str(alignment_status or "").strip().lower()
    error_count = int(warning_counts.get("error", 0))
    warn_count = int(warning_counts.get("warn", 0))

    if error_count > 0 or score < 50.0:
        level = "critical"
        summary = "Alignment is unsafe for parameter decisions; inspect the top blocking rules first."
    elif status == "fail" or score < 70.0:
        level = "high"
        summary = "Alignment drift is high; replay and history differ enough to bias conclusions."
    elif status == "warn" or warn_count > 0 or score < 85.0:
        level = "medium"
        summary = "Alignment is usable with caution; review warnings before trusting the run."
    else:
        level = "low"
        summary = "Alignment looks stable for a first-pass decision screen."

    return level, warning_counts, summary


def _build_alignment_assessment(
    summary: ComparisonSummary,
    *,
    rule_overlap: dict[str, int | float],
    delta_buy_ratio_pct: float,
    new_rule_types_top: list[dict[str, int | str]],
    missing_history_rules_diagnostics: list[dict[str, int | float | str | list[str]]],
    timeframe_overlap_pct: float,
) -> dict[str, object]:
    signal_count_delta_pct = _relative_delta_pct(
        summary.history_signal_count,
        summary.rule_signal_count,
        baseline=max(summary.history_signal_count, 1),
    )
    top_rule_alignment = _build_top_rule_alignment(
        summary.history_signal_type_counts,
        summary.rule_signal_type_counts,
        top_n=_ALIGNMENT_TOP_N,
    )
    top_rule_score = _weighted_top_rule_score(top_rule_alignment)
    rule_overlap_score = (
        float(rule_overlap.get("jaccard_pct") or 0.0)
        + float(rule_overlap.get("history_coverage_pct") or 0.0)
        + float(rule_overlap.get("rule_overlap_pct") or 0.0)
    ) / 3.0
    signal_count_score = _clamp_score(100.0 - signal_count_delta_pct)
    direction_score = _clamp_score(100.0 - abs(float(delta_buy_ratio_pct)))
    timeframe_score = _clamp_score(timeframe_overlap_pct)

    breakdown = {
        "rule_overlap_score": _round2(rule_overlap_score),
        "top_rule_score": _round2(top_rule_score),
        "signal_count_score": _round2(signal_count_score),
        "direction_score": _round2(direction_score),
        "timeframe_score": _round2(timeframe_score),
    }
    alignment_score = _clamp_score(
        sum(breakdown[key] * float(weight) for key, weight in _ALIGNMENT_WEIGHTS.items())
    )

    warnings: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    jaccard_pct = float(rule_overlap.get("jaccard_pct") or 0.0)
    if jaccard_pct < _ALIGNMENT_THRESHOLDS["min_jaccard_pct"]:
        _append_alignment_warning(
            warnings,
            seen,
            kind="rule_type_jaccard_below_threshold",
            severity="warn",
            subject="rule_types",
            actual=jaccard_pct,
            threshold=float(_ALIGNMENT_THRESHOLDS["min_jaccard_pct"]),
            message="Rule-type overlap is too low; history and replay trigger sets are drifting.",
        )

    history_coverage_pct = float(rule_overlap.get("history_coverage_pct") or 0.0)
    if history_coverage_pct < _ALIGNMENT_THRESHOLDS["min_history_coverage_pct"]:
        _append_alignment_warning(
            warnings,
            seen,
            kind="history_rule_coverage_below_threshold",
            severity="warn",
            subject="history_rule_coverage",
            actual=history_coverage_pct,
            threshold=float(_ALIGNMENT_THRESHOLDS["min_history_coverage_pct"]),
            message="Too many history rule types are missing in rule replay output.",
        )

    if signal_count_delta_pct > _ALIGNMENT_THRESHOLDS["max_signal_count_delta_pct"]:
        _append_alignment_warning(
            warnings,
            seen,
            kind="signal_count_delta_above_threshold",
            severity="warn",
            subject="signal_count",
            actual=signal_count_delta_pct,
            threshold=float(_ALIGNMENT_THRESHOLDS["max_signal_count_delta_pct"]),
            message="Total signal count drift is above the acceptable threshold.",
        )

    buy_ratio_delta_abs = abs(float(delta_buy_ratio_pct))
    if buy_ratio_delta_abs > _ALIGNMENT_THRESHOLDS["max_buy_ratio_delta_pct"]:
        _append_alignment_warning(
            warnings,
            seen,
            kind="buy_ratio_delta_above_threshold",
            severity="warn",
            subject="direction_mix",
            actual=buy_ratio_delta_abs,
            threshold=float(_ALIGNMENT_THRESHOLDS["max_buy_ratio_delta_pct"]),
            message="Direction mix drift is above the acceptable threshold.",
        )

    for row in top_rule_alignment:
        key = str(row.get("key") or "--")
        history_count = int(row.get("history_count") or 0)
        rule_count = int(row.get("rule_count") or 0)
        delta_pct = float(row.get("delta_pct") or 0.0)
        if history_count > 0 and rule_count <= 0:
            _append_alignment_warning(
                warnings,
                seen,
                kind="top_rule_missing_in_rule_replay",
                severity="error",
                subject=key,
                actual=delta_pct,
                threshold=float(_ALIGNMENT_THRESHOLDS["max_top_rule_delta_pct"]),
                message="A key history rule is completely missing in rule replay output.",
            )
            continue

        if delta_pct > _ALIGNMENT_THRESHOLDS["max_top_rule_delta_pct"]:
            _append_alignment_warning(
                warnings,
                seen,
                kind="top_rule_delta_above_threshold",
                severity="warn",
                subject=key,
                actual=delta_pct,
                threshold=float(_ALIGNMENT_THRESHOLDS["max_top_rule_delta_pct"]),
                message="A key history rule has excessive trigger-count drift.",
            )

    for row in new_rule_types_top:
        key = str(row.get("key") or "--")
        rule_count = int(row.get("rule_count") or 0)
        share_pct = _safe_pct(rule_count, max(summary.rule_signal_count, 1))
        if share_pct > _ALIGNMENT_THRESHOLDS["max_new_rule_share_pct"]:
            _append_alignment_warning(
                warnings,
                seen,
                kind="new_rule_share_above_threshold",
                severity="warn",
                subject=key,
                actual=share_pct,
                threshold=float(_ALIGNMENT_THRESHOLDS["max_new_rule_share_pct"]),
                message="Rule replay is creating a large signal block for a rule absent from history.",
            )

    for row in missing_history_rules_diagnostics:
        key = str(row.get("key") or "--")
        reason = str(row.get("primary_block_reason") or "")
        if reason != "timeframe_no_data":
            continue
        _append_alignment_warning(
            warnings,
            seen,
            kind="top_rule_timeframe_no_data",
            severity="error",
            subject=key,
            message="Rule replay lacks overlapping timeframe data for a key history rule.",
        )

    if any(str(row.get("severity") or "") == "error" for row in warnings):
        status = "fail"
    elif warnings:
        status = "warn"
    else:
        status = "pass"

    risk_level, warning_counts, risk_summary = _summarize_alignment_risk(
        alignment_score=alignment_score,
        alignment_status=status,
        warnings=warnings,
    )

    return {
        "alignment_score": _round2(alignment_score),
        "alignment_status": status,
        "alignment_risk_level": risk_level,
        "alignment_risk_summary": risk_summary,
        "alignment_warning_counts": warning_counts,
        "alignment_breakdown": breakdown,
        "alignment_inputs": {
            "signal_count_delta_pct": _round2(signal_count_delta_pct),
            "buy_ratio_delta_pct": _round2(buy_ratio_delta_abs),
            "timeframe_overlap_pct": _round2(timeframe_score),
        },
        "alignment_top_rules": top_rule_alignment,
        "alignment_thresholds": {key: _round2(float(value)) for key, value in _ALIGNMENT_THRESHOLDS.items()},
        "alignment_warnings": warnings,
    }


def build_comparison_summary(run_id: str, history: RunnerResult, rule_replay: RunnerResult) -> ComparisonSummary:
    return ComparisonSummary(
        run_id=run_id,
        history_run_id=history.run_id,
        rule_run_id=rule_replay.run_id,
        history_return_pct=float(history.metrics.total_return_pct),
        rule_return_pct=float(rule_replay.metrics.total_return_pct),
        history_max_drawdown_pct=float(history.metrics.max_drawdown_pct),
        rule_max_drawdown_pct=float(rule_replay.metrics.max_drawdown_pct),
        history_trade_count=int(history.metrics.trade_count),
        rule_trade_count=int(rule_replay.metrics.trade_count),
        history_excess_return_pct=float(history.metrics.excess_return_pct),
        rule_excess_return_pct=float(rule_replay.metrics.excess_return_pct),
        history_signal_count=int(history.metrics.signal_count),
        rule_signal_count=int(rule_replay.metrics.signal_count),
        history_bar_count=int(history.metrics.bar_count),
        rule_bar_count=int(rule_replay.metrics.bar_count),
        history_signal_type_counts=_normalize_counter(history.metrics.signal_type_counts),
        rule_signal_type_counts=_normalize_counter(rule_replay.metrics.signal_type_counts),
        history_direction_counts=_normalize_counter(history.metrics.direction_counts),
        rule_direction_counts=_normalize_counter(rule_replay.metrics.direction_counts),
        history_timeframe_counts=_normalize_counter(history.metrics.timeframe_counts),
        rule_timeframe_counts=_normalize_counter(rule_replay.metrics.timeframe_counts),
    )


def write_comparison_artifacts(
    backtest_root: Path,
    summary: ComparisonSummary,
    *,
    rule_run_dir: Path | None = None,
) -> Path:
    out_dir = Path(backtest_root) / f"{summary.run_id}-compare"
    out_dir.mkdir(parents=True, exist_ok=True)

    signal_type_delta_top = _counter_delta(
        summary.history_signal_type_counts,
        summary.rule_signal_type_counts,
        top_n=12,
    )
    timeframe_delta_top = _counter_delta(
        summary.history_timeframe_counts,
        summary.rule_timeframe_counts,
        top_n=8,
    )
    direction_delta = _counter_delta(
        summary.history_direction_counts,
        summary.rule_direction_counts,
        top_n=8,
    )
    missing_history_rules_top = _top_missing_history_rules(
        summary.history_signal_type_counts,
        summary.rule_signal_type_counts,
        top_n=8,
    )
    new_rule_types_top = _top_new_rule_types(
        summary.history_signal_type_counts,
        summary.rule_signal_type_counts,
        top_n=8,
    )

    history_direction_mix = _direction_mix(summary.history_direction_counts)
    rule_direction_mix = _direction_mix(summary.rule_direction_counts)
    rule_overlap = _rule_overlap(summary.history_signal_type_counts, summary.rule_signal_type_counts)
    history_timeframe_profile = _timeframe_profile(summary.history_timeframe_counts)
    rule_timeframe_profile = _timeframe_profile(summary.rule_timeframe_counts)
    timeframe_overlap = sorted(
        set(summary.history_timeframe_counts.keys()) & set(summary.rule_timeframe_counts.keys())
    )

    rule_diagnostics, rule_timeframe_profiles = _load_rule_replay_diagnostics(rule_run_dir)
    missing_history_rules_diagnostics = []
    if rule_diagnostics or rule_timeframe_profiles:
        missing_history_rules_diagnostics = _build_missing_rule_diagnostics(
            missing_history_rules_top,
            rule_diagnostics,
            rule_timeframe_profiles,
        )

    delta_buy_ratio_pct = float(rule_direction_mix["buy_ratio_pct"] - history_direction_mix["buy_ratio_pct"])
    timeframe_overlap_pct = _timeframe_overlap_pct(
        summary.history_timeframe_counts,
        summary.rule_timeframe_counts,
    )
    alignment_assessment = _build_alignment_assessment(
        summary,
        rule_overlap=rule_overlap,
        delta_buy_ratio_pct=delta_buy_ratio_pct,
        new_rule_types_top=new_rule_types_top,
        missing_history_rules_diagnostics=missing_history_rules_diagnostics,
        timeframe_overlap_pct=timeframe_overlap_pct,
    )

    payload = {
        **asdict(summary),
        "generated_at": _utc_now_iso(),
        "delta_return_pct": summary.rule_return_pct - summary.history_return_pct,
        "delta_max_drawdown_pct": summary.rule_max_drawdown_pct - summary.history_max_drawdown_pct,
        "delta_trade_count": summary.rule_trade_count - summary.history_trade_count,
        "delta_excess_return_pct": summary.rule_excess_return_pct - summary.history_excess_return_pct,
        "delta_signal_count": summary.rule_signal_count - summary.history_signal_count,
        "history_direction_mix": history_direction_mix,
        "rule_direction_mix": rule_direction_mix,
        "delta_buy_ratio_pct": delta_buy_ratio_pct,
        "direction_delta": direction_delta,
        "timeframe_delta_top": timeframe_delta_top,
        "signal_type_delta_top": signal_type_delta_top,
        "rule_overlap": rule_overlap,
        "history_timeframe_profile": history_timeframe_profile,
        "rule_timeframe_profile": rule_timeframe_profile,
        "timeframe_overlap": timeframe_overlap,
        "timeframe_overlap_pct": _round2(timeframe_overlap_pct),
        "missing_history_rules_top": missing_history_rules_top,
        "new_rule_types_top": new_rule_types_top,
        "missing_history_rules_diagnostics": missing_history_rules_diagnostics,
        **alignment_assessment,
    }

    (out_dir / "comparison.json").write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    alignment_breakdown = payload["alignment_breakdown"]
    alignment_inputs = payload["alignment_inputs"]
    alignment_warnings = payload["alignment_warnings"]
    alignment_warning_counts = payload["alignment_warning_counts"]

    md = [
        "# Backtest Mode Comparison",
        "",
        f"- run_id: `{summary.run_id}`",
        f"- generated_at: `{payload['generated_at']}`",
        f"- history_run: `{summary.history_run_id}`",
        f"- rule_replay_run: `{summary.rule_run_id}`",
        "",
        "## Metrics",
        "",
        f"- Return: history `{summary.history_return_pct:+.2f}%` vs rule `{summary.rule_return_pct:+.2f}%`",
        (
            f"- Max Drawdown: history `{summary.history_max_drawdown_pct:.2f}%` "
            f"vs rule `{summary.rule_max_drawdown_pct:.2f}%`"
        ),
        f"- Trades: history `{summary.history_trade_count}` vs rule `{summary.rule_trade_count}`",
        (
            f"- Excess Return(BH): history `{summary.history_excess_return_pct:+.2f}%` "
            f"vs rule `{summary.rule_excess_return_pct:+.2f}%`"
        ),
        f"- Signal Count: history `{summary.history_signal_count}` vs rule `{summary.rule_signal_count}`",
        f"- Bar Count: history `{summary.history_bar_count}` vs rule `{summary.rule_bar_count}`",
        "",
        "## Delta (rule - history)",
        "",
        f"- Return Delta: `{payload['delta_return_pct']:+.2f}%`",
        f"- Max Drawdown Delta: `{payload['delta_max_drawdown_pct']:+.2f}%`",
        f"- Trade Count Delta: `{payload['delta_trade_count']:+d}`",
        f"- Excess Return Delta: `{payload['delta_excess_return_pct']:+.2f}%`",
        f"- Signal Count Delta: `{payload['delta_signal_count']:+d}`",
        "",
        "## Rule Alignment",
        "",
        (
            f"- Alignment Score: `{float(payload['alignment_score']):.2f}` / 100 "
            f"(status=`{payload['alignment_status']}`, risk=`{payload['alignment_risk_level']}`)"
        ),
        (
            f"- Risk Summary: `{payload['alignment_risk_summary']}` | "
            f"warnings=error:{int(alignment_warning_counts.get('error', 0))} "
            f"warn:{int(alignment_warning_counts.get('warn', 0))}"
        ),
        (
            f"- Breakdown: overlap `{float(alignment_breakdown['rule_overlap_score']):.2f}` | "
            f"top-rules `{float(alignment_breakdown['top_rule_score']):.2f}` | "
            f"signal-count `{float(alignment_breakdown['signal_count_score']):.2f}` | "
            f"direction `{float(alignment_breakdown['direction_score']):.2f}` | "
            f"timeframe `{float(alignment_breakdown['timeframe_score']):.2f}`"
        ),
        (
            f"- Rule Type Overlap: shared `{rule_overlap['shared_rule_types']}` / "
            f"history `{rule_overlap['history_rule_types']}` / rule `{rule_overlap['rule_rule_types']}`"
        ),
        (
            f"- Jaccard: `{float(rule_overlap['jaccard_pct']):.2f}%` | "
            f"history coverage: `{float(rule_overlap['history_coverage_pct']):.2f}%` | "
            f"rule overlap: `{float(rule_overlap['rule_overlap_pct']):.2f}%`"
        ),
        (
            f"- Signal Count Delta Pct: `{float(alignment_inputs['signal_count_delta_pct']):.2f}%` | "
            f"Buy Ratio Delta: `{float(alignment_inputs['buy_ratio_delta_pct']):.2f}%` | "
            f"Timeframe Set Overlap: `{float(payload['timeframe_overlap_pct']):.2f}%`"
        ),
        (
            f"- Timeframe overlap keys: `{', '.join(timeframe_overlap) if timeframe_overlap else '--'}` | "
            f"history dominant={history_timeframe_profile['dominant']} | "
            f"rule dominant={rule_timeframe_profile['dominant']}"
        ),
    ]

    md.extend(["", "### Threshold Warnings", ""])
    if alignment_warnings:
        for row in alignment_warnings:
            actual = row.get("actual")
            threshold = row.get("threshold")
            detail = []
            if actual is not None:
                detail.append(f"actual={float(actual):.2f}")
            if threshold is not None:
                detail.append(f"threshold={float(threshold):.2f}")
            detail_text = f" ({', '.join(detail)})" if detail else ""
            md.append(
                f"- [{row['severity']}] {row['kind']} / {row['subject']}: {row['message']}{detail_text}"
            )
    else:
        md.append("- none")

    md.extend(
        [
            "",
            "### Missing in Rule Replay (history>0, rule=0)",
            "",
            "| signal_type | history | rule | delta |",
            "|---|---:|---:|---:|",
        ]
    )

    if missing_history_rules_top:
        for row in missing_history_rules_top:
            md.append(
                "| "
                f"{row['key']} | {row['history_count']} | {row['rule_count']} | {int(row['delta']):+d} |"
            )
    else:
        md.append("| -- | -- | -- | -- |")

    if missing_history_rules_diagnostics:
        md.extend(
            [
                "",
                "### Missing Rule Diagnostics",
                "",
                "| signal_type | evaluated | condition_fail | tf_filter | volume_filter | cooldown | reason |",
                "|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in missing_history_rules_diagnostics:
            md.append(
                "| "
                f"{row['key']} | {row['evaluated']} | {row['condition_failed']} | "
                f"{row['timeframe_filtered']} | {row['volume_filtered']} | "
                f"{row['cooldown_blocked']} | {row['primary_block_reason']} |"
            )

    md.extend(
        [
            "",
            "### New in Rule Replay (history=0, rule>0)",
            "",
            "| signal_type | history | rule | delta |",
            "|---|---:|---:|---:|",
        ]
    )

    if new_rule_types_top:
        for row in new_rule_types_top:
            md.append(
                "| "
                f"{row['key']} | {row['history_count']} | {row['rule_count']} | {int(row['delta']):+d} |"
            )
    else:
        md.append("| -- | -- | -- | -- |")

    md.extend(
        [
            "",
            "## Signal Profile",
            "",
            (
                "- Direction Mix (history): "
                f"BUY={history_direction_mix['buy']} SELL={history_direction_mix['sell']} "
                f"OTHER={history_direction_mix['other']} BUY_RATIO={history_direction_mix['buy_ratio_pct']:.2f}%"
            ),
            (
                "- Direction Mix (rule): "
                f"BUY={rule_direction_mix['buy']} SELL={rule_direction_mix['sell']} "
                f"OTHER={rule_direction_mix['other']} BUY_RATIO={rule_direction_mix['buy_ratio_pct']:.2f}%"
            ),
            f"- Buy Ratio Delta: `{payload['delta_buy_ratio_pct']:+.2f}%`",
            (
                "- Timeframe dominant: "
                f"history={history_timeframe_profile['dominant']} "
                f"({history_timeframe_profile['dominant_ratio_pct']:.2f}%), "
                f"rule={rule_timeframe_profile['dominant']} "
                f"({rule_timeframe_profile['dominant_ratio_pct']:.2f}%)"
            ),
            "",
            "### Top Signal-Type Delta",
            "",
            "| signal_type | history | rule | delta |",
            "|---|---:|---:|---:|",
        ]
    )

    if signal_type_delta_top:
        for row in signal_type_delta_top:
            md.append(
                "| "
                f"{row['key']} | {row['history_count']} | {row['rule_count']} | {int(row['delta']):+d} |"
            )
    else:
        md.append("| -- | -- | -- | -- |")

    md.extend(
        [
            "",
            "### Timeframe Delta",
            "",
            "| timeframe | history | rule | delta |",
            "|---|---:|---:|---:|",
        ]
    )

    if timeframe_delta_top:
        for row in timeframe_delta_top:
            md.append(
                "| "
                f"{row['key']} | {row['history_count']} | {row['rule_count']} | {int(row['delta']):+d} |"
            )
    else:
        md.append("| -- | -- | -- | -- |")

    md.extend(
        [
            "",
            "### Direction Delta",
            "",
            "| direction | history | rule | delta |",
            "|---|---:|---:|---:|",
        ]
    )

    if direction_delta:
        for row in direction_delta:
            md.append(
                "| "
                f"{row['key']} | {row['history_count']} | {row['rule_count']} | {int(row['delta']):+d} |"
            )
    else:
        md.append("| -- | -- | -- | -- |")

    md.extend(
        [
            "",
            "## Notes",
            "",
            "- `history` = signal_history 历史信号回测",
            "- `rule` = SQLite 129规则离线重放回测",
        ]
    )

    (out_dir / "comparison.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return out_dir
