"""Fee model. Buy side and sell side, explicitly separated.

Buying and reselling both happen on Vinted, which makes it very easy to
double-count a fee or forget one. So the two sides are computed by two
functions that each take only their own parameters:

  buy side   what leaves my pocket: listing price + buyer protection + shipping
             + packaging
  sell side  what reaches my pocket: resale price - seller commission
             - seller fixed fee - any shipping I absorb

Profit is the difference, ROI is profit over what I actually spent (not over
the sticker price - that would flatter every deal).
"""

from __future__ import annotations

from app.config import FeeConfig
from app.models.opportunity import Economics


def buy_total_cost(listing_price_eur: float, fees: FeeConfig) -> float:
    """Everything I pay to own the racket."""
    return round(
        listing_price_eur * (1.0 + fees.buyer_protection_pct)
        + fees.buyer_protection_fixed_eur
        + fees.buy_shipping_eur
        + fees.packaging_cost_eur,
        2,
    )


def net_sale_proceeds(resale_price_eur: float, fees: FeeConfig) -> float:
    """Everything I keep after Vinted takes its cut on the sell side."""
    return round(
        resale_price_eur * (1.0 - fees.seller_fee_pct)
        - fees.seller_fee_fixed_eur
        - fees.sell_shipping_cost_eur,
        2,
    )


def compute_economics(
    listing_price_eur: float,
    estimated_resale_eur: float | None,
    fees: FeeConfig,
) -> Economics:
    """Full picture for one listing.

    ``estimated_resale_eur`` is None during cold start, when there is no
    usable estimate. Profit and ROI are then None rather than zero - an
    unknown margin is not a zero margin.
    """
    cost = buy_total_cost(listing_price_eur, fees)
    if estimated_resale_eur is None:
        return Economics(listing_price_eur=listing_price_eur, buy_total_cost_eur=cost)
    proceeds = net_sale_proceeds(estimated_resale_eur, fees)
    profit = round(proceeds - cost, 2)
    return Economics(
        listing_price_eur=listing_price_eur,
        buy_total_cost_eur=cost,
        expected_net_proceeds_eur=proceeds,
        expected_profit_eur=profit,
        roi=round(profit / cost, 4) if cost > 0 else None,
    )
