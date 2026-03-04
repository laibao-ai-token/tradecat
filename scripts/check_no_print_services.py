#!/usr/bin/env python3
"""Guard against print call usage in service source modules.

Policy:
- scan tracked `.py` files under `services/` and `services-preview/`
- ignore `tests/` and service-level `scripts/` subtrees
- fail on runtime print calls (AST-based)
"""

from __future__ import annotations

import ast
import logging
import subprocess


ROOTS = ("services", "services-preview")
logger = logging.getLogger(__name__)


def _is_excluded_path(path: str) -> bool:
    """Exclude tests and service-level scripts directories only.

    Keep `src/scripts/` in scan scope to catch print regressions inside source modules.
    """

    norm = "/" + path.replace(chr(92), "/")
    if "/tests/" in norm:
        return True

    parts = norm.strip("/").split("/")
    # Match: services/<service>/scripts/... and services-preview/<service>/scripts/...
    if len(parts) >= 4 and parts[0] in ROOTS and parts[2] == "scripts":
        return True

    return False


def _iter_tracked_python_files() -> list[str]:
    out = subprocess.check_output(["git", "ls-files", "-z", "--", *ROOTS], text=False)
    files = [p for p in out.decode("utf-8", errors="surrogateescape").split("\0") if p.endswith(".py")]
    result: list[str] = []
    for path in files:
        if _is_excluded_path(path):
            continue
        result.append(path)
    return sorted(result)


def _find_print_calls(path: str) -> list[int]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()
    tree = ast.parse(source, filename=path)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "print":
            lines.append(int(getattr(node, "lineno", 0) or 0))
    return sorted(set(lines))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    violations: list[tuple[str, int]] = []
    for path in _iter_tracked_python_files():
        try:
            lines = _find_print_calls(path)
        except SyntaxError as exc:
            logger.warning("[skip-syntax-error] %s: %s", path, exc)
            continue
        for line in lines:
            violations.append((path, line))

    if not violations:
        logger.info("OK: services source has no print calls")
        return 0

    logger.error("发现 services 源码中的 print 调用（请改为 logger）:")
    for path, line in violations:
        logger.error("  - %s:%d", path, line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
