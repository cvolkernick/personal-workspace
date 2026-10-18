"""Retry once when ops/NODES.md differs from the sibling branch (#1239).

Sleep and git fetch are injected. These tests do not wait 60 seconds.
"""

from __future__ import annotations

import check_nodes_md as m


CLEAN = "# Nodes\n\nPublic overview only.\n"


def test_default_wait_is_60_seconds():
    assert m.RETRY_WAIT_SECONDS == 60


def test_first_match_does_not_retry():
    sleeps: list[float] = []
    fetches: list[str] = []

    def fetch(branch: str) -> str:
        fetches.append(branch)
        return CLEAN

    status = m.compare_sibling_once_with_retry(
        CLEAN, "work/treasury", fetch=fetch, sleep=sleeps.append
    )
    assert status == "identical"
    assert fetches == ["work/treasury"]
    assert sleeps == []


def test_mismatch_then_match_passes_without_sleeping():
    copies = iter(["stale copy\n", CLEAN])
    sleeps: list[float] = []

    status = m.compare_sibling_once_with_retry(
        CLEAN,
        "work/treasury",
        fetch=lambda _branch: next(copies),
        sleep=sleeps.append,
        wait_s=60,
    )
    assert status == "identical-after-retry"
    assert sleeps == [60]


def test_mismatch_then_mismatch_is_a_persistent_differ():
    sleeps: list[float] = []
    status = m.compare_sibling_once_with_retry(
        CLEAN,
        "master",
        fetch=lambda _branch: "other copy\n",
        sleep=sleeps.append,
    )
    assert status == "differs-after-retry"
    assert sleeps == [m.RETRY_WAIT_SECONDS]


def test_missing_sibling_does_not_retry():
    sleeps: list[float] = []
    status = m.compare_sibling_once_with_retry(
        CLEAN, "work/treasury", fetch=lambda _branch: None, sleep=sleeps.append
    )
    assert status == "missing"
    assert sleeps == []


def test_cli_mismatch_then_match_exits_0(tmp_path, monkeypatch, capsys):
    path = tmp_path / "NODES.md"
    path.write_text(CLEAN, encoding="utf-8")
    copies = iter(["stale\n", CLEAN])
    monkeypatch.setattr(m, "sibling_text", lambda _branch: next(copies))
    monkeypatch.setattr(m.time, "sleep", lambda _s: None)
    rc = m.main([str(path), "--sibling-branch", "work/treasury"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "identical after one retry" in out
    assert "differs from" not in out


def test_cli_persistent_mismatch_fails_and_says_retried(tmp_path, monkeypatch, capsys):
    path = tmp_path / "NODES.md"
    path.write_text(CLEAN, encoding="utf-8")
    monkeypatch.setattr(m, "sibling_text", lambda _branch: "other\n")
    slept: list[float] = []
    monkeypatch.setattr(m.time, "sleep", slept.append)
    rc = m.main([str(path), "--sibling-branch", "work/treasury"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "Retried once" in out
    assert "after one retry" in out
    assert slept == [60]
    assert "ok (public-safe" not in out


def test_cli_persistent_mismatch_on_a_pr_warns_and_exits_0(tmp_path, monkeypatch, capsys):
    path = tmp_path / "NODES.md"
    path.write_text(CLEAN, encoding="utf-8")
    monkeypatch.setattr(m, "sibling_text", lambda _branch: "other\n")
    monkeypatch.setattr(m.time, "sleep", lambda _s: None)
    rc = m.main([str(path), "--sibling-branch", "master", "--warn-on-differ"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "::warning::" in out
    assert "Retried once" in out
    assert "ok (public-safe" not in out


def test_compare_flag_retries_once_when_the_sibling_branch_is_known(tmp_path, monkeypatch, capsys):
    """The workflow calls --compare on the file it just fetched. A differ waits and re-fetches."""
    path = tmp_path / "NODES.md"
    stale = tmp_path / "other.md"
    path.write_text(CLEAN, encoding="utf-8")
    stale.write_text("stale copy\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_REF_NAME", "master")
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    seen: list[str] = []

    def fetch(branch: str) -> str:
        seen.append(branch)
        return CLEAN

    monkeypatch.setattr(m, "sibling_text", fetch)
    slept: list[float] = []
    monkeypatch.setattr(m.time, "sleep", slept.append)
    rc = m.main([str(path), "--compare", str(stale)])
    out = capsys.readouterr().out
    assert rc == 0
    assert seen == ["work/treasury"]
    assert slept == [60]
    assert "identical after one retry" in out
    assert "Retried once" not in out


def test_compare_flag_fails_when_the_refetch_still_differs(tmp_path, monkeypatch, capsys):
    path = tmp_path / "NODES.md"
    stale = tmp_path / "other.md"
    path.write_text(CLEAN, encoding="utf-8")
    stale.write_text("stale copy\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_BASE_REF", "work/treasury")
    monkeypatch.setattr(m, "sibling_text", lambda _branch: "still different\n")
    slept: list[float] = []
    monkeypatch.setattr(m.time, "sleep", slept.append)
    rc = m.main([str(path), "--compare", str(stale)])
    out = capsys.readouterr().out
    assert rc == 1
    assert slept == [60]
    assert "differs from master after one retry" in out
    assert "Retried once" in out


def test_compare_flag_without_github_env_does_not_retry(tmp_path, monkeypatch, capsys):
    path = tmp_path / "NODES.md"
    stale = tmp_path / "other.md"
    path.write_text(CLEAN, encoding="utf-8")
    stale.write_text("stale copy\n", encoding="utf-8")
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    monkeypatch.delenv("GITHUB_REF_NAME", raising=False)
    monkeypatch.setattr(m, "sibling_text", lambda _branch: CLEAN)
    slept: list[float] = []
    monkeypatch.setattr(m.time, "sleep", slept.append)
    rc = m.main([str(path), "--compare", str(stale)])
    out = capsys.readouterr().out
    assert rc == 1
    assert slept == []
    assert "Retried" not in out
    assert f"differs from {stale}" in out


def test_fetch_failure_is_not_reported_as_a_retried_mismatch(tmp_path, monkeypatch, capsys):
    path = tmp_path / "NODES.md"
    path.write_text(CLEAN, encoding="utf-8")

    def boom(_branch: str) -> str:
        raise m.FetchFailed("git fetch origin work/treasury failed: network")

    monkeypatch.setattr(m, "sibling_text", boom)
    slept: list[float] = []
    monkeypatch.setattr(m.time, "sleep", slept.append)
    rc = m.main([str(path), "--sibling-branch", "work/treasury"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "git fetch" in err
    assert "Retried" not in err
    assert slept == []
