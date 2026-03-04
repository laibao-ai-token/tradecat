"""Shared helpers to resolve database URLs from environment variables."""

from __future__ import annotations

import os

DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5434/market_data"


def resolve_database_url(*env_keys: str, default: str = DEFAULT_DATABASE_URL) -> str:
    """Resolve database URL from env keys in order (first non-empty wins)."""

    for key in env_keys:
        value = (os.getenv(key) or "").strip()
        if value:
            return value
    return default

