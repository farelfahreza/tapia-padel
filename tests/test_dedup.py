"""Duplicate detection. Dedup state lives in Postgres, so keys must be stable."""

from app.utils.dedup import canonical_url, dedup_key, item_id_from_url
from tests.factories import make_listing


def test_prefers_the_source_item_id():
    assert dedup_key(source="vinted", external_id="12345") == "vinted:12345"


def test_falls_back_to_the_item_id_in_the_url():
    key = dedup_key(
        source="vinted",
        external_id=None,
        url="https://www.vinted.fr/items/12345-raquette-padel?referrer=catalog",
    )
    assert key == "vinted:12345"


def test_tracking_parameters_do_not_change_the_key():
    base = "https://www.vinted.fr/items/999-raquette"
    assert dedup_key(source="vinted", external_id=None, url=base) == dedup_key(
        source="vinted", external_id=None, url=f"{base}?utm_source=x&referrer=y"
    )


def test_hash_fallback_is_stable_and_sensitive_to_price():
    common = dict(source="vinted", external_id=None, url=None, title="Nox AT10", location="Paris")
    first = dedup_key(price_eur=90.0, **common)
    assert first == dedup_key(price_eur=90.0, **common)
    assert first != dedup_key(price_eur=95.0, **common)


def test_same_listing_seen_twice_produces_one_key():
    first = make_listing(external_id="42", price_eur=100)
    second = make_listing(external_id="42", price_eur=100)
    assert first.dedup_key == second.dedup_key


def test_different_listings_produce_different_keys():
    assert make_listing(external_id="1").dedup_key != make_listing(external_id="2").dedup_key


def test_canonical_url_helpers():
    assert canonical_url("https://www.vinted.fr/items/7-x/?a=b#c") == (
        "https://www.vinted.fr/items/7-x"
    )
    assert item_id_from_url("https://www.vinted.fr/items/7-x") == "7"
    assert item_id_from_url("https://www.vinted.fr/catalog/4597") is None
