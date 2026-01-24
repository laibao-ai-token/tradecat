"""信号数据路由"""

import sqlite3

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from src.config import get_settings
from src.utils.errors import ErrorCode, api_response, error_response

router = APIRouter(tags=["signal"])


@router.get("/signal/cooldown")
async def get_cooldown_status() -> dict:
    """获取信号冷却状态"""
    settings = get_settings()
    db_path = settings.SQLITE_COOLDOWN_PATH

    if not db_path.exists():
        return error_response(ErrorCode.SERVICE_UNAVAILABLE, "冷却数据库不可用")

    def _fetch_rows():
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, timestamp FROM cooldown ORDER BY timestamp DESC")
            rows = cursor.fetchall()
            return [
                {
                    "key": row[0],
                    "timestamp": int(row[1] * 1000),  # 转为毫秒
                    "expireTime": int(row[1] * 1000)
                }
                for row in rows
            ]

    try:
        data = await run_in_threadpool(_fetch_rows)
        return api_response(data)
    except Exception as e:
        return error_response(ErrorCode.INTERNAL_ERROR, f"查询失败: {e}")
