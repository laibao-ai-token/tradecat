"""健康检查：DB 连通性 + Binance Ping。"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import requests
import psycopg

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config import settings  # noqa: E402
from runtime.logging_utils import setup_logging  # noqa: E402


def _db_check() -> Dict[str, Any]:
    result = {"ok": False, "error": None, "database": None}
    try:
        with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database()")
                result["database"] = cur.fetchone()[0]
        result["ok"] = True
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
    return result


def _http_check() -> Dict[str, Any]:
    result = {"ok": False, "error": None, "status": None}
    proxies = {"http": settings.http_proxy, "https": settings.http_proxy} if settings.http_proxy else None
    try:
        resp = requests.get("https://fapi.binance.com/fapi/v1/ping", timeout=5, proxies=proxies)
        result["status"] = resp.status_code
        result["ok"] = resp.status_code == 200
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
    return result


def main() -> int:
    setup_logging(level=settings.log_level, fmt=settings.log_format, component="healthcheck", log_file=settings.log_file)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    report = {
        "ts": now,
        "db": _db_check(),
        "http": _http_check(),
        "proxy": settings.http_proxy or "",
    }

    report_path = ROOT / "tasks" / "health-check-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 返回码：DB 或 HTTP 任一失败返回 1
    return 0 if report["db"]["ok"] and report["http"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
