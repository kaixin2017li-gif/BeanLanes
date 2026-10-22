"""Tests for the judgement windows.

These are the numbers the whole game rests on, so the boundaries are tested on
both sides: exactly 50 ms has to be PERFECT, 50.001 ms has to be GOOD.
"""

from __future__ import annotations  # modern hint syntax

import sys  # to reach the repo root
import unittest  # the runner
from pathlib import Path  # for the repo root path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the same line in every test file, tests run from any folder

from beatlanes import config  # noqa: E402  # the window numbers
from beatlanes.judge import (  # noqa: E402  # everything under test
    Judgement,  # for the immutability test
    Verdict,  # the expected outcomes
    hold_was_kept,  # week 12: the tail rule
    is_inside_good_window,  # the yes/no helper
    judge_tap,  # the full judgement
    verdict_for_error,  # the window rule
)  # imports done


class VerdictTests(unittest.TestCase):  # the window boundaries
    def test_exact_hit_is_perfect(self):  # zero error is the best case
        self.assertIs(verdict_for_error(0.0), Verdict.PERFECT)  # dead on

    def test_perfect_window_edges_are_inclusive(self):  # exactly on the edge still counts
        self.assertIs(verdict_for_error(config.PERFECT_MS), Verdict.PERFECT)  # 50 ms late
        self.assertIs(verdict_for_error(-config.PERFECT_MS), Verdict.PERFECT)  # 50 ms early

    def test_just_outside_perfect_is_good(self):  # a hair past the edge drops a grade
        self.assertIs(verdict_for_error(config.PERFECT_MS + 0.001), Verdict.GOOD)  # late side
        self.assertIs(verdict_for_error(-(config.PERFECT_MS + 0.001)), Verdict.GOOD)  # early side

    def test_good_window_edges_are_inclusive(self):  # same rule for the wide window
        self.assertIs(verdict_for_error(config.GOOD_MS), Verdict.GOOD)  # 120 ms late
        self.assertIs(verdict_for_error(-config.GOOD_MS), Verdict.GOOD)  # 120 ms early

    def test_beyond_good_is_miss(self):  # past the wide window there is nothing
        self.assertIs(verdict_for_error(config.GOOD_MS + 0.001), Verdict.MISS)  # just over
        self.assertIs(verdict_for_error(-400.0), Verdict.MISS)  # way over

    def test_good_window_helper(self):  # the yes/no version the session uses
        self.assertTrue(is_inside_good_window(config.GOOD_MS))  # on the edge is inside
        self.assertFalse(is_inside_good_window(config.GOOD_MS + 0.001))  # one tick out is not


class JudgeTapTests(unittest.TestCase):  # the full tap flow
    def test_error_is_tap_minus_target(self):  # the core subtraction
        judgement = judge_tap(tap_ms=1000.0, target_ms=1032.0)  # tapped 32 ms early
        self.assertAlmostEqual(judgement.error_ms, -32.0)  # negative means early

    def test_early_tap_has_a_negative_error(self):  # the flag follows the sign
        self.assertTrue(judge_tap(900.0, 1000.0).early)  # 100 ms early

    def test_late_tap_is_not_early(self):  # the other side
        self.assertFalse(judge_tap(1100.0, 1000.0).early)  # 100 ms late

    def test_verdict_comes_from_the_error(self):  # all three windows in one go
        self.assertIs(judge_tap(1010.0, 1000.0).verdict, Verdict.PERFECT)  # 10 ms off, perfect
        self.assertIs(judge_tap(1090.0, 1000.0).verdict, Verdict.GOOD)  # 90 ms off, good
        self.assertIs(judge_tap(1400.0, 1000.0).verdict, Verdict.MISS)  # 400 ms off, miss

    def test_offset_text_reads_the_sign(self):  # the popup text
        self.assertEqual(judge_tap(968.0, 1000.0).offset_text, "-32 ms early")  # early reads minus
        self.assertEqual(judge_tap(1020.0, 1000.0).offset_text, "+20 ms late")  # late reads plus

    def test_judgement_is_immutable(self):  # frozen dataclass, on purpose
        judgement = Judgement(error_ms=10.0, verdict=Verdict.PERFECT)  # a normal one
        with self.assertRaises(Exception):  # changing it must fail
            judgement.error_ms = 20.0  # type: ignore[misc]  # the assignment we expect to blow up



class HoldReleaseTests(unittest.TestCase):  # week 12: the second half of a hold note
    def test_letting_go_on_the_tail_keeps_it(self):  # the ideal release
        self.assertTrue(hold_was_kept(release_ms=3000.0, end_ms=3000.0))  # exactly on the tail

    def test_letting_go_after_the_tail_keeps_it(self):  # holding too long is never punished
        self.assertTrue(hold_was_kept(release_ms=3400.0, end_ms=3000.0))  # well past it

    def test_the_allowance_edge_is_inclusive(self):  # same rule as the judgement windows
        self.assertTrue(hold_was_kept(3000.0 - config.HOLD_RELEASE_MS, 3000.0))  # exactly on the edge still counts
        self.assertFalse(hold_was_kept(3000.0 - config.HOLD_RELEASE_MS - 0.001, 3000.0))  # a hair earlier drops it

    def test_letting_go_halfway_drops_it(self):  # the case the rule is for
        self.assertFalse(hold_was_kept(release_ms=2000.0, end_ms=3000.0))  # a whole second early


if __name__ == "__main__":  # run directly
    unittest.main()  # run the tests
