#!/usr/bin/env python3
"""Generate and optionally apply issue-fill snippets from backtest validation artifacts."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACTS_ROOT = REPO_ROOT / "artifacts" / "backtest"
DEFAULT_ISSUES_ROOT = REPO_ROOT / ".issues" / "open" / "006-backtest"
ISSUE_FILES = {
    "006-01": "006-01-feature-backtest-p0-1-binance-liquidation-model.md",
    "006-02": "006-02-feature-backtest-p0-2-binance-cost-model.md",
    "006-03": "006-03-feature-backtest-p0-3-input-quality-artifacts.md",
    "006-04": "006-04-feature-backtest-p0-4-alignment-score.md",
}


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _read_csv_rows(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _fmt(value: Any, *, digits: int = 2, suffix: str = "") -> str:
    if value is None or value == "":
        return "未找到"
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value}{suffix}"
    if isinstance(value, float):
        return f"{value:.{digits}f}{suffix}"
    return str(value)


def _json_match(path: Path, *, run_id: str, mode: str | None = None) -> bool:
    payload = _read_json(path)
    if not payload:
        return False
    if str(payload.get("run_id") or "") != run_id:
        return False
    if mode is not None and str(payload.get("mode") or "") != mode:
        return False
    return True


def _discover_metrics_dir(artifacts_root: Path, *, run_id: str, mode: str | None = None) -> Path | None:
    candidates = [path.parent for path in artifacts_root.rglob("metrics.json") if _json_match(path, run_id=run_id, mode=mode)]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0]


def _discover_comparison_dir(artifacts_root: Path, *, run_id: str) -> Path | None:
    candidates: list[Path] = []
    for path in artifacts_root.rglob("comparison.json"):
        payload = _read_json(path)
        if str(payload.get("run_id") or "") == run_id:
            candidates.append(path.parent)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0]


def _checkbox(done: bool) -> str:
    return "x" if done else " "


def _top_text(rows: list[tuple[str, float]], limit: int = 3, *, digits: int = 2, suffix: str = "") -> str:
    if not rows:
        return "未找到"
    return " / ".join(f"{name}:{value:.{digits}f}{suffix}" for name, value in rows[:limit])


def _liquidation_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        reason = str(row.get("reason") or "").lower()
        exit_kind = str(row.get("exit_kind") or "").lower()
        if "liquid" in reason or "liquid" in exit_kind or reason == "strong_close":
            out.append(row)
    return out


def _first_side_row(rows: list[dict[str, str]], side: str) -> dict[str, str] | None:
    target = side.upper()
    for row in rows:
        if str(row.get("side") or "").upper() == target:
            return row
    return None


def _sample_trade(row: dict[str, str] | None) -> str:
    if not row:
        return "未找到"
    symbol = row.get("symbol") or "--"
    entry_ts = row.get("entry_ts") or "--"
    exit_ts = row.get("exit_ts") or "--"
    qty = row.get("qty") or "--"
    reason = row.get("reason") or row.get("exit_kind") or "--"
    return f"{symbol} | {entry_ts} -> {exit_ts} | qty={qty} | reason={reason}"


def _quality_summary(input_quality: dict[str, Any]) -> dict[str, str]:
    symbol_rows = input_quality.get("symbol_rows") or []
    lowest_quality: list[tuple[str, float]] = []
    if isinstance(symbol_rows, list):
        for row in symbol_rows:
            if not isinstance(row, dict):
                continue
            lowest_quality.append((str(row.get("symbol") or "--"), float(row.get("quality_score") or 0.0)))
        lowest_quality.sort(key=lambda item: item[1])

    return {
        "quality_score": _fmt(input_quality.get("quality_score")),
        "quality_status": str(input_quality.get("quality_status") or "未找到"),
        "signal_days": _fmt(input_quality.get("signal_days") or input_quality.get("aggregated_signal_bucket_count")),
        "signal_count": _fmt(input_quality.get("signal_count")),
        "candle_coverage_pct": _fmt(input_quality.get("candle_coverage_pct"), suffix="%"),
        "no_next_open_bucket_count": _fmt(input_quality.get("no_next_open_bucket_count")),
        "dropped_signal_count": _fmt(input_quality.get("dropped_signal_count")),
        "lowest_quality_symbols": _top_text(lowest_quality),
        "quality_breakdown": json.dumps(input_quality.get("quality_breakdown") or {}, ensure_ascii=False),
    }


def _alignment_summary(comparison: dict[str, Any]) -> dict[str, str]:
    warning_counts = comparison.get("alignment_warning_counts") or {}
    warnings = comparison.get("alignment_warnings") or []
    missing_reasons: dict[str, int] = {}
    if isinstance(warnings, list):
        for row in warnings:
            if not isinstance(row, dict):
                continue
            subject = str(row.get("subject") or row.get("kind") or "unknown")
            missing_reasons[subject] = missing_reasons.get(subject, 0) + 1
    top_missing = sorted(missing_reasons.items(), key=lambda item: (-item[1], item[0]))
    return {
        "alignment_score": _fmt(comparison.get("alignment_score")),
        "alignment_status": str(comparison.get("alignment_status") or "未找到"),
        "alignment_risk_level": str(comparison.get("alignment_risk_level") or "未找到"),
        "alignment_warning_counts": json.dumps(warning_counts, ensure_ascii=False),
        "top_missing_reasons": ", ".join(f"{name}:{count}" for name, count in top_missing[:5]) or "未找到",
        "risk_summary": str(comparison.get("alignment_risk_summary") or "未找到"),
    }


def _build_context(
    *,
    run_prefix: str,
    executed_command: str,
    db_target: str,
    history_dir: Path | None,
    compare_dir: Path | None,
    walk_dir: Path | None,
) -> dict[str, Any]:
    history_metrics = _read_json(history_dir / "metrics.json") if history_dir else {}
    history_input_quality = _read_json(history_dir / "input_quality.json") if history_dir else {}
    comparison = _read_json(compare_dir / "comparison.json") if compare_dir else {}
    walk_metrics = _read_json(walk_dir / "metrics.json") if walk_dir else {}
    trades = _read_csv_rows(history_dir / "trades.csv") if history_dir else []
    quality = _quality_summary(history_input_quality)
    alignment = _alignment_summary(comparison)
    liquidations = _liquidation_rows(trades)
    history_run_id = str(history_metrics.get("run_id") or "未找到")
    compare_run_id = str(comparison.get("run_id") or "未找到")
    walk_run_id = str(walk_metrics.get("run_id") or "未找到")
    symbols = history_metrics.get("symbols") or []
    symbols_text = ",".join(str(item) for item in symbols) if isinstance(symbols, list) and symbols else "未找到"
    window_text = f"{history_metrics.get('start') or '未找到'} -> {history_metrics.get('end') or '未找到'}"
    generated_at = datetime.now(tz=timezone.utc).isoformat(sep=" ")
    return {
        "generated_at": generated_at,
        "run_prefix": run_prefix or "--",
        "executed_command": executed_command or "待人工补充（可按 run_prefix 反查 shell 历史）",
        "db_target": db_target or "待人工补充",
        "history_dir": history_dir,
        "compare_dir": compare_dir,
        "walk_dir": walk_dir,
        "history_metrics": history_metrics,
        "history_input_quality": history_input_quality,
        "comparison": comparison,
        "walk_metrics": walk_metrics,
        "trades": trades,
        "liquidations": liquidations,
        "long_liq": _first_side_row(liquidations, "LONG"),
        "short_liq": _first_side_row(liquidations, "SHORT"),
        "quality": quality,
        "alignment": alignment,
        "history_run_id": history_run_id,
        "compare_run_id": compare_run_id,
        "walk_run_id": walk_run_id,
        "symbols_text": symbols_text,
        "window_text": window_text,
    }


def _section_header(ctx: dict[str, Any]) -> list[str]:
    return [
        "## 真实窗口回填模板",
        "",
        f"> 本区块由 `scripts/backtest_issue_fill.py` 自动生成，时间：`{ctx['generated_at']}`",
        "> 带“需人工复核 / 待人工补充”的项仍需人工最终确认。",
        "",
    ]


def _build_issue_007_section(ctx: dict[str, Any]) -> str:
    lines = _section_header(ctx) + [
        "### 执行信息",
        "",
        f"- 执行日期：`{ctx['generated_at']}`",
        f"- 执行命令：`{ctx['executed_command']}`",
        f"- `run_id`：`{ctx['history_run_id']}`",
        f"- 时间窗口：`{ctx['window_text']}`",
        f"- symbols：`{ctx['symbols_text']}`",
        f"- DB target：`{ctx['db_target']}`",
        "",
        "### 真实窗口观察",
        "",
        f"- 强平触发笔数：`{len(ctx['liquidations'])}`",
        f"- LONG 强平是否可解释：`{'窗口内未发现 LONG 强平' if ctx['long_liq'] is None else '需结合强平样例人工复核'}`",
        f"- SHORT 强平是否可解释：`{'窗口内未发现 SHORT 强平' if ctx['short_liq'] is None else '需结合强平样例人工复核'}`",
        "- 强平触发价格口径是否合理：`需人工复核`",
        "- 强平成本是否偏大/偏小：`需人工复核`",
        "- 是否出现异常权益跳变：`需人工复核 equity_curve.csv`",
        "",
        "### 重点产物摘录",
        "",
        f"- `trades.csv` 强平样例行：`{_sample_trade(ctx['long_liq'] or ctx['short_liq'])}`",
        f"- `report.md` 强平摘要：`{ctx['history_dir'] / 'report.md' if ctx['history_dir'] else '未找到'}`",
        f"- `metrics.json` 相关字段：`{ctx['history_dir'] / 'metrics.json' if ctx['history_dir'] else '未找到'}`",
        "",
        "### 参数与结论",
        "",
        "- 当前 `maintenance_margin_ratio` 是否合理：`待人工补充`",
        "- 当前 `liquidation_fee_bps` 是否合理：`待人工补充`",
        "- 当前 `liquidation_buffer_bps` 是否合理：`待人工补充`",
        "- 是否需要继续调参：`待人工补充`",
        "- 最终结论：`待人工补充`",
        "",
        "### 回填完成检查",
        "",
        f"- [{_checkbox(ctx['history_run_id'] != '未找到')}] 已粘贴真实窗口执行命令与 `run_id`",
        f"- [{_checkbox(True)}] 已给出至少 1 笔 LONG/SHORT 强平样例或说明窗口内未出现强平",
        "- [ ] 已判断强平价格口径与成本是否可接受",
        "- [ ] 已给出是否调参的明确结论",
    ]
    return "\n".join(lines) + "\n"


def _build_issue_008_section(ctx: dict[str, Any]) -> str:
    metrics = ctx["history_metrics"]
    lines = _section_header(ctx) + [
        "### 执行信息",
        "",
        f"- 执行日期：`{ctx['generated_at']}`",
        f"- 执行命令：`{ctx['executed_command']}`",
        f"- `run_id`：`{ctx['history_run_id']}`",
        f"- 时间窗口：`{ctx['window_text']}`",
        f"- symbols：`{ctx['symbols_text']}`",
        f"- DB target：`{ctx['db_target']}`",
        "",
        "### 真实窗口观察",
        "",
        f"- `gross_pnl`：`{_fmt(metrics.get('gross_pnl'))}`",
        f"- `trading_fee`：`{_fmt(metrics.get('trading_fee'))}`",
        f"- `funding_fee`：`{_fmt(metrics.get('funding_fee'))}`",
        f"- `net_pnl`：`{_fmt(metrics.get('net_pnl'))}`",
        f"- `gross_to_net_retention_pct`：`{_fmt(metrics.get('gross_to_net_retention_pct'), suffix='%')}`",
        f"- funding 方向解释是否合理：`{'需人工复核' if metrics else '未找到'}`",
        f"- 成本是否明显侵蚀策略收益：`{metrics.get('cost_summary') or '需人工复核'}`",
        "",
        "### 重点产物摘录",
        "",
        f"- `metrics.json` 成本字段：`{ctx['history_dir'] / 'metrics.json' if ctx['history_dir'] else '未找到'}`",
        f"- `report.md` 成本摘要：`{ctx['history_dir'] / 'report.md' if ctx['history_dir'] else '未找到'}`",
        f"- `trades.csv` 单笔成本样例：`{_sample_trade(ctx['trades'][0] if ctx['trades'] else None)}`",
        "",
        "### 参数与结论",
        "",
        "- 当前 `maker_fee_bps` 是否合理：`待人工补充`",
        "- 当前 `taker_fee_bps` 是否合理：`待人工补充`",
        "- 当前 `funding_rate_bps_per_8h` 是否合理：`待人工补充`",
        "- 是否需要按 Binance VIP / maker-taker 场景细分：`待人工补充`",
        "- 最终结论：`待人工补充`",
        "",
        "### 回填完成检查",
        "",
        f"- [{_checkbox(metrics.get('gross_pnl') is not None)}] 已回填 `gross_pnl / trading_fee / funding_fee / net_pnl`",
        "- [ ] 已说明 funding 在 LONG/SHORT 方向上的解释是否合理",
        "- [ ] 已判断成本侵蚀是否符合 Binance USD-M 首版预期",
        "- [ ] 已给出是否调参/分层的明确结论",
    ]
    return "\n".join(lines) + "\n"


def _build_issue_009_section(ctx: dict[str, Any]) -> str:
    quality = ctx["quality"]
    lines = _section_header(ctx) + [
        "### 执行信息",
        "",
        f"- 执行日期：`{ctx['generated_at']}`",
        f"- 执行命令：`{ctx['executed_command']}`",
        f"- `run_id`：`{ctx['history_run_id']}`",
        f"- 时间窗口：`{ctx['window_text']}`",
        f"- symbols：`{ctx['symbols_text']}`",
        f"- DB target：`{ctx['db_target']}`",
        "",
        "### 真实窗口观察",
        "",
        f"- `quality_score`：`{quality['quality_score']}`",
        f"- `quality_status`：`{quality['quality_status']}`",
        f"- `signal_days`：`{quality['signal_days']}`",
        f"- `signal_count`：`{quality['signal_count']}`",
        f"- `candle_coverage_pct`：`{quality['candle_coverage_pct']}`",
        f"- 缺失 bar 是否集中在少数 symbol：`{quality['lowest_quality_symbols']}`",
        f"- 无 `next_open` 可成交次数是否异常：`{quality['no_next_open_bucket_count']}`",
        "",
        "### 重点产物摘录",
        "",
        f"- `input_quality.json` 摘要：`{ctx['history_dir'] / 'input_quality.json' if ctx['history_dir'] else '未找到'}`",
        f"- `report.md` 质量摘要：`{ctx['history_dir'] / 'report.md' if ctx['history_dir'] else '未找到'}`",
        f"- `quality_breakdown` / penalty 来源：`{quality['quality_breakdown']}`",
        "",
        "### 参数与结论",
        "",
        "- 默认 `--min-signal-days 7` 是否合理：`待人工补充`",
        "- 默认 `--min-signal-count 200` 是否合理：`待人工补充`",
        "- 默认 `--min-candle-coverage-pct 95` 是否合理：`待人工补充`",
        "- 是否需要上调/下调门槛：`待人工补充`",
        "- 最终结论：`待人工补充`",
        "",
        "### 回填完成检查",
        "",
        f"- [{_checkbox(quality['quality_score'] != '未找到')}] 已回填 `quality_score / quality_status / coverage` 关键结果",
        "- [ ] 已说明缺失 bar / 无 next_open 是否集中于个别 symbol",
        "- [ ] 已判断默认 coverage gate 是否需要调整",
        "- [ ] 已给出是否继续阻断/放宽的明确结论",
    ]
    return "\n".join(lines) + "\n"


def _build_issue_010_section(ctx: dict[str, Any]) -> str:
    comparison = ctx["comparison"]
    alignment = ctx["alignment"]
    lines = _section_header(ctx) + [
        "### 执行信息",
        "",
        f"- 执行日期：`{ctx['generated_at']}`",
        f"- 执行命令：`{ctx['executed_command']}`",
        f"- `run_id`：`{ctx['compare_run_id']}`",
        f"- 时间窗口：`{ctx['window_text']}`",
        f"- symbols：`{ctx['symbols_text']}`",
        f"- DB target：`{ctx['db_target']}`",
        "",
        "### 真实窗口观察",
        "",
        f"- `alignment_score`：`{alignment['alignment_score']}`",
        f"- `alignment_status`：`{alignment['alignment_status']}`",
        f"- `alignment_risk_level`：`{alignment['alignment_risk_level']}`",
        f"- `alignment_warning_counts`：`{alignment['alignment_warning_counts']}`",
        f"- Top missing reasons：`{alignment['top_missing_reasons']}`",
        f"- 是否主要受 coverage / timeframe / rule drift 影响：`{alignment['risk_summary']}`",
        "",
        "### 重点产物摘录",
        "",
        f"- `comparison.json` 评分摘要：`{ctx['compare_dir'] / 'comparison.json' if ctx['compare_dir'] else '未找到'}`",
        f"- `comparison.md` 风险摘要：`{ctx['compare_dir'] / 'comparison.md' if ctx['compare_dir'] else '未找到'}`",
        f"- `rule_replay_diagnostics.json` 关键诊断：`{ctx['compare_dir'] / 'rule_replay_diagnostics.json' if ctx['compare_dir'] and (ctx['compare_dir'] / 'rule_replay_diagnostics.json').exists() else '未找到'}`",
        "",
        "### 阈值与结论",
        "",
        "- 当前 `--alignment-min-score 70` 是否合理：`待人工补充`",
        "- 当前 `--alignment-max-risk-level medium` 是否合理：`待人工补充`",
        "- 是否需要区分 BTC/ETH 与长尾币阈值：`待人工补充`",
        "- 是否可以接入本地 CI gate：`待人工补充`",
        "- 最终结论：`待人工补充`",
        "",
        "### 回填完成检查",
        "",
        f"- [{_checkbox(comparison.get('alignment_score') is not None)}] 已回填 `alignment_score / alignment_status / alignment_risk_level`",
        "- [ ] 已说明主要偏差来自 coverage 还是规则口径漂移",
        "- [ ] 已判断现有 gate 阈值是否需要调整",
        "- [ ] 已给出是否可接 CI/local gate 的明确结论",
    ]
    return "\n".join(lines) + "\n"


def _build_combined_markdown(ctx: dict[str, Any]) -> str:
    sections = [
        "# Backtest Issue Fill Draft",
        "",
        f"- generated_at: `{ctx['generated_at']}`",
        f"- run_prefix: `{ctx['run_prefix']}`",
        f"- history_dir: `{ctx['history_dir']}`" if ctx["history_dir"] else "- history_dir: `未找到`",
        f"- compare_dir: `{ctx['compare_dir']}`" if ctx["compare_dir"] else "- compare_dir: `未找到`",
        f"- walk_forward_dir: `{ctx['walk_dir']}`" if ctx["walk_dir"] else "- walk_forward_dir: `未找到`",
        "",
        "---",
        "",
        "#006-01",
        "",
        _build_issue_007_section(ctx).strip(),
        "",
        "---",
        "",
        "#006-02",
        "",
        _build_issue_008_section(ctx).strip(),
        "",
        "---",
        "",
        "#006-03",
        "",
        _build_issue_009_section(ctx).strip(),
        "",
        "---",
        "",
        "#006-04",
        "",
        _build_issue_010_section(ctx).strip(),
        "",
    ]
    return "\n".join(sections)


def _replace_template_block(issue_path: Path, replacement: str) -> None:
    text = issue_path.read_text(encoding="utf-8")
    start_marker = "## 真实窗口回填模板\n"
    end_marker = "\n## 进展记录\n"
    start = text.find(start_marker)
    if start < 0:
        raise ValueError(f"template section not found: {issue_path}")
    end = text.find(end_marker, start)
    if end < 0:
        raise ValueError(f"progress section not found: {issue_path}")
    new_text = text[:start] + replacement.rstrip() + "\n\n" + text[end + 1 :]
    issue_path.write_text(new_text, encoding="utf-8")


def _apply_issue_updates(issues_root: Path, ctx: dict[str, Any]) -> list[Path]:
    sections = {
        "006-01": _build_issue_007_section(ctx),
        "006-02": _build_issue_008_section(ctx),
        "006-03": _build_issue_009_section(ctx),
        "006-04": _build_issue_010_section(ctx),
    }
    updated: list[Path] = []
    for issue_id, filename in ISSUE_FILES.items():
        issue_path = issues_root / filename
        if not issue_path.exists():
            raise FileNotFoundError(f"issue file not found: {issue_path}")
        _replace_template_block(issue_path, sections[issue_id])
        updated.append(issue_path)
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or apply issue-fill markdown from backtest artifacts")
    parser.add_argument("--artifacts-root", default=str(DEFAULT_ARTIFACTS_ROOT), help="Backtest artifacts root")
    parser.add_argument("--issues-root", default=str(DEFAULT_ISSUES_ROOT), help="Issue directory root")
    parser.add_argument("--run-prefix", default="", help="Validation run prefix, e.g. real-window-20260308-000000")
    parser.add_argument("--history-dir", default="", help="Explicit history backtest output dir")
    parser.add_argument("--compare-dir", default="", help="Explicit compare output dir")
    parser.add_argument("--walk-forward-dir", default="", help="Explicit walk-forward output dir")
    parser.add_argument("--executed-command", default="", help="Optional original validation command")
    parser.add_argument("--db-target", default="", help="Optional DB target label, e.g. localhost:5434/market_data")
    parser.add_argument("--output", default="", help="Combined markdown output path")
    parser.add_argument("--print", action="store_true", dest="print_stdout", help="Print combined markdown to stdout")
    parser.add_argument("--apply-issues", action="store_true", help="Write filled sections back into #006-01/#006-02/#006-03/#006-04 issue files")
    args = parser.parse_args()

    artifacts_root = Path(args.artifacts_root).expanduser().resolve()
    issues_root = Path(args.issues_root).expanduser().resolve()
    run_prefix = str(args.run_prefix or "").strip()
    history_dir = Path(args.history_dir).expanduser().resolve() if args.history_dir else None
    compare_dir = Path(args.compare_dir).expanduser().resolve() if args.compare_dir else None
    walk_dir = Path(args.walk_forward_dir).expanduser().resolve() if args.walk_forward_dir else None

    if history_dir is None and run_prefix:
        history_dir = _discover_metrics_dir(artifacts_root, run_id=f"{run_prefix}-history")
    if compare_dir is None and run_prefix:
        compare_dir = _discover_comparison_dir(artifacts_root, run_id=f"{run_prefix}-compare")
    if walk_dir is None and run_prefix:
        walk_dir = _discover_metrics_dir(artifacts_root, run_id=f"{run_prefix}-wf", mode="walk_forward")

    if history_dir is None and compare_dir is None and walk_dir is None:
        raise SystemExit("No artifacts found. Provide --run-prefix or explicit --history-dir/--compare-dir/--walk-forward-dir.")

    ctx = _build_context(
        run_prefix=run_prefix,
        executed_command=str(args.executed_command or "").strip(),
        db_target=str(args.db_target or "").strip(),
        history_dir=history_dir,
        compare_dir=compare_dir,
        walk_dir=walk_dir,
    )

    combined_markdown = _build_combined_markdown(ctx)
    output_path = Path(args.output).expanduser().resolve() if args.output else artifacts_root / f"issue-fill-{run_prefix or 'manual'}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(combined_markdown, encoding="utf-8")

    updated_paths: list[Path] = []
    if args.apply_issues:
        updated_paths = _apply_issue_updates(issues_root, ctx)

    if args.print_stdout:
        print(combined_markdown, end="")
    else:
        print(output_path)
    if updated_paths:
        for path in updated_paths:
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
