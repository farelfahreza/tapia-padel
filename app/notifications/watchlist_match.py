"""Matching a listing against the watchlist.

Reuses the same normalization as everything else, so "AT10", "at 10" and
"Nox AT10" behave the way I would expect when I type them into Telegram.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.models.listing import Listing
from app.models.watchlist import WatchlistEntry, WatchlistMatch
from app.utils.normalize import matches_query


def find_match(
    listing: Listing, entries: Sequence[WatchlistEntry]
) -> WatchlistMatch | None:
    """First matching entry, most specific (longest query) first.

    Longest-first so a "Nox AT10 Genius" entry with its own budget wins over a
    broad "Nox" entry.
    """
    ordered = sorted(entries, key=lambda e: len(e.normalized_query), reverse=True)
    for entry in ordered:
        if matches_query(listing.title, entry.query_text, listing.source_brand):
            return WatchlistMatch(entry=entry, matched_on=entry.normalized_query)
    return None
