"""Small helpers so tests read as tests, not as setup code."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.listing import Condition, Listing, PriceObservation
from app.utils.normalize import normalize_listing


def make_listing(
    title: str = "Raquette padel Bullpadel Vertex 04 Comfort",
    price_eur: float = 100.0,
    condition: Condition | None = Condition.VERY_GOOD,
    location: str | None = "Paris, France",
    external_id: str = "1",
    source_brand: str | None = "Bullpadel",
) -> Listing:
    return normalize_listing(
        Listing(
            source="vinted",
            external_id=external_id,
            url=f"https://www.vinted.fr/items/{external_id}-raquette",
            title=title,
            price_eur=price_eur,
            condition=condition,
            location=location,
            source_brand=source_brand,
        )
    )


def make_observations(
    model_key: str,
    prices: list[float],
    condition: Condition = Condition.VERY_GOOD,
    days_ago: int = 1,
) -> list[PriceObservation]:
    observed_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return [
        PriceObservation(
            model_key=model_key,
            condition=condition,
            price_eur=price,
            observed_at=observed_at,
            listing_dedup_key=f"vinted:obs-{index}",
        )
        for index, price in enumerate(prices)
    ]
