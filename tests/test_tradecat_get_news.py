from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import scripts.tradecat_get_news as cli
from scripts.lib.tradecat_news import query_news_articles, resolve_news_database_url


def test_resolve_news_database_url_reads_config_env(tmp_path: Path) -> None:
    root = tmp_path
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / ".env").write_text(
        "MARKETS_SERVICE_DATABASE_URL=postgresql://file-user:file-pass@127.0.0.1:5434/market_data\n",
        encoding="utf-8",
    )

    assert (
        resolve_news_database_url(root, env={})
        == "postgresql://file-user:file-pass@127.0.0.1:5434/market_data"
    )


def test_query_news_articles_builds_filtered_sql_and_parses_rows() -> None:
    stdout = (
        "dedup_hash,published_at,source,url,title,summary,symbols,categories,language\n"
        "abc,1709800000.0,J10,https://www.jin10.com/,Headline A,Summary A,BTC|BTCUSDT,macro|policy,zh\n"
        "def,1709799900.0,FED,https://www.federalreserve.gov/,Headline B,Summary B,,policy,en\n"
    )

    with patch("scripts.lib.tradecat_news.subprocess.run") as mock_run:
        mock_run.return_value = SimpleNamespace(returncode=0, stdout=stdout, stderr="")
        rows = query_news_articles(
            "postgresql://postgres:postgres@localhost:5434/market_data",
            symbol="BTCUSDT",
            query="Headline",
            limit=5,
            since_minutes=30,
            timeout_s=3.0,
        )

    assert len(rows) == 2
    assert rows[0].symbols == ("BTC", "BTCUSDT")
    assert rows[0].categories == ("macro", "policy")
    sql = mock_run.call_args.args[0][-1]
    assert "INTERVAL '30 minutes'" in sql
    assert "LIMIT 5" in sql
    assert "POSITION(" in sql
    assert "Headline" in sql
    assert "'BTC'" in sql
    assert "'BTCUSDT'" in sql


def test_query_news_articles_retries_local_port_candidates() -> None:
    stdout = (
        "dedup_hash,published_at,source,url,title,summary,symbols,categories,language\n"
        "abc,1709800000.0,J10,https://www.jin10.com/,Headline A,Summary A,BTC,macro,zh\n"
    )
    side_effect = [
        SimpleNamespace(
            returncode=2,
            stdout="",
            stderr='connection to server at "localhost" (::1), port 5432 failed: Connection refused',
        ),
        SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    ]

    with patch("scripts.lib.tradecat_news.subprocess.run", side_effect=side_effect) as mock_run:
        rows = query_news_articles(
            "postgresql://postgres:postgres@localhost:5432/market_data",
            limit=10,
            since_minutes=60,
            timeout_s=3.0,
        )

    assert len(rows) == 1
    assert ":5432/market_data" in mock_run.call_args_list[0].args[0][1]
    assert ":5434/market_data" in mock_run.call_args_list[1].args[0][1]


def test_main_outputs_ok_json(capsys) -> None:
    published_at = 1709800000.0
    rows = [
        cli.StoredNewsArticle(
            dedup_hash="abc",
            published_at=published_at,
            source="J10",
            url="https://www.jin10.com/",
            title="BTC broke resistance",
            summary="Momentum is strong",
            symbols=("BTC",),
            categories=("macro", "policy"),
            language="zh",
        )
    ]

    with patch.object(cli, "resolve_news_database_url", return_value="postgresql://example"), patch.object(
        cli, "query_news_articles", return_value=rows
    ):
        code = cli.main(["--symbol", "BTCUSDT", "--query", "btc", "--limit", "1", "--since-minutes", "120"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["tool"] == "tradecat_get_news"
    assert payload["source"] == {
        "type": "postgresql",
        "table": "alternative.news_articles",
        "reader": "scripts/lib/tradecat_news.py",
        "writes": False,
    }
    assert payload["request"] == {
        "symbol": "BTCUSDT",
        "query": "btc",
        "limit": 1,
        "since_minutes": 120,
    }
    assert payload["error"] is None
    assert payload["data"] == [
        {
            "title": "BTC broke resistance",
            "summary": "Momentum is strong",
            "published_at": datetime.fromtimestamp(published_at, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "provider": "J10",
            "url": "https://www.jin10.com/",
            "symbols": ["BTC"],
            "category": "macro",
        }
    ]


def test_main_outputs_structured_error_json(capsys) -> None:
    with patch.object(cli, "resolve_news_database_url", return_value="postgresql://example"), patch.object(
        cli, "query_news_articles", side_effect=RuntimeError("psql_not_found")
    ):
        code = cli.main(["--limit", "2"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["source"] == {
        "type": "postgresql",
        "table": "alternative.news_articles",
        "reader": "scripts/lib/tradecat_news.py",
        "writes": False,
    }
    assert payload["request"] == {
        "symbol": None,
        "query": None,
        "limit": 2,
        "since_minutes": 1440,
    }
    assert payload["data"] == []
    assert payload["error"] == {
        "code": "psql_not_found",
        "message": "psql_not_found",
    }
