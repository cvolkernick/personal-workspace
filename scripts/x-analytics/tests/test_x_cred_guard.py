"""Gitleaks config, pre-commit hook, and value-free X credential audit (#1130)."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import tomllib

import audit_x_creds as audit

ROOT = Path(__file__).resolve().parents[3]
HOOK = ROOT / ".githooks" / "pre-commit"
CONFIG = ROOT / ".gitleaks.toml"
WORKFLOWS = (
    ROOT / ".github" / "workflows" / "test.yml",
    ROOT / "ops" / "github-workflows" / "test.yml",
)


def assert_values_absent(blob: str, values) -> None:
    for value in values:
        if value and value in blob:
            raise AssertionError("output contained a credential fixture value")


def _git(repo: Path, *args: str, env: dict | None = None) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "config", "user.email", "audit@example.com")
    _git(path, "config", "user.name", "audit")
    return path


def commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def run_audit(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "x-analytics" / "audit_x_creds.py"), *argv],
        check=False,
        capture_output=True,
        text=True,
    )


def test_gitleaks_config_has_x_rule_and_fake_allowlist():
    data = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    assert data["extend"]["useDefault"] is False
    rule = data["rules"][0]
    assert rule["id"] == "x-api-key-name"
    assert rule["secretGroup"] == 2
    for name in audit.X_ENV_VARS:
        assert name in rule["regex"]
        assert name in rule["keywords"]
    allow = data["allowlists"][0]
    assert allow["targetRules"] == ["x-api-key-name"]
    assert allow["regexTarget"] == "secret"
    assert allow["regexes"] == [r"^FAKE-[A-Za-z0-9_-]+$"]
    cfg = audit.load_guard_config(CONFIG)
    fixture = "FAKE-ck-9f3a1b7c2d"
    assert cfg.allow_regex.fullmatch(fixture)
    assert not cfg.allow_regex.fullmatch("q" * 24)


def test_hook_script_refuses_without_gitleaks_and_passes_redact():
    text = HOOK.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh")
    assert "gitleaks protect --staged --redact=100 --no-banner --config" in text
    assert "refusing to commit" in text
    assert ".gitleaks.toml" in text
    assert stat.S_IMODE(HOOK.stat().st_mode) & 0o111


def test_hook_fails_closed_when_gitleaks_missing(tmp_path):
    repo = init_repo(tmp_path / "repo")
    (repo / "README").write_text("ok\n")
    commit_all(repo, "init")
    env = os.environ.copy()
    env["PATH"] = "/usr/bin:/bin"
    proc = subprocess.run([str(HOOK)], cwd=repo, capture_output=True, text=True, env=env)
    assert proc.returncode == 1
    assert "not installed" in proc.stderr
    assert "FAKE-" not in proc.stderr + proc.stdout


def test_hook_fails_closed_when_config_missing(tmp_path):
    repo = init_repo(tmp_path / "repo")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gitleaks"
    stub.write_text("#!/bin/sh\necho should-not-run > \"$STUB_OUT\"\nexit 0\n")
    stub.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    env["STUB_OUT"] = str(tmp_path / "stub")
    proc = subprocess.run([str(HOOK)], cwd=repo, capture_output=True, text=True, env=env)
    assert proc.returncode == 1
    assert "missing .gitleaks.toml" in proc.stderr
    assert not (tmp_path / "stub").exists()


def test_hook_invokes_gitleaks_and_propagates_failure(tmp_path):
    repo = init_repo(tmp_path / "repo")
    (repo / ".gitleaks.toml").write_text(CONFIG.read_text(encoding="utf-8"))
    (repo / "README").write_text("ok\n")
    commit_all(repo, "init")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gitleaks"
    stub.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            printf '%s\\n' "$@" > "$STUB_OUT"
            exit "${STUB_EXIT:-0}"
            """
        )
    )
    stub.chmod(0o755)
    out = tmp_path / "args"
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    env["STUB_OUT"] = str(out)
    env["STUB_EXIT"] = "0"
    ok = subprocess.run([str(HOOK)], cwd=repo, capture_output=True, text=True, env=env)
    assert ok.returncode == 0
    args = out.read_text(encoding="utf-8")
    assert "protect" in args and "--staged" in args and "--redact=100" in args
    assert str(repo / ".gitleaks.toml") in args
    env["STUB_EXIT"] = "1"
    bad = subprocess.run([str(HOOK)], cwd=repo, capture_output=True, text=True, env=env)
    assert bad.returncode == 1


