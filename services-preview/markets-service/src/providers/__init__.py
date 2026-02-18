"""Providers 模块 - 数据源适配器"""
# 说明：
# - ProviderRegistry 的注册由装饰器在“对应 provider 模块被 import”时触发。
# - 不要在这里无条件 import 所有 providers，否则会因为未安装的可选依赖而导致 CLI 启动失败。

__all__ = [
    "ccxt", "cryptofeed",
    "akshare", "baostock",
    "yfinance",
    "sina",
    "tencent",
    "eastmoney",
    "nasdaq",
    "alltick",
    "fredapi",
    "quantlib",
    "openbb",
]
