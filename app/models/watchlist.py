"""Watchlist entries - the rackets I have told the bot to pay attention to."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class WatchlistEntry(BaseModel):
    #: What I typed, kept verbatim so /list reads back the way I wrote it.
    query_text: str
    #: Normalized with the same normalizer used on listing titles.
    normalized_query: str
    #: Optional hard budget ceiling. A ceiling on top of the scoring, never a
    #: replacement for it.
    max_price_eur: float | None = None
    id: int | None = None
    created_at: datetime | None = None


class WatchlistMatch(BaseModel):
    entry: WatchlistEntry
    #: Which tokens of the query were found in the listing.
    matched_on: str
