"""GET /api/workout/exercise/available — capped catalog names. POST is read-only."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from api.workout._util import PREVIEW_READ_ONLY, require_user, write_json


def available_body(headers):
    user, err = require_user(headers)
    if err:
        return err
    from rt_dashboard.workout_store import (
        apply_goals_volume_caps,
        catalog_names,
        load_workspace_catalog,
        load_workspace_goals,
    )

    goals, goals_src = load_workspace_goals()
    catalog, catalog_src = load_workspace_catalog()
    catalog = apply_goals_volume_caps(catalog, goals)
    return 200, {
        "ok": True,
        "readonly": True,
        "catalog": catalog,
        "names": catalog_names(catalog),
        "sources": {"catalog": catalog_src, "goals": goals_src},
        "write": {"ok": False, "readonly": True},
    }


def available_write(headers):
    user, err = require_user(headers)
    if err:
        return err
    return 403, dict(PREVIEW_READ_ONLY)


available_read = available_body

class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status, body = available_body(self.headers)
        write_json(self, status, body)

    def do_POST(self) -> None:
        status, body = available_write(self.headers)
        write_json(self, status, body)

    def log_message(self, format: str, *args) -> None:
        return


app = handler
application = handler
