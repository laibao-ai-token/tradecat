"""News models (standardized).

This is intentionally lightweight for MVP news ingestion:
- Provider fetchers normalize RSS/Atom entries into NewsArticle
- Storage layer handles dedup via `dedup_hash`
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NewsQuery(BaseModel):
    """News query params."""

    feeds: list[str] = Field(default_factory=list, description="RSS/Atom feed URLs")
    limit: int = Field(default=50, ge=1, le=500, description="Max items per run")
    window_hours: int = Field(default=24, ge=1, le=24 * 14, description="Only keep items within N hours")
    timeout_s: int = Field(default=20, ge=1, le=120, description="HTTP timeout seconds")


class NewsArticle(BaseModel):
    """Standardized news article record (ready for DB insert)."""

    dedup_hash: str = Field(..., description="Unique hash for dedup (sha256)")
    source: str = Field(..., description="Source label (provider/feed)")
    url: str | None = None
    published_at: datetime
    title: str
    summary: str | None = None
    content: str | None = None
    symbols: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    language: str = Field(default="en")

