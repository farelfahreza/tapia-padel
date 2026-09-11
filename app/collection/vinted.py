"""Vinted collector - the fragile part of the system, kept in one file.

Uses the internal JSON endpoint ``/api/v2/catalog/items``: the same endpoint
the Vinted web app calls. There is no official public Vinted API, this
endpoint is undocumented, it needs a valid session cookie, and using it sits
in tension with Vinted's terms of service. Consequences, all deliberate:

  * the client identifies itself honestly through a configurable User-Agent;
  * every request goes through a configurable rate limit;
  * 401/403 raises ``SourceAuthError`` and 429 raises ``SourceRateLimited``;
    the run then slows down or stops. There is no retry storm, no rotation, no
    attempt of any kind to work around an anti-bot measure;
  * parsing is defensive - the response shape is inferred, not documented, so
    malformed items are counted and skipped rather than crashing the run.

No HTML scraping and no headless browser. Both are out of scope.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

from app.collection.base import (
    ListingSource,
    SourceAuthError,
    SourceError,
    SourceRateLimited,
)
from app.collection.rate_limit import RateLimiter
from app.config import CollectionConfig
from app.models.listing import Condition, Listing
from app.utils.normalize import normalize_listing

logger = logging.getLogger(__name__)

SOURCE_NAME = "vinted"

#: Vinted's condition labels, French and English, mapped onto our fixed enum.
CONDITION_TEXT_MAP: dict[str, Condition] = {
    "neuf avec etiquette": Condition.NEW_WITH_TAGS,
    "neuf avec etiquettes": Condition.NEW_WITH_TAGS,
    "new with tags": Condition.NEW_WITH_TAGS,
    "neuf sans etiquette": Condition.NEW_WITHOUT_TAGS,
    "neuf sans etiquettes": Condition.NEW_WITHOUT_TAGS,
    "new without tags": Condition.NEW_WITHOUT_TAGS,
    "tres bon etat": Condition.VERY_GOOD,
    "very good": Condition.VERY_GOOD,
    "bon etat": Condition.GOOD,
    "good": Condition.GOOD,
    "satisfaisant": Condition.SATISFACTORY,
    "satisfactory": Condition.SATISFACTORY,
}

#: Best-effort ``status_id`` mapping, used only as a fallback when the text
#: label is missing. Overridable in config; the post-fetch allow-list filter
#: runs regardless, so a wrong id here cannot let a bad condition through.
CONDITION_ID_MAP: dict[int, Condition] = {
    6: Condition.NEW_WITH_TAGS,
    1: Condition.NEW_WITHOUT_TAGS,
    2: Condition.VERY_GOOD,
    3: Condition.GOOD,
    4: Condition.SATISFACTORY,
}


class ListingParseError(ValueError):
    """One item in the response could not be understood. Skip it, count it."""


def _first(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, "", {}):
            return value
    return None


def _parse_price(item: dict[str, Any]) -> tuple[float, str]:
    raw = _first(item, "price", "total_item_price")
    if isinstance(raw, dict):
        amount = raw.get("amount")
        currency = str(raw.get("currency_code") or raw.get("currency") or "EUR")
    else:
        amount = raw
        currency = str(item.get("currency") or "EUR")
    if amount is None:
        raise ListingParseError("item has no price")
    try:
        return float(amount), currency.upper()
    except (TypeError, ValueError) as exc:
        raise ListingParseError(f"unparseable price {amount!r}") from exc


def _parse_condition(item: dict[str, Any]) -> Condition | None:
    from app.utils.normalize import normalize_text

    text = _first(item, "status", "condition", "status_title")
    if isinstance(text, str):
        mapped = CONDITION_TEXT_MAP.get(normalize_text(text))
        if mapped:
            return mapped
        logger.debug("unrecognised condition label", extra={"label": text})
    status_id = item.get("status_id")
    if isinstance(status_id, int):
        return CONDITION_ID_MAP.get(status_id)
    return None


def _parse_published_at(item: dict[str, Any]) -> datetime | None:
    """Publication time, approximate.

    The catalog endpoint does not reliably expose a creation timestamp; the
    photo timestamp is the usual stand-in. Treated as approximate everywhere.
    """
    for key in ("created_at_ts", "created_at"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    photo = item.get("photo")
    if isinstance(photo, dict):
        high_res = photo.get("high_resolution")
        if isinstance(high_res, dict) and isinstance(high_res.get("timestamp"), (int, float)):
            return datetime.fromtimestamp(high_res["timestamp"], tz=timezone.utc)
    return None


def _parse_location(item: dict[str, Any]) -> str | None:
    """Listing-level location only (city / country as shown on the listing).

    Nothing that identifies the seller is read here: no id, login, name or
    profile URL, ever.
    """
    parts: list[str] = []
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    city = _first(item, "city") or (user.get("city") if user else None)
    country = _first(item, "country_title", "country") or (
        user.get("country_title") if user else None
    )
    for value in (city, country):
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return ", ".join(parts) or None


def _parse_seller_is_business(item: dict[str, Any]) -> bool | None:
    """The only seller-derived field we keep: a non-identifying boolean."""
    user = item.get("user")
    if isinstance(user, dict):
        for key in ("business", "is_business", "business_account"):
            if isinstance(user.get(key), bool):
                return user[key]
    if isinstance(item.get("is_business"), bool):
        return item["is_business"]
    return None


def parse_item(item: dict[str, Any], base_url: str) -> Listing:
    """Validate one raw API item into a normalized ``Listing``.

    Shared with the mock source so dry runs exercise the same validation and
    normalization path as real collection.
    """
    if not isinstance(item, dict):
        raise ListingParseError("item is not an object")

    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ListingParseError("item has no title")

    url = _first(item, "url")
    if not isinstance(url, str) or not url.startswith("http"):
        path = item.get("path")
        if isinstance(path, str) and path:
            url = f"{base_url.rstrip('/')}{path if path.startswith('/') else '/' + path}"
        elif item.get("id") is not None:
            url = f"{base_url.rstrip('/')}/items/{item['id']}"
        else:
            raise ListingParseError("item has neither url, path nor id")

    price, currency = _parse_price(item)
    external_id = str(item["id"]) if item.get("id") is not None else None

    listing = Listing(
        source=SOURCE_NAME,
        external_id=external_id,
        url=url,
        title=title.strip(),
        price_eur=price,
        currency=currency,
        condition=_parse_condition(item),
        location=_parse_location(item),
        published_at=_parse_published_at(item),
        source_brand=item.get("brand_title") if isinstance(item.get("brand_title"), str) else None,
        seller_is_business=_parse_seller_is_business(item),
    )
    return normalize_listing(listing)


def parse_items(items: Iterable[Any], base_url: str) -> tuple[list[Listing], int]:
    """Parse a page. Returns the good listings and the number skipped."""
    parsed: list[Listing] = []
    skipped = 0
    for item in items:
        try:
            parsed.append(parse_item(item, base_url))
        except Exception as exc:  # validation errors must never kill a run
            skipped += 1
            logger.warning(
                "skipping malformed item",
                extra={"error": str(exc), "item_id": (item or {}).get("id") if isinstance(item, dict) else None},
            )
    return parsed, skipped


class VintedCatalogSource(ListingSource):
    """Collector for catalog 4597 (padel racquets) on vinted.fr."""

    name = SOURCE_NAME

    def __init__(
        self,
        config: CollectionConfig,
        session: requests.Session | None = None,
    ) -> None:
        if not config.session_cookie:
            raise SourceAuthError(
                "VINTED_SESSION_COOKIE is not set - the internal JSON endpoint "
                "requires a valid session cookie"
            )
        self.config = config
        self.limiter = RateLimiter(config.requests_per_minute)
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.user_agent,
                "Accept": "application/json",
                "Accept-Language": "fr-FR,fr;q=0.9",
                "Cookie": config.session_cookie,
            }
        )

    # -- public API ---------------------------------------------------------

    def browse(self, *, max_pages: int | None = None) -> list[Listing]:
        pages = max_pages if max_pages is not None else self.config.browse_max_pages
        return self._collect(search_text=None, max_pages=pages)

    def search(self, query: str, *, max_pages: int | None = None) -> list[Listing]:
        pages = max_pages if max_pages is not None else self.config.search_max_pages
        return self._collect(search_text=query, max_pages=pages)

    def close(self) -> None:
        self.session.close()

    # -- internals ----------------------------------------------------------

    def _collect(self, *, search_text: str | None, max_pages: int) -> list[Listing]:
        collected: list[Listing] = []
        total_skipped = 0
        for page in range(1, max(1, max_pages) + 1):
            payload = self._get_page(page=page, search_text=search_text)
            items = payload.get("items")
            if not isinstance(items, list):
                logger.warning("response has no items array", extra={"page": page})
                break
            listings, skipped = parse_items(items, self.config.base_url)
            total_skipped += skipped
            collected.extend(listings)
            logger.info(
                "collected page",
                extra={
                    "mode": "targeted" if search_text else "browse",
                    "query": search_text,
                    "page": page,
                    "items": len(items),
                    "parsed": len(listings),
                    "skipped": skipped,
                },
            )
            if len(items) < self.config.per_page:
                break
            pagination = payload.get("pagination")
            if isinstance(pagination, dict):
                total_pages = pagination.get("total_pages")
                if isinstance(total_pages, int) and page >= total_pages:
                    break
        if total_skipped:
            logger.warning("items skipped during parsing", extra={"skipped": total_skipped})
        return collected

    def _get_page(self, *, page: int, search_text: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "catalog_ids": self.config.catalog_id,
            "page": page,
            "per_page": self.config.per_page,
            "order": "newest_first",
        }
        if self.config.status_ids:
            # Query-level condition filter, so excluded listings are never
            # fetched in the first place.
            params["status_ids"] = ",".join(str(i) for i in self.config.status_ids)
        if search_text:
            params["search_text"] = search_text

        url = f"{self.config.base_url}/api/v2/catalog/items"
        self.limiter.wait()
        try:
            response = self.session.get(
                url, params=params, timeout=self.config.timeout_seconds
            )
        except requests.RequestException as exc:
            raise SourceError(f"request to Vinted failed: {exc}") from exc

        if response.status_code in (401, 403):
            raise SourceAuthError(
                f"Vinted returned {response.status_code}. The session cookie is "
                "likely expired or rejected. Refresh VINTED_SESSION_COOKIE and "
                "re-run; do not retry in a loop."
            )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                seconds = float(retry_after) if retry_after else self.config.backoff_seconds
            except ValueError:
                seconds = self.config.backoff_seconds
            raise SourceRateLimited(
                f"Vinted returned 429. Backing off for {seconds:.0f}s and stopping "
                "collection for this run.",
                retry_after_seconds=seconds,
            )
        if response.status_code >= 400:
            raise SourceError(
                f"Vinted returned HTTP {response.status_code} for page {page}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError("Vinted response was not JSON") from exc
        if not isinstance(payload, dict):
            raise SourceError("Vinted response was not a JSON object")
        return payload
