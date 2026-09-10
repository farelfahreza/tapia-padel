"""Persistence.

``Store`` is the only interface the pipeline knows about. ``PostgresStore`` is
the real one; ``InMemoryStore`` backs dry runs without a database and the
tests. Keeping them behind one protocol is what lets a dry run exercise the
whole pipeline without a server.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from app.models.listing import Condition, Listing, ModelStats, PriceObservation
from app.models.opportunity import Opportunity
from app.models.watchlist import WatchlistEntry

logger = logging.getLogger(__name__)

TELEGRAM_OFFSET_KEY = "telegram_updates_offset"


@dataclass(frozen=True)
class ListingRecord:
    """What happened when a collected listing hit the database."""

    id: int
    is_new: bool
    previous_price_eur: float | None = None

    @property
    def price_changed(self) -> bool:
        return self.previous_price_eur is not None and self.previous_price_eur != 0.0


class Store(Protocol):
    # -- listings & observations -------------------------------------------
    def upsert_listing(self, listing: Listing) -> ListingRecord: ...
    def record_observation(self, observation: PriceObservation) -> None: ...
    def observations_for_models(
        self, model_keys: Sequence[str], since: datetime
    ) -> dict[str, list[PriceObservation]]: ...
    def model_stats(
        self, model_keys: Sequence[str], *, demand_window_days: int, stale_days: int
    ) -> dict[str, ModelStats]: ...

    # -- opportunities & notifications -------------------------------------
    def save_opportunity(self, listing_id: int, opportunity: Opportunity) -> None: ...
    def last_notified_prices(self, listing_ids: Sequence[int]) -> dict[int, float]: ...
    def record_notification(
        self,
        *,
        listing_id: int,
        bucket: str,
        matched_query: str | None,
        score: float,
        price_eur: float,
        message: str,
    ) -> None: ...

    # -- watchlist ----------------------------------------------------------
    def watchlist(self) -> list[WatchlistEntry]: ...
    def add_watchlist_entry(
        self, query_text: str, normalized_query: str, max_price_eur: float | None
    ) -> bool: ...
    def remove_watchlist_entry(self, normalized_query: str) -> bool: ...

    # -- small key/value state ---------------------------------------------
    def get_state(self, key: str) -> str | None: ...
    def set_state(self, key: str, value: str) -> None: ...

    # -- transaction --------------------------------------------------------
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class PostgresStore:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    # -- listings & observations -------------------------------------------

    def upsert_listing(self, listing: Listing) -> ListingRecord:
        dedup_key = listing.required_dedup_key
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id, price_eur FROM listings WHERE source = %s AND dedup_key = %s",
                (listing.source, dedup_key),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    """
                    INSERT INTO listings (
                        source, external_id, dedup_key, url, title, title_normalized,
                        brand, model_key, price_eur, currency, condition, location,
                        published_at, seller_is_business
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        listing.source,
                        listing.external_id,
                        dedup_key,
                        listing.url,
                        listing.title,
                        listing.title_normalized or listing.title.lower(),
                        listing.brand,
                        listing.required_model_key,
                        listing.price_eur,
                        listing.currency,
                        listing.condition.value if listing.condition else None,
                        listing.location,
                        listing.published_at,
                        listing.seller_is_business,
                    ),
                )
                listing_id = cur.fetchone()[0]
                self._touch_model(cur, listing, increment=True)
                return ListingRecord(id=listing_id, is_new=True)

            listing_id, old_price = int(row[0]), float(row[1])
            cur.execute(
                """
                UPDATE listings
                   SET price_eur = %s,
                       url = %s,
                       title = %s,
                       title_normalized = %s,
                       brand = %s,
                       model_key = %s,
                       condition = %s,
                       location = %s,
                       seller_is_business = %s,
                       last_seen_at = NOW(),
                       seen_count = seen_count + 1,
                       updated_at = NOW()
                 WHERE id = %s
                """,
                (
                    listing.price_eur,
                    listing.url,
                    listing.title,
                    listing.title_normalized or listing.title.lower(),
                    listing.brand,
                    listing.required_model_key,
                    listing.condition.value if listing.condition else None,
                    listing.location,
                    listing.seller_is_business,
                    listing_id,
                ),
            )
            self._touch_model(cur, listing, increment=False)
            return ListingRecord(
                id=listing_id, is_new=False, previous_price_eur=old_price
            )

    @staticmethod
    def _touch_model(cur: Any, listing: Listing, *, increment: bool) -> None:
        cur.execute(
            """
            INSERT INTO racket_models (model_key, brand, display_name, listings_seen)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (model_key) DO UPDATE
               SET listings_seen = racket_models.listings_seen + %s,
                   last_seen_at = NOW(),
                   updated_at = NOW()
            """,
            (
                listing.required_model_key,
                listing.brand,
                listing.title[:200],
                1 if increment else 0,
                1 if increment else 0,
            ),
        )

    def record_observation(self, observation: PriceObservation) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO price_observations
                    (model_key, condition, price_eur, source, listing_dedup_key, observed_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    observation.model_key,
                    observation.condition.value,
                    observation.price_eur,
                    observation.source,
                    observation.listing_dedup_key,
                    observation.observed_at,
                ),
            )

    def observations_for_models(
        self, model_keys: Sequence[str], since: datetime
    ) -> dict[str, list[PriceObservation]]:
        if not model_keys:
            return {}
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT model_key, condition, price_eur, observed_at, listing_dedup_key, source
                  FROM price_observations
                 WHERE model_key = ANY(%s) AND observed_at >= %s
                """,
                (list(model_keys), since),
            )
            rows = cur.fetchall()
        grouped: dict[str, list[PriceObservation]] = {key: [] for key in model_keys}
        for model_key, condition, price, observed_at, dedup_key, source in rows:
            grouped.setdefault(model_key, []).append(
                PriceObservation(
                    model_key=model_key,
                    condition=Condition(condition),
                    price_eur=float(price),
                    observed_at=observed_at,
                    listing_dedup_key=dedup_key,
                    source=source,
                )
            )
        return grouped

    def model_stats(
        self, model_keys: Sequence[str], *, demand_window_days: int, stale_days: int
    ) -> dict[str, ModelStats]:
        if not model_keys:
            return {}
        now = datetime.now(timezone.utc)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT model_key,
                       COUNT(*) FILTER (WHERE first_seen_at >= %s) AS listings_seen,
                       COUNT(*) FILTER (WHERE seen_count >= 2 AND last_seen_at < %s)
                           AS turnover_signals
                  FROM listings
                 WHERE model_key = ANY(%s)
                 GROUP BY model_key
                """,
                (
                    now - timedelta(days=demand_window_days),
                    now - timedelta(days=stale_days),
                    list(model_keys),
                ),
            )
            rows = cur.fetchall()
        stats = {key: ModelStats(model_key=key) for key in model_keys}
        for model_key, listings_seen, turnover in rows:
            stats[model_key] = ModelStats(
                model_key=model_key,
                listings_seen=int(listings_seen or 0),
                turnover_signals=int(turnover or 0),
            )
        return stats

    # -- opportunities & notifications -------------------------------------

    def save_opportunity(self, listing_id: int, opportunity: Opportunity) -> None:
        economics, estimate = opportunity.economics, opportunity.estimate
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO opportunities (
                    listing_id, score, estimated_resale_eur, estimate_observations,
                    estimate_confidence, estimate_method, buy_total_cost_eur,
                    expected_net_proceeds_eur, expected_profit_eur, roi, score_breakdown
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    listing_id,
                    opportunity.score,
                    estimate.estimated_resale_eur,
                    estimate.observations,
                    estimate.confidence.value,
                    estimate.method.value,
                    economics.buy_total_cost_eur,
                    economics.expected_net_proceeds_eur,
                    economics.expected_profit_eur,
                    economics.roi,
                    json.dumps(opportunity.breakdown.model_dump()),
                ),
            )

    def last_notified_prices(self, listing_ids: Sequence[int]) -> dict[int, float]:
        if not listing_ids:
            return {}
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (listing_id) listing_id, price_eur
                  FROM notifications
                 WHERE listing_id = ANY(%s)
                 ORDER BY listing_id, sent_at DESC
                """,
                (list(listing_ids),),
            )
            return {int(row[0]): float(row[1]) for row in cur.fetchall()}

    def record_notification(
        self,
        *,
        listing_id: int,
        bucket: str,
        matched_query: str | None,
        score: float,
        price_eur: float,
        message: str,
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO notifications
                    (listing_id, channel, bucket, matched_watchlist_query, score,
                     price_eur, message)
                VALUES (%s, 'telegram', %s, %s, %s, %s, %s)
                """,
                (listing_id, bucket, matched_query, score, price_eur, message),
            )

    # -- watchlist ----------------------------------------------------------

    def watchlist(self) -> list[WatchlistEntry]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, query_text, normalized_query, max_price_eur, created_at
                  FROM watchlist ORDER BY created_at
                """
            )
            return [
                WatchlistEntry(
                    id=int(row[0]),
                    query_text=row[1],
                    normalized_query=row[2],
                    max_price_eur=float(row[3]) if row[3] is not None else None,
                    created_at=row[4],
                )
                for row in cur.fetchall()
            ]

    def add_watchlist_entry(
        self, query_text: str, normalized_query: str, max_price_eur: float | None
    ) -> bool:
        """True when a new entry was created, False when one was updated."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO watchlist (query_text, normalized_query, max_price_eur)
                VALUES (%s, %s, %s)
                ON CONFLICT (normalized_query) DO UPDATE
                   SET query_text = EXCLUDED.query_text,
                       max_price_eur = EXCLUDED.max_price_eur,
                       updated_at = NOW()
                RETURNING (xmax = 0) AS inserted
                """,
                (query_text, normalized_query, max_price_eur),
            )
            return bool(cur.fetchone()[0])

    def remove_watchlist_entry(self, normalized_query: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM watchlist WHERE normalized_query = %s", (normalized_query,)
            )
            return cur.rowcount > 0

    # -- key/value state ----------------------------------------------------

    def get_state(self, key: str) -> str | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT value FROM app_state WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_state (key, value) VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE
                   SET value = EXCLUDED.value, updated_at = NOW()
                """,
                (key, value),
            )

    # -- transaction --------------------------------------------------------

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()


