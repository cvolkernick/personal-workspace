"""GitHub Releases client for the private fitdash-backups repo.

Stdlib urllib only. The token is read by the caller from
``FITDASH_BACKUP_GH_TOKEN`` and is never logged.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from backup_common import note

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DEFAULT_REPO = "cvolkernick/fitdash-backups"
API = "https://api.github.com"


def redact(text: str, token: str) -> str:
    if token and token in text:
        return text.replace(token, "[redacted]")
    return text


def repo_name(explicit: Optional[str] = None) -> str:
    repo = (explicit or os.environ.get("FITDASH_BACKUP_GH_REPO") or DEFAULT_REPO).strip()
    if not REPO_RE.fullmatch(repo):
        raise RuntimeError("invalid GitHub repo name")
    return repo


class GitHubReleases:
    """Small Releases API wrapper. ``__repr__`` omits the token."""

    def __init__(self, token: str, repo: str, timeout: float = 60.0) -> None:
        self._token = token
        self.repo = repo
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"GitHubReleases(repo={self.repo!r})"

    def _request(
        self,
        method: str,
        url: str,
        data: Optional[bytes] = None,
        headers: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> tuple[int, dict, bytes]:
        hdrs = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "fitdash-backup",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if headers:
            hdrs.update(headers)
        request = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def _fail(self, code: int, body: bytes) -> RuntimeError:
        text = redact(body.decode("utf-8", "replace")[:800], self._token)
        return RuntimeError(f"GitHub HTTP {code}: {text}")

    def tag_taken(self, tag: str) -> bool:
        release = self._request(
            "GET", f"{API}/repos/{self.repo}/releases/tags/{urllib.parse.quote(tag)}"
        )
        if release[0] == 200:
            return True
        if release[0] not in (404,):
            raise self._fail(release[0], release[2])
        ref = self._request(
            "GET", f"{API}/repos/{self.repo}/git/ref/tags/{urllib.parse.quote(tag)}"
        )
        if ref[0] == 200:
            return True
        if ref[0] == 404:
            return False
        raise self._fail(ref[0], ref[2])

    def create_release(self, tag: str, name: str, body: str) -> dict:
        payload = json.dumps(
            {
                "tag_name": tag,
                "name": name,
                "body": body,
                "draft": False,
                "prerelease": False,
            }
        ).encode("utf-8")
        code, _headers, raw = self._request(
            "POST",
            f"{API}/repos/{self.repo}/releases",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        if code not in (200, 201):
            raise self._fail(code, raw)
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict) or "upload_url" not in parsed:
            raise RuntimeError("GitHub release response had no upload_url")
        return parsed

    def upload_asset(self, release: dict, filename: str, data: bytes, content_type: str) -> None:
        upload_url = str(release.get("upload_url") or "")
        base = upload_url.split("{", 1)[0]
        if not base.startswith("https://uploads.github.com/"):
            raise RuntimeError("refusing unexpected GitHub upload URL")
        url = base + "?" + urllib.parse.urlencode({"name": filename})
        code, _headers, raw = self._request(
            "POST",
            url,
            data=data,
            headers={"Content-Type": content_type},
            timeout=180,
        )
        if code not in (200, 201):
            raise self._fail(code, raw)

    def list_releases(self) -> list[dict]:
        url: Optional[str] = f"{API}/repos/{self.repo}/releases?per_page=100"
        found: list[dict] = []
        pages = 0
        while url:
            pages += 1
            if pages > 50:
                raise RuntimeError("GitHub release pagination did not end")
            code, headers, raw = self._request("GET", url)
            if code != 200:
                raise self._fail(code, raw)
            batch = json.loads(raw.decode("utf-8"))
            if not isinstance(batch, list):
                raise RuntimeError("GitHub releases response was not a list")
            found.extend(item for item in batch if isinstance(item, dict))
            url = _next_link(headers.get("Link") or headers.get("link") or "")
        return found

    def delete_release(self, release_id: int, tag: str) -> None:
        code, _headers, raw = self._request(
            "DELETE", f"{API}/repos/{self.repo}/releases/{int(release_id)}"
        )
        if code not in (204, 404):
            raise self._fail(code, raw)
        ref = urllib.parse.quote(tag)
        code, _headers, raw = self._request(
            "DELETE", f"{API}/repos/{self.repo}/git/refs/tags/{ref}"
        )
        if code not in (204, 404, 422):
            raise self._fail(code, raw)
        note(f"deleted release {tag}")


def _next_link(header: str) -> Optional[str]:
    for part in header.split(","):
        if 'rel="next"' not in part:
            continue
        match = re.search(r"<([^>]+)>", part)
        if match:
            return match.group(1)
    return None


def client_from_env(repo: Optional[str] = None) -> GitHubReleases:
    token = (os.environ.get("FITDASH_BACKUP_GH_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("FITDASH_BACKUP_GH_TOKEN is not set")
    return GitHubReleases(token, repo_name(repo))


def parse_github_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
