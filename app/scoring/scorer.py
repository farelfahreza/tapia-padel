"""Opportunity scoring: 0-100, and always decomposable.

Design rules:
  * every point comes from a named component with an explanation string;
  * confidence is a multiplier on the whole score, not a component, because a
    weak estimate should drag down the entire result rather than cost a few
    points in one place;
  * hard caps are applied last and recorded by name, so "why is this only 40?"
    always has an answer.

The scoring leans conservative on purpose. Buying and reselling on the same
platform means the gap between a good deal and a waste of time is small: a
listing only scores highly when the price gap clearly beats fees plus a real
margin AND the estimate behind it rests on enough observations.
"""

from __future__ import annotations

import logging

from app.config import ScoringConfig
from app.models.listing import CONDITION_QUALITY, Listing, ModelStats
from app.models.opportunity import (
    Economics,
    Opportunity,
    ResaleEstimate,
    ScoreBreakdown,
    ScoreComponent,
)
from app.utils.normalize import normalize_text

logger = logging.getLogger(__name__)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


class OpportunityScorer:
    def __init__(self, config: ScoringConfig) -> None:
        self.config = config

    def score(
        self,
        listing: Listing,
        estimate: ResaleEstimate,
        economics: Economics,
        stats: ModelStats | None = None,
    ) -> Opportunity:
        cfg = self.config
        stats = stats or ModelStats(model_key=listing.model_key or "")
        components = [
            self._price_advantage(listing, estimate),
            self._profit(economics),
            self._condition(listing),
            self._demand(stats),
            self._liquidity(stats),
            self._location(listing),
        ]

        base = round(sum(component.points for component in components), 2)
        confidence_factor = estimate.confidence_factor
        after_confidence = round(base * confidence_factor, 2)

        caps: list[str] = []
        final = after_confidence

        if not estimate.usable:
            caps.append(
                f"no usable resale estimate (only {estimate.observations} observations) "
                f"- capped at {cfg.cold_start_score_cap:.0f}"
            )
            final = min(final, cfg.cold_start_score_cap)
        else:
            profit = economics.expected_profit_eur
            roi = economics.roi
            if profit is None or profit < cfg.min_profit_eur:
                caps.append(
                    f"expected profit {self._fmt(profit)} below minimum "
                    f"EUR {cfg.min_profit_eur:.0f} - capped at {cfg.unprofitable_score_cap:.0f}"
                )
                final = min(final, cfg.unprofitable_score_cap)
            if roi is None or roi < cfg.min_roi:
                caps.append(
                    f"ROI {self._fmt_pct(roi)} below minimum {cfg.min_roi:.0%} - "
                    f"capped at {cfg.unprofitable_score_cap:.0f}"
                )
                final = min(final, cfg.unprofitable_score_cap)

        final = round(max(0.0, min(100.0, final)), 2)
        breakdown = ScoreBreakdown(
            components=components,
            base_score=base,
            confidence_factor=confidence_factor,
            score_after_confidence=after_confidence,
            caps_applied=caps,
            final_score=final,
        )
        return Opportunity(
            listing=listing,
            estimate=estimate,
            economics=economics,
            breakdown=breakdown,
            score=final,
            reasons=self._reasons(components, estimate, economics),
        )

    # -- components ---------------------------------------------------------

    def _price_advantage(self, listing: Listing, estimate: ResaleEstimate) -> ScoreComponent:
        weight = self.config.weight_price_advantage
        typical = estimate.typical_listing_price_eur
        if not typical:
            return self._component(
                "price_advantage", weight, 0.0, "no typical price observed for this model yet"
            )
        discount = (typical - listing.price_eur) / typical
        raw = _clamp01(discount / self.config.target_discount)
        direction = "below" if discount >= 0 else "above"
        return self._component(
            "price_advantage",
            weight,
            raw,
            f"asking EUR {listing.price_eur:.0f}, typical EUR {typical:.0f} "
            f"({abs(discount):.0%} {direction} typical; full marks at "
            f"{self.config.target_discount:.0%} below)",
        )

    def _profit(self, economics: Economics) -> ScoreComponent:
        weight = self.config.weight_profit
        profit, roi = economics.expected_profit_eur, economics.roi
        if profit is None or roi is None:
            return self._component("profit", weight, 0.0, "no estimate, so no expected margin")
        # min() of the two targets: a high ROI on a EUR 5 profit is not a deal,
        # and neither is a fat absolute profit on a huge outlay.
        raw = _clamp01(
            min(roi / self.config.target_roi, profit / self.config.target_profit_eur)
        )
        return self._component(
            "profit",
            weight,
            raw,
            f"net profit EUR {profit:.0f} at {roi:.0%} ROI after fees "
            f"(targets: EUR {self.config.target_profit_eur:.0f} / {self.config.target_roi:.0%})",
        )

    def _condition(self, listing: Listing) -> ScoreComponent:
        weight = self.config.weight_condition
        if listing.condition is None:
            return self._component("condition", weight, 0.3, "condition unknown")
        raw = CONDITION_QUALITY[listing.condition]
        return self._component("condition", weight, raw, listing.condition.value)

    def _demand(self, stats: ModelStats) -> ScoreComponent:
        weight = self.config.weight_demand
        raw = _clamp01(stats.listings_seen / max(1, self.config.demand_target_listings))
        return self._component(
            "demand",
            weight,
            raw,
            f"{stats.listings_seen} listings seen for this model "
            f"(proxy for demand; full marks at {self.config.demand_target_listings})",
        )

    def _liquidity(self, stats: ModelStats) -> ScoreComponent:
        weight = self.config.weight_liquidity
        raw = _clamp01(stats.turnover_signals / max(1, self.config.liquidity_target_signals))
        return self._component(
            "liquidity",
            weight,
            raw,
            f"{stats.turnover_signals} listings disappeared after being seen "
            "(proxy for sales, not confirmed sales data)",
        )

    def _location(self, listing: Listing) -> ScoreComponent:
        weight = self.config.weight_location
        if not listing.location:
            return self._component("location", weight, 0.4, "location not published")
        normalized = normalize_text(listing.location)
        for preferred in self.config.preferred_locations:
            if normalize_text(preferred) in normalized:
                return self._component(
                    "location", weight, 1.0, f"{listing.location} (preferred area)"
                )
        return self._component(
            "location", weight, 0.5, f"{listing.location} (shipping only)"
        )

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _component(name: str, weight: float, raw: float, explanation: str) -> ScoreComponent:
        raw = _clamp01(raw)
        return ScoreComponent(
            name=name,
            weight=weight,
            raw=round(raw, 4),
            points=round(raw * weight, 2),
            explanation=explanation,
        )

    @staticmethod
    def _fmt(value: float | None) -> str:
        return "unknown" if value is None else f"EUR {value:.0f}"

    @staticmethod
    def _fmt_pct(value: float | None) -> str:
        return "unknown" if value is None else f"{value:.0%}"

    def _reasons(
        self,
        components: list[ScoreComponent],
        estimate: ResaleEstimate,
        economics: Economics,
    ) -> list[str]:
        """Short bullets for the Telegram alert. Honest, not salesy."""
        by_name = {component.name: component for component in components}
        reasons: list[str] = []

        price = by_name["price_advantage"]
        if price.raw >= 0.6:
            reasons.append("Listing price well below the typical Vinted price for this model")
        elif price.raw > 0:
            reasons.append("Listing price somewhat below the typical Vinted price for this model")

        if economics.expected_profit_eur is not None and economics.roi is not None:
            reasons.append(
                f"Expected margin EUR {economics.expected_profit_eur:.0f} "
                f"({economics.roi:.0%} ROI) after buy and sell fees"
            )

        if by_name["demand"].raw >= 0.6:
            reasons.append("Frequently listed model (demand proxy)")
        if by_name["liquidity"].raw >= 0.6:
            reasons.append("Listings for this model tend to disappear quickly (sales proxy)")
        if estimate.confidence_factor < 0.5:
            reasons.append(
                f"Caution: resale estimate rests on only {estimate.observations} observations"
            )
        return reasons
