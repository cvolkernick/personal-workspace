"""Test wrapper: load api/ask.py by path so this package is not required on Vercel."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "ask.py"
_spec = importlib.util.spec_from_file_location("_ask_py_impl", _path)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)

ask_body = _mod.ask_body
handler = _mod.handler
app = _mod.app
application = _mod.application
