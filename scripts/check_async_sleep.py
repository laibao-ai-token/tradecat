#!/usr/bin/env python3
"""检查异步函数中是否错误使用 time.sleep。"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

SCAN_PREFIXES = ("services/", "services-preview/", "scripts/", "libs/")


def iter_python_files() -> list[Path]:
    files = subprocess.check_output(["git", "ls-files", "*.py"], text=True).splitlines()
    return [Path(f) for f in files if f.startswith(SCAN_PREFIXES)]


def find_async_time_sleep(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(text)
    except Exception:
        return []

    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "time"
            and func.attr == "sleep"
        ):
            continue
        cur: ast.AST = node
        owner: ast.AST | None = None
        while cur in parent:
            cur = parent[cur]
            if isinstance(cur, (ast.AsyncFunctionDef, ast.FunctionDef)):
                owner = cur
                break
        if isinstance(owner, ast.AsyncFunctionDef):
            hits.append((node.lineno, owner.name))
    return hits


def main() -> int:
    violations: list[str] = []
    for path in iter_python_files():
        for lineno, func_name in find_async_time_sleep(path):
            violations.append(f"{path}:{lineno} (async {func_name})")

    if violations:
        print("发现 async def 中使用 time.sleep（请改为 await asyncio.sleep）:")
        for item in violations:
            print(f"  - {item}")
        return 1

    print("OK: 未发现 async def 中的 time.sleep")
    return 0


if __name__ == "__main__":
    sys.exit(main())
