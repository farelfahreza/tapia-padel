"""Typed listing model, validated at the collection boundary.

Anything that fails validation here never reaches pricing or scoring.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Condition(str, Enum):
    """Vinted's fixed condition levels. No free-text conditions anywhere."""

    NEW_WITH_TAGS = "New with tags"
    NEW_WITHOUT_TAGS = "New without tags"
    VERY_GOOD = "Very good"
    GOOD = "Good"
    SATISFACTORY = "Satisfactory"


#: Ordered best-first. Used for condition scoring and for fallback pricing.
CONDITION_ORDER: tuple[Condition, ...] = (
    Condition.NEW_WITH_TAGS,
    Condition.NEW_WITHOUT_TAGS,
    Condition.VERY_GOOD,
    Condition.GOOD,
    Condition.SATISFACTORY,
)

#: Rough price ratios between conditions, relative to "Very good" = 1.0.
#: Only used when we have to borrow observations from another condition, and
#: that always costs the estimate confidence.
CONDITION_PRICE_FACTORS: dict[Condition, float] = {
    Condition.NEW_WITH_TAGS: 1.30,
    Condition.NEW_WITHOUT_TAGS: 1.20,
    Condition.VERY_GOOD: 1.00,
    Condition.GOOD: 0.85,
    Condition.SATISFACTORY: 0.70,
}

#: How desirable each condition is to resell.
CONDITION_QUALITY: dict[Condition, float] = {
    Condition.NEW_WITH_TAGS: 1.00,
    Condition.NEW_WITHOUT_TAGS: 0.90,
    Condition.VERY_GOOD: 0.70,
    Condition.GOOD: 0.40,
    Condition.SATISFACTORY: 0.20,
}


class Listing(BaseModel):
    """A single racket for sale, as observed on a source.

    Deliberately carries no seller identity: no name, handle, id or profile
    URL. ``seller_is_business`` is the only seller-derived field, because a
    professional seller prices differently from a private one, and it is a
    non-identifying aggregate attribute.
    """

    source: str
    external_id: str | None = None
    url: str
    title: str = Field(min_length=1, max_length=500)
    price_eur: float = Field(gt=0, le=5000)
    currency: str = "EUR"
    condition: Condition | None = None
    location: str | None = None
    published_at: datetime | None = None
    source_brand: str | None = None
    seller_is_business: bool | None = None

    # Filled in by app.utils.normalize.normalize_listing, not by the source.
    brand: str | None = None
    model_key: str | None = None
    title_normalized: str | None = None
    dedup_key: str | None = None

    @field_validator("currency")
    @classmethod
    def _only_eur(cls, value: str) -> str:
        if value.upper() != "EUR":
            raise ValueError(f"only EUR is supported, got {value!r}")
        return "EUR"

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"listing url must be absolute, got {value!r}")
        return value

    @field_validator("published_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @property
    def required_model_key(self) -> str:
        if not self.model_key:
            raise ValueError("listing has not been normalized yet")
        return self.model_key

    @property
    def required_dedup_key(self) -> str:
        if not self.dedup_key:
            raise ValueError("listing has not been normalized yet")
        return self.dedup_key


class PriceObservation(BaseModel):
    """One observed asking price for a model+condition at a point in time."""

    model_key: str
    condition: Condition
    price_eur: float
    observed_at: datetime
    listing_dedup_key: str
    source: str = "vinted"


class ModelStats(BaseModel):
    """Aggregates about a racket model, used by the demand/liquidity factors."""

    model_key: str
    listings_seen: int = 0
    #: Listings we saw at least twice and then stopped seeing. A proxy for
    #: "sold or delisted" - it is NOT confirmed sales data, and is labelled as
    #: a proxy everywhere it surfaces.
    turnover_signals: int = 0
