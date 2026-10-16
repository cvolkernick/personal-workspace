"""FCC inline scripts must parse under node.

#1124 deleted through a statement in financial-command/index.html and left a
syntax error in the only homepage <script> block. The page never called
loadTreasury() or initFccMobileShell(). 1a253fea restored the block. This
test is the guard that class of parse error cannot ship again (#1146).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FCC = ROOT / "financial-command"

# type="" is the classic inline script. JSON-LD and templates are not JS.
_JS_TYPES = {
    "",
    "text/javascript",
    "application/javascript",
    "text/ecmascript",
    "application/ecmascript",
    "module",
}

# The over-deleted fragment. Valid only as a complete expression; alone it
# is the SyntaxError that took the homepage down.
_BROKEN_1124 = "metric(\n  ex.tabs.Discretionary)) ||\n{};\n"

_HOMEPAGE_SYMBOLS = (
    "async function loadTreasury(",
    "function initFccMobileShell(",
    "const persTab",
    "const fleetTab",
    "const collTab",
    "const discTab",
)


class _InlineScriptParser(HTMLParser):
    """Collect inline JavaScript bodies, skipping src= and non-JS types."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self._buf: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        ad = {k: (v or "") for k, v in attrs}
        if ad.get("src"):
            self._buf = None
            return
        typ = ad.get("type", "").strip().lower()
        if typ not in _JS_TYPES:
            self._buf = None
            return
        self._buf = []

    def handle_data(self, data: str) -> None:
        if self._buf is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "script" or self._buf is None:
            return
        self.scripts.append("".join(self._buf))
        self._buf = None


def inline_scripts(html: str) -> list[str]:
    parser = _InlineScriptParser()
    parser.feed(html)
    parser.close()
    return [src for src in parser.scripts if src.strip()]


def node_syntax_error(source: str) -> str | None:
    """Return node --check stderr, or None when the source parses."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", encoding="utf-8", delete=False
    ) as handle:
        handle.write(source)
        name = handle.name
    try:
        proc = subprocess.run(
            ["node", "--check", name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    finally:
        Path(name).unlink(missing_ok=True)
    if proc.returncode == 0:
        return None
    return (proc.stderr or proc.stdout or "node --check failed").strip()


class TestFccHomepageBootSymbols(unittest.TestCase):
    """Declarations #1124 removed are still in the homepage script."""

    def test_index_keeps_boot_and_tab_declarations(self) -> None:
        html = (FCC / "index.html").read_text(encoding="utf-8")
        blob = "\n".join(inline_scripts(html))
        self.assertTrue(blob.strip(), "index.html has no inline script")
        for needle in _HOMEPAGE_SYMBOLS:
            self.assertIn(needle, blob, needle)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestFccInlineScriptsParse(unittest.TestCase):
    def test_every_financial_command_html_inline_script_parses(self) -> None:
        failures: list[str] = []
        checked = 0
        pages = sorted(FCC.glob("*.html"))
        self.assertTrue(pages, "no financial-command HTML pages")
        for path in pages:
            html = path.read_text(encoding="utf-8")
            for index, source in enumerate(inline_scripts(html)):
                checked += 1
                err = node_syntax_error(source)
                if err:
                    tail = err.splitlines()[-4:]
                    failures.append(f"{path.name}#{index}: " + " | ".join(tail))
        self.assertGreater(checked, 0, "extracted no inline scripts")
        self.assertEqual(failures, [])

    def test_checker_rejects_the_1124_fragment(self) -> None:
        err = node_syntax_error(_BROKEN_1124)
        self.assertIsNotNone(err)
        assert err is not None
        self.assertIn("SyntaxError", err)


if __name__ == "__main__":
    unittest.main()
