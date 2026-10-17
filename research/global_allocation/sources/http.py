"""HTTP GET for source adapters.

The User-Agent is a named research client. It is not ``python-requests``,
``curl``, ``wget``, or ``Scrapy``: the ECB data API robots.txt disallows those
tokens for the whole host. This is the allowed ``User-agent: *`` group, which
permits ``/service/data/``.
"""

from __future__ import annotations

USER_AGENT = "personal-workspace-global-allocation/1.0"


def fetch(url: str, timeout: float = 60) -> tuple[int, bytes]:
    import requests

    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )
    return response.status_code, response.content


def call(source_id: str, source_name: str, url: str, parse, timeout: float = 60):
    from research.global_allocation.sources.base import unavailable

    try:
        status, body = fetch(url, timeout=timeout)
    except Exception as exc:
        return unavailable(source_id, source_name, url.split("?")[0], type(exc).__name__)
    if status >= 400:
        return unavailable(source_id, source_name, url.split("?")[0], f"HTTP {status}")
    try:
        return parse(body)
    except Exception as exc:
        return unavailable(source_id, source_name, url.split("?")[0], f"parse {type(exc).__name__}")