def test_ci_workflows_run_gitleaks_and_match():
    bodies = [path.read_text(encoding="utf-8") for path in WORKFLOWS]
    assert bodies[0] == bodies[1]
    for body in bodies:
        assert "gitleaks detect --no-git --redact=100 --no-banner --config .gitleaks.toml" in body
        assert "REQUIRE_GITLEAKS" in body
        assert "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb" in body


def test_tracked_repo_code_has_no_hardcoded_x_credentials():
    report = audit.scan([ROOT], audit.load_guard_config(), set(), git=False, tracked=True)
    if report.findings:
        raise AssertionError(report.render("text"))
    rendered = report.render("text")
    assert "findings=0" in rendered
    assert "FAKE-" not in rendered


def test_audit_reports_fingerprint_and_hides_value(tmp_path):
    probe = "m" * 32
    digest = hashlib.sha256(probe.encode()).hexdigest()
    root = tmp_path / "tree"
    root.mkdir()
    (root / "note.txt").write_text("prefix " + probe + " suffix\n")
    fps = tmp_path / "fps.txt"
    fps.write_text("# comment\nnot-a-fingerprint\n" + digest + "\n")
    proc = run_audit(["--root", str(root), "--fingerprints", str(fps), "--format", "json"])
    blob = proc.stdout + proc.stderr
    assert proc.returncode == 1
    assert_values_absent(blob, [probe, "not-a-fingerprint"])
    payload = json.loads(proc.stdout)
    assert payload["counts"]["fingerprint"] == 1
    assert payload["counts"]["ignored_fingerprint_lines"] == 1
    assert payload["findings"][0]["fingerprint"] == f"sha256:{digest}"
    assert payload["findings"][0]["match"] == "fingerprint"
    assert "value" not in payload["findings"][0]


def test_audit_ignores_fake_fixtures_and_flags_other_assignments(tmp_path):
    fixture = "FAKE-audit-probe-0001"
    probe = "q" * 24
    root = init_repo(tmp_path / "repo")
    (root / "ok.txt").write_text("X_CONSUMER_KEY=" + fixture + "\n")
    (root / "bad.txt").write_text("X_ACCESS_TOKEN_SECRET=" + probe + "\n")
    commit_all(root, "add fixtures")
    (root / "bad.txt").write_text("clean\n")
    removed = commit_all(root, "remove probe")
    proc = run_audit(["--root", str(root), "--git", "--tracked", "--format", "text"])
    blob = proc.stdout + proc.stderr
    assert proc.returncode == 1
    assert_values_absent(blob, [fixture, probe])
    assert f"sha256:{hashlib.sha256(probe.encode()).hexdigest()}" in proc.stdout
    assert "match=key-name" in proc.stdout
    assert "key=X_ACCESS_TOKEN_SECRET" in proc.stdout
    assert "ignored_fixtures=" in proc.stdout
    assert removed not in proc.stdout or "bad.txt" in proc.stdout
    assert fixture not in proc.stdout


