"""Fee model, matching how Vinted actually works.

  Buy side   I pay the listing price, the 5% buyer protection fee, and the
             shipping, which depends on where the racket ships from.
  Sell side  Vinted takes no seller fee from a private seller, and my buyer
             pays the shipping. What I net is the sale price, minus only the
             costs I actually absorb (packaging).

Every one of those is still a configurable parameter rather than a constant:
"Vinted takes nothing from the seller" is true today, and is exactly the kind
of thing a marketplace changes. Keeping both sides as separate functions is
what stops a fee being counted twice or forgotten when that day comes.
"""

from __future__ import annotations

from app.config import FeeConfig
from app.models.opportunity import Economics
from app.utils.normalize import normalize_text


def buy_shipping_for(location: str | None, fees: FeeConfig) -> float:
    """Shipping I pay to receive the racket, by where it ships from.

    Matches the first configured location fragment found in the listing's
    location, and falls back to the default shipping cost. Deliberately a
    lookup table and not a carrier integration.
    """
    if location:
        normalized = normalize_text(location)
        for fragment, amount in fees.buy_shipping_by_location:
            if normalize_text(fragment) in normalized:
                return amount
    return fees.buy_shipping_eur


def buy_total_cost(
    listing_price_eur: float, fees: FeeConfig, location: str | None = None
) -> float:
    """Everything I pay to own the racket: price + buyer fee + shipping."""
    return round(
        listing_price_eur * (1.0 + fees.buyer_protection_pct)
        + fees.buyer_protection_fixed_eur
        + buy_shipping_for(location, fees),
        2,
    )


def net_sale_proceeds(resale_price_eur: float, fees: FeeConfig) -> float:
    """Everything I keep when it resells.

    No seller fee and no shipping on this side - the buyer pays both. Only my
    own packaging cost comes out of the sale.
    """
    return round(
        resale_price_eur * (1.0 - fees.seller_fee_pct)
        - fees.seller_fee_fixed_eur
        - fees.sell_shipping_cost_eur
        - fees.packaging_cost_eur,
        2,
    )


def compute_economics(
    listing_price_eur: float,
    estimated_resale_eur: float | None,
    fees: FeeConfig,
    location: str | None = None,
) -> Economics:
    """Full picture for one listing.

    ``estimated_resale_eur`` is None during cold start, when there is no
    usable estimate. Profit and ROI are then None rather than zero - an
    unknown margin is not a zero margin.
    """
    cost = buy_total_cost(listing_price_eur, fees, location)
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
