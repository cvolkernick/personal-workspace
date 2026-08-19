"""Shared JSON + auth helpers for /api/workout* adapters. Never a route file."""

from __future__ import annotations

from api.ask._json import auth_required, require_user, write_json

__all__ = ["auth_required", "require_user", "write_json"]
