"""统一日志配置与结构化输出。"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

DEFAULT_PLAIN_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"


def _env(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name)
    if val is None:
        return default
    return val


def _normalize_level(level: str | None) -> int:
    raw = (level or "INFO").upper()
    return getattr(logging, raw, logging.INFO)


def _resolve_log_file(log_file: str | None) -> str | None:
    if not log_file:
        return None
    if log_file.lower() in ("none", "null", "stdout", "stderr"):
        return None

    path = Path(log_file)
    if path.is_absolute():
        return str(path)

    log_dir = _env("DATACAT_LOG_DIR")
    if log_dir:
        return str(Path(log_dir) / path)
    return str(path)


class JsonFormatter(logging.Formatter):
    """JSON 结构化日志格式。"""

    _RESERVED = {
        "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName", "levelname",
        "levelno", "lineno", "module", "msecs", "message", "msg", "name", "pathname", "process",
        "processName", "relativeCreated", "stack_info", "thread", "threadName",
    }

    def __init__(self, component: str | None = None):
        super().__init__()
        self._component = component

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
            "pid": record.process,
            "thread": record.thread,
        }
        if self._component:
            payload["component"] = self._component

        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else ""
            payload["exc"] = self.formatException(record.exc_info)

        extra = {k: v for k, v in record.__dict__.items() if k not in self._RESERVED}
        if extra:
            payload.update(extra)

        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str | None = None, fmt: str | None = None,
                  component: str | None = None, log_file: str | None = None) -> None:
    """初始化日志系统。

    - DATACAT_LOG_LEVEL: INFO/DEBUG/WARN/ERROR
    - DATACAT_LOG_FORMAT: plain/json
    - DATACAT_LOG_FILE: 指定日志文件（相对路径基于 DATACAT_LOG_DIR）
    """
    level_val = _normalize_level(level or _env("DATACAT_LOG_LEVEL", "INFO"))
    fmt_val = (fmt or _env("DATACAT_LOG_FORMAT", "plain")).lower()

    handlers: list[logging.Handler] = []
    stream_handler = logging.StreamHandler(sys.stdout)
    if fmt_val == "json":
        stream_handler.setFormatter(JsonFormatter(component=component))
    else:
        stream_handler.setFormatter(logging.Formatter(DEFAULT_PLAIN_FORMAT))
    handlers.append(stream_handler)

    file_path = _resolve_log_file(log_file or _env("DATACAT_LOG_FILE"))
    if file_path:
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        if fmt_val == "json":
            file_handler.setFormatter(JsonFormatter(component=component))
        else:
            file_handler.setFormatter(logging.Formatter(DEFAULT_PLAIN_FORMAT))
        handlers.append(file_handler)

    logging.basicConfig(level=level_val, handlers=handlers, force=True)


def get_logger(name: str, component: str | None = None) -> logging.LoggerAdapter:
    """返回带组件字段的日志。"""
    base = logging.getLogger(name)
    if component:
        return logging.LoggerAdapter(base, {"component": component})
    return logging.LoggerAdapter(base, {})
