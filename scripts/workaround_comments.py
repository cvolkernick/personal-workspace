#!/usr/bin/env python3
"""Groom sidecars must not import the writer module ``youtube_groom``.

The repeated "do not import" notes on those sidecars are gone. This check
is what fails CI instead. ``youtube_groom_*`` modules are sidecars, not the
writer. Marker comments are a separate gate: ``scripts/check_workaround_comments.py``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SIDECARS = (
    "scripts/youtube_groom_control.py",
    "scripts/youtube_groom_health.py",
    "scripts/youtube_groom_quota.py",
    "scripts/youtube_groom_shorts.py",
    "scripts/youtube_groom_supply.py",
    "scripts/youtube_groom_tick_report.py",
)
WRITER_MODULE = "youtube_groom"


def _is_writer_module(name: str) -> bool:
    return name == WRITER_MODULE or name.startswith(WRITER_MODULE + ".")


def writer_imports(text: str) -> list[tuple[int, str]]:
    """Import sites that load the writer module, not a ``youtube_groom_*`` sidecar."""
    tree = ast.parse(text)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_writer_module(alias.name):
                    hits.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _is_writer_module(module):
                hits.append((node.lineno, f"from {module} import"))
            elif node.module is None and any(alias.name == WRITER_MODULE for alias in node.names):
                hits.append((node.lineno, f"from . import {WRITER_MODULE}"))
        elif isinstance(node, ast.Call) and node.args:
            name = _call_name(node.func)
            if name in {"import_module", "importlib.import_module", "__import__"}:
                arg = node.args[0]
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and _is_writer_module(arg.value)
                ):
                    hits.append((node.lineno, f"{name}({arg.value!r})"))
    return hits


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def sidecar_import_hits(root: Path = ROOT) -> list[str]:
    found: list[str] = []
    for rel in SIDECARS:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        for line, kind in writer_imports(text):
            found.append(f"{rel}:{line}: {kind}")
    return found


def main(argv: list[str] | None = None) -> int:
    del argv
    imports = sidecar_import_hits(ROOT)
    for hit in imports:
        print(hit)
    if imports:
        print(f"{len(imports)} writer import(s)", file=sys.stderr)
        return 1
    print("groom sidecars do not import the writer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
