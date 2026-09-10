"""Racket name normalization.

Used in three places, and it matters that it is the same code in all three:
  * building the model key that price observations are grouped under;
  * matching a listing against a watchlist entry;
  * deduplicating listings that have no usable source id.

Deliberately rule-based and boring. No LLM, no fuzzy library.
"""

from __future__ import annotations

import re
import unicodedata

#: Words that carry no model information in a Vinted title.
NOISE_WORDS: frozenset[str] = frozenset(
    {
        "raquette", "raquettes", "racket", "rackets", "racquet", "racquets",
        "pala", "palas", "padel", "paddle", "tennis",
        "de", "du", "des", "la", "le", "les", "un", "une", "et", "avec", "pour",
        "the", "and", "with", "for",
        "neuf", "neuve", "occasion", "etat", "tres", "bon", "bonne", "parfait",
        "parfaite", "excellent", "excellente", "comme", "jamais", "servi",
        "used", "new", "condition", "good", "very",
        "vends", "vend", "vendre", "envoi", "rapide", "prix", "eur", "euros",
        "housse", "sac", "cadeau", "offert", "promo", "solde", "soldes",
    }
)

#: alias (already normalized) -> canonical brand. Multi-word aliases are
#: matched against the whole normalized title before single tokens.
BRAND_ALIASES: dict[str, str] = {
    "nox": "Nox",
    "bullpadel": "Bullpadel",
    "bull padel": "Bullpadel",
    "head": "Head",
    "adidas": "Adidas",
    "babolat": "Babolat",
    "wilson": "Wilson",
    "siux": "Siux",
    "starvie": "StarVie",
    "star vie": "StarVie",
    "dunlop": "Dunlop",
    "varlion": "Varlion",
    "black crown": "Black Crown",
    "blackcrown": "Black Crown",
    "drop shot": "Drop Shot",
    "dropshot": "Drop Shot",
    "royal padel": "Royal Padel",
    "vibora": "Vibor-A",
    "vibor a": "Vibor-A",
    "kuikma": "Kuikma",
    "decathlon": "Kuikma",
    "joma": "Joma",
    "tecnifibre": "Tecnifibre",
    "oxdog": "Oxdog",
    "enebe": "Enebe",
    "akkeron": "Akkeron",
    "mystica": "Mystica",
    "cartri": "Cartri",
    "softee": "Softee",
    "munich": "Munich",
    "prince": "Prince",
    "wingpadel": "Wingpadel",
    "just ten": "Just Ten",
}

#: How many tokens after the brand make up the model key. Too few merges
#: different price tiers together, too many fragments the price database.
MAX_MODEL_TOKENS = 3

_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_LEADING_ZERO_RE = re.compile(r"(?<=[a-z])0+(?=\d)")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_text(text: str) -> str:
    """Lowercase, de-accent, strip punctuation, collapse whitespace."""
    lowered = strip_accents(text or "").lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def _merge_alpha_digit_tokens(tokens: list[str]) -> list[str]:
    """Join a short letter token with the number that follows it.

    So "at 10" and "AT10" both end up as "at10", and "vertex 04" matches
    "Vertex 4".
    """
    merged: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        nxt = tokens[index + 1] if index + 1 < len(tokens) else None
        if (
            nxt is not None
            and token.isalpha()
            and len(token) <= 8
            and nxt.isdigit()
            and not _YEAR_RE.match(nxt)
        ):
            merged.append(token + nxt)
            index += 2
            continue
        merged.append(token)
        index += 1
    return [_LEADING_ZERO_RE.sub("", token) for token in merged]


def tokenize(text: str, *, drop_noise: bool = True) -> list[str]:
    """Normalized, noise-free, digit-merged tokens."""
    tokens = normalize_text(text).split()
    if drop_noise:
        tokens = [t for t in tokens if t not in NOISE_WORDS]
    tokens = [t for t in tokens if not _YEAR_RE.match(t)]
    return _merge_alpha_digit_tokens(tokens)


def extract_brand(text: str, source_brand: str | None = None) -> str | None:
    """Canonical brand from the source's brand field, else from the title."""
    for candidate in (source_brand, text):
        if not candidate:
            continue
        normalized = normalize_text(candidate)
        # Multi-word aliases first ("black crown" before "crown").
        for alias in sorted(BRAND_ALIASES, key=len, reverse=True):
            if " " in alias and alias in normalized:
                return BRAND_ALIASES[alias]
        for token in normalized.split():
            if token in BRAND_ALIASES:
                return BRAND_ALIASES[token]
    return None


def model_key(title: str, source_brand: str | None = None) -> str:
    """Stable key that price observations are grouped under.

    ``"Raquette padel Nox AT10 Genius 18K"`` -> ``"nox at10 genius 18k"``.
    Falls back to the plain normalized title when no brand is recognisable,
    which keeps unknown brands out of each other's price pools.
    """
    brand = extract_brand(title, source_brand)
    tokens = tokenize(title)
    if brand:
        brand_tokens = set(tokenize(brand, drop_noise=False))
        tokens = [t for t in tokens if t not in brand_tokens]
    model_tokens = tokens[:MAX_MODEL_TOKENS]
    parts = ([normalize_text(brand)] if brand else []) + model_tokens
    return " ".join(p for p in parts if p).strip()


def normalized_query(text: str) -> str:
    """Normalization for watchlist entries - same rules as listing titles."""
    return " ".join(tokenize(text))


def matches_query(listing_title: str, query: str, source_brand: str | None = None) -> bool:
    """True when every token of the watchlist query appears in the listing.

    Simple, rule-based, and predictable: "AT10", "at 10" and "Nox AT10" all
    match a "Nox AT10 Genius" listing; "Nox AT2" does not.
    """
    query_tokens = tokenize(query)
    if not query_tokens:
        return False
    haystack = set(tokenize(listing_title))
    brand = extract_brand(listing_title, source_brand)
    if brand:
        haystack.update(tokenize(brand, drop_noise=False))
    return all(_token_matches(token, haystack) for token in query_tokens)


def _token_matches(token: str, haystack: set[str]) -> bool:
    """Exact match, or a model family matching one of its numbered versions.

    Watching "Bullpadel Vertex" should catch a "Vertex 04", but watching
    "Nox AT2" must never catch an "AT10".
    """
    if token in haystack:
        return True
    if not token.isalpha():
        return False
    return any(
        candidate.startswith(token) and candidate[len(token):].isdigit()
        for candidate in haystack
    )


def normalize_listing(listing: "Listing") -> "Listing":
    """Attach brand, model key, normalized title and dedup key to a listing.

    Runs once, right after validation, so every downstream module sees the
    same normalized values.
    """
    from app.models.listing import Listing  # local import keeps models dependency-free
    from app.utils.dedup import canonical_url, dedup_key, item_id_from_url

    assert isinstance(listing, Listing)
    external_id = listing.external_id or item_id_from_url(listing.url)
    return listing.model_copy(
        update={
            "url": canonical_url(listing.url),
            "external_id": external_id,
            "brand": extract_brand(listing.title, listing.source_brand),
            "model_key": model_key(listing.title, listing.source_brand),
            "title_normalized": normalize_text(listing.title),
            "dedup_key": dedup_key(
                source=listing.source,
                external_id=external_id,
                url=listing.url,
                title=listing.title,
                price_eur=listing.price_eur,
                location=listing.location,
            ),
        }
    )
