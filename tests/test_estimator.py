"""Resale estimation and the confidence that goes with it."""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import PricingConfig
from app.models.listing import Condition
from app.models.opportunity import ConfidenceLevel, EstimateMethod
from app.pricing.estimator import ResalePriceEstimator
from tests.factories import make_observations

CONFIG = PricingConfig(
    realization_factor=0.9,
    observation_window_days=180,
    min_observations=3,
    target_observations=10,
    medium_confidence_observations=5,
    high_confidence_observations=8,
)
ESTIMATOR = ResalePriceEstimator(CONFIG)
MODEL = "bullpadel vertex4 comfort"


def test_cold_start_produces_no_estimate_at_all():
    estimate = ESTIMATOR.estimate(MODEL, Condition.VERY_GOOD, [])
    assert not estimate.usable
    assert estimate.method is EstimateMethod.INSUFFICIENT_DATA
    assert estimate.confidence is ConfidenceLevel.NONE
    assert estimate.confidence_factor == 0.0


def test_below_the_minimum_observation_count_there_is_still_no_estimate():
    observations = make_observations(MODEL, [100, 110])
    assert not ESTIMATOR.estimate(MODEL, Condition.VERY_GOOD, observations).usable


def test_estimate_is_the_median_haircut_by_the_realization_factor():
    observations = make_observations(MODEL, [100, 120, 140])
    estimate = ESTIMATOR.estimate(MODEL, Condition.VERY_GOOD, observations)
    assert estimate.typical_listing_price_eur == 120.0
    assert estimate.estimated_resale_eur == pytest.approx(108.0)
    assert estimate.is_estimate is True


def test_median_ignores_a_single_absurd_outlier():
    observations = make_observations(MODEL, [100, 120, 140, 5000])
    estimate = ESTIMATOR.estimate(MODEL, Condition.VERY_GOOD, observations)
    assert estimate.typical_listing_price_eur == 130.0


def test_confidence_grows_with_the_number_of_observations():
    few = ESTIMATOR.estimate(MODEL, Condition.VERY_GOOD, make_observations(MODEL, [100] * 3))
    many = ESTIMATOR.estimate(MODEL, Condition.VERY_GOOD, make_observations(MODEL, [100] * 10))
    assert few.confidence is ConfidenceLevel.LOW
    assert many.confidence is ConfidenceLevel.HIGH
    assert few.confidence_factor < many.confidence_factor
    assert many.confidence_factor == 1.0


def test_widely_disagreeing_observations_cost_confidence():
    tight = ESTIMATOR.estimate(
        MODEL, Condition.VERY_GOOD, make_observations(MODEL, [98, 99, 100, 101, 102])
    )
    noisy = ESTIMATOR.estimate(
        MODEL, Condition.VERY_GOOD, make_observations(MODEL, [40, 60, 100, 180, 260])
    )
    assert noisy.dispersion > tight.dispersion
    assert noisy.confidence_factor < tight.confidence_factor


def test_stale_observations_are_ignored():
    old = make_observations(MODEL, [100, 110, 120], days_ago=400)
    assert not ESTIMATOR.estimate(MODEL, Condition.VERY_GOOD, old).usable


def test_a_listing_is_never_part_of_its_own_reference_price():
    observations = make_observations(MODEL, [100, 110, 120])
    observations[0].listing_dedup_key = "vinted:self"
    estimate = ESTIMATOR.estimate(
        MODEL, Condition.VERY_GOOD, observations, exclude_dedup_key="vinted:self"
    )
    assert estimate.observations == 2
    assert not estimate.usable  # two observations is below the minimum


def test_other_conditions_are_borrowed_only_as_a_penalised_fallback():
    observations = make_observations(MODEL, [100, 110, 120], condition=Condition.GOOD)
    estimate = ESTIMATOR.estimate(MODEL, Condition.VERY_GOOD, observations)
    assert estimate.method is EstimateMethod.CONDITION_ADJUSTED_FALLBACK
    # "Good" observations rescaled up to "Very good" (1.00 / 0.85).
    assert estimate.typical_listing_price_eur > 110
    direct = ESTIMATOR.estimate(MODEL, Condition.VERY_GOOD, make_observations(MODEL, [100, 110, 120]))
    assert estimate.confidence_factor < direct.confidence_factor


def test_a_borrowed_estimate_never_claims_high_confidence():
    observations = make_observations(MODEL, [100] * 12, condition=Condition.GOOD)
    estimate = ESTIMATOR.estimate(MODEL, Condition.VERY_GOOD, observations)
    assert estimate.confidence is ConfidenceLevel.MEDIUM


def test_observations_of_other_models_are_not_mixed_in():
    observations = make_observations("nox at10 genius", [100, 110, 120])
    assert not ESTIMATOR.estimate(MODEL, Condition.VERY_GOOD, observations).usable


def test_estimates_describe_themselves_honestly():
    estimate = ESTIMATOR.estimate(MODEL, Condition.VERY_GOOD, make_observations(MODEL, [100] * 3))
    assert "estimate" in estimate.describe()
    assert "3 observations" in estimate.describe()
