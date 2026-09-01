"""Thin GitHub REST client. Injectable for tests. Optional token from env."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

API_ROOT = "https://api.github.com"
USER_AGENT = "personal-workspace-oomwoo-status"


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _token() -> str:
    return (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("GH_PAT")
        or ""
    ).strip()


class GitHubClient:
    def __init__(
        self,
        *,
        get_json: Optional[Callable[[str], Any]] = None,
        get_text: Optional[Callable[[str], str]] = None,
        token: Optional[str] = None,
        timeout: float = 12.0,
    ) -> None:
        self._get_json = get_json
        self._get_text = get_text
        self.token = token if token is not None else _token()
        self.timeout = timeout

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _open(self, path: str, accept: str) -> tuple[int, bytes]:
        url = path if path.startswith("http") else API_ROOT + path
        req = urllib.request.Request(url, headers=self._headers(accept), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return int(resp.status), resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp else b""
            raise GitHubError(f"{exc.code} {path}: {body[:200]!r}", status=int(exc.code)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GitHubError(f"network {path}: {exc}") from exc

    def get_json(self, path: str) -> Any:
        if self._get_json is not None:
            return self._get_json(path)
        _status, raw = self._open(path, "application/vnd.github+json")
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def get_text(self, path: str) -> str:
        if self._get_text is not None:
            return self._get_text(path)
        _status, raw = self._open(path, "application/vnd.github.raw")
        return raw.decode("utf-8")