def test_audit_flags_credential_filename_without_printing_contents(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    secret_file = root / ".x_credentials.json"
    secret_file.write_text('{"note":"plain"}\n')
    proc = run_audit(["--root", str(root), "--format", "text"])
    assert proc.returncode == 1
    assert "match=filename" in proc.stdout
    assert "plain" not in proc.stdout + proc.stderr
    assert hashlib.sha256(secret_file.read_bytes()).hexdigest() in proc.stdout


def test_loose_fingerprint_file_under_secrets_fails_closed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    secrets = home / ".secrets"
    secrets.mkdir(parents=True)
    os.chmod(secrets, 0o755)
    fps = secrets / "x-api.sha256"
    fps.write_text("ab" * 32 + "\n")
    os.chmod(fps, 0o600)
    monkeypatch.setenv("HOME", str(home))
    proc = run_audit(["--root", str(tmp_path), "--fingerprints", str(fps)])
    assert proc.returncode == 2
    assert "need-0700" in proc.stderr
    assert "ab" * 32 not in proc.stdout + proc.stderr


def test_emit_fingerprints_hides_values(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    secrets = home / ".secrets"
    secrets.mkdir(parents=True)
    os.chmod(secrets, 0o700)
    mapping = {
        "X_CONSUMER_KEY": "FAKE-ck-emit-9f3a1b7c2d",
        "X_CONSUMER_SECRET": "FAKE-cs-emit-5e8d4c6a0b",
        "X_ACCESS_TOKEN": "FAKE-at-emit-1234567890",
        "X_ACCESS_TOKEN_SECRET": "FAKE-ats-emit-zYxWvUtSrQ",
    }
    env = secrets / "x-api.env"
    env.write_text("".join(f"{key}={value}\n" for key, value in mapping.items()))
    os.chmod(env, 0o600)
    monkeypatch.setenv("HOME", str(home))
    for key in mapping:
        monkeypatch.delenv(key, raising=False)
    assert audit.main(["--emit-fingerprints", "--env-file", str(env)]) == 0
    cap = capsys.readouterr()
    blob = cap.out + cap.err
    assert_values_absent(blob, mapping.values())
    for key, value in mapping.items():
        assert f"sha256:{hashlib.sha256(value.encode()).hexdigest()} key={key}" in cap.out


def _gitleaks_required() -> str:
    from shutil import which

    path = which("gitleaks")
    if path:
        return path
    if os.environ.get("REQUIRE_GITLEAKS") == "1":
        pytest.fail("gitleaks is required in CI")
    pytest.skip("gitleaks not installed")


def _gitleaks(source: Path, config: Path) -> subprocess.CompletedProcess[str]:
    _gitleaks_required()
    return subprocess.run(
        [
            "gitleaks",
            "detect",
            "--no-git",
            "--redact=100",
            "--no-banner",
            "--config",
            str(config),
            "--source",
            str(source),
            "--report-format",
            "json",
            "--report-path",
            "-",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _finding_summary(proc: subprocess.CompletedProcess[str]) -> list[tuple[str, str]]:
    raw = proc.stdout.strip()
    if not raw:
        return []
    data = json.loads(raw)
    return [(item.get("File", ""), item.get("RuleID", "")) for item in data]


def test_gitleaks_allows_fake_and_flags_other_values(tmp_path):
    _gitleaks_required()
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "creds.txt").write_text('X_CONSUMER_KEY="FAKE-ck-9f3a1b7c2d"\n')
    allowed = _gitleaks(clean, CONFIG)
    assert allowed.returncode == 0, _finding_summary(allowed)
    assert "FAKE-ck-9f3a1b7c2d" not in allowed.stdout + allowed.stderr

    dirty = tmp_path / "dirty"
    dirty.mkdir()
    probe = "q" * 24
    (dirty / "creds.txt").write_text("x_consumer_key=" + probe + "\n")
    caught = _gitleaks(dirty, CONFIG)
    assert caught.returncode == 1
    assert_values_absent(caught.stdout + caught.stderr, [probe])
    summary = _finding_summary(caught)
    assert len(summary) == 1
    assert summary[0][1] == "x-api-key-name"
    assert summary[0][0].endswith("creds.txt")

    stripped = tmp_path / "no-allow.toml"
    stripped.write_text(CONFIG.read_text(encoding="utf-8").split("[[allowlists]]", 1)[0])
    fixture_only = tmp_path / "fixture"
    fixture_only.mkdir()
    fixture = "FAKE-ck-9f3a1b7c2d"
    (fixture_only / "creds.txt").write_text("X_CONSUMER_KEY=" + fixture + "\n")
    without_allow = _gitleaks(fixture_only, stripped)
    assert without_allow.returncode == 1
    assert_values_absent(without_allow.stdout + without_allow.stderr, [fixture])


def test_gitleaks_clean_on_this_repo():
    _gitleaks_required()
    proc = _gitleaks(ROOT, CONFIG)
    summary = _finding_summary(proc)
    if proc.returncode != 0 or summary:
        raise AssertionError("gitleaks findings: " + ", ".join(f"{path} {rule}" for path, rule in summary))
