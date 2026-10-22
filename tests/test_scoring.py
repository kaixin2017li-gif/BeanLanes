"""Tests for combo, score and accuracy."""

from __future__ import annotations  # modern hint syntax

import sys  # to reach the repo root
import unittest  # the runner
from pathlib import Path  # for the repo root path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the same line in every test file, tests run from any folder

from beatlanes import config  # noqa: E402  # the point values
from beatlanes.judge import Verdict  # noqa: E402  # the verdicts
from beatlanes.scoring import Score  # noqa: E402  # the thing under test


class ScoreTests(unittest.TestCase):  # the scoring rules
    def test_a_perfect_note_is_full_points(self):  # the best case pays full
        score = Score(1)  # one note in the chart
        score.register(Verdict.PERFECT)  # a perfect
        self.assertEqual(score.points, config.POINTS_PERFECT)  # full points
        self.assertEqual(score.accuracy, 100.0)  # and full accuracy

    def test_a_good_note_is_half_points(self):  # the middle case
        score = Score(1)  # one note
        score.register(Verdict.GOOD)  # a good
        self.assertEqual(score.points, config.POINTS_GOOD)  # half the points
        self.assertEqual(score.accuracy, 50.0)  # half the accuracy

    def test_a_miss_scores_nothing(self):  # the worst case
        score = Score(1)  # one note
        score.register(Verdict.MISS)  # a miss
        self.assertEqual(score.points, 0)  # no points
        self.assertEqual(score.accuracy, 0.0)  # no accuracy

    def test_combo_counts_consecutive_hits(self):  # hits in a row add up
        score = Score(4)  # four notes
        score.register(Verdict.PERFECT)  # first hit
        score.register(Verdict.GOOD)  # second hit
        self.assertEqual(score.combo, 2)  # combo of 2

    def test_a_miss_resets_the_combo_but_keeps_the_best(self):  # the streak breaks, the record stays
        score = Score(4)  # four notes
        for _ in range(3):  # three perfects
            score.register(Verdict.PERFECT)  # one by one
        score.register(Verdict.MISS)  # then a miss
        self.assertEqual(score.combo, 0)  # combo gone
        self.assertEqual(score.best_combo, 3)  # best combo remembers the 3

    def test_early_and_late_taps_are_counted(self):  # the early/late split
        score = Score(3)  # three notes
        score.register(Verdict.PERFECT, early=True)  # tapped early
        score.register(Verdict.GOOD, early=False)  # tapped late
        score.register(Verdict.MISS)  # misses are neither
        self.assertEqual(score.early_taps, 1)  # one early
        self.assertEqual(score.late_taps, 1)  # one late

    def test_stray_taps_are_counted_separately(self):  # strays are their own bucket
        score = Score(1)  # one note, untouched
        score.register_stray()  # press on nothing
        score.register_stray()  # and again
        self.assertEqual(score.stray_taps, 2)  # two strays
        self.assertEqual(score.judged, 0)  # but no notes judged

    def test_progress_and_completion(self):  # the run lifecycle
        score = Score(3)  # three notes
        self.assertEqual(score.remaining, 3)  # all left
        self.assertFalse(score.finished)  # not done
        for _ in range(3):  # judge all three
            score.register(Verdict.PERFECT)  # perfect each
        self.assertTrue(score.finished)  # now done
        self.assertEqual(score.remaining, 0)  # nothing left

    def test_accuracy_over_a_mixed_run(self):  # the accuracy formula over a real mix
        # 21 perfect, 21 good, 6 miss out of 48 notes
        score = Score(48)  # 48 notes
        for _ in range(21):  # 21 perfects
            score.register(Verdict.PERFECT)  # full points each
        for _ in range(21):  # 21 goods
            score.register(Verdict.GOOD)  # half points each
        for _ in range(6):  # 6 misses
            score.register(Verdict.MISS)  # almost nothing
        self.assertAlmostEqual(score.accuracy, 65.625, places=3)  # matches the hand calculation

    def test_rank_thresholds(self):  # each cut-off, top to bottom
        cases = [(48, 48, "S"), (45, 48, "A"), (40, 48, "B"), (33, 48, "C"), (10, 48, "D")]  # perfect count, total, expected rank
        for perfect, total, expected in cases:  # build each case
            score = Score(total)  # the chart size
            for _ in range(perfect):  # the perfects
                score.register(Verdict.PERFECT)  # first the hits
            for _ in range(total - perfect):  # the rest miss
                score.register(Verdict.MISS)  # one by one
            self.assertEqual(score.rank, expected, f"{perfect}/{total}")  # the rank must match

    def test_an_empty_chart_does_not_divide_by_zero(self):  # the empty chart guard
        score = Score(0)  # no notes
        self.assertEqual(score.accuracy, 0.0)  # zero, not a crash
        self.assertTrue(score.finished)  # and trivially finished

    def test_summary_mentions_the_totals(self):  # the summary line
        score = Score(2)  # two notes
        score.register(Verdict.PERFECT)  # one perfect
        score.register(Verdict.MISS)  # one miss
        summary = score.summary()  # build the text
        self.assertIn("PERFECT 1", summary)  # the perfect shows
        self.assertIn("MISS 1", summary)  # the miss shows


if __name__ == "__main__":  # run directly
    unittest.main()  # run the tests
