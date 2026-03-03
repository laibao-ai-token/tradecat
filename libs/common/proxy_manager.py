"""代理管理器 - 带重试和冷却逻辑"""

import os
import time
import logging
import requests
from dataclasses import dataclass
from typing import Optional

LOGGER = logging.getLogger(__name__)

# 冷却配置
PROXY_COOLDOWN_SECONDS = 3600  # 代理失败后冷却1小时
PROXY_RETRY_COUNT = 3          # 重试次数
PROXY_RETRY_DELAY = 2          # 重试间隔（秒）

@dataclass
class ProxyRuntimeState:
    """代理运行时状态。"""

    proxy_disabled_until: float = 0.0
    original_proxy: Optional[str] = None


_RUNTIME_STATE = ProxyRuntimeState()


def get_proxy() -> Optional[str]:
    """获取代理，如果在冷却期则返回 None"""
    # 首次调用保存原始代理
    if _RUNTIME_STATE.original_proxy is None:
        _RUNTIME_STATE.original_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")

    # 检查冷却期
    now = time.time()
    if now < _RUNTIME_STATE.proxy_disabled_until:
        remaining = int(_RUNTIME_STATE.proxy_disabled_until - now)
        LOGGER.debug(f"代理冷却中，剩余 {remaining}s")
        return None

    return _RUNTIME_STATE.original_proxy


def disable_proxy(duration: int = PROXY_COOLDOWN_SECONDS):
    """禁用代理一段时间"""
    _RUNTIME_STATE.proxy_disabled_until = time.time() + duration
    LOGGER.warning(f"代理已禁用 {duration}s（{duration//3600}小时）")


def check_proxy() -> bool:
    """检查代理是否可用（带重试）"""
    proxy = get_proxy()
    if not proxy:
        return False
    
    for i in range(PROXY_RETRY_COUNT):
        try:
            resp = requests.get(
                "https://api.binance.com/api/v3/ping",
                proxies={"http": proxy, "https": proxy},
                timeout=3
            )
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        
        if i < PROXY_RETRY_COUNT - 1:
            time.sleep(PROXY_RETRY_DELAY)
    
    # 重试失败，进入冷却
    disable_proxy()
    return False


def request_with_proxy(url: str, **kwargs) -> requests.Response:
    """带代理管理的请求"""
    proxy = get_proxy()
    if proxy:
        kwargs.setdefault("proxies", {"http": proxy, "https": proxy})
    
    try:
        return requests.get(url, **kwargs)
    except requests.exceptions.ProxyError:
        # 代理错误，进入冷却
        disable_proxy()
        # 无代理重试一次
        kwargs.pop("proxies", None)
        return requests.get(url, **kwargs)
