#!/usr/bin/env python3
"""Shared compat loader for repository-level ``config/.env``."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Callable


def _load_module_from_path(module_name: str, module_path: Path) -> ModuleType | None:
    if not module_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, FileNotFoundError):
        return None
    return module


def _resolve_load_repo_env(repo_root: Path) -> Callable[..., dict[str, str]] | None:
    for module_name in ("common.config_loader", "libs.common.config_loader"):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        loader = getattr(module, "load_repo_env", None)
        if callable(loader):
            return loader

    module = _load_module_from_path(
        "tradecat_config_loader",
        repo_root / "libs" / "common" / "config_loader.py",
    )
    if module is None:
        return None
    loader = getattr(module, "load_repo_env", None)
    if callable(loader):
        return loader
    return None


def load_repo_env_compat(
    repo_root: Path | str | None,
    *,
    set_os_env: bool = True,
    override: bool = False,
) -> dict[str, str]:
    """Load ``<repo_root>/config/.env`` with a single shared fallback strategy."""
    if repo_root is None:
        root = Path(__file__).resolve().parents[2]
    else:
        root = Path(repo_root).resolve()

    loader = _resolve_load_repo_env(root)
    if loader is None:
        return {}
    return loader(repo_root=root, set_os_env=set_os_env, override=override)
