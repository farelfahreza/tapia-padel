"""Fee handling on BOTH sides, and the ROI that comes out of it.

How Vinted works today, and what these tests pin down:
  * the BUYER pays the 5% protection fee and the shipping - on both legs, so
    I pay them when I buy and my own buyer pays them when I resell;
  * Vinted takes no seller fee, so nothing comes off the top of the resale;
  * shipping depends on where the racket ships from.

The seller-side parameters still exist and are still tested, because a
marketplace introducing a seller fee is the change that would silently wreck
every margin the tool reports.
"""

import pytest

from app.config import FeeConfig
from app.pricing.fees import (
    buy_shipping_for,
    buy_total_cost,
    compute_economics,
    net_sale_proceeds,
)

VINTED = FeeConfig(
    buyer_protection_pct=0.05,
    buyer_protection_fixed_eur=0.0,
    buy_shipping_eur=5.00,
    buy_shipping_by_location=(("corse", 9.00), ("belgique", 8.00)),
    packaging_cost_eur=1.00,
)


def test_buy_side_is_price_plus_five_percent_plus_shipping():
    # 100 * 1.05 = 105, + 5.00 shipping
    assert buy_total_cost(100.0, VINTED) == 110.00


def test_sell_side_keeps_the_sale_price_minus_only_my_own_costs():
    # No seller fee and no shipping on this leg - my buyer pays both.
    assert net_sale_proceeds(150.0, VINTED) == 149.00


def test_shipping_depends_on_where_the_racket_ships_from():
    assert buy_shipping_for("Paris, France", VINTED) == 5.00
    assert buy_shipping_for("Ajaccio, Corse", VINTED) == 9.00
    assert buy_shipping_for("Bruxelles, Belgique", VINTED) == 8.00
    assert buy_shipping_for(None, VINTED) == 5.00


def test_location_matching_ignores_accents_and_case():
    fees = FeeConfig(buy_shipping_by_location=(("ile-de-france", 3.5),))
    assert buy_shipping_for("Saint-Denis, Île-de-France", fees) == 3.5


def test_a_costlier_origin_eats_into_the_margin():
    near = compute_economics(100.0, 150.0, VINTED, location="Paris")
    far = compute_economics(100.0, 150.0, VINTED, location="Corse")
    assert far.buy_total_cost_eur - near.buy_total_cost_eur == pytest.approx(4.0)
    assert far.expected_profit_eur < near.expected_profit_eur
    assert far.roi < near.roi


def test_profit_is_net_of_both_sides_not_the_raw_gap():
    economics = compute_economics(100.0, 150.0, VINTED)
    assert economics.expected_profit_eur == pytest.approx(149.00 - 110.00, abs=0.01)
    assert economics.expected_profit_eur < 150.0 - 100.0


def test_roi_is_over_total_outlay_not_over_the_sticker_price():
    economics = compute_economics(100.0, 150.0, VINTED)
    assert economics.roi == pytest.approx(39.0 / 110.0, abs=0.0001)
    # The flattering version would be 50/100 = 50%.
    assert economics.roi < 0.50


def test_fees_are_never_applied_twice():
    economics = compute_economics(100.0, 150.0, VINTED)
    assert economics.buy_total_cost_eur == buy_total_cost(100.0, VINTED)
    assert economics.expected_net_proceeds_eur == net_sale_proceeds(150.0, VINTED)


def test_the_buyer_fee_is_not_charged_again_on_the_resale():
    # The 5% appears exactly once: on what I pay, not on what I receive.
    assert net_sale_proceeds(150.0, VINTED) > 150.0 * 0.95


def test_zero_everything_reduces_to_the_raw_gap():
    free = FeeConfig(
        buyer_protection_pct=0.0,
        buyer_protection_fixed_eur=0.0,
        buy_shipping_eur=0.0,
        packaging_cost_eur=0.0,
    )
    economics = compute_economics(100.0, 150.0, free)
    assert economics.expected_profit_eur == 50.0
    assert economics.roi == pytest.approx(0.5)


def test_a_thin_gap_becomes_a_loss_once_fees_are_counted():
    economics = compute_economics(100.0, 110.0, VINTED)
    assert economics.expected_profit_eur < 0
    assert economics.roi < 0


def test_a_seller_fee_would_be_honoured_if_vinted_ever_introduced_one():
    future = FeeConfig(
        buyer_protection_pct=0.05,
        buy_shipping_eur=5.00,
        packaging_cost_eur=1.00,
        seller_fee_pct=0.10,
        seller_fee_fixed_eur=0.50,
    )
    assert net_sale_proceeds(150.0, future) == pytest.approx(150 * 0.9 - 0.5 - 1.0)
    assert compute_economics(100.0, 150.0, future).expected_profit_eur < compute_economics(
        100.0, 150.0, VINTED
    ).expected_profit_eur


def test_no_estimate_means_unknown_margin_not_zero_margin():
    economics = compute_economics(100.0, None, VINTED)
    assert economics.buy_total_cost_eur == 110.00
    assert economics.expected_profit_eur is None
    assert economics.roi is None
