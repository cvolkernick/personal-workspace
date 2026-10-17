"""BTC issued-supply heights named in #1250."""

from decimal import Decimal

from research.global_allocation.model import issued_supply


def test_supply_at_named_heights():
    assert issued_supply(0) == 0
    assert issued_supply(210_000) == Decimal("10500000")
    assert issued_supply(630_000) == Decimal("18375000")
    assert issued_supply(971_907) == Decimal("20099709.375")
