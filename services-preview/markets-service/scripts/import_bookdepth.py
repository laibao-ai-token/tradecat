#!/usr/bin/env python3
"""导入 bookDepth ZIP 数据到 raw.crypto_book_depth。"""

from __future__ import annotations

import csv
import importlib.util
import io
import logging
import os
import re
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from psycopg import connect, sql

SERVICE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SERVICE_ROOT.parent.parent


def _load_repo_env() -> None:
    try:
        from common.config_loader import load_repo_env
    except ModuleNotFoundError:
        try:
            from libs.common.config_loader import load_repo_env
        except ModuleNotFoundError:
            loader_path = PROJECT_ROOT / "libs" / "common" / "config_loader.py"
            if not loader_path.exists():
                return
            spec = importlib.util.spec_from_file_location("tradecat_config_loader", loader_path)
            if spec is None or spec.loader is None:
                return
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except OSError:
                return
            load_repo_env = getattr(module, "load_repo_env", None)
            if load_repo_env is None:
                return
    load_repo_env(repo_root=PROJECT_ROOT, set_os_env=True, override=False)


_load_repo_env()

DB_URL = os.getenv(
    "MARKETS_SERVICE_DATABASE_URL",
    os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5434/market_data"),
)
DATA_ROOT = Path(os.getenv("MARKETS_SERVICE_DATA_DIR", str(PROJECT_ROOT / "libs" / "database" / "csv")))
DATA_DIR = DATA_ROOT / "downloads" / "bookDepth"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def parse_bookdepth_file(fpath: Path) -> list[dict]:
    """解析单个 bookDepth ZIP 文件。"""
    match = re.match(r"(\w+)-bookDepth-", fpath.name)
    if not match:
        logger.warning("无法解析文件名: %s", fpath.name)
        return []

    symbol = match.group(1)
    rows: list[dict] = []
    with zipfile.ZipFile(fpath) as zf:
        for name in zf.namelist():
            if not name.endswith(".csv"):
                continue
            with zf.open(name) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                for row in reader:
                    ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    rows.append(
                        {
                            "timestamp": ts,
                            "exchange": "binance_futures_um",
                            "symbol": symbol,
                            "percentage": int(row["percentage"]),
                            "depth": Decimal(row["depth"]),
                            "notional": Decimal(row["notional"]),
                        }
                    )
    return rows


def import_to_db(rows: list[dict], db_url: str) -> int:
    """批量导入到数据库。"""
    if not rows:
        return 0
    cols = ["timestamp", "exchange", "symbol", "percentage", "depth", "notional"]

    with connect(db_url) as conn:
        with conn.cursor() as cur:
            with cur.copy(
                sql.SQL("COPY raw.crypto_book_depth ({}) FROM STDIN").format(
                    sql.SQL(", ").join(map(sql.Identifier, cols))
                )
            ) as copy:
                for row in rows:
                    copy.write_row(tuple(row[c] for c in cols))
        conn.commit()
    return len(rows)


def _print_summary(db_url: str) -> None:
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT symbol, COUNT(*), MIN(timestamp), MAX(timestamp)
                FROM raw.crypto_book_depth
                GROUP BY symbol
                ORDER BY symbol
                """
            )
            rows = cur.fetchall()

    print("\n=== 导入结果 ===")
    for symbol, cnt, ts_min, ts_max in rows:
        print(f"{symbol:<12} {cnt:>10}  {ts_min}  ->  {ts_max}")


def main() -> None:
    files = sorted(DATA_DIR.glob("*.zip"))
    logger.info("目录: %s", DATA_DIR)
    logger.info("找到 %d 个文件", len(files))
    if not files:
        return

    total = 0
    for fpath in files:
        rows = parse_bookdepth_file(fpath)
        if not rows:
            continue
        n = import_to_db(rows, DB_URL)
        total += n
        logger.info("导入 %s: %d 行", fpath.name, n)

    logger.info("导入完成: 共 %d 行", total)
    _print_summary(DB_URL)


if __name__ == "__main__":
    main()
