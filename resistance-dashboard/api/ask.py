"""POST /api/ask Vercel entry. Load _post by path so this file is never api.ask._post."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parent / "ask" / "_post.py"
_spec = importlib.util.spec_from_file_location("_ask_post_impl", _path)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)

ask_body = _mod.ask_body
handler = _mod.handler
app = _mod.app
application = _mod.application
