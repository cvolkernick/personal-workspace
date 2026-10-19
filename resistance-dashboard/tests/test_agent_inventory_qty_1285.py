"""Contract: /api/agent/inventory qty is a number or null, never a string (#1285)."""
from rt_dashboard.agent_read import build_inventory


def _items(*ings):
    data, _ = build_inventory({"payload": {"ingredients": list(ings)}}, [])
    return data["items"]


def test_qty_missing_is_null():
    (it,) = _items({"name": "Oats", "in_stock": True})
    assert it["qty"] is None
    assert it["in_stock"] is True


def test_qty_numeric_passes_through():
    a, b = _items({"name": "Eggs", "qty": 12}, {"name": "Milk", "quantity": 1.5})
    assert a["qty"] == 12 and b["qty"] == 1.5


def test_stock_string_never_in_qty():
    (it,) = _items({"name": "Rice", "stock": "in", "in_stock": True})
    assert it["qty"] is None
    assert it["in_stock"] is True
    for i in _items({"name": "X", "qty": "in"}, {"name": "Y", "stock": "low", "qty": 3}):
        assert i["qty"] is None or isinstance(i["qty"], (int, float))
        assert not isinstance(i["qty"], str)
