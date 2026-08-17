"""Load KEY=VALUE env files without overriding the process environment."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping


DEFAULT_ENV_PATH = Path.home() / ".config" / "auto-fleet" / "env"


def load_env_file(path: Path | None = None) -> dict[str, str]:
    """Parse KEY=VALUE / export KEY=VALUE lines. Missing file → empty dict."""
    p = Path(path) if path is not None else DEFAULT_ENV_PATH
    if not p.is_file():
        return {}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if not key or val.startswith("PASTE_YOUR_"):
            continue
        out[key] = val
    return out


def merge_env(
    file_env: Mapping[str, str] | None = None,
    process_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Process env wins over file so tests can inject without writing secrets."""
    merged = dict(file_env or {})
    if process_env:
        for key, val in process_env.items():
            if key.startswith("DIMO_") or key.startswith("TURO_") or key.startswith("AUTO_FLEET_"):
                if val:
                    merged[key] = val
    return merged
