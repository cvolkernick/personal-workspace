"""Calorie pace green band = ±10% of paced expected (both sides); label agrees."""
import unittest

from rt_dashboard.calorie_bars import calorie_pacing, pace_vs_expected


def _cal(consumed, target=2000, frac=0.5):
    return pace_vs_expected(
        consumed=consumed, target=target, window_fraction=frac, kind="calories"
    )


class CaloriePaceGreen10Tests(unittest.TestCase):
    # target 2000 at 50% of window → paced 1000
    def test_over_boundaries(self):
        self.assertEqual(_cal(1099)["band"], "green")  # 9.9%
        self.assertEqual(_cal(1100)["band"], "green")  # 10.0%
        self.assertEqual(_cal(1101)["band"], "yellow")  # 10.1%
        self.assertEqual(_cal(1200)["band"], "yellow")  # 20.0%
        self.assertEqual(_cal(1201)["band"], "red")  # 20.1%

    def test_under_boundaries(self):
        self.assertEqual(_cal(901)["band"], "green")
        self.assertEqual(_cal(900)["band"], "green")
        self.assertEqual(_cal(899)["band"], "yellow")
        self.assertEqual(_cal(800)["band"], "yellow")
        self.assertEqual(_cal(799)["band"], "red")

    def test_protein_unchanged(self):
        def p(c):
            return pace_vs_expected(
                consumed=c, target=200, window_fraction=0.5, kind="protein"
            )["band"]

        self.assertEqual(p(95), "green")  # −5%
        self.assertEqual(p(94), "yellow")  # −6% (tight under ladder)
        self.assertEqual(p(115), "green")  # +15%
        self.assertEqual(p(116), "yellow")
        self.assertEqual(p(131), "red")

    def test_other_kinds_keep_5pct(self):
        for kind in ("carbs", "fat"):
            r = pace_vs_expected(
                consumed=108, target=200, window_fraction=0.5, kind=kind
            )
            self.assertEqual(r["band"], "yellow", kind)
        hyd = pace_vs_expected(
            consumed=920, target=2000, window_fraction=0.5, kind="hydration"
        )
        self.assertEqual(hyd["band"], "yellow")
        sod = pace_vs_expected(
            consumed=1080, target=2000, window_fraction=0.5, kind="sodium"
        )
        self.assertEqual(sod["band"], "yellow")

    def test_green_iff_on_pace_label(self):
        for target in (1500, 2000, 2800):
            for frac in (0.0, 0.02, 0.03, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0):
                for consumed in range(0, int(target * 1.4), 7):
                    band = _cal(consumed, target, frac)["band"]
                    status = calorie_pacing(
                        consumed=consumed, target=target, window_fraction=frac
                    )["status"]
                    self.assertEqual(
                        band == "green",
                        status in ("on_pace", "start"),
                        (target, frac, consumed, band, status),
                    )

    def test_start_special_case_kept(self):
        self.assertEqual(_cal(0, 2000, 0.01)["band"], "green")
        self.assertEqual(
            calorie_pacing(consumed=0, target=2000, window_fraction=0.01)["status"],
            "start",
        )

    def test_label_matches_at_boundaries(self):
        for c, want in ((1100, "on_pace"), (1101, "ahead"), (900, "on_pace"), (899, "behind")):
            self.assertEqual(
                calorie_pacing(consumed=c, target=2000, window_fraction=0.5)["status"],
                want,
            )

    def test_early_window_small_miss_label_matches_color(self):
        # Early window: paced 100, +40 kcal is 40% rel → red, and label is ahead
        # (no 50 kcal floor any more).
        self.assertEqual(_cal(140, 2000, 0.05)["band"], "red")
        self.assertEqual(
            calorie_pacing(consumed=140, target=2000, window_fraction=0.05)["status"],
            "ahead",
        )


if __name__ == "__main__":
    unittest.main()
