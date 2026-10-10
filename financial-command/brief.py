#!/usr/bin/env python3
"""Daily Brief: compatibility shim (#1091).

The newspaper moved out of FCC. The edition store, schema and publish CLI now
live in ``research/daily_brief/``, and the pages are served by the Horizon
host at ``/daily-brief``. FCC's ``/brief`` routes 302 there
(``BRIEF_BASE_URL``): ``server.py``'s ``brief_route`` calls ``route`` below,
which comes from ``brief_redirect.py``.

This file stays so existing invocations keep working unchanged::

  python3 ~/personal-workspace/financial-command/brief.py publish -  < edition.json
  financial-command/fcc brief publish <file.json|->

Same commands, same JSON output, same store
(``~/.local/share/fcc/brief-editions``; ``DAILY_BRIEF_DIR`` or the legacy
``FCC_BRIEF_DIR`` override it).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from research.daily_brief.cli import main  # noqa: E402,F401
from research.daily_brief.store import *  # noqa: E402,F401,F403
from research.daily_brief.store import (  # noqa: E402,F401
    BriefError,
    edition_url,
    mac_tasks_from_markdown,
    normalize,
    publish,
    store_dir,
    validate,
)


def _load_redirect():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "fcc_brief_redirect", Path(__file__).resolve().parent / "brief_redirect.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


_redirect = _load_redirect()
DEFAULT_BRIEF_BASE_URL = _redirect.DEFAULT_BRIEF_BASE_URL
brief_base_url = _redirect.brief_base_url
brief_redirect_target = _redirect.redirect_target
route = _redirect.route


if __name__ == "__main__":
    raise SystemExit(main())
