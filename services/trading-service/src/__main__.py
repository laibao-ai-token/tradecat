"""
入口: python -m indicator_service

用法:
    python -m indicator_service --once                   # 一次性计算（推荐）
    python -m indicator_service --engine core            # 主引擎（默认）
    ENABLE_EXPERIMENTAL_ENGINES=1 python -m indicator_service --engine full_async
    ENABLE_EXPERIMENTAL_ENGINES=1 python -m indicator_service --engine event
    python -m indicator_service --symbols BTCUSDT,ETHUSDT --intervals 5m,15m
"""
import argparse
import logging
import os
import sys
import warnings
from pathlib import Path

from common.config_loader import load_repo_env

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)


load_repo_env(repo_root=Path(__file__).resolve().parents[3], set_os_env=True, override=False)
LOG = logging.getLogger(__name__)
_EXPERIMENTAL_ENGINES = {"event", "full_async"}


def _is_engine_arg_explicit(argv: list[str] | None = None) -> bool:
    """Return whether --engine was explicitly provided in argv."""

    args = list(sys.argv[1:] if argv is None else argv)
    for arg in args:
        if arg == "--engine" or arg.startswith("--engine="):
            return True
    return False


def _resolve_engine(args: argparse.Namespace, *, engine_explicit: bool = False) -> tuple[str, str | None]:
    """Resolve engine selection with legacy flag compatibility."""

    legacy_engine: str | None = None
    if args.full_async and args.event:
        raise ValueError("--full-async 和 --event 不能同时使用")
    if args.full_async:
        legacy_engine = "full_async"
    elif args.event:
        legacy_engine = "event"

    selected = args.engine
    if legacy_engine:
        if engine_explicit:
            raise ValueError("请勿混用 --engine 与遗留参数 --event/--full-async，请仅保留 --engine")
        if selected != "core" and selected != legacy_engine:
            raise ValueError(f"--engine={selected} 与遗留参数冲突（期望 {legacy_engine}）")
        selected = legacy_engine

    return selected, legacy_engine


def main():
    parser = argparse.ArgumentParser(description="指标计算服务")
    parser.add_argument("--once", action="store_true", help="一次性计算（推荐，可配合crontab）")
    parser.add_argument(
        "--engine",
        choices=["core", "event", "full_async"],
        default="core",
        help="引擎类型：core(默认)/event(实验)/full_async(实验)",
    )
    parser.add_argument("--full-async", dest="full_async", action="store_true", help="遗留参数，等价于 --engine full_async")
    parser.add_argument("--event", action="store_true", help="遗留参数，等价于 --engine event")
    parser.add_argument("--mode", choices=["all", "batch", "incremental"], default="all", help="计算模式")
    parser.add_argument("--symbols", type=str, help="交易对，逗号分隔")
    parser.add_argument("--intervals", type=str, help="周期，逗号分隔")
    parser.add_argument("--indicators", type=str, help="指标名，逗号分隔")
    parser.add_argument("--lookback", type=int, default=300, help="K线窗口大小")
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("MAX_WORKERS", "4")),
        help="并行进程数",
    )
    parser.add_argument("--log-file", type=str, help="日志文件路径")
    parser.add_argument("--log-level", type=str, default="INFO", help="日志级别")
    parser.add_argument("--json-log", action="store_true", help="使用JSON格式日志")
    parser.add_argument("--metrics-file", type=str, help="指标输出文件路径")

    args = parser.parse_args()
    try:
        selected_engine, legacy_engine = _resolve_engine(
            args, engine_explicit=_is_engine_arg_explicit()
        )
    except ValueError as exc:
        parser.error(str(exc))

    from .core.experimental import is_experimental_enabled

    if selected_engine in _EXPERIMENTAL_ENGINES and not is_experimental_enabled():
        parser.error(
            f"--engine {selected_engine} 当前默认禁用，请先设置 ENABLE_EXPERIMENTAL_ENGINES=1"
        )
    if selected_engine in _EXPERIMENTAL_ENGINES and args.once:
        parser.error("--once 仅支持 --engine core")

    # 初始化可观测性
    from .observability import metrics, setup_logging
    from .observability.alerting import setup_alerting

    setup_logging(
        level=args.log_level,
        log_file=args.log_file,
        json_format=args.json_log,
    )
    if legacy_engine:
        LOG.warning("参数 --%s 已废弃，建议改用 --engine %s", legacy_engine.replace("_", "-"), legacy_engine)

    # 配置告警文件
    if args.log_file:
        alert_file = Path(args.log_file).parent / "alerts.jsonl"
        setup_alerting(file_path=alert_file)

    from . import indicators  # noqa - 触发指标注册

    # 优先读 --symbols 参数，其次读 TEST_SYMBOLS 环境变量
    symbols = args.symbols.split(",") if args.symbols else None
    if not symbols:
        test_symbols = os.environ.get("TEST_SYMBOLS")
        if test_symbols:
            symbols = test_symbols.split(",")

    intervals = args.intervals.split(",") if args.intervals else None
    indicator_list = args.indicators.split(",") if args.indicators else None

    try:
        if selected_engine == "full_async":
            from .core.async_full_engine import run_async_full
            run_async_full(
                symbols=symbols,
                intervals=intervals,
                indicators=indicator_list,
                high_workers=args.workers,
                low_workers=max(1, args.workers // 2),
            )
        elif selected_engine == "event":
            from .core.event_engine import run_event_engine
            run_event_engine(
                symbols=symbols,
                intervals=intervals,
                workers=args.workers,
            )
        else:
            # 默认：一次性计算
            from .core.engine import Engine
            Engine(
                symbols=symbols,
                intervals=intervals or ["1m", "5m", "15m", "1h", "4h", "1d", "1w"],
                indicators=indicator_list,
                lookback=args.lookback,
                max_workers=args.workers,
            ).run(mode=args.mode)
    finally:
        # 保存指标
        if args.metrics_file:
            metrics.save(Path(args.metrics_file))


if __name__ == "__main__":
    main()
