"""More-tab MCP section lists the tools the FitDash MCP server actually registers."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
MCP_SRC = (REPO / "tools" / "fitdash-mcp" / "fitdash_mcp.py").read_text(encoding="utf-8")


def _catalog() -> list[dict]:
    module = ast.parse(MCP_SRC)
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "TOOL_CATALOG" for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        return list(value)
    raise AssertionError("TOOL_CATALOG missing")


def _registered_tools() -> list[str]:
    names = []
    lines = MCP_SRC.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "@mcp.tool()":
            continue
        for nxt in lines[index + 1 : index + 6]:
            stripped = nxt.strip()
            if stripped.startswith("def "):
                names.append(stripped.split("(", 1)[0][4:])
                break
    return names


def _mcp_section() -> str:
    start = HTML.index('id="mcp-section"')
    end = HTML.index('id="connections-card"', start)
    return HTML[start:end]


class McpMoreTab(unittest.TestCase):
    def test_section_lists_exactly_the_registered_tools(self):
        catalog = _catalog()
        names = [row["name"] for row in catalog]
        self.assertEqual(names, _registered_tools())
        self.assertIn("get_phase", names)
        self.assertIn("get_coach_card", names)
        self.assertIn("get_coaching_targets", names)
        self.assertIn("set_coaching_target", names)
        section = _mcp_section()
        listed = []
        for part in section.split("<code>")[1:]:
            listed.append(part.split("</code>", 1)[0])
        self.assertEqual(listed, names)
        for row in catalog:
            self.assertIn(row["returns"], section, row["name"])
            self.assertNotIn("token", row["returns"].lower())
            self.assertNotIn("secret", row["returns"].lower())

    def test_section_starts_collapsed_and_cannot_call_the_write_tool(self):
        section = _mcp_section()
        head = section.index('data-collapse="more-mcp"')
        body = section.index('id="more-mcp-body"')
        self.assertIn("is-collapsed", section[:head])
        self.assertIn('aria-expanded="false"', section[head : head + 80])
        open_tag = section[body - 80 : section.index(">", body) + 1]
        self.assertIn(" hidden", open_tag)
        inner = section[body:]
        self.assertNotIn("<button", inner)
        self.assertNotIn("<form", inner)
        self.assertNotIn("<input", inner)
        self.assertIn("set_coaching_target", inner)
        self.assertIn("not available from this page", inner)
        self.assertIn("This page does not call it.", inner)
        self.assertIn('"more-mcp"', JS)
        self.assertIn("documentation only", section)


if __name__ == "__main__":
    unittest.main()
