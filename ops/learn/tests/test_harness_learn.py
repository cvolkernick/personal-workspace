#!/usr/bin/env python3
"""Tests for ops/learn/harness_learn.py (#575)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MOD_PATH = ROOT / "ops" / "learn" / "harness_learn.py"


def _load():
    spec = importlib.util.spec_from_file_location("harness_learn", MOD_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _session(
    home: Path,
    sid: str,
    *,
    prompt: str,
    skill_read: str | None = None,
    friction: str | None = None,
    mcp_tool: str | None = None,
    updated: str = "2026-09-10T12:00:00Z",
    kind: str | None = None,
) -> None:
    d = home / "sessions" / "%2Ftmp%2Fws" / sid
    summary = {
        "info": {"id": sid, "cwd": "/tmp/ws"},
        "updated_at": updated,
        "generated_title": sid,
    }
    if kind:
        summary["session_kind"] = kind
    _write(d / "summary.json", json.dumps(summary))
    turns = []
    body = friction or prompt
    turns.append(
        {
            "type": "user",
            "content": [{"type": "text", "text": f"<user_query>{body}</user_query>"}],
        }
    )
    if skill_read:
        turns.append(
            {
                "type": "assistant",
                "tool_calls": [
                    {
                        "name": "read_file",
                        "arguments": json.dumps(
                            {"target_file": f"/x/skills/{skill_read}/SKILL.md"}
                        ),
                    }
                ],
            }
        )
    if mcp_tool:
        turns.append(
            {
                "type": "assistant",
                "tool_calls": [
                    {
                        "name": "use_tool",
                        "arguments": json.dumps({"tool_name": f"{mcp_tool}__list"}),
                    }
                ],
            }
        )
    if not skill_read and not mcp_tool:
        turns.append(
            {
                "type": "assistant",
                "tool_calls": [{"name": "read_file", "arguments": json.dumps({"target_file": "/tmp/ws/README.md"})}],
            }
        )
    _write(d / "chat_history.jsonl", "\n".join(json.dumps(t) for t in turns) + "\n")


class TestHarnessLearn(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory(prefix="learn-")
        self.home = Path(self.td.name) / "grok"
        self.ws = Path(self.td.name) / "ws"
        self.out = Path(self.td.name) / "out"
        self.ws.mkdir()
        (self.ws / "present.py").write_text("ok\n", encoding="utf-8")
        _write(
            self.home / "skills" / "used-skill" / "SKILL.md",
            "---\nname: used-skill\n---\nRead `present.py`.\n",
        )
        _write(
            self.home / "skills" / "stale-skill" / "SKILL.md",
            "---\nname: stale-skill\n---\nRun `missing/nope.py` then done.\n",
        )
        _write(
            self.home / "skills" / "idle-skill" / "SKILL.md",
            "---\nname: idle-skill\n---\nNever loaded.\n",
        )
        _write(
            self.home / "config.toml",
            '[mcp_servers.github]\nurl = "http://example.invalid"\n'
            '[mcp_servers.idle_mcp]\nurl = "http://example.invalid"\n',
        )
        _session(
            self.home,
            "s1",
            prompt="please dump the weekly treasury snapshot now",
            skill_read="used-skill",
            mcp_tool="github",
        )
        _session(
            self.home,
            "s2",
            prompt="please dump the weekly treasury snapshot now",
            skill_read="used-skill",
        )
        _session(
            self.home,
            "s3",
            prompt="review the open pull request",
            friction="No, don't merge that, I said wait",
        )

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_collection_is_read_only_on_grok_home(self) -> None:
        before = _tree_hash(self.home)
        M.collect(self.home, days=30)
        self.assertEqual(_tree_hash(self.home), before)

    def test_drops_subagents_and_old_sessions(self) -> None:
        _session(self.home, "sub", prompt="please dump the weekly treasury snapshot now", kind="subagent")
        _session(
            self.home,
            "old",
            prompt="please dump the weekly treasury snapshot now",
            updated="2020-01-01T00:00:00Z",
        )
        c = M.collect(self.home, days=14)
        ids = {s["id"] for s in c["sessions"]}
        self.assertIn("s1", ids)
        self.assertNotIn("sub", ids)
        self.assertNotIn("old", ids)
        self.assertGreaterEqual(c["dropped"].get("subagent", 0), 1)
        self.assertGreaterEqual(c["dropped"].get("older_than_window", 0), 1)

    def test_report_groups_create_fix_delete_with_evidence(self) -> None:
        c = M.collect(self.home, days=30)
        actions = M.build_actions(c, workspace=self.ws)
        create = next(a for a in actions if a["action"] == "create")
        self.assertIn("weekly treasury snapshot", create["evidence"]["quote"])
        self.assertGreaterEqual(create["evidence"]["count"], 2)
        self.assertTrue(create["requires_confirmation"])
        stale = next(a for a in actions if a["target"] == "stale-skill" and a["action"] in ("edit", "propose"))
        self.assertIn("missing/nope.py", stale["evidence"]["quote"])
        idle = next(a for a in actions if a["target"] == "idle-skill")
        self.assertEqual(idle["action"], "ask")
        self.assertEqual(idle["evidence"]["count"], 0)
        self.assertIn("never auto-deleted", idle["reason"].lower())
        idle_mcp = next(a for a in actions if a["target"] == "idle_mcp")
        self.assertEqual(idle_mcp["kind"], "mcp")
        self.assertEqual(idle_mcp["action"], "ask")
        self.assertNotIn("used-skill", {a["target"] for a in actions if a["action"] == "ask"})
        md = M.render_report(c, actions)
        self.assertIn("## 1. Repeated phrases -> skills", md)
        self.assertIn("## 2. Skills to update", md)
        self.assertIn("## 3. Unused -> delete or disable", md)
        self.assertIn("sessions/", md)  # coverage names traces
        self.assertIn("never touches", md.lower())

    def test_apply_rejected_and_write_stays_in_out(self) -> None:
        before = _tree_hash(self.home)
        rc = M.main(
            [
                "--grok-home",
                str(self.home),
                "--out",
                str(self.out),
                "--workspace",
                str(self.ws),
                "--days",
                "30",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(_tree_hash(self.home), before)
        reports = list(self.out.glob("*/report.md"))
        self.assertEqual(len(reports), 1)
        actions = json.loads((reports[0].parent / "actions.json").read_text(encoding="utf-8"))
        self.assertFalse(actions["apply"])
        self.assertTrue(all(a["requires_confirmation"] for a in actions["actions"]))
        rc2 = M.main(["--apply", "--grok-home", str(self.home), "--out", str(self.out)])
        self.assertEqual(rc2, 2)
        self.assertEqual(_tree_hash(self.home), before)

    def test_redacts_secrets_in_turns(self) -> None:
        _session(
            self.home,
            "sec",
            prompt="rotate key sk-abcdefghijklmnopqrstuvwxyz123456 please dump the weekly treasury snapshot now",
        )
        c = M.collect(self.home, days=30)
        blob = " ".join(t for s in c["sessions"] if s["id"] == "sec" for t in s["turns"])
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", blob)
        self.assertIn("[redacted]", blob)

    def test_strips_buzz_harness_wrappers_from_phrases(self) -> None:
        wrapped = (
            "<base>You are an agent operating inside Buzz — a Nostr-based messaging platform.</base>"
            "<context>Scope: channel</context>"
            "<buzz-event type=\"@mention\">\nContent: please dump the weekly treasury snapshot now\nTags: []\n</buzz-event>"
        )
        _session(self.home, "wrap1", prompt=wrapped)
        _session(self.home, "wrap2", prompt=wrapped)
        c = M.collect(self.home, days=30)
        blob = " ".join(c.get("phrases") or {})
        self.assertNotIn("nostr-based messaging platform", blob)
        self.assertTrue(any("weekly treasury snapshot" in p for p in (c.get("phrases") or {})))

    def test_mcp_names_only_no_url_in_inventory(self) -> None:
        c = M.collect(self.home, days=30)
        dumped = json.dumps(c["mcp_servers"])
        self.assertIn("github", dumped)
        self.assertNotIn("example.invalid", dumped)


if __name__ == "__main__":
    unittest.main()
