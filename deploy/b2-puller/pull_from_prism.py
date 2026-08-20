#!/usr/bin/env python3
"""Pull allowlisted prism books/state onto finley. Runs ON finley-gateway.

Usage (on finley-agent@finley-gateway):
  python3 deploy/b2-puller/pull_from_prism.py
  python3 deploy/b2-puller/pull_from_prism.py --dry-run
  python3 deploy/b2-puller/pull_from_prism.py --src-root /tmp/prism-home --dest /tmp/pull

Writes only what was pulled. Refuses venue keys and any dest that would
put raw treasury on Vercel or a Mac. Does not push B2 onto prism.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from paths import (  # noqa: E402
    DEFAULT_PRISM_HOME,
    DEFAULT_PULL_DEST,
    FINLEY_HOSTNAME,
    PULL_RELATIVE,
    ROLE_B2_PULLER,
    assert_pull_list_clean,
    classify_dest,
    classify_source,
    dest_for_source,
    is_refused_dest,
    is_refused_name,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def refuse(msg: str) -> int:
    print(f"REFUSE: {msg}", file=sys.stderr)
    return 2


def _host_ok_for_live_pull() -> bool:
    """Live (SSH) pull is intended for finley. Tests use --src-root and skip this."""
    if (os.environ.get("B2_PULLER_ALLOW_HOST") or "").strip() == "1":
        return True
    host = socket.gethostname().split(".")[0]
    return host in (FINLEY_HOSTNAME, "finley")


def _copy_local(src: Path, dest: Path) -> None:
    if src.is_dir():
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _rsync_remote(remote: str, src: str, dest: Path, dry: bool) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", "-a", "--protect-args"]
    if dry:
        cmd.append("--dry-run")
    if src.endswith("/"):
        dest.mkdir(parents=True, exist_ok=True)
        cmd.extend([f"{remote}:{src}", str(dest) + "/"])
    else:
        cmd.extend([f"{remote}:{src}", str(dest)])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        print(f"skip (rsync {proc.returncode}): {src} — {err}", file=sys.stderr)
        return False
    return True


def _iter_written(dest: Path) -> list[str]:
    if not dest.exists():
        return []
    if dest.is_file():
        return [str(dest)]
    return [str(p) for p in dest.rglob("*") if p.is_file()]


def _nested_refused(root: Path) -> list[str]:
    if not root.exists():
        return []
    files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
    return [str(p) for p in files if is_refused_name(p)]


def _purge(paths: list[str]) -> None:
    for s in paths:
        p = Path(s)
        if p.is_file():
            p.unlink(missing_ok=True)


def pull(
    *,
    dest_root: Path,
    src_root: Optional[Path] = None,
    remote: str = "prism-agent@prism-gateway",
    dry_run: bool = False,
    require_finley: bool = True,
) -> dict[str, Any]:
    assert_pull_list_clean()
    dest_c = classify_dest(dest_root)
    if not dest_c.allowed or is_refused_dest(dest_root):
        raise RuntimeError(f"{dest_c.reason} ({dest_root})")

    if src_root is None and require_finley and not _host_ok_for_live_pull():
        raise RuntimeError(
            f"live pull must run on {FINLEY_HOSTNAME} (role {ROLE_B2_PULLER}); "
            "use --src-root for fixtures"
        )

    pulled: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []

    for rel in PULL_RELATIVE:
        src_cls = classify_source(rel)
        if not src_cls.allowed:
            refused.append({"path": rel, "reason": src_cls.reason})
            continue
        dest = dest_for_source(rel, dest_root)
        dest_cls = classify_dest(dest)
        if not dest_cls.allowed:
            refused.append({"path": rel, "dest": str(dest), "reason": dest_cls.reason})
            continue

        if src_root is not None:
            src = Path(src_root) / rel
            if not src.exists():
                skipped.append({"path": rel, "reason": "source missing"})
                continue
            nested_bad = _nested_refused(src)
            if nested_bad:
                refused.append(
                    {
                        "path": rel,
                        "reason": "refuse: nested venue-key path(s)",
                        "files": nested_bad,
                    }
                )
                continue
            if dry_run:
                pulled.append({"path": rel, "dest": str(dest), "dry_run": True})
                continue
            _copy_local(src, dest)
            landed_bad = _nested_refused(dest)
            if landed_bad:
                _purge(landed_bad)
                refused.append(
                    {
                        "path": rel,
                        "reason": "refuse: venue-key landed; purged",
                        "files": landed_bad,
                    }
                )
                continue
            pulled.append({"path": rel, "dest": str(dest), "wrote": _iter_written(dest)})
            continue

        remote_src = str(Path(DEFAULT_PRISM_HOME) / rel)
        ok = _rsync_remote(remote, remote_src, dest, dry_run)
        if not ok:
            skipped.append({"path": rel, "reason": "remote missing or rsync failed"})
            continue
        if not dry_run:
            landed_bad = _nested_refused(dest)
            if landed_bad:
                _purge(landed_bad)
                refused.append(
                    {
                        "path": rel,
                        "reason": "refuse: venue-key landed; purged",
                        "files": landed_bad,
                    }
                )
                continue
        pulled.append({"path": rel, "dest": str(dest), "dry_run": dry_run})

    if refused:
        raise RuntimeError(
            "REFUSE: pull aborted; venue keys or blocked dest in plan: "
            + json.dumps(refused)
        )

    manifest = {
        "ok": True,
        "role": ROLE_B2_PULLER,
        "host": socket.gethostname().split(".")[0],
        "pulled_at": utc_now(),
        "dest": str(dest_root),
        "wrote_only_pulled": True,
        "pulled": pulled,
        "skipped": skipped,
        "refused": refused,
    }
    if not dry_run:
        dest_root.mkdir(parents=True, exist_ok=True)
        (dest_root / "MANIFEST.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    return manifest


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Pull prism books/state onto finley")
    p.add_argument(
        "--dest",
        default=os.environ.get("B2_PULL_DEST", DEFAULT_PULL_DEST),
        help="finley dest root (default ~/b2-pulls/prism)",
    )
    p.add_argument(
        "--src-root",
        default=None,
        help="local fixture root (tests). Omit for SSH pull from prism.",
    )
    p.add_argument(
        "--remote",
        default=os.environ.get("B2_PULL_REMOTE", "prism-agent@prism-gateway"),
        help="SSH target for live pull (MagicDNS; Tailscale 100.67.114.2 works)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--allow-any-host",
        action="store_true",
        help="skip finley hostname check (Forge dry-run / tests)",
    )
    args = p.parse_args(argv)

    dest = Path(args.dest).expanduser()
    src_root = Path(args.src_root).expanduser() if args.src_root else None
    try:
        man = pull(
            dest_root=dest,
            src_root=src_root,
            remote=args.remote,
            dry_run=args.dry_run,
            require_finley=not args.allow_any_host and src_root is None,
        )
    except RuntimeError as e:
        return refuse(str(e))
    print(json.dumps(man, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
