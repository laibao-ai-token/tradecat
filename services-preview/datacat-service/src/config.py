"""配置占位：后续统一从 config/.env 加载。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    """配置模型（占位）。"""

    service_name: str = "datacat-service"
