from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import requests

from src.collectors.downloader import Downloader


class _DummyLimiter:
    def __init__(self) -> None:
        self.calls = 0

    def acquire(self, weight: int = 1) -> None:
        self.calls += weight


class _FakeResponse:
    def __init__(self, status_code: int = 200, chunks: list[bytes] | None = None) -> None:
        self.status_code = status_code
        self._chunks = chunks or [b"ok"]

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def iter_content(self, chunk_size: int = 8192):
        del chunk_size
        yield from self._chunks


def test_download_skips_existing_file(tmp_path: Path) -> None:
    limiter = _DummyLimiter()
    downloader = Downloader(rate_limiter=limiter)
    target = tmp_path / "exists.bin"
    target.write_bytes(b"exists")

    assert downloader.download("https://example.com/data.bin", target)
    assert target.read_bytes() == b"exists"
    assert limiter.calls == 0


def test_download_retries_with_fallback_proxy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    limiter = _DummyLimiter()
    downloader = Downloader(
        rate_limiter=limiter,
        http_proxy="http://primary.proxy:9000",
        fallback_proxy="http://fallback.proxy:9001",
    )
    target = tmp_path / "download.bin"

    seen_proxies: list[dict[str, str] | None] = []

    def _fake_get(url: str, *, proxies=None, timeout: int = 60, stream: bool = True):
        del url, timeout, stream
        seen_proxies.append(proxies)
        if len(seen_proxies) == 1:
            raise requests.RequestException("primary proxy failed")
        return _FakeResponse(status_code=200, chunks=[b"hello"])

    monkeypatch.setattr("src.collectors.downloader.requests.get", _fake_get)

    assert downloader.download("https://example.com/data.bin", target)
    assert target.read_bytes() == b"hello"
    assert limiter.calls == 1
    assert seen_proxies[0] == {"http": "http://primary.proxy:9000", "https": "http://primary.proxy:9000"}
    assert seen_proxies[1] == {"http": "http://fallback.proxy:9001", "https": "http://fallback.proxy:9001"}


def test_download_returns_false_on_404(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    limiter = _DummyLimiter()
    downloader = Downloader(rate_limiter=limiter)
    target = tmp_path / "missing.bin"

    def _fake_get(url: str, *, proxies=None, timeout: int = 60, stream: bool = True):
        del url, proxies, timeout, stream
        return _FakeResponse(status_code=404)

    monkeypatch.setattr("src.collectors.downloader.requests.get", _fake_get)

    assert not downloader.download("https://example.com/missing.bin", target)
    assert not target.exists()
    assert limiter.calls == 1
