#!/usr/bin/env python3
"""Read existing backtest artifact summaries without running a new backtest."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_NAME = "tradecat_get_backtest_summary"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACTS_ROOT = REPO_ROOT / "artifacts" / "backtest"


@dataclass(frozen=True)
class SummaryCandidate:
    """Loaded summary candidate from an existing artifact directory."""

    kind: str
    run_id: str
    match_ids: tuple[str, ...]
    strategy_label: str
    strategy_config_path: str
    strategy_summary: str
    symbols: tuple[str, ...]
    start: str
    end: str
    timeframe: str
    summary_path: Path
    artifact_dir: Path
    sort_key: float
    payload: dict[str, Any]
    metadata_payload: dict[str, Any]
    history_payload: dict[str, Any]
    rule_payload: dict[str, Any]
    walk_forward_payload: dict[str, Any]


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(raw: Any) -> str:
    return str(raw or "").strip()


def _normalize_symbols(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        values = raw.split(",")
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    elif raw is None:
        values = []
    else:
        values = [raw]

    normalized: list[str] = []
    for item in values:
        text = str(item or "").upper().strip()
        if text:
            normalized.append(text)
    return tuple(sorted(dict.fromkeys(normalized)))


def _normalize_compare_base_run_id(run_id: str) -> str:
    rid = _clean_text(run_id)
    if not rid:
        return ""
    for suffix in ("-history", "-rules", "-compare"):
        if rid.endswith(suffix):
            return rid[: -len(suffix)]
    return rid


def _safe_load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _metric_pct(payload: dict[str, Any], pct_key: str, legacy_key: str) -> float | None:
    if pct_key in payload:
        try:
            return float(payload.get(pct_key))
        except Exception:
            return None

    if legacy_key in payload:
        try:
            return float(payload.get(legacy_key)) * 100.0
        except Exception:
            return None

    return None


def _build_file_entry(path: Path) -> dict[str, str]:
    return {
        "path": str(path),
        "relative_path": _relative_to_repo(path),
    }


def _relative_to_repo(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT.resolve()))
    except Exception:
        return str(resolved)


def _load_walk_forward_payload(metrics_payload: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    summary_path = artifact_dir / "walk_forward_summary.json"
    file_payload = _safe_load_json(summary_path)
    if file_payload is not None:
        return file_payload

    nested_payload = metrics_payload.get("walk_forward_summary")
    if isinstance(nested_payload, dict):
        return nested_payload
    return {}


def _load_metrics_candidate(summary_path: Path) -> SummaryCandidate | None:
    payload = _safe_load_json(summary_path)
    if payload is None:
        return None

    artifact_dir = summary_path.parent
    walk_forward_payload = _load_walk_forward_payload(payload, artifact_dir)
    run_id = _clean_text(payload.get("run_id")) or artifact_dir.name
    strategy_label = _clean_text(payload.get("strategy_label"))
    strategy_config_path = _clean_text(payload.get("strategy_config_path"))
    strategy_summary = _clean_text(payload.get("strategy_summary"))

    try:
        sort_key = float(summary_path.stat().st_mtime)
    except Exception:
        sort_key = 0.0

    mode = _clean_text(payload.get("mode"))
    kind = "walk_forward" if mode == "walk_forward" or walk_forward_payload else "metrics"

    return SummaryCandidate(
        kind=kind,
        run_id=run_id,
        match_ids=(run_id,),
        strategy_label=strategy_label,
        strategy_config_path=strategy_config_path,
        strategy_summary=strategy_summary,
        symbols=_normalize_symbols(payload.get("symbols")),
        start=_clean_text(payload.get("start")),
        end=_clean_text(payload.get("end")),
        timeframe=_clean_text(payload.get("timeframe")),
        summary_path=summary_path,
        artifact_dir=artifact_dir,
        sort_key=sort_key,
        payload=payload,
        metadata_payload=payload,
        history_payload={},
        rule_payload={},
        walk_forward_payload=walk_forward_payload,
    )


def _load_comparison_candidate(summary_path: Path) -> SummaryCandidate | None:
    payload = _safe_load_json(summary_path)
    if payload is None:
        return None

    artifact_dir = summary_path.parent
    compare_run_id = _clean_text(payload.get("run_id")) or _normalize_compare_base_run_id(artifact_dir.name)
    base_run_id = _normalize_compare_base_run_id(compare_run_id or artifact_dir.name)
    session_dir = artifact_dir.parent

    history_metrics_path = session_dir / f"{base_run_id}-history" / "metrics.json"
    rule_metrics_path = session_dir / f"{base_run_id}-rules" / "metrics.json"
    history_payload = _safe_load_json(history_metrics_path) or {}
    rule_payload = _safe_load_json(rule_metrics_path) or {}
    metadata_payload = rule_payload or history_payload

    strategy_label = _clean_text(metadata_payload.get("strategy_label"))
    strategy_config_path = _clean_text(metadata_payload.get("strategy_config_path"))
    strategy_summary = _clean_text(metadata_payload.get("strategy_summary"))

    match_ids = []
    for candidate_id in (
        compare_run_id,
        base_run_id,
        _clean_text(payload.get("history_run_id")),
        _clean_text(payload.get("rule_run_id")),
    ):
        if candidate_id and candidate_id not in match_ids:
            match_ids.append(candidate_id)

    try:
        sort_key = float(summary_path.stat().st_mtime)
    except Exception:
        sort_key = 0.0

    return SummaryCandidate(
        kind="comparison",
        run_id=compare_run_id or base_run_id,
        match_ids=tuple(match_ids),
        strategy_label=strategy_label,
        strategy_config_path=strategy_config_path,
        strategy_summary=strategy_summary,
        symbols=_normalize_symbols(metadata_payload.get("symbols")),
        start=_clean_text(metadata_payload.get("start")),
        end=_clean_text(metadata_payload.get("end")),
        timeframe=_clean_text(metadata_payload.get("timeframe")),
        summary_path=summary_path,
        artifact_dir=artifact_dir,
        sort_key=sort_key,
        payload=payload,
        metadata_payload=metadata_payload,
        history_payload=history_payload,
        rule_payload=rule_payload,
        walk_forward_payload={},
    )


def _iter_summary_paths(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []

    paths: list[Path] = []
    for filename in ("metrics.json", "comparison.json"):
        for path in root.rglob(filename):
            try:
                relative_parts = path.relative_to(root).parts
            except Exception:
                relative_parts = ()
            if relative_parts and relative_parts[0] == "latest":
                continue
            if path.is_file():
                paths.append(path)

    paths.sort(key=lambda item: str(item))
    return paths


def _scan_candidates(root: Path) -> list[SummaryCandidate]:
    candidates: list[SummaryCandidate] = []
    for path in _iter_summary_paths(root):
        if path.name == "comparison.json":
            candidate = _load_comparison_candidate(path)
        else:
            candidate = _load_metrics_candidate(path)
        if candidate is not None and candidate.run_id:
            candidates.append(candidate)
    return candidates


def _matches_strategy(candidate: SummaryCandidate, strategy_query: str) -> bool:
    query = _clean_text(strategy_query).lower()
    if not query:
        return True

    fields = (
        candidate.strategy_label,
        candidate.strategy_config_path,
        candidate.strategy_summary,
    )
    return any(query in field.lower() for field in fields if field)


def _matches_symbols(candidate: SummaryCandidate, requested_symbols: tuple[str, ...]) -> bool:
    if not requested_symbols:
        return True
    if not candidate.symbols:
        return False
    return set(requested_symbols).issubset(set(candidate.symbols))


def _match_rank(candidate: SummaryCandidate, requested_run_id: str) -> int:
    run_id = _clean_text(requested_run_id)
    if not run_id:
        return -1
    if candidate.run_id == run_id:
        return 30
    if run_id in candidate.match_ids:
        return 20

    base_run_id = _normalize_compare_base_run_id(run_id)
    if base_run_id and candidate.run_id == base_run_id:
        return 15
    if base_run_id and base_run_id in candidate.match_ids:
        return 10
    return -1


def _kind_priority(kind: str) -> int:
    if kind == "comparison":
        return 2
    if kind == "walk_forward":
        return 1
    return 0


def _select_candidate(
    candidates: list[SummaryCandidate],
    *,
    run_id: str,
    strategy: str,
    symbols: tuple[str, ...],
) -> tuple[SummaryCandidate | None, int]:
    matches: list[tuple[int, float, int, SummaryCandidate]] = []
    for candidate in candidates:
        rank = _match_rank(candidate, run_id)
        if rank < 0:
            continue
        if not _matches_strategy(candidate, strategy):
            continue
        if not _matches_symbols(candidate, symbols):
            continue
        matches.append((rank, candidate.sort_key, _kind_priority(candidate.kind), candidate))

    if not matches:
        return None, 0

    matches.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return matches[0][3], len(matches)


def _build_strategy_payload(candidate: SummaryCandidate) -> dict[str, str]:
    return {
        "label": candidate.strategy_label,
        "config_path": candidate.strategy_config_path,
        "summary": candidate.strategy_summary,
    }


def _build_window_payload(candidate: SummaryCandidate) -> dict[str, str]:
    return {
        "start": candidate.start,
        "end": candidate.end,
        "timeframe": candidate.timeframe,
    }


def _build_metrics_summary(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}

    return {
        "mode": _clean_text(payload.get("mode")),
        "total_return_pct": _metric_pct(payload, "total_return_pct", "total_return"),
        "max_drawdown_pct": _metric_pct(payload, "max_drawdown_pct", "max_drawdown"),
        "sharpe": payload.get("sharpe"),
        "trade_count": payload.get("trade_count"),
        "win_rate_pct": _metric_pct(payload, "win_rate_pct", "win_rate"),
        "profit_factor": payload.get("profit_factor"),
        "avg_holding_minutes": payload.get("avg_holding_minutes"),
        "signal_count": payload.get("signal_count"),
        "bar_count": payload.get("bar_count"),
        "excess_return_pct": payload.get("excess_return_pct"),
        "strategy_summary": _clean_text(payload.get("strategy_summary")),
    }


def _build_metrics_payload(candidate: SummaryCandidate) -> dict[str, Any]:
    metrics = _build_metrics_summary(candidate.payload)
    if candidate.kind == "walk_forward":
        metrics["walk_forward"] = candidate.walk_forward_payload
    return metrics


def _build_comparison_metrics_payload(candidate: SummaryCandidate) -> dict[str, Any]:
    payload = candidate.payload
    return {
        "mode": "compare_history_rule",
        "history": _build_metrics_summary(candidate.history_payload),
        "rule": _build_metrics_summary(candidate.rule_payload),
        "delta": {
            "return_pct": payload.get("delta_return_pct"),
            "max_drawdown_pct": payload.get("delta_max_drawdown_pct"),
            "trade_count": payload.get("delta_trade_count"),
            "excess_return_pct": payload.get("delta_excess_return_pct"),
            "signal_count": payload.get("delta_signal_count"),
            "buy_ratio_pct": payload.get("delta_buy_ratio_pct"),
        },
        "alignment": {
            "score": payload.get("alignment_score"),
            "status": _clean_text(payload.get("alignment_status")),
            "risk_level": _clean_text(payload.get("alignment_risk_level")),
            "risk_summary": _clean_text(payload.get("alignment_risk_summary")),
            "warnings": payload.get("alignment_warnings") if isinstance(payload.get("alignment_warnings"), list) else [],
        },
        "rule_overlap": payload.get("rule_overlap") if isinstance(payload.get("rule_overlap"), dict) else {},
        "signal_type_delta_top": (
            payload.get("signal_type_delta_top") if isinstance(payload.get("signal_type_delta_top"), list) else []
        ),
        "timeframe_delta_top": (
            payload.get("timeframe_delta_top") if isinstance(payload.get("timeframe_delta_top"), list) else []
        ),
    }


def _build_artifacts_payload(candidate: SummaryCandidate) -> dict[str, Any]:
    files: dict[str, dict[str, str]] = {
        "summary": _build_file_entry(candidate.summary_path),
    }

    if candidate.kind == "comparison":
        history_metrics_path = candidate.summary_path.parent.parent / f"{candidate.run_id}-history" / "metrics.json"
        rule_metrics_path = candidate.summary_path.parent.parent / f"{candidate.run_id}-rules" / "metrics.json"
        history_report_path = history_metrics_path.parent / "report.md"
        rule_report_path = rule_metrics_path.parent / "report.md"

        if history_metrics_path.exists():
            files["history_metrics_json"] = _build_file_entry(history_metrics_path)
        if rule_metrics_path.exists():
            files["rule_metrics_json"] = _build_file_entry(rule_metrics_path)
        if history_report_path.exists():
            files["history_report_md"] = _build_file_entry(history_report_path)
        if rule_report_path.exists():
            files["rule_report_md"] = _build_file_entry(rule_report_path)
    else:
        for name, filename in (
            ("metrics_json", "metrics.json"),
            ("report_md", "report.md"),
            ("trades_csv", "trades.csv"),
            ("equity_curve_csv", "equity_curve.csv"),
            ("input_quality_json", "input_quality.json"),
            ("stability_report_json", "stability_report.json"),
            ("walk_forward_summary_json", "walk_forward_summary.json"),
        ):
            path = candidate.artifact_dir / filename
            if path.exists():
                files[name] = _build_file_entry(path)

    return {
        "kind": candidate.kind,
        "artifact_dir": str(candidate.artifact_dir),
        "artifact_dir_rel": _relative_to_repo(candidate.artifact_dir),
        "summary_file": str(candidate.summary_path),
        "summary_file_rel": _relative_to_repo(candidate.summary_path),
        "files": files,
    }


def _build_data_payload(candidate: SummaryCandidate) -> dict[str, Any]:
    if candidate.kind == "comparison":
        metrics = _build_comparison_metrics_payload(candidate)
    else:
        metrics = _build_metrics_payload(candidate)

    return {
        "run_id": candidate.run_id,
        "strategy": _build_strategy_payload(candidate),
        "symbols": list(candidate.symbols),
        "window": _build_window_payload(candidate),
        "metrics": metrics,
        "artifacts": _build_artifacts_payload(candidate),
    }


def _matched_by(candidate: SummaryCandidate, run_id: str, strategy: str, symbols: tuple[str, ...]) -> list[str]:
    reasons: list[str] = []
    requested_run_id = _clean_text(run_id)
    if candidate.run_id == requested_run_id:
        reasons.append("run_id")
    elif requested_run_id in candidate.match_ids:
        reasons.append("related_run_id")
    else:
        base_run_id = _normalize_compare_base_run_id(requested_run_id)
        if base_run_id and base_run_id in candidate.match_ids:
            reasons.append("base_run_id")

    if _clean_text(strategy):
        reasons.append("strategy")
    if symbols:
        reasons.append("symbols")
    return reasons


def _response(
    *,
    ok: bool,
    source: dict[str, Any],
    request: dict[str, Any],
    data: dict[str, Any] | None,
    error: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "tool": TOOL_NAME,
        "ts": _utc_now_iso(),
        "source": source,
        "request": request,
        "data": data,
        "error": error,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read an existing TradeCat backtest summary artifact.")
    parser.add_argument("--run-id", required=True, help="Backtest run_id to resolve from existing artifacts")
    parser.add_argument(
        "--strategy",
        default="",
        help="Optional case-insensitive filter against strategy_label / strategy_config_path / strategy_summary",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Optional comma-separated symbol subset filter, e.g. BTCUSDT,ETHUSDT",
    )
    parser.add_argument(
        "--artifacts-root",
        default=str(DEFAULT_ARTIFACTS_ROOT),
        help="Backtest artifacts root, defaults to artifacts/backtest under the repo root",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv or sys.argv[1:]))
    artifacts_root = Path(str(args.artifacts_root)).expanduser().resolve()
    requested_symbols = _normalize_symbols(args.symbols)
    request = {
        "run_id": _clean_text(args.run_id),
        "strategy": _clean_text(args.strategy),
        "symbols": list(requested_symbols),
        "artifacts_root": str(artifacts_root),
    }
    source = {
        "kind": "artifacts/backtest",
        "artifacts_root": str(artifacts_root),
    }

    try:
        if not artifacts_root.exists() or not artifacts_root.is_dir():
            payload = _response(
                ok=False,
                source=source,
                request=request,
                data=None,
                error={
                    "code": "artifacts_root_missing",
                    "message": f"Backtest artifacts root not found: {artifacts_root}",
                    "details": {},
                },
            )
            print(json.dumps(payload, ensure_ascii=True, indent=2))
            return 1

        candidates = _scan_candidates(artifacts_root)
        candidate, matched_count = _select_candidate(
            candidates,
            run_id=args.run_id,
            strategy=args.strategy,
            symbols=requested_symbols,
        )

        if candidate is None:
            payload = _response(
                ok=False,
                source={
                    **source,
                    "scan": {
                        "candidate_count": len(candidates),
                        "matched_count": matched_count,
                    },
                },
                request=request,
                data=None,
                error={
                    "code": "artifact_not_found",
                    "message": "No backtest summary artifact matched the request.",
                    "details": {},
                },
            )
            print(json.dumps(payload, ensure_ascii=True, indent=2))
            return 1

        payload = _response(
            ok=True,
            source={
                **source,
                "artifact_kind": candidate.kind,
                "summary_path": str(candidate.summary_path),
                "artifact_dir": str(candidate.artifact_dir),
                "matched_by": _matched_by(candidate, args.run_id, args.strategy, requested_symbols),
                "scan": {
                    "candidate_count": len(candidates),
                    "matched_count": matched_count,
                },
            },
            request=request,
            data=_build_data_payload(candidate),
            error=None,
        )
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0
    except Exception as exc:
        payload = _response(
            ok=False,
            source=source,
            request=request,
            data=None,
            error={
                "code": "unexpected_error",
                "message": f"{type(exc).__name__}: {exc}",
                "details": {},
            },
        )
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
