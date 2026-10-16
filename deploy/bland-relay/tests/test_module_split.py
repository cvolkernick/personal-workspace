"""#1107: one bland_relay namespace, no production source file over 40 KB."""
import importlib
import inspect
from pathlib import Path

import pytest

import bland_relay

ROOT = Path(bland_relay.__file__).resolve().parent
LIMIT = 40 * 1024


def test_production_sources_stay_under_40kb():
    sizes = {path.name: path.stat().st_size for path in sorted(ROOT.glob("*.py"))}
    over = {name: size for name, size in sizes.items() if size > LIMIT}
    assert over == {}
    assert sizes["bland_relay.py"] < 8 * 1024


def test_parts_share_the_bland_relay_namespace():
    assert bland_relay.H.do_POST.__globals__ is vars(bland_relay)
    assert bland_relay.main.__module__ == "bland_relay"
    assert inspect.getsource(bland_relay.main).startswith("def main")
    assert "api.bland.ai" not in inspect.getsource(bland_relay.main)
    for name in (
        "relay_config.py",
        "relay_audit.py",
        "relay_sms.py",
        "relay_fc.py",
        "relay_send.py",
        "relay_rcs.py",
        "relay_turo.py",
        "relay_http.py",
    ):
        assert (ROOT / name).is_file()
        assert name in bland_relay._PARTS


def test_part_file_refuses_a_direct_import():
    with pytest.raises(RuntimeError, match=r"load relay_config\.py through bland_relay\.py"):
        importlib.import_module("relay_config")
