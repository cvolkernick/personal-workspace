#!/usr/bin/env python3
"""Shorts rule: <=180s or YouTube serves /shorts/<id>. Fixtures only; no network, no API."""

from __future__ import annotations

import ast
import importlib.util
import io
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SH = _load(ROOT / "youtube_groom_shorts.py", "youtube_groom_shorts")


# --- HTTP fixtures ----------------------------------------------------------


def _headers(**kv: str) -> Message:
    msg = Message()
    for k, v in kv.items():
        msg[k] = v
    return msg


class _Resp:
    def __init__(self, code: int, url: str, headers: Message | None = None) -> None:
        self._code = code
        self._url = url
        self.headers = headers or _headers()

    def getcode(self) -> int:
        return self._code

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        pass


class _Opener:
    """Replays a scripted answer per HTTP method and records requests."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.requests: list[tuple[str, str]] = []

    def open(self, req, timeout=None):
        self.requests.append((req.get_method(), req.full_url))
        ans = self.answers[req.get_method()]
        if isinstance(ans, BaseException):
            raise ans
        return ans


def _shorts_200(vid: str) -> _Opener:
    return _Opener({"HEAD": _Resp(200, f"https://www.youtube.com/shorts/{vid}")})


def _redirect_to_watch(vid: str) -> _Opener:
    exc = urllib.error.HTTPError(
        f"https://www.youtube.com/shorts/{vid}",
        303,
        "See Other",
        _headers(Location=f"https://www.youtube.com/watch?v={vid}"),
        io.BytesIO(b""),
    )
    return _Opener({"HEAD": exc})


def _network_error() -> _Opener:
    return _Opener({"HEAD": urllib.error.URLError("timed out")})


class _NoWait:
    def wait(self) -> None:
        pass


NOWAIT = _NoWait()


def _probe_with(opener: _Opener):
    return lambda vid: SH.probe_shorts_url(vid, opener=opener, throttle=NOWAIT)


VID = "AbCdEfGh123"


class TestDurationRule(unittest.TestCase):
    def _never(self, vid):  # duration-only answers must not touch the network
        raise AssertionError(f"probed {vid}")

    def test_59s_is_a_short(self):
        self.assertEqual(SH.shorts_reason(59, VID, probe=self._never), "short<=180s")

    def test_61s_is_a_short(self):
        self.assertEqual(SH.shorts_reason(61, VID, probe=self._never), "short<=180s")

    def test_180s_is_a_short(self):
        self.assertEqual(SH.shorts_reason(180, VID, probe=self._never), "short<=180s")

    def test_181s_is_long_form_when_youtube_says_watch(self):
        self.assertEqual(SH.shorts_reason(181, VID, probe=_probe_with(_redirect_to_watch(VID))), "")

    def test_constants(self):
        self.assertEqual(SH.SHORTS_MAX_SEC, 180)
        self.assertEqual(SH.MIN_LONGFORM_SEC, 181)


class TestShortsUrlProbe(unittest.TestCase):
    def test_shorts_url_200_is_a_short(self):
        opener = _shorts_200(VID)
        self.assertIs(SH.probe_shorts_url(VID, opener=opener, throttle=NOWAIT), True)
        self.assertEqual(opener.requests, [("HEAD", f"https://www.youtube.com/shorts/{VID}")])
        # Over the duration cap but served as a Short: still a Short.
        self.assertEqual(SH.shorts_reason(240, VID, probe=_probe_with(_shorts_200(VID))), "short-url")

    def test_redirect_to_watch_is_not_a_short(self):
        self.assertIs(SH.probe_shorts_url(VID, opener=_redirect_to_watch(VID), throttle=NOWAIT), False)

    def test_followed_redirect_to_watch_is_not_a_short(self):
        opener = _Opener({"HEAD": _Resp(200, f"https://www.youtube.com/watch?v={VID}")})
        self.assertIs(SH.probe_shorts_url(VID, opener=opener, throttle=NOWAIT), False)

    def test_redirect_elsewhere_is_unknown(self):
        exc = urllib.error.HTTPError(
            "u", 302, "Found", _headers(Location="https://consent.youtube.com/m?x=1"), io.BytesIO(b"")
        )
        self.assertIsNone(SH.probe_shorts_url(VID, opener=_Opener({"HEAD": exc}), throttle=NOWAIT))

    def test_head_refused_falls_back_to_get(self):
        exc = urllib.error.HTTPError("u", 405, "Method Not Allowed", _headers(), io.BytesIO(b""))
        opener = _Opener({"HEAD": exc, "GET": _Resp(200, f"https://www.youtube.com/shorts/{VID}")})
        self.assertIs(SH.probe_shorts_url(VID, opener=opener, throttle=NOWAIT), True)
        self.assertEqual([m for m, _ in opener.requests], ["HEAD", "GET"])

    def test_network_error_is_unknown_and_falls_back_to_duration(self):
        self.assertIsNone(SH.probe_shorts_url(VID, opener=_network_error(), throttle=NOWAIT))
        probe = _probe_with(_network_error())
        # Known long duration: the duration rule says long-form.
        self.assertEqual(SH.shorts_reason(600, VID, probe=probe), "")
        # Short duration never needed the probe.
        self.assertEqual(SH.shorts_reason(90, VID, probe=probe), "short<=180s")
        # Unknown duration + unknown probe: unknown (caller decides; prune keeps it).
        self.assertIsNone(SH.shorts_reason(0, VID, probe=probe))
        self.assertIsNone(SH.shorts_reason(None, VID, probe=probe))

    def test_bad_video_id_is_not_fetched(self):
        opener = _shorts_200("x")
        self.assertIsNone(SH.probe_shorts_url("../../etc", opener=opener, throttle=NOWAIT))
        self.assertEqual(opener.requests, [])


class TestCacheBudgetThrottle(unittest.TestCase):
    def test_definite_answers_are_cached_errors_are_not(self):
        cache: dict = {}
        calls: list[str] = []

        def probe(vid):
            calls.append(vid)
            return {"long": False, "short": True}.get(vid)

        SH.shorts_reason(600, "long", cache=cache, probe=probe)
        SH.shorts_reason(600, "short", cache=cache, probe=probe)
        SH.shorts_reason(600, "flaky", cache=cache, probe=probe)
        self.assertEqual(cache, {"long": False, "short": True})
        SH.shorts_reason(600, "long", cache=cache, probe=probe)
        SH.shorts_reason(600, "short", cache=cache, probe=probe)
        self.assertEqual(calls, ["long", "short", "flaky"])

    def test_budget_exhausted_falls_back_to_duration(self):
        budget = SH.ProbeBudget(1)
        probe = lambda vid: True  # noqa: E731
        self.assertEqual(SH.shorts_reason(600, "a", probe=probe, budget=budget), "short-url")
        self.assertEqual(SH.shorts_reason(600, "b", probe=probe, budget=budget), "")

    def test_throttle_spaces_probes(self):
        now = [100.0]
        slept: list[float] = []

        def sleep(s):
            slept.append(s)
            now[0] += s

        th = SH._Throttle(0.25, clock=lambda: now[0], sleep=sleep)
        th.wait()
        th.wait()
        now[0] += 1.0
        th.wait()
        self.assertEqual(slept, [0.25])


class TestPrunePass(unittest.TestCase):
    def test_every_pass_flags_shorts_already_on_the_playlist(self):
        items = [
            {"video_id": "s59", "duration_sec": 59},
            {"video_id": "s61", "duration_sec": 61},
            {"video_id": "s180", "duration_sec": 180},
            {"video_id": "l181", "duration_sec": 181},
            {"video_id": "url200", "duration_sec": 200},
            {"video_id": "neterr", "duration_sec": 900},
            {"video_id": "unknown", "duration_sec": 0},
            {"video_id": "dead", "duration_sec": 30, "dead": True},
        ]
        answers = {"l181": False, "url200": True, "neterr": None, "unknown": None}
        cache: dict = {}
        why = {
            it["video_id"]: SH.shorts_prune_reason(it, cache, probe=lambda v: answers[v])
            for it in items
        }
        self.assertEqual(
            why,
            {
                "s59": "short<=180s",
                "s61": "short<=180s",
                "s180": "short<=180s",
                "l181": "",
                "url200": "short-url",
                "neterr": "",
                "unknown": "",
                "dead": "",
            },
        )


class TestLiveWriterPatch(unittest.TestCase):
    def setUp(self):
        self.text = (FIXTURES / "youtube_groom_live_shorts_excerpt.py.txt").read_text(encoding="utf-8")

    def test_patch_rewires_hotfix_and_is_idempotent(self):
        once = SH.patch_writer_source(self.text)
        self.assertEqual(SH.patch_writer_source(once), once)
        ast.parse(once)
        self.assertNotIn("SHORTS_PRUNE_MIN_SEC", once)
        self.assertNotIn("def _shorts_url_status", once)
        self.assertIn("from youtube_groom_shorts import shorts_prune_reason", once)
        self.assertIn('probe_cache=state.setdefault("shorts_probe_adds", {})', once)
        self.assertIn("and dur > 180\n", once)
        self.assertNotIn("0 < dur < 60", once)
        self.assertIn("def _groom_after_gate(", once)

    def test_drifted_writer_raises(self):
        with self.assertRaises(SH.PatchError):
            SH.patch_writer_source("def _groom_after_gate():\n    pass\n")

    def test_patch_file_backs_up_first(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "youtube_groom.py"
            path.write_text(self.text, encoding="utf-8")
            res = SH.patch_writer_file(path)
            self.assertTrue(res["changed"])
            self.assertEqual(Path(res["backup"]).read_text(encoding="utf-8"), self.text)
            self.assertFalse(SH.patch_writer_file(path)["changed"])

    def test_module_has_no_oauth_or_data_api(self):
        src = (ROOT / "youtube_groom_shorts.py").read_text(encoding="utf-8")
        for banned in ("googleapiclient", "InstalledAppFlow", "token.json", "playlistItems"):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main()
