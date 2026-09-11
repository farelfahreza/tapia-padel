"""Deduplication keys.

Dedup state lives in Postgres, so these keys have to be stable across runs
and across processes - no randomness, no in-memory caches.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

from app.utils.normalize import normalize_text

_VINTED_ITEM_ID_RE = re.compile(r"/items/(\d+)")


def canonical_url(url: str) -> str:
    """Drop query string and fragment - Vinted appends tracking params."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def item_id_from_url(url: str) -> str | None:
    match = _VINTED_ITEM_ID_RE.search(url or "")
    return match.group(1) if match else None


def dedup_key(
    *,
    source: str,
    external_id: str | None,
    url: str | None = None,
    title: str = "",
    price_eur: float = 0.0,
    location: str | None = None,
) -> str:
    """Prefer the canonical item id, then the canonical URL, then a hash.

    The hash fallback is deliberately price-sensitive: without a stable id the
    best we can do is treat a re-listing at a different price as a different
    listing, which is the safe direction (we would rather look twice than
    silently swallow a genuinely new listing).
    """
    if external_id:
        return f"{source}:{external_id}"
    if url:
        item_id = item_id_from_url(url)
        if item_id:
            return f"{source}:{item_id}"
        return f"{source}:url:{canonical_url(url)}"
    digest = hashlib.sha256(
        "|".join(
            [
                source,
                normalize_text(title),
                f"{price_eur:.2f}",
                normalize_text(location or ""),
            ]
        ).encode("utf-8")
    ).hexdigest()
    return f"{source}:hash:{digest[:32]}"
