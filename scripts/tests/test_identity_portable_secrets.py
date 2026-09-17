"""Portable identity files must not contain token literals (#792 AC5)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IDENTITY = ROOT / "docs" / "identity"
FORBIDDEN = re.compile(r"GITHUB_TOKEN|sk-[A-Za-z0-9]|Bearer\s+\S+")


def test_portable_identity_files_exist():
    assert IDENTITY.is_dir()
    assert (IDENTITY / "ROSTER.md").is_file()
    for name in ("PROFILE.md", "INSTRUCTIONS.md", "MEMORY.md"):
        assert (IDENTITY / "nakatoshi" / name).is_file()
        assert (IDENTITY / "_template" / name).is_file()


def test_portable_identity_files_have_no_secret_literals():
    files = list(IDENTITY.rglob("*.md"))
    assert files
    hits: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if FORBIDDEN.search(text):
            hits.append(str(path.relative_to(ROOT)))
    assert hits == [], f"token literals in portable files: {hits}"
