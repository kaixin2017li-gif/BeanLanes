"""Tests for the song clock.

The tick and mixer readings are supplied by small stand-in functions, so this
runs without a sound card and without waiting for real time to pass.
"""

from __future__ import annotations  # modern hint syntax

import sys  # to reach the repo root
import unittest  # the runner
from pathlib import Path  # for the repo root path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the same line in every test file, tests run from any folder

from beatlanes.songclock import SongClock  # noqa: E402  # the thing under test


class SongClockTests(unittest.TestCase):  # the clock rules
    def setUp(self):  # fresh stand-ins for every test
        self.state = {"ticks": 1000, "mixer": -1}  # the fake readings, changed per test
        self.clock = SongClock(  # the clock wired to the fakes
            ticks_provider=lambda: self.state["ticks"],  # ticks come from the dict
            mixer_pos_provider=lambda: self.state["mixer"],  # so does the mixer reading
        )  # clock wired

    def test_before_start_the_song_is_at_zero(self):  # before start there is no song
        self.assertFalse(self.clock.running)  # not running yet
        self.assertEqual(self.clock.song_time_ms(), 0.0)  # sits at zero

    def test_song_time_is_ticks_since_the_music_started(self):  # the basic elapsed maths
        self.clock.start()  # music starts at tick 1000
        self.state["ticks"] = 3500  # jump the clock forward
        self.assertEqual(self.clock.song_time_ms(), 2500.0)  # 3500 - 1000

    def test_start_can_be_pinned_to_a_supplied_tick(self):  # tests can pin the start anywhere
        self.clock.start(at_ticks=5000)  # pretend it started at 5000
        self.state["ticks"] = 6000  # now at 6000
        self.assertEqual(self.clock.song_time_ms(), 1000.0)  # 1000 since the pinned start

    def test_offset_is_subtracted_from_song_time(self):  # the calibration offset shifts everything
        self.clock.latency_offset = 80.0  # an 80 ms offset
        self.clock.start()  # start at 1000
        self.state["ticks"] = 2000  # now at 2000
        self.assertEqual(self.clock.song_time_ms(), 920.0)  # 1000 elapsed minus the 80 offset

    def test_offset_can_be_changed_after_a_calibration_run(self):  # the setter works on a live clock
        self.clock.start()  # running already
        self.clock.latency_offset = -25.0  # change it mid flight
        self.state["ticks"] = 2000  # same tick as before
        self.assertEqual(self.clock.song_time_ms(), 1025.0)  # now the other direction

    def test_mixer_minus_one_means_no_reading(self):  # -1 is pygame's no-sound value
        self.assertEqual(self.clock.mixer_position_ms(), None)  # reads as None
        self.assertIsNone(self.clock.drift_ms())  # so drift is None too

    def test_mixer_reading_is_passed_through(self):  # a real reading passes through
        self.state["mixer"] = 1500  # the mixer says 1500
        self.assertEqual(self.clock.mixer_position_ms(), 1500)  # straight through

    def test_drift_is_the_difference_between_the_two_clocks(self):  # the two-clock comparison
        self.clock.start()  # start at 1000
        self.state["ticks"] = 4000  # tick clock at 4000, so 3000 elapsed
        self.state["mixer"] = 2940  # mixer only at 2940
        self.assertAlmostEqual(self.clock.drift_ms(), 60.0)  # 3000 - 2940

    def test_sample_keeps_both_readings_together(self):  # the pair is stored
        self.clock.start()  # start
        self.state["ticks"] = 3000  # song at 2000
        self.state["mixer"] = 1990  # mixer at 1990
        song_ms, position = self.clock.sample()  # read both at once
        self.assertEqual(song_ms, 2000.0)  # the song half
        self.assertEqual(position, 1990)  # the mixer half
        self.assertEqual(self.clock.samples, [(2000.0, 1990)])  # and the saved pair matches

    def test_starting_again_clears_old_samples(self):  # a restart is a fresh run
        self.clock.start()  # collect one sample
        self.clock.sample()  # then start again
        self.clock.start()  # samples belong to the old run
        self.assertEqual(self.clock.samples, [])  # so the list is empty

    def test_a_broken_mixer_provider_does_not_crash_the_game(self):  # sound must never take the game down
        def explode() -> int:  # a provider that dies
            raise RuntimeError("mixer went away")  # the explosion

        clock = SongClock(ticks_provider=lambda: 0, mixer_pos_provider=explode)  # clock wired to the bad mixer
        self.assertIsNone(clock.mixer_position_ms())  # None, not a crash

    def test_stop_puts_the_clock_back_to_zero(self):  # stop resets everything
        self.clock.start()  # start
        self.state["ticks"] = 9000  # run it forward
        self.clock.stop()  # stop
        self.assertEqual(self.clock.song_time_ms(), 0.0)  # back to zero


if __name__ == "__main__":  # run directly
    unittest.main()  # run the tests
