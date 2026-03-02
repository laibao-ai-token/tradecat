"""Shared loader for repository-wide ``config/.env`` settings."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_env_value(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""

    # Keep quoted values as-is (without outer quotes).
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]

    # Unquoted values: trim inline comment only when '#' is preceded by whitespace.
    for idx, ch in enumerate(value):
        if ch == "#" and idx > 0 and value[idx - 1].isspace():
            value = value[:idx].rstrip()
            break
    return value.strip()


def parse_env_lines(lines: Iterable[str]) -> dict[str, str]:
    """Parse dotenv-style lines into a key-value map (last key wins)."""
    parsed: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not _ENV_KEY_RE.match(key):
            continue
        parsed[key] = _parse_env_value(value)
    return parsed


def load_env_file(
    env_path: Path,
    *,
    set_os_env: bool = True,
    override: bool = False,
    encoding: str = "utf-8",
) -> dict[str, str]:
    """
    Load env entries from file.

    - ``set_os_env=True`` updates ``os.environ`` (default behavior).
    - ``override=False`` keeps existing env vars (setdefault semantics).
    """
    if not env_path.exists():
        return {}

    parsed = parse_env_lines(env_path.read_text(encoding=encoding).splitlines())
    if not set_os_env:
        return parsed

    for key, value in parsed.items():
        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)
    return parsed


def find_repo_root(start: Path | None = None) -> Path:
    """Find repository root by walking upward until a ``config`` directory exists."""
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (candidate / "config").exists():
            return candidate

    # Fallback: libs/common/config_loader.py -> repo root at parents[2]
    return Path(__file__).resolve().parents[2]


def load_repo_env(
    *,
    repo_root: Path | None = None,
    set_os_env: bool = True,
    override: bool = False,
) -> dict[str, str]:
    """Load ``<repo_root>/config/.env``."""
    root = (repo_root or find_repo_root()).resolve()
    return load_env_file(root / "config" / ".env", set_os_env=set_os_env, override=override)

