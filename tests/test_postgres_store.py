"""Schema and PostgresStore checks.

Skipped unless TEST_DATABASE_URL points at a throwaway database, so the suite
still runs anywhere. Worth having: the SQL is where a typo hides until 3am.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

psycopg = pytest.importorskip("psycopg")

from app.database.migrations import run_migrations
from app.database.store import TELEGRAM_OFFSET_KEY, PostgresStore
from app.models.listing import Condition, PriceObservation
from tests.factories import make_listing

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not set")


@pytest.fixture()
def store():
    conn = psycopg.connect(DSN, autocommit=False)
    run_migrations(conn)
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE notifications, opportunities, price_observations, listings, "
            "racket_models, watchlist, app_state RESTART IDENTITY CASCADE"
        )
    conn.commit()
    yield PostgresStore(conn)
    conn.rollback()
    conn.close()


def test_migrations_are_idempotent():
    conn = psycopg.connect(DSN, autocommit=False)
    try:
        run_migrations(conn)
        assert run_migrations(conn) == []
    finally:
        conn.close()


def test_a_listing_is_inserted_once_and_then_updated(store):
    listing = make_listing(external_id="500", price_eur=100)
    first = store.upsert_listing(listing)
    assert first.is_new

    second = store.upsert_listing(make_listing(external_id="500", price_eur=90))
    assert not second.is_new
    assert second.id == first.id
    assert second.previous_price_eur == 100.0

    with store.conn.cursor() as cur:
        cur.execute("SELECT seen_count, price_eur FROM listings WHERE id = %s", (first.id,))
        seen_count, price = cur.fetchone()
    assert seen_count == 2
    assert float(price) == 90.0


def test_observations_round_trip_and_respect_the_window(store):
    now = datetime.now(timezone.utc)
    for index, (price, age) in enumerate([(120, 1), (130, 2), (900, 400)]):
        store.record_observation(
            PriceObservation(
                model_key="bullpadel vertex4 comfort",
                condition=Condition.VERY_GOOD,
                price_eur=price,
                observed_at=now - timedelta(days=age),
                listing_dedup_key=f"vinted:{index}",
            )
        )
    grouped = store.observations_for_models(
        ["bullpadel vertex4 comfort"], now - timedelta(days=180)
    )
    prices = sorted(obs.price_eur for obs in grouped["bullpadel vertex4 comfort"])
    assert prices == [120.0, 130.0]


def test_model_stats_counts_listings_seen(store):
    store.upsert_listing(make_listing(external_id="1"))
    store.upsert_listing(make_listing(external_id="2"))
    stats = store.model_stats(
        ["bullpadel vertex4 comfort"], demand_window_days=90, stale_days=14
    )
    assert stats["bullpadel vertex4 comfort"].listings_seen == 2
    assert stats["bullpadel vertex4 comfort"].turnover_signals == 0


def test_notifications_record_the_bucket_and_the_last_price(store):
    record = store.upsert_listing(make_listing(external_id="7", price_eur=82))
    store.record_notification(
        listing_id=record.id,
        bucket="exceptional",
        matched_query=None,
        score=85.0,
        price_eur=82.0,
        message="...",
    )
    assert store.last_notified_prices([record.id]) == {record.id: 82.0}


def test_watchlist_add_update_and_remove(store):
    assert store.add_watchlist_entry("Nox AT10", "nox at10", 90.0) is True
    assert store.add_watchlist_entry("Nox AT10", "nox at10", 110.0) is False
    entries = store.watchlist()
    assert len(entries) == 1
    assert entries[0].max_price_eur == 110.0
    assert store.remove_watchlist_entry("nox at10") is True
    assert store.remove_watchlist_entry("nox at10") is False


def test_telegram_offset_survives_as_app_state(store):
    assert store.get_state(TELEGRAM_OFFSET_KEY) is None
    store.set_state(TELEGRAM_OFFSET_KEY, "42")
    store.set_state(TELEGRAM_OFFSET_KEY, "43")
    assert store.get_state(TELEGRAM_OFFSET_KEY) == "43"
