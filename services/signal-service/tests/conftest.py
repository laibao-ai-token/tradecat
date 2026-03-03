"""
Pytest 配置和 fixtures
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"


def _bootstrap_src_package() -> None:
    if "src" in sys.modules:
        return
    init_file = SRC_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "src",
        init_file,
        submodule_search_locations=[str(SRC_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 signal-service src 包: {SRC_DIR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["src"] = module
    spec.loader.exec_module(module)


_bootstrap_src_package()


@pytest.fixture
def sample_signal_event():
    """示例信号事件"""
    from src.events.types import SignalEvent
    
    return SignalEvent(
        symbol="BTCUSDT",
        signal_type="price_surge",
        direction="BUY",
        strength=75,
        message_key="signal.pg.msg.price_surge",
        message_params={"pct": "3.5"},
        price=50000.0,
        timeframe="5m",
    )


@pytest.fixture
def clean_publisher():
    """清理 SignalPublisher 状态"""
    from src.events.publisher import SignalPublisher
    
    SignalPublisher.clear()
    yield SignalPublisher
    SignalPublisher.clear()
