"""Vault path resolution for b2_kb modules.

Full vault index/search lives with the B2 UX package when present; Meet
recordings only needs resolve_vault_path + default ~/B2.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_PKG_ROOT = Path(__file__).resolve().parent.parent  # .../b2-ux
_REPO_VAULT = _PKG_ROOT.parent / "brain2"
_HOME_VAULT = Path.home() / "B2"


def _default_vault() -> Path:
    env = (os.environ.get("B2_VAULT_PATH") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if _HOME_VAULT.exists():
        return _HOME_VAULT.expanduser().resolve()
    return _REPO_VAULT.resolve()


DEFAULT_VAULT_PATH = _default_vault()


def resolve_vault_path(vault_path: Optional[os.PathLike | str] = None) -> Path:
    if vault_path is None:
        return _default_vault()
    return Path(vault_path).expanduser().resolve()
