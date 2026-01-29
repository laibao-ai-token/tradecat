#!/usr/bin/env python3
"""
SQLite 检查器
- 列出表
- 可选输出行数与 schema
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite 结构/统计检查")
    parser.add_argument("db_path", help="SQLite 文件路径")
    parser.add_argument("--tables", help="指定表名（逗号分隔）")
    parser.add_argument("--count", action="store_true", help="输出行数统计")
    parser.add_argument("--count-all", action="store_true", help="允许对全部表执行 COUNT")
    parser.add_argument("--schema", action="store_true", help="输出表结构 (PRAGMA table_info)")
    parser.add_argument("--format", choices=["json", "table"], default="json")
    parser.add_argument("--readwrite", action="store_true", help="允许读写连接（默认只读）")
    parser.add_argument("--timeout", type=float, default=5.0, help="数据库忙等待超时（秒）")
    parser.add_argument("--strict", action="store_true", help="遇到错误直接失败")
    args = parser.parse_args()

    db_path = Path(args.db_path).resolve()
    if not db_path.exists():
        raise SystemExit(f"DB 不存在: {db_path}")

    if args.count and not args.tables and not args.count_all:
        raise SystemExit("COUNT 需要指定 --tables 或显式设置 --count-all")

    timeout = max(0.1, args.timeout)
    errors: list[str] = []
    if args.readwrite:
        conn = sqlite3.connect(db_path, timeout=timeout)
    else:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout)

    with conn:
        cursor = conn.cursor()
        try:
            cursor.execute("PRAGMA query_only = ON")
            cursor.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
        except sqlite3.Error as exc:
            errors.append(f"PRAGMA 失败: {exc}")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        all_tables = [row[0] for row in cursor.fetchall()]

        if args.tables:
            target_tables = [t.strip() for t in args.tables.split(",") if t.strip()]
        else:
            target_tables = all_tables

        counts: dict[str, int] = {}
        schemas: dict[str, list[dict[str, Any]]] = {}

        if args.count:
            for table in target_tables:
                if table not in all_tables:
                    errors.append(f"表不存在: {table}")
                    continue
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {quote(table)}")
                    counts[table] = cursor.fetchone()[0]
                except sqlite3.Error as exc:
                    errors.append(f"COUNT 失败 {table}: {exc}")

        if args.schema:
            for table in target_tables:
                if table not in all_tables:
                    errors.append(f"表不存在: {table}")
                    continue
                try:
                    cursor.execute(f"PRAGMA table_info({quote(table)})")
                    rows = cursor.fetchall()
                    schemas[table] = [
                        {
                            "cid": r[0],
                            "name": r[1],
                            "type": r[2],
                            "notnull": r[3],
                            "dflt_value": r[4],
                            "pk": r[5],
                        }
                        for r in rows
                    ]
                except sqlite3.Error as exc:
                    errors.append(f"SCHEMA 失败 {table}: {exc}")

    payload = {
        "db": str(db_path),
        "tables": all_tables,
        "counts": counts,
        "schemas": schemas,
        "errors": errors,
    }

    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.strict and errors:
            raise SystemExit(2)
        return

    print(f"DB\t{db_path}")
    print("TABLES")
    for table in all_tables:
        print(f"- {table}")

    if counts:
        print("COUNTS")
        for table, count in counts.items():
            print(f"- {table}: {count}")

    if schemas:
        print("SCHEMAS")
        for table, cols in schemas.items():
            print(f"- {table}")
            for col in cols:
                print(f"  - {col['name']}: {col['type']}")
    if errors:
        for item in errors:
            print(f"ERROR\t{item}", file=sys.stderr)
        if args.strict:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
