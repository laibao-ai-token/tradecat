from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.news_db import (
    fetch_recent_news_articles,
    resolve_news_database_schema,
    resolve_news_database_url,
)


class TestNewsDb(unittest.TestCase):
    def test_resolve_news_database_url_prefers_env(self) -> None:
        env = {
            "DATABASE_URL": "postgresql://env-user:env-pass@localhost:5434/market_data",
        }
        root = Path("/tmp/tradecat-test-root")
        self.assertEqual(
            resolve_news_database_url(root, env=env),
            "postgresql://env-user:env-pass@localhost:5434/market_data",
        )

    def test_resolve_news_database_url_reads_config_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_dir = root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / ".env").write_text(
                "MARKETS_SERVICE_DATABASE_URL=postgresql://file-user:file-pass@127.0.0.1:5434/market_data\n",
                encoding="utf-8",
            )
            self.assertEqual(
                resolve_news_database_url(root, env={}),
                "postgresql://file-user:file-pass@127.0.0.1:5434/market_data",
            )

    def test_resolve_news_database_url_falls_back_to_default(self) -> None:
        root = Path("/tmp/tradecat-test-root-missing")
        self.assertEqual(
            resolve_news_database_url(root, env={}),
            "postgresql://postgres:postgres@localhost:5434/market_data",
        )

    def test_resolve_news_database_schema_prefers_env(self) -> None:
        env = {"ALTERNATIVE_DB_SCHEMA": "alt_news"}
        root = Path("/tmp/tradecat-test-root")
        self.assertEqual(resolve_news_database_schema(root, env=env), "alt_news")

    def test_resolve_news_database_schema_reads_config_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_dir = root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / ".env").write_text("ALTERNATIVE_DB_SCHEMA=custom_alt\n", encoding="utf-8")
            self.assertEqual(resolve_news_database_schema(root, env={}), "custom_alt")

    def test_resolve_news_database_schema_falls_back_to_default(self) -> None:
        root = Path("/tmp/tradecat-test-root-missing")
        self.assertEqual(resolve_news_database_schema(root, env={}), "alternative")

    def test_fetch_recent_news_articles_parses_csv_rows(self) -> None:
        stdout = (
            "dedup_hash,published_at,source,url,title,summary,symbols,categories,language\n"
            "abc,1709800000.0,J10,https://www.jin10.com/,Headline A,Summary A,BTC|ETH,macro|policy,zh\n"
            "def,1709799900.0,www.benzinga.com,https://www.benzinga.com/a,Headline B,Summary B,NVDA,company,en\n"
        )

        with patch("src.news_db.subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=0, stdout=stdout, stderr="")
            rows = fetch_recent_news_articles(
                "postgresql://postgres:postgres@localhost:5434/market_data",
                limit=10,
                window_hours=24,
                timeout_s=3.0,
                schema="custom_alt",
            )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].dedup_hash, "abc")
        self.assertEqual(rows[0].symbols, ("BTC", "ETH"))
        self.assertEqual(rows[0].categories, ("macro", "policy"))
        self.assertEqual(rows[0].language, "zh")
        self.assertEqual(rows[1].source, "www.benzinga.com")
        self.assertIn('FROM "custom_alt".news_articles', mock_run.call_args.args[0][-1])

    def test_fetch_recent_news_articles_retries_local_port_candidates(self) -> None:
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

        with patch("src.news_db.subprocess.run", side_effect=side_effect) as mock_run:
            rows = fetch_recent_news_articles(
                "postgresql://postgres:postgres@localhost:5432/market_data",
                limit=10,
                window_hours=24,
                timeout_s=3.0,
            )

        self.assertEqual(len(rows), 1)
        self.assertIn(":5432/market_data", mock_run.call_args_list[0].args[0][1])
        self.assertIn(":5434/market_data", mock_run.call_args_list[1].args[0][1])

    def test_fetch_recent_news_articles_raises_runtime_error(self) -> None:
        with patch("src.news_db.subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=2, stdout="", stderr="permission denied")
            with self.assertRaises(RuntimeError):
                fetch_recent_news_articles(
                    "postgresql://postgres:postgres@localhost:5434/market_data",
                    limit=10,
                    window_hours=24,
                    timeout_s=3.0,
                )

    def test_fetch_recent_news_articles_timeout_maps_to_runtime_error(self) -> None:
        with patch("src.news_db.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["psql"], timeout=3.0)):
            with self.assertRaises(RuntimeError):
                fetch_recent_news_articles(
                    "postgresql://postgres:postgres@localhost:5434/market_data",
                    limit=10,
                    window_hours=24,
                    timeout_s=3.0,
                )


if __name__ == "__main__":
    unittest.main()
