"""服务入口（占位）。"""

import logging

from .config import AppConfig


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    cfg = AppConfig()
    logging.info("%s 启动（结构占位）", cfg.service_name)


if __name__ == "__main__":
    main()
