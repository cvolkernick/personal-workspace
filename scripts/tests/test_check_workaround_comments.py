"""Tests for the unexplained-marker-comment guard (#1184)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_workaround_comments as guard  # noqa: E402


def test_python_marker_comments_are_flagged():
    src = "x = 1  # TODO: later\n# HACK around the parser\n# FIXME\n# XXX\n# a workaround for the API\n"
    assert [n for n, _ in guard.scan_text(src, ".py")] == [1, 2, 3, 4, 5]


def test_issue_reference_explains_a_marker():
    src = "# TODO(#1234): paginate\n# workaround, see #1234\n"
    assert guard.scan_text(src, ".py") == []


def test_strings_and_prose_are_not_flagged():
    src = 'for bad in ("placeholder", "TODO"):\n    pass\ns = "# HACK"\n# todo list view and hack day are fine in lowercase prose\n'
    assert guard.scan_text(src, ".py") == []


def test_non_python_comment_styles():
    assert [n for n, _ in guard.scan_text("echo hi\n# HACK: x\n", ".sh")] == [2]
    assert [n for n, _ in guard.scan_text("a: 1\n# workaround for b\n", ".yml")] == [2]
    assert [n for n, _ in guard.scan_text("x();\n// TODO fix\n", ".js")] == [2]
    assert [n for n, _ in guard.scan_text("<!-- FIXME -->\n", ".html")] == [1]
    assert [n for n, _ in guard.scan_text("/* a\n * XXX b\n */\n", ".css")] == [2]
    assert guard.scan_text("// TODO(#9) fix\n", ".js") == []
    assert guard.scan_text("# TODO\n", ".md") == []


def test_untokenizable_python_still_checked():
    assert [n for n, _ in guard.scan_text("def (:\n  # TODO x\n", ".py")] == [2]


def test_skip_dirs_and_self_exempt(tmp_path):
    for rel in ("archive/a.py", "node_modules/b.py", "x/vendor/c.py", "x/experimental/d.py", "live/e.py"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# TODO: x\n")
    errs = guard.scan_paths(sorted(tmp_path.rglob("*.py")), root=tmp_path)
    assert len(errs) == 1 and errs[0].startswith("live/e.py:1:")
    assert not guard.in_scope(ROOT / "scripts/check_workaround_comments.py")


def test_cli_exit_codes(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("# FIXME: nope\n")
    good = tmp_path / "good.py"
    good.write_text("# FIXME(#1): tracked\n")
    assert guard.main([str(bad)]) == 1
    assert guard.main([str(good)]) == 0


def test_live_tree_has_no_unexplained_marker_comments():
    errs = guard.scan_paths(guard.tracked_files())
    assert errs == [], "\n".join(errs)
