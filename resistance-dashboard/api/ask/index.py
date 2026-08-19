"""POST /api/ask Vercel entry. Package index so api.ask is the real package."""

from __future__ import annotations

try:
    from ._post import application, app, ask_body, handler
except ImportError:  # Vercel may load this file as module api.ask
    import importlib.util
    from pathlib import Path

    _path = Path(__file__).with_name("_post.py")
    _spec = importlib.util.spec_from_file_location("_ask_post_impl", _path)
    _mod = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(_mod)
    ask_body = _mod.ask_body
    handler = _mod.handler
    app = _mod.app
    application = _mod.application
