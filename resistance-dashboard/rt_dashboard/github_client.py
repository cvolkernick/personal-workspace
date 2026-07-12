"""GitHub Contents API client for lift-log markdown files."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .models import Session
from .parse import (
    WORKOUT_PATHS,
    append_session_to_markdown,
    parse_all_workouts,
    parse_workout_markdown,
)


API_ROOT = "https://api.github.com"


@dataclass
class FileContent:
    path: str
    content: str
    sha: str


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class GitHubLiftClient:
    """Pull/push fitness/workouts/*.md via GitHub Contents API."""

    def __init__(
        self,
        owner: Optional[str] = None,
        repo: Optional[str] = None,
        token: Optional[str] = None,
        branch: Optional[str] = None,
        local_fallback_dir: Optional[str] = None,
        prefer_local: Optional[bool] = None,
    ):
        self.owner = owner or os.environ.get("GITHUB_OWNER", "cvolkernick")
        self.repo = repo or os.environ.get("GITHUB_REPO", "personal-workspace")
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        self.branch = branch or os.environ.get("GITHUB_BRANCH", "master")
        self.local_fallback_dir = local_fallback_dir or os.environ.get(
            "LOCAL_WORKSPACE_DIR", ""
        )
        if prefer_local is None:
            self.prefer_local = os.environ.get("GITHUB_PREFER_LOCAL", "").lower() in (
                "1",
                "true",
                "yes",
            )
        else:
            self.prefer_local = bool(prefer_local)

    def _headers(self) -> Dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "resistance-dashboard/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
    ) -> Tuple[int, dict]:
        url = f"{API_ROOT}{path}"
        data = None
        headers = self._headers()
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            # Keep tight so a stalled network cannot freeze the whole dashboard.
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise GitHubError(
                f"GitHub API {method} {path} failed: HTTP {e.code}",
                status=e.code,
                body=err_body,
            ) from e
        except urllib.error.URLError as e:
            raise GitHubError(f"GitHub network error: {e}") from e

    def get_file(self, path: str, ref: Optional[str] = None) -> FileContent:
        if self.prefer_local and self.local_fallback_dir:
            return self._get_local(path)

        q = f"?ref={ref or self.branch}"
        status, data = self._request(
            "GET", f"/repos/{self.owner}/{self.repo}/contents/{path}{q}"
        )
        if status != 200:
            raise GitHubError(f"Unexpected status {status} fetching {path}", status)
        if data.get("encoding") != "base64":
            # some APIs return raw; handle base64 only for contents API
            if "content" not in data:
                raise GitHubError(f"No content for {path}")
        content_b64 = data.get("content", "").replace("\n", "")
        text = base64.b64decode(content_b64).decode("utf-8")
        return FileContent(path=path, content=text, sha=data["sha"])

    def _get_local(self, path: str) -> FileContent:
        full = os.path.join(self.local_fallback_dir, path)
        with open(full, "r", encoding="utf-8") as f:
            text = f.read()
        # synthetic sha for local
        import hashlib

        sha = hashlib.sha1(text.encode("utf-8")).hexdigest()
        return FileContent(path=path, content=text, sha=sha)

    def put_file(
        self,
        path: str,
        content: str,
        message: str,
        sha: Optional[str] = None,
    ) -> dict:
        if self.prefer_local and self.local_fallback_dir:
            return self._put_local(path, content, message)

        if not self.token:
            raise GitHubError(
                "GITHUB_TOKEN is required to write lift logs via the GitHub API"
            )
        body = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": self.branch,
        }
        if sha:
            body["sha"] = sha
        _status, data = self._request(
            "PUT", f"/repos/{self.owner}/{self.repo}/contents/{path}", body=body
        )
        return data

    def _put_local(self, path: str, content: str, message: str) -> dict:
        full = os.path.join(self.local_fallback_dir, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return {"commit": {"message": message}, "content": {"path": path}, "local": True}

    def list_workout_paths(self) -> List[str]:
        """Return workout markdown paths (core PPL + any extra logs like canaries)."""
        paths = list(WORKOUT_PATHS.values())
        # Also discover extra .md files under fitness/workouts/
        try:
            if self.prefer_local and self.local_fallback_dir:
                import os as _os

                wdir = _os.path.join(self.local_fallback_dir, "fitness", "workouts")
                if _os.path.isdir(wdir):
                    for name in sorted(_os.listdir(wdir)):
                        if name.endswith(".md") and name != "README.md":
                            rel = f"fitness/workouts/{name}"
                            if rel not in paths:
                                paths.append(rel)
            else:
                status, data = self._request(
                    "GET",
                    f"/repos/{self.owner}/{self.repo}/contents/fitness/workouts?ref={self.branch}",
                )
                if status == 200 and isinstance(data, list):
                    for entry in data:
                        name = entry.get("name") or ""
                        if name.endswith(".md") and name != "README.md":
                            path = entry.get("path") or f"fitness/workouts/{name}"
                            if path not in paths:
                                paths.append(path)
        except GitHubError:
            pass
        return paths

    def pull_workout_files(self) -> Dict[str, str]:
        files: Dict[str, str] = {}
        for path in self.list_workout_paths():
            try:
                fc = self.get_file(path)
                files[path] = fc.content
            except GitHubError as e:
                # Core files should raise; extras may 404
                if path in WORKOUT_PATHS.values():
                    raise
                continue
        return files

    def pull_sessions(self) -> List[Session]:
        files = self.pull_workout_files()
        return parse_all_workouts(files)

    def append_workout(self, session: Session) -> dict:
        """Append a session; alias of append_workout_safe."""
        return self.append_workout_safe(session)

    def append_workout_safe(self, session: Session) -> dict:
        """Append with correct SHA handling for GitHub Contents update."""
        st = session.session_type.lower()
        if st not in WORKOUT_PATHS:
            raise GitHubError(
                f"Unknown session_type {session.session_type!r}; expected push|pull|legs"
            )
        path = WORKOUT_PATHS[st]
        session.source_file = path
        fc = self.get_file(path)
        updated = append_session_to_markdown(fc.content, session)
        parsed = parse_workout_markdown(updated, session_type=st, source_file=path)
        if self.prefer_local and self.local_fallback_dir:
            result = self._put_local(
                path,
                updated,
                f"log {st} workout {session.date} via resistance-dashboard",
            )
        else:
            if not self.token:
                raise GitHubError(
                    "GITHUB_TOKEN is required to write lift logs via the GitHub API"
                )
            result = self.put_file(
                path,
                updated,
                message=f"log {st} workout {session.date} via resistance-dashboard",
                sha=fc.sha,
            )
        # Re-read from storage (disk or remote) — never trust in-memory buffer alone
        verified = False
        readback_error = None
        try:
            again_text = self.get_file(path).content
            again = parse_workout_markdown(
                again_text, session_type=st, source_file=path
            )
            target_names = {e.name for e in session.exercises}
            verified = any(
                s.date == session.date
                and s.session_type == st
                and target_names.issubset({e.name for e in s.exercises})
                for s in again
            )
        except Exception as e:  # noqa: BLE001 — surface any readback failure
            readback_error = str(e)
            verified = False
        return {
            "path": path,
            "result": result,
            "session": session.to_dict(),
            "parsed_count_after": len(parsed),
            "verified_on_readback": verified,
            "readback_error": readback_error,
        }
