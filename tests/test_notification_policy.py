"""The two-threshold notification decision."""

from app.config import FeeConfig, NotificationConfig, PricingConfig, ScoringConfig
from app.models.listing import Condition, ModelStats
from app.models.watchlist import WatchlistEntry, WatchlistMatch
from app.notifications.policy import NotificationBucket, decide
from app.pricing.estimator import ResalePriceEstimator
from app.pricing.fees import compute_economics
from app.scoring.scorer import OpportunityScorer
from tests.factories import make_listing, make_observations

CONFIG = NotificationConfig(
    watchlist_score_threshold=60.0,
    exceptional_score_threshold=80.0,
    min_observations_to_notify=5,
    renotify_price_drop_pct=0.10,
)
ESTIMATOR = ResalePriceEstimator(PricingConfig())
SCORER = OpportunityScorer(ScoringConfig())
STATS = ModelStats(model_key="bullpadel vertex4 comfort", listings_seen=8, turnover_signals=1)


def build(price: float, comps: list[float], condition=Condition.VERY_GOOD):
    listing = make_listing(price_eur=price, condition=condition)
    estimate = ESTIMATOR.estimate(
        listing.model_key, condition, make_observations(listing.model_key, comps)
    )
    economics = compute_economics(price, estimate.estimated_resale_eur, FeeConfig())
    return SCORER.score(listing, estimate, economics, STATS)


def match_for(opportunity, max_price=None, text="Bullpadel Vertex"):
    return WatchlistMatch(
        entry=WatchlistEntry(
            query_text=text, normalized_query="bullpadel vertex", max_price_eur=max_price
        ),
        matched_on="bullpadel vertex",
    )


EXCEPTIONAL = [140] * 15   # rich price history, deep discount at EUR 82
DECENT = [125] * 15        # a decent but not exceptional deal at EUR 82


def test_an_exceptional_deal_is_notified_even_off_watchlist():
    opportunity = build(82, EXCEPTIONAL)
    assert opportunity.score >= CONFIG.exceptional_score_threshold
    decision = decide(opportunity, None, CONFIG)
    assert decision.should_notify
    assert decision.bucket is NotificationBucket.EXCEPTIONAL
    assert decision.bucket_label == "Not on watchlist - exceptional deal"


def test_a_merely_decent_deal_off_watchlist_stays_quiet():
    opportunity = build(82, DECENT)
    assert CONFIG.watchlist_score_threshold <= opportunity.score < CONFIG.exceptional_score_threshold
    decision = decide(opportunity, None, CONFIG)
    assert not decision.should_notify
    assert any("exceptional-deal threshold" in reason for reason in decision.reasons)


def test_the_same_decent_deal_on_the_watchlist_is_notified():
    opportunity = build(82, DECENT)
    decision = decide(opportunity, match_for(opportunity), CONFIG)
    assert decision.should_notify
    assert decision.bucket is NotificationBucket.WATCHLIST
    assert "your watchlist (Bullpadel Vertex)" in decision.bucket_label


def test_being_under_my_max_price_is_not_by_itself_a_reason_to_alert():
    # Cheap in absolute terms, but the resale margin does not survive fees.
    opportunity = build(118, [125] * 15)
    decision = decide(opportunity, match_for(opportunity, max_price=200), CONFIG)
    assert not decision.should_notify


def test_a_price_above_my_max_is_suppressed_even_with_a_great_margin():
    opportunity = build(82, EXCEPTIONAL)
    assert opportunity.score >= CONFIG.exceptional_score_threshold
    decision = decide(opportunity, match_for(opportunity, max_price=70), CONFIG)
    assert not decision.should_notify
    assert any("above your max" in reason for reason in decision.reasons)


def test_a_price_under_my_max_still_has_to_clear_the_score():
    opportunity = build(82, EXCEPTIONAL)
    decision = decide(opportunity, match_for(opportunity, max_price=90), CONFIG)
    assert decision.should_notify


def test_a_low_confidence_estimate_never_alerts():
    opportunity = build(82, [140, 145, 150])  # 3 observations
    decision = decide(opportunity, match_for(opportunity), CONFIG)
    assert not decision.should_notify
    assert any("observations" in reason for reason in decision.reasons)


def test_cold_start_never_alerts():
    opportunity = build(82, [])
    decision = decide(opportunity, match_for(opportunity), CONFIG)
    assert not decision.should_notify
    assert any("no usable resale estimate" in reason for reason in decision.reasons)


def test_an_already_alerted_listing_is_not_repeated():
    opportunity = build(82, EXCEPTIONAL)
    decision = decide(opportunity, None, CONFIG, already_notified_price_eur=82.0)
    assert not decision.should_notify
    assert any("already alerted" in reason for reason in decision.reasons)


def test_a_real_price_drop_reopens_an_already_alerted_listing():
    opportunity = build(70, EXCEPTIONAL)
    decision = decide(opportunity, None, CONFIG, already_notified_price_eur=82.0)
    assert decision.should_notify


def test_thresholds_are_configurable_in_one_place():
    opportunity = build(82, DECENT)
    strict = NotificationConfig(watchlist_score_threshold=95.0, exceptional_score_threshold=99.0)
    assert not decide(opportunity, match_for(opportunity), strict).should_notify
    loose = NotificationConfig(
        watchlist_score_threshold=10.0,
        exceptional_score_threshold=20.0,
        min_observations_to_notify=1,
    )
    assert decide(opportunity, None, loose).should_notify
