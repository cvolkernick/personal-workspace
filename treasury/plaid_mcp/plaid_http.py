"""Stdlib Plaid REST client. Read-only endpoints only. No SDK."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional

CONFIG_DIR = Path.home() / ".config" / "plaid-bank"

PLAID_HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "production": "https://production.plaid.com",
}

# Refuse even if a caller tries to pass these paths.
WRITE_PATH_PREFIXES = (
    "/transfer",
    "/payment_initiation",
    "/processor",
    "/sandbox/transfer",
    "/item/remove",
    "/item/webhook",
    "/link/token/create",  # Link is CLI-only, not MCP
    "/item/public_token/exchange",
)

READ_PATHS = frozenset(
    {
        "/accounts/get",
        "/accounts/balance/get",
        "/transactions/get",
        "/item/get",
        "/institutions/get_by_id",
    }
)

PostFn = Callable[[str, Dict[str, Any]], Dict[str, Any]]


def _read_first_line(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except OSError:
        return ""


def load_secret(env_name: str, filename: str) -> str:
    env = (os.environ.get(env_name) or "").strip()
    if env:
        return env
    return _read_first_line(CONFIG_DIR / filename)


def load_credentials() -> Dict[str, str]:
    return {
        "client_id": load_secret("PLAID_CLIENT_ID", "client_id"),
        "secret": load_secret("PLAID_SECRET", "secret"),
        "access_token": load_secret("PLAID_ACCESS_TOKEN", "access_token"),
        "env": (os.environ.get("PLAID_ENV") or load_secret("PLAID_ENV", "env") or "production").strip().lower(),
    }


class PlaidError(RuntimeError):
    def __init__(self, message: str, *, status: Optional[int] = None, body: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.body = body


class PlaidClient:
    def __init__(
        self,
        *,
        client_id: str = "",
        secret: str = "",
        access_token: str = "",
        env: str = "production",
        post_fn: Optional[PostFn] = None,
        timeout: float = 30.0,
    ):
        self.client_id = client_id
        self.secret = secret
        self.access_token = access_token
        env_key = (env or "production").strip().lower()
        if env_key not in PLAID_HOSTS:
            raise PlaidError(f"unsupported PLAID_ENV={env_key!r} (sandbox|production)")
        self.env = env_key
        self.host = PLAID_HOSTS[env_key]
        self.timeout = timeout
        self._post = post_fn or self._http_post

    @classmethod
    def from_env(cls, *, post_fn: Optional[PostFn] = None) -> "PlaidClient":
        creds = load_credentials()
        return cls(
            client_id=creds["client_id"],
            secret=creds["secret"],
            access_token=creds["access_token"],
            env=creds["env"],
            post_fn=post_fn,
        )

    def require_item(self) -> None:
        missing = [k for k, v in (("PLAID_CLIENT_ID", self.client_id), ("PLAID_SECRET", self.secret), ("PLAID_ACCESS_TOKEN", self.access_token)) if not v]
        if missing:
            raise PlaidError(
                "missing credentials: " + ", ".join(missing) + " (env or ~/.config/plaid-bank/)"
            )

    def _http_post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self.host + path
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "personal-workspace-plaid-bank/0.1",
                "PLAID-CLIENT-ID": self.client_id,
                "PLAID-SECRET": self.secret,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")[:800]
            raise PlaidError(f"Plaid HTTP {e.code} {path}: {err_body}", status=e.code, body=err_body) from e
        except urllib.error.URLError as e:
            raise PlaidError(f"Plaid network error {path}: {e}") from e
        try:
            out = json.loads(raw)
        except json.JSONDecodeError as e:
            raise PlaidError(f"Plaid invalid JSON {path}: {e}") from e
        if not isinstance(out, dict):
            raise PlaidError(f"Plaid unexpected payload {path}")
        return out

    def post(self, path: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not path.startswith("/"):
            path = "/" + path
        for prefix in WRITE_PATH_PREFIXES:
            if path == prefix or path.startswith(prefix + "/"):
                raise PlaidError(f"refused write/link path {path}")
        if path not in READ_PATHS:
            raise PlaidError(f"refused unknown path {path}")
        self.require_item()
        payload: Dict[str, Any] = {
            "client_id": self.client_id,
            "secret": self.secret,
            "access_token": self.access_token,
        }
        if extra:
            payload.update(extra)
        return self._post(path, payload)

    def accounts_get(self) -> Dict[str, Any]:
        return self.post("/accounts/get")

    def balances_get(self) -> Dict[str, Any]:
        return self.post("/accounts/balance/get")

    def transactions_get(self, *, start_date: str, end_date: str, count: int = 100, offset: int = 0) -> Dict[str, Any]:
        return self.post(
            "/transactions/get",
            {
                "start_date": start_date,
                "end_date": end_date,
                "options": {"count": min(max(count, 1), 500), "offset": max(offset, 0)},
            },
        )

    def item_get(self) -> Dict[str, Any]:
        return self.post("/item/get")
