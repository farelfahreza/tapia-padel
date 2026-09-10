"""Fee handling on BOTH sides, and the ROI that comes out of it.

Buying and reselling on the same platform makes double-counting easy, so
these tests pin the arithmetic down explicitly.
"""

import pytest

from app.config import FeeConfig
from app.pricing.fees import buy_total_cost, compute_economics, net_sale_proceeds

FEES = FeeConfig(
    buyer_protection_pct=0.05,
    buyer_protection_fixed_eur=0.70,
    buy_shipping_eur=4.50,
    packaging_cost_eur=1.00,
    seller_fee_pct=0.10,
    seller_fee_fixed_eur=0.50,
    sell_shipping_cost_eur=2.00,
)


def test_buy_side_adds_protection_percentage_fixed_shipping_and_packaging():
    # 100 * 1.05 = 105, + 0.70 + 4.50 + 1.00
    assert buy_total_cost(100.0, FEES) == 111.20


def test_sell_side_subtracts_commission_fixed_fee_and_absorbed_shipping():
    # 150 * 0.90 = 135, - 0.50 - 2.00
    assert net_sale_proceeds(150.0, FEES) == 132.50


def test_profit_is_net_of_both_sides_not_the_raw_gap():
    economics = compute_economics(100.0, 150.0, FEES)
    raw_gap = 150.0 - 100.0
    assert economics.expected_profit_eur == pytest.approx(132.50 - 111.20, abs=0.01)
    assert economics.expected_profit_eur < raw_gap


def test_roi_is_over_total_outlay_not_over_the_sticker_price():
    economics = compute_economics(100.0, 150.0, FEES)
    assert economics.roi == pytest.approx(21.30 / 111.20, abs=0.0001)
    # The flattering version would be 50/100 = 50%.
    assert economics.roi < 0.50


def test_fees_are_never_applied_twice():
    economics = compute_economics(100.0, 150.0, FEES)
    assert economics.buy_total_cost_eur == buy_total_cost(100.0, FEES)
    assert economics.expected_net_proceeds_eur == net_sale_proceeds(150.0, FEES)


def test_zero_fee_configuration_reduces_to_the_raw_gap():
    free = FeeConfig(
        buyer_protection_pct=0.0,
        buyer_protection_fixed_eur=0.0,
        buy_shipping_eur=0.0,
        packaging_cost_eur=0.0,
        seller_fee_pct=0.0,
        seller_fee_fixed_eur=0.0,
        sell_shipping_cost_eur=0.0,
    )
    economics = compute_economics(100.0, 150.0, free)
    assert economics.expected_profit_eur == 50.0
    assert economics.roi == pytest.approx(0.5)


def test_a_thin_gap_becomes_a_loss_once_fees_are_counted():
    economics = compute_economics(100.0, 110.0, FEES)
    assert economics.expected_profit_eur < 0
    assert economics.roi < 0


def test_no_estimate_means_unknown_margin_not_zero_margin():
    economics = compute_economics(100.0, None, FEES)
    assert economics.buy_total_cost_eur == 111.20
    assert economics.expected_profit_eur is None
    assert economics.roi is None
