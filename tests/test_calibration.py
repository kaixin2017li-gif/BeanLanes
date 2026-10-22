"""Tests for the tap-to-metronome calibration."""

from __future__ import annotations  # modern hint syntax

import json  # to read and write the json
import sys  # to reach the repo root
import tempfile  # for the temp storage tests
import unittest  # the runner
from pathlib import Path  # for the temp paths

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the same line in every test file, tests run from any folder

from beatlanes import config  # noqa: E402  # the tolerance rule
from beatlanes.calibration import (  # noqa: E402  # everything under test
    CalibrationRun,  # the run itself
    load_calibration,  # reading the file back
    save_calibration,  # writing the file
)  # imports done


class CalibrationRunTests(unittest.TestCase):  # the tap filtering rules
    def setUp(self):  # fresh run for every test
        self.run = CalibrationRun(beats=8, beat_ms=500.0, tolerance_ms=150.0)  # a small fixed run

    def test_taps_land_on_the_clicks(self):  # the click grid
        self.assertEqual(self.run.beat_time_ms(3), 1500.0)  # click 3 is at 1500
        self.assertEqual(self.run.nearest_beat_index(1520.0), 3)  # 1520 rounds to click 3

    def test_a_tap_records_its_offset_from_the_click(self):  # a good tap is kept
        offset = self.run.register_tap(1480.0)  # 20 ms early
        self.assertAlmostEqual(offset, -20.0)  # the offset is -20
        self.assertEqual(self.run.tap_count, 1)  # one usable tap

    def test_a_tap_far_from_any_click_is_ignored(self):  # the tolerance filter
        self.assertIsNone(self.run.register_tap(1700.0))  # 200 ms off, no click owns it
        self.assertEqual(self.run.tap_count, 0)  # nothing kept
        self.assertEqual(self.run.skipped, 1)  # one ignored

    def test_a_tap_outside_the_run_is_ignored(self):  # outside the run entirely
        self.assertIsNone(self.run.register_tap(9000.0))  # past the last click
        self.assertEqual(self.run.skipped, 1)  # counted as skipped

    def test_the_saved_value_is_the_median(self):  # the median beats the mean here
        # a distracted tap that is still inside the tolerance: the mean moves,
        # the median does not, which is why the median is what gets saved
        for offset in (10.0, 12.0, 8.0, 120.0):  # three normal taps and one wild one
            self.run.register_tap(offset)  # fed in one by one
        self.assertAlmostEqual(self.run.median_offset_ms, 11.0)  # median stays at 11
        self.assertGreater(self.run.mean_offset_ms, 30.0)  # the mean got dragged past 30
        self.assertAlmostEqual(self.run.offset_ms, 11.0)  # and the median is what gets saved

    def test_spread_reports_consistency(self):  # steady taps, zero spread
        for offset in (0.0, 0.0, 0.0):  # three identical taps
            self.run.register_tap(offset)  # one by one
        self.assertEqual(self.run.spread_ms, 0.0)  # no spread at all

    def test_not_finished_until_enough_taps_arrive(self):  # the minimum tap rule
        # eight beats, so at least six have to land before the number is trusted
        self.assertFalse(self.run.finished)
        for offset in (-5.0, -4.0, -6.0, 0.0, 3.0):  # five usable taps
            self.run.register_tap(offset)  # still short
        self.assertFalse(self.run.finished)  # not enough yet
        self.run.register_tap(2.0)  # the sixth tap
        self.assertTrue(self.run.finished)  # now trusted

    def test_no_taps_means_no_offset(self):  # no taps, no numbers
        self.assertIsNone(self.run.offset_ms)  # no median
        self.assertIsNone(self.run.mean_offset_ms)  # no mean
        self.assertIn("no usable taps", self.run.summary())  # and the summary says so

    def test_the_tolerance_has_to_stay_under_half_a_beat(self):  # the filter would be useless otherwise
        # otherwise every tap is within tolerance of some click and the filter
        # does nothing at all
        self.assertLess(config.CALIBRATION_TOLERANCE_MS, config.CALIBRATION_BEAT_MS / 2.0)  # 180 must stay below 250

    def test_beat_phase_runs_from_zero_to_one(self):  # the phase the circle pulses with
        self.assertAlmostEqual(self.run.beat_phase(0.0), 0.0)  # at the click
        self.assertAlmostEqual(self.run.beat_phase(250.0), 0.5)  # halfway
        self.assertAlmostEqual(self.run.beat_phase(500.0), 0.0)  # next click


class StorageTests(unittest.TestCase):  # the json file side
    def setUp(self):  # fresh temp dir per test
        self._tmp = tempfile.TemporaryDirectory()  # a throwaway folder
        self.path = Path(self._tmp.name) / "calibration.json"  # the file inside it

    def tearDown(self):  # clean up the temp dir
        self._tmp.cleanup()  # gone

    def test_round_trip(self):  # save then load
        save_calibration(-37.5, taps=14, path=self.path)  # write it
        self.assertAlmostEqual(load_calibration(self.path), -37.5)  # read it back, same number

    def test_saved_file_explains_itself(self):  # the file documents its own offset
        save_calibration(12.0, taps=16, path=self.path)  # write one
        payload = json.loads(self.path.read_text(encoding="utf-8"))  # read the raw json
        self.assertEqual(payload["measured_from_taps"], 16)  # the tap count is in there
        self.assertIn("tick clock", payload["note"])  # and the note explains the clock

    def test_a_missing_file_means_no_offset(self):  # no file is not an error
        self.assertEqual(load_calibration(self.path), 0.0)  # defaults to zero

    def test_a_broken_file_means_no_offset(self):  # rubbish in, zero out
        self.path.write_text("not json at all", encoding="utf-8")  # not json at all
        self.assertEqual(load_calibration(self.path), 0.0)  # still zero

    def test_a_file_without_the_key_means_no_offset(self):  # wrong shape, zero out
        self.path.write_text(json.dumps({"something": "else"}), encoding="utf-8")  # valid json, wrong key
        self.assertEqual(load_calibration(self.path), 0.0)  # zero again


if __name__ == "__main__":  # run directly
    unittest.main()  # run the tests
