#!/usr/bin/env python3
"""Weekly X (Twitter) account analytics for @ChrisVolkernick.

Ported from the OpenClaw workspace script (``x-analytics/twitter_metrics.py``)
so the weekly job can run from a plain systemd user timer, with no gateway and
no LLM.  See ``scripts/x-analytics/README.md`` for install / rollback.

What it does
  * Reads the account's public metrics, the 20 most recent tweets and a sample
    of 100 followers (verified count) from the X API v2 (OAuth 1.0a).
  * Prints a human-readable report plus one machine-readable JSON line.
  * Writes the same report and a JSON snapshot to the output directory, and
    prints the change in the headline numbers vs the previous snapshot.

Credentials
  Read only from the environment or ``~/.secrets/x-api.env`` (KEY=VALUE):
  X_CONSUMER_KEY, X_CONSUMER_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET.
  Values are never printed or logged.  Any text that leaves this script through
  ``emit()`` is scrubbed of the credential values first, and API error bodies
  are never printed (only the HTTP status and the error ``title``).

Output directory (first match wins)
  ``--output-dir PATH``, ``$X_ANALYTICS_OUTPUT_DIR``, ``~/x-analytics-output``.
  Files: ``YYYY-MM-DD.json`` (snapshot), ``YYYY-MM-DD.txt`` (report),
  ``latest.json`` (copy of the newest snapshot).

Exit codes
  0  everything fetched and written
  1  fatal: missing credentials, or the account lookup failed (no snapshot)
  2  partial: account metrics fetched but tweets and/or followers failed
     (snapshot written with an ``errors`` list)

``--check-config`` only confirms credentials load (no network, no values).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from requests_oauthlib import OAuth1

USERNAME = "ChrisVolkernick"
BASE_URL = "https://api.twitter.com/2"
HTTP_TIMEOUT = 30

X_ENV_FILE = os.path.expanduser("~/.secrets/x-api.env")
X_ENV_VARS = ("X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
DEFAULT_OUTPUT_DIR = "~/x-analytics-output"
OUTPUT_DIR_ENV = "X_ANALYTICS_OUTPUT_DIR"

EXIT_OK, EXIT_FATAL, EXIT_PARTIAL = 0, 1, 2

# Credential values to scrub from anything we emit. Filled by configure_secrets().
_SECRETS: tuple[str, ...] = ()


def configure_secrets(creds: dict[str, str]) -> None:
    """Remember credential values so emit() can scrub them (longest first)."""
    global _SECRETS
    _SECRETS = tuple(sorted({v for v in creds.values() if v}, key=len, reverse=True))


def scrub(text: str) -> str:
    for secret in _SECRETS:
        text = text.replace(secret, "[redacted]")
    return text


def emit(message: str = "", *, err: bool = False) -> None:
    """The only way this script writes to stdout/stderr."""
    print(scrub(message), file=sys.stderr if err else sys.stdout)


def load_x_creds(path: str = X_ENV_FILE) -> dict[str, str]:
    """X API creds from the environment, else ``path`` (KEY=VALUE). Never print them.

    Raises SystemExit naming only the *missing keys* (never values)."""
    creds = {k: os.environ.get(k, "") for k in X_ENV_VARS}
    if not all(creds.values()) and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip().removeprefix("export ").strip()
                if key in creds and not creds[key]:
                    creds[key] = value.strip().strip("'\"")
    missing = [k for k, v in creds.items() if not v]
    if missing:
        raise SystemExit(f"missing X credentials: {', '.join(missing)} (set them or fill {path})")
    return creds


def make_auth(creds: dict[str, str]) -> OAuth1:
    return OAuth1(
        creds["X_CONSUMER_KEY"],
        client_secret=creds["X_CONSUMER_SECRET"],
        resource_owner_key=creds["X_ACCESS_TOKEN"],
        resource_owner_secret=creds["X_ACCESS_TOKEN_SECRET"],
    )


def _error_title(response: requests.Response) -> str:
    """Short, body-free description of an API error: the JSON ``title`` only."""
    try:
        title = response.json().get("title", "")
    except Exception:  # not JSON / not a dict
        title = ""
    title = re.sub(r"\s+", " ", str(title)).strip()[:80]
    return title or "no title"


def api_get(auth: OAuth1, path: str, params: dict, errors: list[str], what: str):
    """GET ``BASE_URL + path``. Returns parsed JSON on HTTP 200, else None.

    On failure prints only the status and error title (never the response body
    or exception text, which could echo request details) and appends to errors."""
    try:
        response = requests.get(f"{BASE_URL}{path}", auth=auth, params=params, timeout=HTTP_TIMEOUT)
    except requests.RequestException as exc:
        emit(f"Error: {what}: request failed ({type(exc).__name__})", err=True)
        errors.append(f"{what}: request failed ({type(exc).__name__})")
        return None
    if response.status_code == 200:
        try:
            return response.json()
        except ValueError:
            emit(f"Error: {what}: HTTP 200 but response was not JSON", err=True)
            errors.append(f"{what}: invalid JSON")
            return None
    title = _error_title(response)
    emit(f"Error: {what}: HTTP {response.status_code} - {title}", err=True)
    errors.append(f"{what}: HTTP {response.status_code} {title}")
    return None


def get_user_by_username(auth, username, errors):
    """Get user ID and metrics by username."""
    return api_get(
        auth,
        f"/users/by/username/{username.replace('@', '')}",
        {"user.fields": "public_metrics,verified,created_at,description"},
        errors,
        "user lookup",
    )


def get_user_tweets(auth, user_id, errors, max_results=10):
    """Get recent tweets for a user."""
    return api_get(
        auth,
        f"/users/{user_id}/tweets",
        {
            "max_results": max_results,
            "tweet.fields": "public_metrics,created_at,text",
            "expansions": "author_id",
        },
        errors,
        "recent tweets",
    )


def get_followers(auth, user_id, errors, max_results=100):
    """Get followers list with verification status."""
    return api_get(
        auth,
        f"/users/{user_id}/followers",
        {
            "max_results": min(max_results, 100),
            "user.fields": "public_metrics,verified,username,name",
        },
        errors,
        "followers",
    )


def calculate_engagement_rate(tweet):
    """Calculate engagement rate for a tweet."""
    metrics = tweet.get("public_metrics", {})
    impressions = metrics.get("impression_count", 0)
    engagements = (
        metrics.get("like_count", 0)
        + metrics.get("retweet_count", 0)
        + metrics.get("reply_count", 0)
        + metrics.get("bookmark_count", 0)
        + metrics.get("quote_count", 0)
    )
    if impressions > 0:
        return (engagements / impressions) * 100
    return 0


def resolve_output_dir(cli_value: str | None = None) -> Path:
    raw = cli_value or os.environ.get(OUTPUT_DIR_ENV) or DEFAULT_OUTPUT_DIR
    return Path(os.path.expanduser(raw))


def latest_snapshot(out_dir: Path, today: str) -> dict | None:
    """Newest ``YYYY-MM-DD.json`` snapshot dated before ``today``."""
    candidates = sorted(
        p for p in out_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json") if p.stem < today
    )
    for path in reversed(candidates):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("metrics"), dict):
            data["_snapshot_date"] = path.stem
            return data
    return None


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def build_report(now: datetime, user, tweets_data, followers_data, previous):
    """Return (report_lines, snapshot_dict). Pure: no I/O."""
    lines: list[str] = []
    metrics = user.get("public_metrics", {})

    lines += [
        f"=== X Account Analysis for @{USERNAME} ===",
        f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "--- CURRENT METRICS ---",
        f"Followers: {metrics.get('followers_count', 0):,}",
        f"Following: {metrics.get('following_count', 0):,}",
        f"Tweets: {metrics.get('tweet_count', 0):,}",
        f"Listed: {metrics.get('listed_count', 0):,}",
        f"Verified: {user.get('verified', False)}",
        "",
    ]

    averages = None
    if tweets_data and "data" in tweets_data and tweets_data["data"]:
        tweets = tweets_data["data"]
        lines.append("--- TOP PERFORMING RECENT TWEETS ---")
        sorted_tweets = sorted(tweets, key=calculate_engagement_rate, reverse=True)[:5]
        for i, tweet in enumerate(sorted_tweets, 1):
            m = tweet.get("public_metrics", {})
            full = tweet.get("text", "")
            text = full[:80] + "..." if len(full) > 80 else full
            lines.append(f"{i}. {text}")
            lines.append(
                f"   Likes: {m.get('like_count', 0)} | Retweets: {m.get('retweet_count', 0)}"
                f" | Replies: {m.get('reply_count', 0)} | Impressions: {m.get('impression_count', 0):,}"
            )
            lines.append("")

        def total(key):
            return sum(t.get("public_metrics", {}).get(key, 0) for t in tweets)

        n = len(tweets)
        averages = {
            "impressions": total("impression_count") // n,
            "likes": total("like_count") // n,
            "retweets": total("retweet_count") // n,
            "replies": total("reply_count") // n,
        }
        lines += [
            f"--- WEEKLY AVERAGES (Last {n} tweets) ---",
            f"Avg Impressions: {averages['impressions']:,}",
            f"Avg Likes: {averages['likes']}",
            f"Avg Retweets: {averages['retweets']}",
            f"Avg Replies: {averages['replies']}",
            "",
        ]

    verified_followers: list[str] = []
    if followers_data and "data" in followers_data:
        for follower in followers_data["data"]:
            if follower.get("verified", False):
                verified_followers.append(follower.get("name", follower.get("username")))
    verified_count = len(verified_followers)
    lines += [
        "--- VERIFIED FOLLOWERS ---",
        f"Verified followers (sample of 100): {verified_count}",
    ]
    if verified_followers:
        lines.append(f"Sample verified followers: {', '.join(str(v) for v in verified_followers[:10])}")
    lines.append("")

    snapshot = {
        "username": USERNAME,
        "timestamp": now.astimezone().isoformat(),
        "metrics": metrics,
        "verified_sample_count": verified_count,
    }
    if averages is not None:
        snapshot["tweet_averages"] = averages

    if previous:
        lines.append(f"--- CHANGE VS PREVIOUS SNAPSHOT ({previous['_snapshot_date']}) ---")
        for key, label in (
            ("followers_count", "Followers"),
            ("following_count", "Following"),
            ("tweet_count", "Tweets"),
            ("listed_count", "Listed"),
        ):
            cur, old = metrics.get(key), previous["metrics"].get(key)
            if isinstance(cur, int) and isinstance(old, int):
                lines.append(f"{label}: {cur - old:+,} ({old:,} -> {cur:,})")
        old_avg = previous.get("tweet_averages")
        if averages and isinstance(old_avg, dict) and isinstance(old_avg.get("impressions"), int):
            d = averages["impressions"] - old_avg["impressions"]
            lines.append(f"Avg Impressions (last tweets): {d:+,}")
        if isinstance(previous.get("verified_sample_count"), int):
            lines.append(
                f"Verified followers in sample: {verified_count - previous['verified_sample_count']:+d}"
            )
        lines.append("")
    else:
        lines += ["--- CHANGE VS PREVIOUS SNAPSHOT ---", "No previous snapshot found.", ""]
    return lines, snapshot


def run(output_dir: Path, creds: dict[str, str] | None = None, now: datetime | None = None) -> int:
    creds = creds if creds is not None else load_x_creds()
    configure_secrets(creds)
    auth = make_auth(creds)
    now = now or datetime.now()
    errors: list[str] = []

    user_data = get_user_by_username(auth, USERNAME, errors)
    if not user_data or "data" not in user_data:
        emit("Failed to fetch user data", err=True)
        return EXIT_FATAL
    user = user_data["data"]
    user_id = user["id"]

    tweets_data = get_user_tweets(auth, user_id, errors, max_results=20)
    followers_data = get_followers(auth, user_id, errors, max_results=100)

    today = now.strftime("%Y-%m-%d")
    previous = None
    try:
        previous = latest_snapshot(output_dir, today)
    except OSError:
        pass
    lines, snapshot = build_report(now, user, tweets_data, followers_data, previous)
    if errors:
        snapshot["errors"] = errors
        lines += ["--- ERRORS ---"] + [f"- {e}" for e in errors] + [""]
    json_line = json.dumps(snapshot)
    lines += ["--- JSON OUTPUT ---", json_line]
    report = "\n".join(lines)
    emit(report)

    try:
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _write_private(output_dir / f"{today}.json", scrub(json.dumps(snapshot, indent=2)) + "\n")
        _write_private(output_dir / f"{today}.txt", scrub(report) + "\n")
        _write_private(output_dir / "latest.json", scrub(json.dumps(snapshot, indent=2)) + "\n")
    except OSError as exc:
        emit(f"Error: could not write output to {output_dir} ({type(exc).__name__})", err=True)
        return EXIT_FATAL
    return EXIT_PARTIAL if errors else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Weekly X analytics for @" + USERNAME)
    parser.add_argument("--output-dir", help=f"default: ${OUTPUT_DIR_ENV} or {DEFAULT_OUTPUT_DIR}")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="confirm credentials load; no network, no values printed",
    )
    args = parser.parse_args(argv)
    try:
        creds = load_x_creds()
    except SystemExit as exc:
        emit(str(exc.code), err=True)
        return EXIT_FATAL
    configure_secrets(creds)
    if args.check_config:
        emit(str({k: bool(v) for k, v in creds.items()}))
        return EXIT_OK
    return run(resolve_output_dir(args.output_dir), creds)


if __name__ == "__main__":
    sys.exit(main())
