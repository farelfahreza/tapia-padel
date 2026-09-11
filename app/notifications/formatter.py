"""Alert formatting.

Plain text on purpose: no Markdown or HTML parse mode, so a racket named
"Bela *Pro*" cannot break a message. Every number that is an estimate says so.
"""

from __future__ import annotations

from app.models.opportunity import Opportunity
from app.notifications.policy import NotificationDecision


def format_opportunity(opportunity: Opportunity, decision: NotificationDecision) -> str:
    listing = opportunity.listing
    estimate = opportunity.estimate
    economics = opportunity.economics

    name = listing.title.strip()
    if listing.brand and listing.model_key:
        name = listing.title.strip()

    lines = [
        "PADEL OPPORTUNITY",
        "",
        name,
        "",
        f"Buy price: EUR {listing.price_eur:.0f}",
        f"Estimated resale: {estimate.describe()}",
    ]

    if economics.expected_profit_eur is not None:
        lines.append(
            f"Estimated profit: EUR {economics.expected_profit_eur:.0f} "
            "(estimate, net of buy and sell fees)"
        )
    if economics.roi is not None:
        lines.append(f"ROI: {economics.roi:.0%} (estimate)")
    lines.append(f"Total buy cost incl. fees: EUR {economics.buy_total_cost_eur:.0f}")
    lines.append(f"Opportunity score: {opportunity.score:.0f}/100")
    lines.append("")
    lines.append(f"Condition: {listing.condition.value if listing.condition else 'unknown'}")
    lines.append(f"Location: {listing.location or 'not published'}")
    lines.append(f"Matched: {decision.bucket_label}")

    if opportunity.reasons:
        lines.append("")
        lines.append("Why:")
        lines.extend(f"- {reason}" for reason in opportunity.reasons)

    lines.append("")
    lines.append("Score breakdown:")
    for component in opportunity.breakdown.components:
        lines.append(
            f"- {component.name}: {component.points:.1f}/{component.weight:.0f} "
            f"({component.explanation})"
        )
    lines.append(
        f"- confidence multiplier: x{opportunity.breakdown.confidence_factor:.2f} "
        f"({estimate.observations} observations, {estimate.confidence.value})"
    )

    lines.append("")
    lines.append("URL:")
    lines.append(listing.url)
    lines.append("")
    lines.append("All resale figures are estimates from observed Vinted listings, not "
                 "confirmed sales. You decide whether to buy.")
    return "\n".join(lines)
