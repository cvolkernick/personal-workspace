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


DEPLOY_PY = (
    "bland-relay.py",
    "bland_relay.py",
    "relay_config.py",
    "relay_audit.py",
    "relay_sms.py",
    "relay_fc.py",
    "relay_send.py",
    "relay_rcs.py",
    "relay_turo.py",
    "relay_http.py",
    "caller_context.py",
    "turo_email.py",
    "meeting_check.py",
)
DEPLOY_SCRIPTS = ("bland-relay-setkey", "send-904", "send-904-fc")


def test_deploy_set_matches_service_header_and_readme():
    """#1229: every file ops copies is the service header plus the three scripts."""
    import re

    service = (ROOT / "bland-relay.service").read_text()
    header = service.split("[Unit]", 1)[0]
    header_py = re.findall(r"[A-Za-z0-9_.-]+\.py", header)
    assert tuple(header_py) == DEPLOY_PY
    for part in bland_relay._PARTS:
        assert part in header_py
    assert "smoke.py" not in header

    readme = (ROOT / "README.md").read_text()
    deploy = readme.split("## Deploy", 1)[1].split("## Tests", 1)[0]
    for name in DEPLOY_PY + DEPLOY_SCRIPTS:
        assert name in deploy
    assert "smoke.py" in deploy
    assert "not copied" in deploy
    for line in deploy.splitlines():
        stripped = line.strip()
        if stripped.startswith("cp ") or stripped.startswith("deploy/bland-relay/") or stripped.startswith("~/bin/"):
            assert "smoke.py" not in line
        if stripped.startswith("for f in "):
            assert "smoke.py" not in line


def test_part_file_refuses_a_direct_import():
    with pytest.raises(RuntimeError, match=r"load relay_config\.py through bland_relay\.py"):
        importlib.import_module("relay_config")
