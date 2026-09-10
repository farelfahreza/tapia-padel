"""Racket name normalization - the piece three other modules depend on."""

from app.utils.normalize import (
    extract_brand,
    matches_query,
    model_key,
    normalize_text,
    normalized_query,
    tokenize,
)


def test_strips_accents_punctuation_and_case():
    assert normalize_text("Raquette PADEL Bullpadel Vertex-04 (très bon état)") == (
        "raquette padel bullpadel vertex 04 tres bon etat"
    )


def test_drops_noise_words():
    assert tokenize("Raquette de padel Nox AT10 très bon état") == ["nox", "at10"]


def test_merges_split_model_numbers():
    assert tokenize("Nox AT 10") == tokenize("Nox AT10") == ["nox", "at10"]


def test_leading_zeros_do_not_split_a_model():
    assert model_key("Bullpadel Vertex 04 Comfort") == model_key(
        "Bullpadel Vertex 4 Comfort"
    )


def test_brand_from_title_and_from_source_field():
    assert extract_brand("Raquette padel bullpadel vertex") == "Bullpadel"
    assert extract_brand("Raquette padel vertex 04", "BULLPADEL") == "Bullpadel"


def test_multi_word_brands_win_over_single_tokens():
    assert extract_brand("Pala Black Crown Piton 12K") == "Black Crown"


def test_decathlon_maps_to_its_padel_brand():
    assert extract_brand("Raquette Decathlon PR990") == "Kuikma"


def test_model_key_is_brand_plus_model_tokens():
    assert model_key("Raquette de padel Nox AT10 Genius 18K") == "nox at10 genius 18k"


def test_model_key_ignores_year_noise():
    assert model_key("Bullpadel Vertex 04 Comfort 2023") == "bullpadel vertex4 comfort"


def test_unknown_brand_still_produces_a_key():
    assert model_key("Raquette padel Zzz Superblade") == "zzz superblade"


def test_query_normalization_matches_listing_normalization():
    assert normalized_query("Nox AT 10") == "nox at10"


def test_matches_query_is_token_subset_not_substring():
    title = "Raquette de padel Nox AT10 Genius 18K"
    assert matches_query(title, "AT10")
    assert matches_query(title, "nox at 10")
    assert matches_query(title, "Nox AT10 Genius")
    assert not matches_query(title, "Nox AT2")
    assert not matches_query(title, "Bullpadel")


def test_empty_query_never_matches():
    assert not matches_query("Nox AT10", "   ")
