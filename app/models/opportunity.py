"""Estimate, economics and score models.

Two things this file is careful about:
  * an estimate is always labelled an estimate and always carries the number
    of observations behind it;
  * a score is always decomposable - every point is attributable to a named
    component or a named penalty.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.models.listing import Condition, Listing


class ConfidenceLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EstimateMethod(str, Enum):
    #: Median of observations for this exact model + condition.
    MODEL_CONDITION_MEDIAN = "model_condition_median"
    #: Median across other conditions of the same model, adjusted by the
    #: condition price factors. Always penalised in confidence.
    CONDITION_ADJUSTED_FALLBACK = "condition_adjusted_fallback"
    #: Not enough data to say anything. Correct answer during cold start.
    INSUFFICIENT_DATA = "insufficient_data"


class ResaleEstimate(BaseModel):
    """An estimate. Never a price, never a promise."""

    model_key: str
    condition: Condition | None
    #: Median observed asking price for this model+condition.
    typical_listing_price_eur: float | None = None
    #: The asking price haircut toward a realistic sale price.
    estimated_resale_eur: float | None = None
    observations: int = 0
    confidence: ConfidenceLevel = ConfidenceLevel.NONE
    #: 0..1 multiplier applied to the final score.
    confidence_factor: float = 0.0
    method: EstimateMethod = EstimateMethod.INSUFFICIENT_DATA
    #: Relative inter-quartile range, a spread measure. High spread = noisy model.
    dispersion: float | None = None
    is_estimate: bool = True

    @property
    def usable(self) -> bool:
        return (
            self.estimated_resale_eur is not None
            and self.method is not EstimateMethod.INSUFFICIENT_DATA
        )

    def describe(self) -> str:
        """Human-readable, honest one-liner used in logs and Telegram."""
        if not self.usable:
            return "no estimate (insufficient observations)"
        plural = "observation" if self.observations == 1 else "observations"
        return (
            f"EUR {self.estimated_resale_eur:.0f} (estimate, "
            f"{self.confidence.value} confidence - {self.observations} {plural})"
        )


class Economics(BaseModel):
    """Money, with the buy side and the sell side kept apart."""

    listing_price_eur: float
    buy_total_cost_eur: float
    expected_net_proceeds_eur: float | None = None
    expected_profit_eur: float | None = None
    roi: float | None = None


class ScoreComponent(BaseModel):
    name: str
    weight: float
    #: 0..1 before weighting.
    raw: float = Field(ge=0.0, le=1.0)
    points: float
    explanation: str


class ScoreBreakdown(BaseModel):
    """Why a listing scored what it scored."""

    components: list[ScoreComponent] = Field(default_factory=list)
    base_score: float = 0.0
    confidence_factor: float = 0.0
    score_after_confidence: float = 0.0
    caps_applied: list[str] = Field(default_factory=list)
    final_score: float = 0.0

    def as_text(self) -> str:
        lines = [
            f"{c.name}: {c.points:.1f}/{c.weight:.0f} ({c.explanation})"
            for c in self.components
        ]
        lines.append(
            f"base {self.base_score:.1f} x confidence {self.confidence_factor:.2f} "
            f"= {self.score_after_confidence:.1f}"
        )
        lines.extend(f"cap: {cap}" for cap in self.caps_applied)
        lines.append(f"final {self.final_score:.1f}")
        return "\n".join(lines)


class Opportunity(BaseModel):
    listing: Listing
    estimate: ResaleEstimate
    economics: Economics
    breakdown: ScoreBreakdown
    score: float
    #: Short bullet points shown in the Telegram alert.
    reasons: list[str] = Field(default_factory=list)
