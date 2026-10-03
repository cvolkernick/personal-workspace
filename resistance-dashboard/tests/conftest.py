"""Keep the FitDash suite off live GitHub (#1043).

Production still fetches health metrics and lift logs. Tests must not.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

_BLOCKED_HOSTS = ("api.github.com", "raw.githubusercontent.com")
_REAL_URL_OPEN = urllib.request.urlopen


def _request_url(req) -> str:
    if isinstance(req, str):
        return req
    full = getattr(req, "full_url", None)
    if isinstance(full, str):
        return full
    getter = getattr(req, "get_full_url", None)
    if callable(getter):
        return str(getter())
    return str(req)


@pytest.fixture(autouse=True)
def _block_live_github_fetches(monkeypatch):
    attempted: list[str] = []

    def _guard(req, *args, **kwargs):
        url = _request_url(req)
        for host in _BLOCKED_HOSTS:
            if host in url:
                attempted.append(url)
                raise urllib.error.URLError(
                    f"live GitHub fetch blocked in tests: {url}"
                )
        return _REAL_URL_OPEN(req, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", _guard)
    yield
    if attempted:
        shown = "\n".join(attempted[:8])
        pytest.fail(f"test called live GitHub:\n{shown}")
