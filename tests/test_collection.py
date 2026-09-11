"""The collection boundary: parsing, validation, condition filtering, privacy."""

import pytest

from app.collection.base import filter_accepted_conditions
from app.collection.mock import MockSource
from app.collection.vinted import ListingParseError, parse_item, parse_items
from app.config import DEFAULT_ACCEPTED_CONDITIONS
from app.models.listing import Condition

BASE = "https://www.vinted.fr"


def raw(**overrides):
    item = {
        "id": 4242,
        "title": "Raquette de padel Bullpadel Vertex 04 Comfort",
        "price": {"amount": "120.00", "currency_code": "EUR"},
        "brand_title": "Bullpadel",
        "status": "Tres bon etat",
        "url": "https://www.vinted.fr/items/4242-raquette",
        "city": "Paris",
        "country_title": "France",
        "user": {"id": 77, "login": "some_seller", "business": False},
        "photo": {"high_resolution": {"timestamp": 1757462400}},
    }
    item.update(overrides)
    return item


def test_parses_a_well_formed_item():
    listing = parse_item(raw(), BASE)
    assert listing.external_id == "4242"
    assert listing.price_eur == 120.0
    assert listing.condition is Condition.VERY_GOOD
    assert listing.location == "Paris, France"
    assert listing.brand == "Bullpadel"
    assert listing.model_key == "bullpadel vertex4 comfort"
    assert listing.dedup_key == "vinted:4242"
    assert listing.published_at is not None


def test_no_seller_identity_is_ever_carried_out_of_the_parser():
    listing = parse_item(raw(), BASE)
    dumped = listing.model_dump()
    assert "some_seller" not in str(dumped)
    assert "77" not in str(dumped.get("location"))
    assert set(dumped) & {"user", "seller_id", "seller_name", "profile_url"} == set()
    assert listing.seller_is_business is False


def test_french_and_english_condition_labels_map_onto_the_fixed_levels():
    assert parse_item(raw(status="Neuf avec étiquette"), BASE).condition is Condition.NEW_WITH_TAGS
    assert parse_item(raw(status="New without tags"), BASE).condition is Condition.NEW_WITHOUT_TAGS
    assert parse_item(raw(status="Bon état"), BASE).condition is Condition.GOOD
    assert parse_item(raw(status="Satisfaisant"), BASE).condition is Condition.SATISFACTORY


def test_status_id_is_the_fallback_when_the_label_is_missing():
    item = raw()
    item.pop("status")
    item["status_id"] = 6
    assert parse_item(item, BASE).condition is Condition.NEW_WITH_TAGS


def test_an_unknown_condition_label_is_not_invented():
    assert parse_item(raw(status="Etat correct-ish"), BASE).condition is None


def test_url_is_rebuilt_from_a_path_when_absent():
    item = raw()
    item.pop("url")
    item["path"] = "/items/4242-raquette"
    assert parse_item(item, BASE).url == "https://www.vinted.fr/items/4242-raquette"


def test_tracking_parameters_are_stripped_from_the_stored_url():
    listing = parse_item(raw(url="https://www.vinted.fr/items/4242-raquette?referrer=catalog"), BASE)
    assert listing.url == "https://www.vinted.fr/items/4242-raquette"


@pytest.mark.parametrize(
    "overrides",
    [
        {"price": None},
        {"price": {"amount": "not-a-number", "currency_code": "EUR"}},
        {"title": ""},
        {"price": {"amount": "0", "currency_code": "EUR"}},
    ],
)
def test_malformed_items_are_rejected_at_the_boundary(overrides):
    with pytest.raises((ListingParseError, ValueError)):
        parse_item(raw(**overrides), BASE)


def test_a_bad_item_does_not_take_the_whole_page_down():
    listings, skipped = parse_items([raw(), {"id": 1, "title": "no price"}], BASE)
    assert len(listings) == 1
    assert skipped == 1


def test_non_eur_prices_are_rejected():
    with pytest.raises(ValueError):
        parse_item(raw(price={"amount": "120", "currency_code": "GBP"}), BASE)


def test_condition_allow_list_drops_lower_conditions_and_unknowns():
    listings, _ = parse_items(
        [
            raw(id=1, status="Tres bon etat"),
            raw(id=2, status="Neuf avec etiquette"),
            raw(id=3, status="Bon etat"),
            raw(id=4, status="Satisfaisant"),
            raw(id=5, status="???"),
        ],
        BASE,
    )
    kept, rejected = filter_accepted_conditions(listings, DEFAULT_ACCEPTED_CONDITIONS)
    assert {listing.external_id for listing in kept} == {"1", "2"}
    assert rejected == 3


def test_the_allow_list_can_be_loosened_without_touching_code():
    listings, _ = parse_items([raw(id=3, status="Bon etat")], BASE)
    kept, rejected = filter_accepted_conditions(
        listings, DEFAULT_ACCEPTED_CONDITIONS + (Condition.GOOD,)
    )
    assert len(kept) == 1 and rejected == 0


def test_mock_source_uses_the_same_parser_and_supports_both_modes():
    source = MockSource()
    everything = source.browse()
    assert len(everything) > 10
    targeted = source.search("Nox AT10")
    assert targeted and all("Nox" in listing.title for listing in targeted)
    assert len(targeted) < len(everything)
