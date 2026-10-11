#!/usr/bin/env python3
"""One Shorts rule for every AI Curated writer path.

Chris, 2026-10-03: AI Curated is long-form only. A video is a Short when
either is true:

* ``contentDetails.duration`` is 180 seconds or less (YouTube Shorts can
  run up to 3 minutes, so the old "under 60s" rule let 60–180s Shorts in), or
* YouTube serves it as a Short: ``https://www.youtube.com/shorts/<id>``
  answers 200 without redirecting to ``/watch``.

The URL probe costs no Data API quota. It is rate limited, and a network
error or an odd answer is *unknown*, which falls back to the duration rule.

Every groom pass deletes any Short already on the playlist
(``shorts_prune_reason``), and the add path skips them
(``youtube_groom_supply.filter_longform``).

The live writer on the Pi is not in git. ``patch_writer_source`` rewires its
in-place 2026-10-07 hotfix to call this module. Backup first, idempotent,
raises ``PatchError`` when an anchor drifted. No OAuth, no Data API calls.
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, MutableMapping, Optional, Sequence

SHORTS_MAX_SEC = 180
MIN_LONGFORM_SEC = SHORTS_MAX_SEC + 1
REASON_DURATION = "short<=180s"
REASON_URL = "short-url"
SHORTS_URL = "https://www.youtube.com/shorts/{video_id}"
PROBE_TIMEOUT_SEC = 5.0
PROBE_MIN_INTERVAL_SEC = 0.25  # at most ~4 probes/second
PROBE_MAX_PER_PASS = 400  # past this, a pass falls back to the duration rule
USER_AGENT = "Mozilla/5.0 (X11; Linux aarch64) youtube-groom shorts-check"
_VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{6,20}")
_REDIRECT_CODES = (301, 302, 303, 307, 308)

ProbeFn = Callable[[str], Optional[bool]]


class PatchError(RuntimeError):
    """The live writer text does not have the anchor this patch expects."""


class _Throttle:
    def __init__(
        self,
        min_interval: float = PROBE_MIN_INTERVAL_SEC,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = float(min_interval)
        self.clock = clock
        self.sleep = sleep
        self._last: Optional[float] = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self.clock()
            if self._last is not None:
                gap = self.min_interval - (now - self._last)
                if gap > 0:
                    self.sleep(gap)
                    now = self.clock()
            self._last = now


_THROTTLE = _Throttle()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None  # surface the 3xx as HTTPError so we can read Location


def _header(headers: Any, name: str) -> str:
    try:
        return str(headers.get(name) or "") if headers is not None else ""
    except Exception:
        return ""


def classify_probe(code: Optional[int], location: str = "", final_url: str = "") -> Optional[bool]:
    """True = Short, False = not a Short, None = unknown."""
    if code == 200:
        if "/watch" in (final_url or ""):
            return False  # an opener followed the redirect anyway
        return True
    if code in _REDIRECT_CODES:
        return False if "/watch" in (location or "") else None
    return None


def probe_shorts_url(
    video_id: str,
    *,
    opener: Any = None,
    timeout: float = PROBE_TIMEOUT_SEC,
    throttle: Optional[_Throttle] = None,
) -> Optional[bool]:
    """HEAD (GET if HEAD is refused) of the Shorts URL with redirects off."""
    vid = str(video_id or "")
    if not _VIDEO_ID_RE.fullmatch(vid):
        return None
    (throttle or _THROTTLE).wait()
    opener = opener or urllib.request.build_opener(_NoRedirect)
    url = SHORTS_URL.format(video_id=vid)
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
        try:
            resp = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            code: Optional[int] = exc.code
            location = _header(exc.headers, "Location")
            final_url = ""
        except Exception:
            return None  # network error: unknown
        else:
            try:
                code = int(resp.getcode())
            except Exception:
                code = None
            location = _header(getattr(resp, "headers", None), "Location")
            try:
                final_url = str(resp.geturl() or "")
            except Exception:
                final_url = ""
            try:
                resp.close()
            except Exception:
                pass
        if code == 405 and method == "HEAD":
            continue
        return classify_probe(code, location, final_url)
    return None


def _known_duration(duration_sec: Any) -> Optional[int]:
    if isinstance(duration_sec, bool) or not isinstance(duration_sec, int):
        return None
    if duration_sec <= 0:
        return None  # 0 is how the writer spells missing / P0D
    return duration_sec


class ProbeBudget:
    def __init__(self, limit: int = PROBE_MAX_PER_PASS) -> None:
        self.left = int(limit)

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def shorts_reason(
    duration_sec: Any,
    video_id: str,
    *,
    cache: Optional[MutableMapping[str, Any]] = None,
    probe: Optional[ProbeFn] = None,
    budget: Optional[ProbeBudget] = None,
) -> Optional[str]:
    """Why this video is a Short, ``""`` if it is not, ``None`` if unknown.

    Duration first (no I/O). Otherwise the cached or fresh URL probe. A probe
    that errors falls back to the duration rule: a known >180s duration is
    long-form, an unknown duration stays unknown. Only definite answers are
    cached, so a network blip is retried on a later pass.
    """
    dur = _known_duration(duration_sec)
    if dur is not None and dur <= SHORTS_MAX_SEC:
        return REASON_DURATION
    vid = str(video_id or "")
    flag: Optional[bool] = None
    if vid and cache is not None and isinstance(cache.get(vid), bool):
        flag = cache[vid]
    elif vid and (budget is None or budget.take()):
        flag = (probe or probe_shorts_url)(vid)
        if isinstance(flag, bool) and cache is not None:
            cache[vid] = flag
    if flag is True:
        return REASON_URL
    if flag is False:
        return ""
    return "" if dur is not None else None


def is_short(duration_sec: Any, video_id: str, **kw: Any) -> bool:
    return bool(shorts_reason(duration_sec, video_id, **kw))


def shorts_prune_reason(
    item: dict[str, Any],
    cache: Optional[MutableMapping[str, Any]] = None,
    *,
    probe: Optional[ProbeFn] = None,
    budget: Optional[ProbeBudget] = None,
) -> str:
    """Prune reason for a playlist item already on AI Curated ('' = keep)."""
    if not isinstance(item, dict) or item.get("dead"):
        return ""
    why = shorts_reason(
        item.get("duration_sec"),
        str(item.get("video_id") or ""),
        cache=cache,
        probe=probe,
        budget=budget,
    )
    return why or ""


def trim_cache(cache: MutableMapping[str, Any], keep_ids: Sequence[str]) -> None:
    keep = set(keep_ids)
    for key in list(cache):
        if key not in keep:
            cache.pop(key, None)


# ---------------------------------------------------------------------------
# Live writer patch (the Pi file is not in git; see ops/YOUTUBE_QUEUE.md)
# ---------------------------------------------------------------------------

_PRUNE_START = "# shorts-prune hotfix 2026-10-07: prune any Short already on the playlist.\n"
_PRUNE_END = "def _groom_after_gate("
_PRUNE_NEW = '''# Shorts rule (<=180s or YouTube /shorts/ 200). Every pass prunes. See youtube_groom_shorts.
from youtube_groom_shorts import shorts_prune_reason as _yg_shorts_prune_reason


def _shorts_prune_reason(item: dict[str, Any], video: dict[str, Any], cache: dict[str, Any]) -> str:
    del video  # the rule needs only duration_sec and video_id
    return _yg_shorts_prune_reason(item, cache)


'''
_REPLACEMENTS = (
    (
        "longform-call",
        "            candidates, longform_skipped = filter_longform(candidates, yt, log)\n",
        "            candidates, longform_skipped = filter_longform(\n"
        "                candidates, yt, log, probe_cache=state.setdefault(\"shorts_probe_adds\", {})\n"
        "            )\n",
    ),
    (
        "fallback-known-long",
        "                    and dur >= 60\n",
        "                    and dur > 180\n",
    ),
    (
        "fallback-why",
        "                    why = \"short<60s\" if isinstance(dur, int) and not isinstance(dur, bool) and 0 < dur < 60 else \"duration-unknown\"\n",
        "                    why = \"short<=180s\" if isinstance(dur, int) and not isinstance(dur, bool) and 0 < dur <= 180 else \"duration-unknown\"\n",
    ),
    (
        "fallback-flagged",
        "                    if flagged and not (isinstance(dur, int) and not isinstance(dur, bool) and 0 < dur < 60):\n",
        "                    if flagged and not (isinstance(dur, int) and not isinstance(dur, bool) and 0 < dur <= 180):\n",
    ),
)


def patch_writer_source(text: str, *, parse: bool = True) -> str:
    if _PRUNE_NEW not in text:
        start = text.find(_PRUNE_START)
        end = text.find(_PRUNE_END, start if start >= 0 else 0)
        if start < 0 or end < 0:
            raise PatchError("missing anchor: shorts-prune hotfix block")
        text = text[:start] + _PRUNE_NEW + text[end:]
    for label, old, new in _REPLACEMENTS:
        if new in text:
            continue
        if old not in text:
            raise PatchError(f"missing anchor: {label}")
        text = text.replace(old, new, 1)
    if parse:
        ast.parse(text)
    return text


def patch_writer_file(path: Path, *, backup: bool = True) -> dict[str, Any]:
    original = path.read_text(encoding="utf-8")
    updated = patch_writer_source(original)
    if updated == original:
        return {"changed": False, "path": str(path), "backup": None}
    backup_path = None
    if backup:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_path = path.with_name(f"{path.name}.bak-{stamp}-shorts180")
        shutil.copy2(path, backup_path)
    path.write_text(updated, encoding="utf-8")
    return {"changed": True, "path": str(path), "backup": str(backup_path) if backup_path else None}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--patch-writer", type=Path, help="patch the live writer in place (backup first)")
    parser.add_argument("--check", nargs="*", default=[], metavar="VIDEO_ID", help="probe ids (no quota)")
    args = parser.parse_args(argv)
    if args.patch_writer:
        print(patch_writer_file(args.patch_writer))
        return 0
    for vid in args.check:
        print(vid, probe_shorts_url(vid))
    if not args.check:
        print(f"SHORTS_MAX_SEC={SHORTS_MAX_SEC} reasons={REASON_DURATION},{REASON_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
