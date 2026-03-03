"""markets-service: 全市场数据采集服务。

兼容层说明：
- 历史模块大量使用 ``from config/core/models/...`` 的顶层导入风格；
- 新入口逐步迁移到 ``src.*`` 命名空间。

为避免两套命名空间并存导致 registry/类型重复注册，这里在包初始化时建立
轻量别名（不改 ``sys.path``，仅维护 ``sys.modules`` 映射）。
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType

__version__ = "0.1.0"


def _import_src_module(relative_name: str) -> ModuleType:
    return importlib.import_module(f".{relative_name}", __name__)


def _alias_module(alias: str, relative_name: str) -> ModuleType:
    module = _import_src_module(relative_name)
    sys.modules[alias] = module
    return module


def _bootstrap_legacy_aliases() -> None:
    # Package-level aliases used by legacy imports.
    _alias_module("config", "config")
    _alias_module("core", "core")
    _alias_module("models", "models")
    _alias_module("providers", "providers")

    # Keep key submodules on a single namespace to avoid duplicated registry state.
    core_registry = _import_src_module("core.registry")
    sys.modules["core.registry"] = core_registry
    core_fetcher = _import_src_module("core.fetcher")
    sys.modules["core.fetcher"] = core_fetcher
    models_candle = _import_src_module("models.candle")
    sys.modules["models.candle"] = models_candle


_bootstrap_legacy_aliases()