class InMemoryStore:
    """Non-persistent Store. Used by dry runs without a database and by tests.

    Deliberately not a "cache" or a fallback for production: a real run always
    uses Postgres, because nothing here survives the process.
    """

    def __init__(self) -> None:
        self.listings: dict[str, dict[str, Any]] = {}
        self.observations: list[PriceObservation] = []
        self.opportunities: list[tuple[int, Opportunity]] = []
        self.notifications: list[dict[str, Any]] = []
        self.watchlist_entries: dict[str, WatchlistEntry] = {}
        self.state: dict[str, str] = {}
        self._next_id = 1

    def upsert_listing(self, listing: Listing) -> ListingRecord:
        key = f"{listing.source}:{listing.required_dedup_key}"
        existing = self.listings.get(key)
        now = datetime.now(timezone.utc)
        if existing is None:
            record = {
                "id": self._next_id,
                "listing": listing,
                "first_seen_at": now,
                "last_seen_at": now,
                "seen_count": 1,
            }
            self.listings[key] = record
            self._next_id += 1
            return ListingRecord(id=record["id"], is_new=True)
        previous_price = existing["listing"].price_eur
        existing["listing"] = listing
        existing["last_seen_at"] = now
        existing["seen_count"] += 1
        return ListingRecord(
            id=existing["id"], is_new=False, previous_price_eur=previous_price
        )

    def record_observation(self, observation: PriceObservation) -> None:
        self.observations.append(observation)

    def observations_for_models(
        self, model_keys: Sequence[str], since: datetime
    ) -> dict[str, list[PriceObservation]]:
        wanted = set(model_keys)
        grouped: dict[str, list[PriceObservation]] = {key: [] for key in wanted}
        for observation in self.observations:
            if observation.model_key in wanted and observation.observed_at >= since:
                grouped[observation.model_key].append(observation)
        return grouped

    def model_stats(
        self, model_keys: Sequence[str], *, demand_window_days: int, stale_days: int
    ) -> dict[str, ModelStats]:
        now = datetime.now(timezone.utc)
        demand_cutoff = now - timedelta(days=demand_window_days)
        stale_cutoff = now - timedelta(days=stale_days)
        stats = {key: ModelStats(model_key=key) for key in model_keys}
        for record in self.listings.values():
            key = record["listing"].model_key
            if key not in stats:
                continue
            current = stats[key]
            seen = current.listings_seen + (
                1 if record["first_seen_at"] >= demand_cutoff else 0
            )
            turnover = current.turnover_signals + (
                1
                if record["seen_count"] >= 2 and record["last_seen_at"] < stale_cutoff
                else 0
            )
            stats[key] = ModelStats(
                model_key=key, listings_seen=seen, turnover_signals=turnover
            )
        return stats

    def save_opportunity(self, listing_id: int, opportunity: Opportunity) -> None:
        self.opportunities.append((listing_id, opportunity))

    def last_notified_prices(self, listing_ids: Sequence[int]) -> dict[int, float]:
        wanted = set(listing_ids)
        prices: dict[int, float] = {}
        for notification in self.notifications:
            if notification["listing_id"] in wanted:
                prices[notification["listing_id"]] = notification["price_eur"]
        return prices

    def record_notification(
        self,
        *,
        listing_id: int,
        bucket: str,
        matched_query: str | None,
        score: float,
        price_eur: float,
        message: str,
    ) -> None:
        self.notifications.append(
            {
                "listing_id": listing_id,
                "bucket": bucket,
                "matched_query": matched_query,
                "score": score,
                "price_eur": price_eur,
                "message": message,
                "sent_at": datetime.now(timezone.utc),
            }
        )

    def watchlist(self) -> list[WatchlistEntry]:
        return list(self.watchlist_entries.values())

    def add_watchlist_entry(
        self, query_text: str, normalized_query: str, max_price_eur: float | None
    ) -> bool:
        created = normalized_query not in self.watchlist_entries
        self.watchlist_entries[normalized_query] = WatchlistEntry(
            id=self._next_id,
            query_text=query_text,
            normalized_query=normalized_query,
            max_price_eur=max_price_eur,
            created_at=datetime.now(timezone.utc),
        )
        self._next_id += 1
        return created

    def remove_watchlist_entry(self, normalized_query: str) -> bool:
        return self.watchlist_entries.pop(normalized_query, None) is not None

    def get_state(self, key: str) -> str | None:
        return self.state.get(key)

    def set_state(self, key: str, value: str) -> None:
        self.state[key] = value

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None
