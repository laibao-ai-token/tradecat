#!/usr/bin/env python3
"""
FastAPI 路由扫描器
- 扫描 @app/@router 的 HTTP 与 WebSocket 装饰器
- 提取 app.include_router 的 prefix 映射
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Iterable

ROUTE_RE = re.compile(
    r"^\s*@(?P<obj>app|router)\.(?P<method>get|post|put|delete|patch|options|head|websocket)\(\s*[\"'](?P<path>[^\"']+)[\"']"
)
INCLUDE_RE = re.compile(
    r"^\s*app\.include_router\(\s*(?P<router>\w+)\s*,\s*prefix\s*=\s*[\"'](?P<prefix>[^\"']+)[\"']"
)
METHODS = {
    "get",
    "post",
    "put",
    "delete",
    "patch",
    "options",
    "head",
    "websocket",
}


def iter_py_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            yield root
            continue
        if root.is_dir():
            for path in root.rglob("*.py"):
                yield path


def scan_file(path: Path, root: Path, mode: str) -> tuple[list[dict], list[dict], list[str]]:
    routes: list[dict] = []
    routers: list[dict] = []
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        errors.append(f"{path.relative_to(root)}: {exc}")
        return routes, routers, errors

    if mode == "regex":
        for idx, line in enumerate(text.splitlines(), start=1):
            m = ROUTE_RE.match(line)
            if m:
                routes.append(
                    {
                        "method": m.group("method"),
                        "path": m.group("path"),
                        "decorator": f"@{m.group('obj')}.{m.group('method')}",
                        "file": str(path.relative_to(root)),
                        "line": idx,
                    }
                )
                continue
            m = INCLUDE_RE.match(line)
            if m:
                routers.append(
                    {
                        "router": m.group("router"),
                        "prefix": m.group("prefix"),
                        "file": str(path.relative_to(root)),
                        "line": idx,
                    }
                )
        return routes, routers, errors

    try:
        tree = ast.parse(text)
    except Exception as exc:
        errors.append(f"{path.relative_to(root)}: {exc}")
        return routes, routers, errors

    def build_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            left = build_name(node.value)
            if left:
                return f"{left}.{node.attr}"
            return node.attr
        return None

    def extract_path(call: ast.Call) -> str | None:
        if call.args:
            first = call.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                return first.value
        for kw in call.keywords:
            if kw.arg == "path" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
        return None

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    method = dec.func.attr
                    if method not in METHODS:
                        continue
                    owner = build_name(dec.func.value) or "unknown"
                    path_value = extract_path(dec) or "<dynamic>"
                    routes.append(
                        {
                            "method": method,
                            "path": path_value,
                            "decorator": f"@{owner}.{method}",
                            "file": str(path.relative_to(root)),
                            "line": dec.lineno or node.lineno,
                        }
                    )

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr != "include_router":
                continue
            router_name = None
            if node.args:
                router_name = build_name(node.args[0]) or "<dynamic>"
            prefix_value = "<dynamic>"
            for kw in node.keywords:
                if kw.arg == "prefix":
                    if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        prefix_value = kw.value.value
                    else:
                        prefix_value = "<dynamic>"
            routers.append(
                {
                    "router": router_name or "<dynamic>",
                    "prefix": prefix_value,
                    "file": str(path.relative_to(root)),
                    "line": node.lineno,
                }
            )

    return routes, routers, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="扫描 FastAPI 路由定义")
    parser.add_argument("roots", nargs="+", help="扫描根目录或文件")
    parser.add_argument("--mode", choices=["ast", "regex"], default="ast")
    parser.add_argument("--format", choices=["json", "table"], default="json")
    parser.add_argument("--strict", action="store_true", help="遇到解析错误直接失败")
    args = parser.parse_args()

    roots = [Path(r).resolve() for r in args.roots]
    all_routes: list[dict] = []
    all_routers: list[dict] = []
    all_errors: list[str] = []

    for root in roots:
        for path in iter_py_files([root]):
            routes, routers, errors = scan_file(path, root if root.is_dir() else path.parent, args.mode)
            all_routes.extend(routes)
            all_routers.extend(routers)
            all_errors.extend(errors)

    if args.format == "json":
        print(
            json.dumps(
                {"routers": all_routers, "routes": all_routes, "errors": all_errors},
                ensure_ascii=False,
                indent=2,
            )
        )
        if args.strict and all_errors:
            raise SystemExit(2)
        return

    if all_errors:
        for item in all_errors:
            print(f"ERROR\t{item}", file=sys.stderr)
        if args.strict:
            raise SystemExit(2)

    if all_routers:
        print("ROUTERS\trouter\tprefix\tfile:line")
        for item in all_routers:
            print(
                f"ROUTERS\t{item['router']}\t{item['prefix']}\t{item['file']}:{item['line']}"
            )
        print("")

    print("ROUTES\tmethod\tpath\tfile:line")
    for item in all_routes:
        print(
            f"ROUTES\t{item['method']}\t{item['path']}\t{item['file']}:{item['line']}"
        )


if __name__ == "__main__":
    main()
