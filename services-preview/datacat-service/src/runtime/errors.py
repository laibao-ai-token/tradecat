"""统一异常类型与入口守护。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


@dataclass
class ErrorDetail:
    """结构化错误信息。"""
    code: str
    message: str
    detail: Optional[Dict[str, Any]] = None


class DatacatError(Exception):
    """统一错误基类。"""

    code = "datacat_error"

    def __init__(self, message: str, *, code: Optional[str] = None, detail: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code or self.code
        self.detail = detail or {}


class ConfigError(DatacatError):
    code = "config_error"


class ExternalServiceError(DatacatError):
    code = "external_service_error"


class DataValidationError(DatacatError):
    code = "data_validation_error"


class IOFailure(DatacatError):
    code = "io_error"


def _detail_from_exception(exc: DatacatError) -> ErrorDetail:
    return ErrorDetail(code=exc.code, message=str(exc), detail=exc.detail)


def safe_main(main_func: Callable[[], None], component: Optional[str] = None) -> int:
    """统一入口守护：捕获异常并记录。"""
    logger = logging.getLogger(component or __name__)
    try:
        main_func()
        return 0
    except KeyboardInterrupt:
        logger.warning("收到中断信号，正在退出")
        return 130
    except DatacatError as exc:
        detail = _detail_from_exception(exc)
        logger.error("运行失败: %s", detail.message, extra={"error_code": detail.code, "error_detail": detail.detail})
        return 2
    except Exception as exc:
        logger.exception("未捕获异常: %s", exc)
        return 1
