"""#436: Morpho LTV card barometer — live liq, skip-when-null, no LTV invent."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FCC = ROOT / "financial-command"
INDEX = FCC / "index.html"
SPECTRUM = FCC / "interest-spectrum.html"


def _extract_js_fn(html: str, name: str) -> str:
    start = html.find(f"function {name}(")
    if start < 0:
        raise AssertionError(f"missing function {name}")
    rest = html[start:]
    end = re.search(r"\n      function ", rest[1:])
    if end:
        return rest[: end.start() + 1]
    return rest[:2000]


class TestMorphoLtvBarometerSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = INDEX.read_text(encoding="utf-8")
        cls.spectrum = SPECTRUM.read_text(encoding="utf-8")

    def test_barometer_on_glance_morpho_ltv_card(self) -> None:
        html = self.html
        self.assertIn("function ltvLiqBarometerHtml(", html)
        self.assertIn("function morphoLiqUsd(", html)
        self.assertIn("ltv-liq-baro", html)
        self.assertIn('k: "Morpho LTV"', html)
        self.assertIn("extra: ltvLiqBarometerHtml(f(inp.btc_usd_price), morphoLiqUsd())", html)
        glance = html.find('k: "Morpho LTV"')
        cash = html.find("const ltvChip = kpiHtml({")
        extra_at = html.find("extra: ltvLiqBarometerHtml")
        self.assertGreater(glance, 0)
        self.assertGreater(extra_at, glance)
        self.assertLess(extra_at, cash)

    def test_barometer_not_on_settings_or_spectrum(self) -> None:
        self.assertNotIn("m-liq-price", self.html)
        self.assertNotIn("ltvLiqBarometerHtml", self.spectrum)
        self.assertNotIn("ltv-liq-baro", self.spectrum)
        self.assertNotIn("m-liq-price", self.spectrum)

    def test_skip_when_null_guards_in_source(self) -> None:
        body = _extract_js_fn(self.html, "ltvLiqBarometerHtml")
        self.assertIn("if (!Number.isFinite(l) || !(l > 0)) return \"\"", body)
        self.assertIn("if (!Number.isFinite(s) || !(s > 0)) return \"\"", body)
        self.assertNotIn("ltvVal *", body)
        self.assertNotIn("* 0.86", body)
        morpho = _extract_js_fn(self.html, "morphoLiqUsd")
        self.assertIn("liquidation_price_btc_usd", morpho)
        self.assertNotIn("0.86", morpho)
        self.assertNotIn("lltv", morpho.lower())


class TestMorphoLtvBarometerRuntime(unittest.TestCase):
    """Execute the extracted barometer fn: skip-when-null + no fake $0 needle."""

    def test_skip_when_liq_null_or_zero_and_paint_when_live(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        html = INDEX.read_text(encoding="utf-8")
        fn = _extract_js_fn(html, "ltvLiqBarometerHtml")
        harness = (
            "function money(n) { return '$' + Number(n).toFixed(2); }\n"
            "function escapeHtml(s) { return String(s); }\n"
            + fn
            + "\n"
            "const cases = [\n"
            "  [78000, null],\n"
            "  [78000, undefined],\n"
            "  [78000, ''],\n"
            "  [78000, 0],\n"
            "  [78000, -1],\n"
            "  [null, 40881],\n"
            "  [0, 40881],\n"
            "  [78000, 40881],\n"
            "];\n"
            "const out = cases.map(([s, l]) => ltvLiqBarometerHtml(s, l));\n"
            "console.log(JSON.stringify(out));\n"
        )
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "baro.js"
            p.write_text(harness, encoding="utf-8")
            proc = subprocess.run(
                [node, str(p)], capture_output=True, text=True, check=False
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        import json

        out = json.loads(proc.stdout.strip())
        self.assertEqual(len(out), 8)
        for empty in out[:7]:
            self.assertEqual(empty, "")
        painted = out[7]
        self.assertIn("ltv-liq-baro", painted)
        self.assertIn("liq $40881.00", painted)
        self.assertIn("spot $78000.00", painted)
        self.assertIn("below spot", painted)
        self.assertNotIn("$0.00", painted)


if __name__ == "__main__":
    unittest.main()
