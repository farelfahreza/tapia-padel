"""The source interface.

Everything Vinted-specific sits behind this. Pricing and scoring import from
here at most - never from ``app.collection.vinted``. If the JSON endpoint
stops working, a replacement collector implements ``ListingSource`` and
nothing downstream changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.listing import Condition, Listing


class SourceError(RuntimeError):
    """Generic, retryable-at-next-run failure from a source."""


class SourceAuthError(SourceError):
    """401/403 - the session cookie is missing, expired or rejected.

    The correct response is to stop this run and refresh the cookie. Never to
    retry harder or to work around the block.
    """


class SourceRateLimited(SourceError):
    """429 - the source asked us to slow down.

    Carries the server's ``Retry-After`` when it sent one. The run pauses or
    stops; it never speeds up or rotates anything to get around this.
    """

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ListingSource(ABC):
    """A place where used padel rackets can be observed."""

    #: Short stable identifier stored on every listing and dedup key.
    name: str = "unknown"

    @abstractmethod
    def browse(self, *, max_pages: int | None = None) -> list[Listing]:
        """Page through the category with no search text.

        The day-to-day mode: builds the price database and catches listings I
        never thought to ask for.
        """

    @abstractmethod
    def search(self, query: str, *, max_pages: int | None = None) -> list[Listing]:
        """Query a specific model name within the same category.

        Used to serve watchlist entries.
        """

    def close(self) -> None:
        """Release any network resources. Safe to call more than once."""


def filter_accepted_conditions(
    listings: list[Listing], accepted: tuple[Condition, ...]
) -> tuple[list[Listing], int]:
    """Drop listings outside the accepted condition allow-list.

    Applied immediately after fetch, before anything is priced or stored, even
    when the source also filtered at query level. Belt and braces: a stale
    query-level filter id can then never leak a lower condition through.

    Listings with an unknown condition are dropped too - an unverifiable
    condition is exactly the risk the allow-list exists to avoid.
    """
    kept = [listing for listing in listings if listing.condition in accepted]
    return kept, len(listings) - len(kept)
