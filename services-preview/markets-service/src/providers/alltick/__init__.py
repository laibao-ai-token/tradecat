"""AllTick Provider - 分钟级/实时行情（需 Token）

覆盖: 美股/港股/A股（以 AllTick code 为准，例如: AAPL.US, 700.HK, 000001.SZ）
参考: https://alltick.co/
"""

from .candle import AllTickCandleFetcher  # noqa: F401
