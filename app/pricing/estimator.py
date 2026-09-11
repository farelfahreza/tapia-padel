"""Resale price estimation from observed Vinted listings.

Rule-based on purpose: the median of what this exact model in this exact
condition is being asked for, haircut toward what it plausibly *sells* for.
No LLM in this path, ever - it runs on every listing of every run.

Two honesty rules are enforced here rather than left to callers:

  1. Observations are asking prices, not sales. They are multiplied by a
     configurable realization factor before anything is called a resale
     estimate.
  2. Every estimate carries the number of observations behind it and a
     confidence factor. An estimate from 2 observations is not the same object
     as one from 50, and the scorer is required to care about the difference.

Seams left for later (deliberately not built now): a sales-history source can
replace ``observations`` without touching this signature, and a trend or
seasonality adjustment slots in where the realization factor is applied.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from app.config import PricingConfig
from app.models.listing import (
    CONDITION_PRICE_FACTORS,
    Condition,
    PriceObservation,
)
from app.models.opportunity import ConfidenceLevel, EstimateMethod, ResaleEstimate

logger = logging.getLogger(__name__)


def _median(values: Sequence[float]) -> float:
    return float(statistics.median(values))


def _relative_iqr(values: Sequence[float]) -> float | None:
    """Spread of the observations, relative to their median.

    A model whose listings range from EUR 40 to EUR 300 is telling us the key
    is too coarse or the market is chaotic; either way the estimate deserves
    less confidence.
    """
    if len(values) < 4:
        return None
    ordered = sorted(values)
    quartiles = statistics.quantiles(ordered, n=4, method="inclusive")
    median = _median(ordered)
    if median <= 0:
        return None
    return round((quartiles[2] - quartiles[0]) / median, 4)


class ResalePriceEstimator:
    def __init__(self, config: PricingConfig) -> None:
        self.config = config

    def estimate(
        self,
        model_key: str,
        condition: Condition | None,
        observations: Sequence[PriceObservation],
        *,
        exclude_dedup_key: str | None = None,
        now: datetime | None = None,
    ) -> ResaleEstimate:
        """Estimate the resale value of one model+condition.

        ``exclude_dedup_key`` removes the listing being evaluated from its own
        reference set - otherwise a cheap listing drags down the very average
        it is being compared against.
        """
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self.config.observation_window_days)

        relevant = [
            obs
            for obs in observations
            if obs.model_key == model_key
            and obs.observed_at >= cutoff
            and obs.listing_dedup_key != exclude_dedup_key
        ]

        same_condition = [obs for obs in relevant if obs.condition == condition]
        if condition is not None and len(same_condition) >= self.config.min_observations:
            prices = [obs.price_eur for obs in same_condition]
            return self._build(
                model_key=model_key,
                condition=condition,
                prices=prices,
                method=EstimateMethod.MODEL_CONDITION_MEDIAN,
                fallback_penalty=1.0,
            )

        # Not enough same-condition data: borrow the other conditions of this
        # model, rescaled by the condition price factors. Always penalised.
        if condition is not None and len(relevant) >= self.config.min_observations:
            target_factor = CONDITION_PRICE_FACTORS[condition]
            rescaled = [
                obs.price_eur * (target_factor / CONDITION_PRICE_FACTORS[obs.condition])
                for obs in relevant
            ]
            return self._build(
                model_key=model_key,
                condition=condition,
                prices=rescaled,
                method=EstimateMethod.CONDITION_ADJUSTED_FALLBACK,
                fallback_penalty=0.8,
            )

        logger.debug(
            "no usable estimate",
            extra={
                "model_key": model_key,
                "condition": condition.value if condition else None,
                "observations": len(relevant),
                "min_required": self.config.min_observations,
            },
        )
        return ResaleEstimate(
            model_key=model_key,
            condition=condition,
            observations=len(relevant),
            confidence=ConfidenceLevel.NONE,
            confidence_factor=0.0,
            method=EstimateMethod.INSUFFICIENT_DATA,
        )

    # -- internals ----------------------------------------------------------

    def _build(
        self,
        *,
        model_key: str,
        condition: Condition | None,
        prices: Sequence[float],
        method: EstimateMethod,
        fallback_penalty: float,
    ) -> ResaleEstimate:
        typical = round(_median(prices), 2)
        dispersion = _relative_iqr(prices)
        count = len(prices)
        factor = self._confidence_factor(count, dispersion, fallback_penalty)
        return ResaleEstimate(
            model_key=model_key,
            condition=condition,
            typical_listing_price_eur=typical,
            estimated_resale_eur=round(typical * self.config.realization_factor, 2),
            observations=count,
            confidence=self._confidence_level(count, method),
            confidence_factor=factor,
            method=method,
            dispersion=dispersion,
        )

    def _confidence_level(self, count: int, method: EstimateMethod) -> ConfidenceLevel:
        if count < self.config.min_observations:
            return ConfidenceLevel.NONE
        if count >= self.config.high_confidence_observations:
            level = ConfidenceLevel.HIGH
        elif count >= self.config.medium_confidence_observations:
            level = ConfidenceLevel.MEDIUM
        else:
            level = ConfidenceLevel.LOW
        # A borrowed-condition estimate never claims high confidence.
        if method is EstimateMethod.CONDITION_ADJUSTED_FALLBACK and level is ConfidenceLevel.HIGH:
            return ConfidenceLevel.MEDIUM
        return level

    def _confidence_factor(
        self, count: int, dispersion: float | None, fallback_penalty: float
    ) -> float:
        """0..1 multiplier applied to the final opportunity score.

        Grows with the number of observations, shrinks when they disagree with
        each other, and is docked for a borrowed-condition estimate.
        """
        from_count = min(1.0, count / max(1, self.config.target_observations))
        from_spread = 1.0
        if dispersion is not None and dispersion > 0.30:
            from_spread = max(0.5, 1.0 - (dispersion - 0.30))
        return round(max(0.0, from_count * from_spread * fallback_penalty), 4)
