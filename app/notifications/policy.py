"""The two-threshold notification policy.

Not a strict filter and not a vague score boost:

  * a racket on my watchlist pings me at a LOWER score - I already said I care;
  * anything else pings me only at a HIGHER "exceptional deal" score, so I
    still discover things I did not ask for without being spammed;
  * an optional max price on a watchlist entry is a ceiling applied ON TOP of
    the scoring, never a replacement for it. Under my budget is not a reason
    to alert; over my budget is a reason to stay quiet even when the margin
    looks great.

Everything below the thresholds is dropped with a recorded reason, so a quiet
run can always be explained.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.config import NotificationConfig
from app.models.opportunity import Opportunity
from app.models.watchlist import WatchlistMatch


class NotificationBucket(str, Enum):
    WATCHLIST = "watchlist"
    EXCEPTIONAL = "exceptional"


@dataclass(frozen=True)
class NotificationDecision:
    should_notify: bool
    bucket: NotificationBucket | None = None
    match: WatchlistMatch | None = None
    threshold: float | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def bucket_label(self) -> str:
        """The "Matched:" line of the alert."""
        if self.bucket is NotificationBucket.WATCHLIST and self.match is not None:
            return f"your watchlist ({self.match.entry.query_text})"
        if self.bucket is NotificationBucket.EXCEPTIONAL:
            return "Not on watchlist - exceptional deal"
        return "not notified"


def decide(
    opportunity: Opportunity,
    match: WatchlistMatch | None,
    config: NotificationConfig,
    *,
    already_notified_price_eur: float | None = None,
) -> NotificationDecision:
    """Decide whether one scored listing is worth a Telegram message."""
    reasons: list[str] = []
    bucket = NotificationBucket.WATCHLIST if match else NotificationBucket.EXCEPTIONAL
    threshold = (
        config.watchlist_score_threshold
        if match
        else config.exceptional_score_threshold
    )

    # Gate 1 - the estimate has to be worth trusting. A low-confidence
    # estimate must never produce a confident-looking alert.
    if not opportunity.estimate.usable:
        reasons.append("no usable resale estimate yet")
    elif opportunity.estimate.observations < config.min_observations_to_notify:
        reasons.append(
            f"estimate rests on {opportunity.estimate.observations} observations, "
            f"need {config.min_observations_to_notify}"
        )

    # Gate 2 - the budget ceiling, on top of (never instead of) the scoring.
    if match and match.entry.max_price_eur is not None:
        if opportunity.listing.price_eur > match.entry.max_price_eur:
            reasons.append(
                f"price EUR {opportunity.listing.price_eur:.0f} above your max "
                f"EUR {match.entry.max_price_eur:.0f} for '{match.entry.query_text}'"
            )

    # Gate 3 - the score itself.
    if opportunity.score < threshold:
        reasons.append(
            f"score {opportunity.score:.0f} below the "
            f"{'watchlist' if match else 'exceptional-deal'} threshold {threshold:.0f}"
        )

    # Gate 4 - do not repeat myself. Only a real price drop re-opens a listing.
    if already_notified_price_eur is not None:
        drop = (
            (already_notified_price_eur - opportunity.listing.price_eur)
            / already_notified_price_eur
            if already_notified_price_eur > 0
            else 0.0
        )
        if drop < config.renotify_price_drop_pct:
            reasons.append(
                f"already alerted at EUR {already_notified_price_eur:.0f}; price drop "
                f"{drop:.0%} below the {config.renotify_price_drop_pct:.0%} re-alert rule"
            )

    if reasons:
        return NotificationDecision(
            should_notify=False,
            bucket=None,
            match=match,
            threshold=threshold,
            reasons=tuple(reasons),
        )
    return NotificationDecision(
        should_notify=True, bucket=bucket, match=match, threshold=threshold
    )
