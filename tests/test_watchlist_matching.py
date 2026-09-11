"""Watchlist matching, including the max-price ceiling rule."""

from app.models.watchlist import WatchlistEntry
from app.notifications.watchlist_match import find_match
from app.utils.normalize import normalized_query
from tests.factories import make_listing


def entry(text: str, max_price: float | None = None) -> WatchlistEntry:
    return WatchlistEntry(
        query_text=text,
        normalized_query=normalized_query(text),
        max_price_eur=max_price,
    )


def test_matches_on_brand_and_model():
    listing = make_listing(title="Raquette padel Nox AT10 Genius 18K", source_brand="Nox")
    assert find_match(listing, [entry("Nox AT10")]) is not None


def test_matches_however_i_typed_the_model():
    listing = make_listing(title="Raquette padel Nox AT10 Genius 18K", source_brand="Nox")
    for text in ("AT10", "at 10", "nox at10", "NOX AT 10"):
        assert find_match(listing, [entry(text)]) is not None, text


def test_does_not_match_a_different_model():
    listing = make_listing(title="Raquette padel Nox AT10 Genius", source_brand="Nox")
    assert find_match(listing, [entry("Nox AT2")]) is None
    assert find_match(listing, [entry("Bullpadel Vertex")]) is None


def test_brand_only_entry_matches_any_model_of_that_brand():
    listing = make_listing(title="Pala Bullpadel Hack 04", source_brand="Bullpadel")
    assert find_match(listing, [entry("Bullpadel")]) is not None


def test_empty_watchlist_matches_nothing():
    assert find_match(make_listing(), []) is None


def test_the_most_specific_entry_wins():
    listing = make_listing(title="Raquette padel Nox AT10 Genius 18K", source_brand="Nox")
    match = find_match(listing, [entry("Nox", 500), entry("Nox AT10 Genius", 120)])
    assert match is not None
    assert match.entry.query_text == "Nox AT10 Genius"
    assert match.entry.max_price_eur == 120


def test_the_matched_entry_carries_its_budget_ceiling():
    listing = make_listing(title="Bullpadel Vertex 04 Comfort", source_brand="Bullpadel")
    match = find_match(listing, [entry("Bullpadel Vertex", 100)])
    assert match is not None
    assert match.entry.max_price_eur == 100
    assert match.matched_on == "bullpadel vertex"
