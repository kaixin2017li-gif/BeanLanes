"""Tests for the prepared run behind --selftest, --autoplay and the captures.

Week 12 gave the prepared run key-ups for hold notes. The presses, the key-ups
and the counts the selftest expects all come from one plan, and these tests
check that the plan did not change anything for the two charts without holds:
their expected numbers are the ones quoted in the reflection.
"""

from __future__ import annotations  # modern hint syntax

import sys  # to reach the repo root
import unittest  # the runner
from pathlib import Path  # for the repo root path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the same line in every test file, tests run from any folder

import main  # noqa: E402  # the entry point, where the plan lives
from beatlanes import config  # noqa: E402  # chart paths and windows
from beatlanes.chart import load_chart  # noqa: E402  # the shipped charts


class SelftestPlanTests(unittest.TestCase):  # the plan and the expectation
    def test_the_generated_chart_still_expects_the_old_numbers(self):  # 48 notes, as quoted in the reflection
        counts = main.expected_counts(load_chart(config.CHART_PATH))  # the generated chart
        self.assertEqual((counts["perfect"], counts["good"], counts["miss"], counts["stray"]), (30, 12, 6, 6))  # unchanged since week 8

    def test_charts_without_holds_get_no_key_ups(self):  # nothing new happens to the old charts
        for path in (config.CHART_PATH, config.CHART_2_PATH):  # the two tap charts
            with self.subTest(chart=path.name):  # say which one failed
                self.assertEqual(main.build_selftest_releases(load_chart(path)), [])  # no key-ups at all

    def test_every_hold_gets_exactly_one_key_up_after_its_press(self):  # the holds chart
        chart = load_chart(config.CHART_3_PATH)  # the week 12 chart
        plan = main.selftest_plan(chart)  # one entry per note
        holds = [(note, entry) for note, entry in zip(chart.notes, plan) if note.is_hold]  # just the holds
        self.assertEqual(len(main.build_selftest_releases(chart)), chart.hold_count)  # one key-up each
        for note, (_lane, press, release) in holds:  # each hold
            self.assertGreater(release, press)  # the key comes up after it went down

    def test_the_holds_chart_plans_both_outcomes(self):  # kept and dropped both get exercised
        counts = main.expected_counts(load_chart(config.CHART_3_PATH))  # the expectation
        self.assertGreater(counts["holds_kept"], 0)  # some held to the tail
        self.assertGreater(counts["holds_dropped"], 0)  # and some let go too early


if __name__ == "__main__":  # run directly
    unittest.main()  # run the tests
