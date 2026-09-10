"""Opportunity scoring: the breakdown, the confidence penalty and the caps."""

import pytest

from app.config import FeeConfig, PricingConfig, ScoringConfig
from app.models.listing import Condition, ModelStats
from app.pricing.estimator import ResalePriceEstimator
from app.pricing.fees import compute_economics
from app.scoring.scorer import OpportunityScorer
from tests.factories import make_listing, make_observations

FEES = FeeConfig()
SCORING = ScoringConfig()
PRICING = PricingConfig()
ESTIMATOR = ResalePriceEstimator(PRICING)
SCORER = OpportunityScorer(SCORING)
MODEL = "bullpadel vertex4 comfort"


def score_for(price, prices, *, condition=Condition.VERY_GOOD, stats=None, location="Paris, France"):
    listing = make_listing(price_eur=price, condition=condition, location=location)
    estimate = ESTIMATOR.estimate(
        listing.model_key,
        condition,
        make_observations(listing.model_key, prices, condition=condition),
    )
    economics = compute_economics(listing.price_eur, estimate.estimated_resale_eur, FEES)
    return SCORER.score(listing, estimate, economics, stats)


def test_score_is_fully_decomposable():
    opportunity = score_for(82, [125, 135, 140, 145, 150] * 3)
    names = [component.name for component in opportunity.breakdown.components]
    assert names == [
        "price_advantage",
        "profit",
        "condition",
        "demand",
        "liquidity",
        "location",
    ]
    assert all(component.explanation for component in opportunity.breakdown.components)
    assert opportunity.breakdown.base_score == pytest.approx(
        sum(component.points for component in opportunity.breakdown.components), abs=0.01
    )


def test_component_points_never_exceed_their_weight():
    opportunity = score_for(10, [200] * 15)
    for component in opportunity.breakdown.components:
        assert 0 <= component.points <= component.weight


def test_score_stays_within_bounds():
    assert 0 <= score_for(10, [300] * 20).score <= 100
    assert 0 <= score_for(299, [300] * 20).score <= 100


def test_a_bigger_discount_scores_higher():
    cheap = score_for(80, [140] * 15)
    dear = score_for(120, [140] * 15)
    assert cheap.score > dear.score


def test_confidence_is_a_multiplier_on_the_whole_score():
    thin = score_for(82, [125, 140, 150])
    thick = score_for(82, [125, 140, 150] * 5)
    assert thin.breakdown.base_score == pytest.approx(thick.breakdown.base_score, abs=2.0)
    assert thin.score < thick.score
    assert thin.breakdown.confidence_factor < thick.breakdown.confidence_factor


def test_low_confidence_estimates_cannot_produce_a_high_score():
    # A huge apparent discount, but resting on three observations.
    thin = score_for(50, [200, 210, 220])
    assert thin.breakdown.base_score > 60
    assert thin.score < 60


def test_cold_start_is_capped_and_says_why():
    opportunity = score_for(50, [])
    assert opportunity.score <= SCORING.cold_start_score_cap
    assert any("no usable resale estimate" in cap for cap in opportunity.breakdown.caps_applied)


def test_thin_margin_is_capped_below_both_alert_thresholds():
    # Priced just under the typical price: a real gap, but not one that
    # survives fees.
    opportunity = score_for(135, [140] * 15)
    assert opportunity.score <= SCORING.unprofitable_score_cap
    assert any("below minimum" in cap for cap in opportunity.breakdown.caps_applied)


def test_negative_margin_is_capped_too():
    opportunity = score_for(200, [140] * 15)
    assert opportunity.score <= SCORING.unprofitable_score_cap


def test_better_condition_scores_higher_all_else_equal():
    very_good = score_for(82, [140] * 15, condition=Condition.VERY_GOOD)
    new_tags = score_for(82, [140] * 15, condition=Condition.NEW_WITH_TAGS)
    assert new_tags.score > very_good.score


def test_demand_and_liquidity_are_credited_but_labelled_as_proxies():
    quiet = score_for(82, [140] * 15, stats=ModelStats(model_key=MODEL))
    busy = score_for(
        82,
        [140] * 15,
        stats=ModelStats(model_key=MODEL, listings_seen=20, turnover_signals=10),
    )
    assert busy.score > quiet.score
    liquidity = next(c for c in busy.breakdown.components if c.name == "liquidity")
    assert "proxy" in liquidity.explanation


def test_preferred_location_beats_a_remote_one():
    near = score_for(82, [140] * 15, location="Paris, France")
    far = score_for(82, [140] * 15, location="Ajaccio, France")
    assert near.score > far.score


def test_reasons_flag_a_thin_evidence_base():
    opportunity = score_for(82, [125, 140, 150])
    assert any("only 3 observations" in reason for reason in opportunity.reasons)


def test_breakdown_renders_as_text():
    text = score_for(82, [140] * 15).breakdown.as_text()
    assert "price_advantage" in text and "final" in text
