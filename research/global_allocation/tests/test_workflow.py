"""Scheduler file: cron, permissions, concurrency, and no secret echo."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / "ops" / "github-workflows" / "global-allocation.yml"
RUNBOOK = REPO / "research" / "global_allocation" / "RUNBOOK.md"
UNITS = REPO / "deploy" / "units"
SECRET_NAMES = ("ALPHAVANTAGE_API_KEY", "TWELVEDATA_API_KEY", "GITHUB_TOKEN")


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_crons_permissions_and_concurrency():
    text = _workflow_text()
    assert 'cron: "15 22 * * 5"' in text
    assert 'cron: "0 12 1 * *"' in text
    assert "pull_request_target" not in text
    assert "twelvedata" not in text.lower()
    assert "contents: write" in text
    assert "pull-requests:" not in text
    assert "group: global-allocation" in text
    assert "cancel-in-progress: false" in text
    assert "github.repository == 'cvolkernick/personal-workspace'" in text
    assert "set -x" not in text
    parsed = _parse_yaml(text)
    if parsed is not None:
        crons = [item["cron"] for item in parsed["on"]["schedule"]]
        assert crons == ["15 22 * * 5", "0 12 1 * *"]
        assert parsed["permissions"] == {"contents": "write"}
        assert parsed["concurrency"]["group"] == "global-allocation"
        assert parsed["concurrency"]["cancel-in-progress"] is False
        assert parsed["jobs"]["refresh"]["if"] == "github.repository == 'cvolkernick/personal-workspace'"


def test_monthly_step_draws_charts_and_writes_the_report():
    text = _workflow_text()
    blocks = _monthly_blocks(text)
    assert len(blocks) == 2
    joined = "\n".join(blocks)
    assert "python3 -m research.global_allocation.charts" in joined
    assert 'python3 -m research.global_allocation.report --month "${date_et:0:7}"' in joined
    assert "git add -- research/global_allocation/charts research/global_allocation/reports" in joined
    assert "matplotlib==3.9.4" in joined
    outside = text
    for block in blocks:
        outside = outside.replace(block, "", 1)
    assert "research.global_allocation.charts" not in outside
    assert "research.global_allocation.report" not in outside
    assert "kind=weekly" in text


def _monthly_blocks(text: str) -> list[str]:
    lines = text.splitlines()
    blocks = []
    index = 0
    while index < len(lines):
        if 'if [ "$kind" = monthly ]; then' not in lines[index]:
            index += 1
            continue
        start = index
        index += 1
        while index < len(lines) and lines[index].strip() != "fi":
            index += 1
        blocks.append("\n".join(lines[start : index + 1]))
        index += 1
    return blocks


def test_workflow_never_echoes_a_secret():
    for line in _workflow_text().splitlines():
        if "echo" not in line:
            continue
        for name in SECRET_NAMES:
            assert name not in line, line
        assert "secrets." not in line, line


def test_actionlint_when_installed():
    binary = shutil.which("actionlint")
    if binary is None:
        return
    result = subprocess.run([binary, str(WORKFLOW)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_runbook_names_the_live_runner_fallback_and_hand_rerun():
    text = RUNBOOK.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "live runner" in lowered
    assert "box fallback" in lowered
    assert "run by hand" in lowered
    assert "`workflow` scope" in text
    assert "disabled" in lowered
    assert "ALPHAVANTAGE_API_KEY" not in text or "environment" in lowered


def test_units_ship_disabled_shape_without_hosts_or_secrets():
    weekly = (UNITS / "global-allocation-weekly.timer").read_text(encoding="utf-8")
    monthly = (UNITS / "global-allocation-monthly.timer").read_text(encoding="utf-8")
    assert "Fri *-*-* 22:15:00 UTC" in weekly
    assert "*-*-01 12:00:00 UTC" in monthly
    assert "Persistent=true" in weekly
    assert "Persistent=true" in monthly
    for name in (
        "global-allocation-weekly.service",
        "global-allocation-weekly.timer",
        "global-allocation-monthly.service",
        "global-allocation-monthly.timer",
    ):
        text = (UNITS / name).read_text(encoding="utf-8")
        assert "://" not in text
        assert "@" not in text
        assert "ALPHAVANTAGE" not in text
        assert "SECRET" not in text
        assert "prism" not in text.lower()


def _parse_yaml(text: str):
    try:
        import yaml
    except ImportError:
        return None
    return yaml.safe_load(text)
