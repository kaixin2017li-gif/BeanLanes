"""Tests for the game's key handling and screen changes.

Written in week 11 after a run on a Windows machine with a Chinese input method
switched on: the first notes scored, then nothing did, and on the results
screen R and T were dead while ESC still quit. The cause was the input method
eating letter keys, not the state machine, and these tests pin both halves:
the screen changes work when the keys arrive, and text input is switched off so
that they do arrive.

The window is SDL's dummy driver, so nothing opens on screen, and the events
are handed straight to the handler instead of going through SDL's queue.
"""

from __future__ import annotations  # modern hint syntax

import os  # the SDL driver names
import sys  # to reach the repo root
import tempfile  # the tap log goes somewhere disposable
import unittest  # the runner
from pathlib import Path  # for the temp paths
from unittest import mock  # to hand events in and to spy on stop_text_input

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the same line in every test file, tests run from any folder
os.environ["SDL_VIDEODRIVER"] = "dummy"  # no window
os.environ["SDL_AUDIODRIVER"] = "dummy"  # no sound

import pygame  # noqa: E402  # after the driver names, or a window would open

from beatlanes import config  # noqa: E402  # the chart paths
from beatlanes.app import STATE_COUNTDOWN, STATE_GUIDE, STATE_PLAY, STATE_RESULTS, STATE_TITLE, BeatLanesApp, key_name_for  # noqa: E402  # the thing under test
from beatlanes.session import NoteState  # noqa: E402  # to look at a hold's state


def key_event(kind: int, key: int, scancode: int = 0):  # a key event like SDL would make
    return pygame.event.Event(kind, key=key, scancode=scancode, mod=0, unicode="")  # the fields the handler reads


class AppTests(unittest.TestCase):  # one app per test, on a clock the test controls
    def setUp(self) -> None:  # a fresh app
        self.now = 0  # the fake tick reading, in ms
        self.folder = tempfile.TemporaryDirectory()  # for the tap log
        self.app = BeatLanesApp(  # a plain, non-automatic app
            enable_audio=False,  # no mixer
            ticks_provider=lambda: self.now,  # the test moves time
            mixer_pos_provider=lambda: -1,  # no music
            latency_offset_ms=0.0,  # no calibration file involved
            log_path=Path(self.folder.name) / "taps.csv",  # disposable
        )  # app built
        self.app._setup()  # opens the dummy window

    def tearDown(self) -> None:  # close everything
        self.app._teardown()  # log closed, pygame shut down
        self.folder.cleanup()  # temp folder gone

    def feed(self, *events) -> None:  # run these events through the real handler
        with mock.patch("pygame.event.get", return_value=list(events)):  # instead of SDL's queue
            self.app._handle_events()  # the code under test

    def press(self, key: int, scancode: int = 0) -> None:  # one key down
        self.feed(key_event(pygame.KEYDOWN, key, scancode))  # through the handler

    def play_to_the_end(self) -> None:  # from the title screen to the results screen
        self.press(pygame.K_SPACE)  # start
        self.now += 60_000  # well past the last note
        self.app._update()  # countdown ends
        self.app._update()  # every note missed, the run ends

    def test_space_starts_and_a_full_run_reaches_the_results(self):  # the basic path
        self.press(pygame.K_SPACE)  # start
        self.assertEqual(self.app.state, STATE_COUNTDOWN)  # the 3, 2, 1 first
        self.now += 60_000  # past the end
        self.app._update()  # countdown over
        self.assertEqual(self.app.state, STATE_PLAY)  # into the song
        self.app._update()  # notes missed, run over
        self.assertEqual(self.app.state, STATE_RESULTS)  # the end screen

    def test_r_on_the_results_screen_plays_again(self):  # part of the week 11 report
        self.play_to_the_end()  # results showing
        self.press(pygame.K_r)  # retry
        self.assertEqual(self.app.state, STATE_COUNTDOWN)  # a new run started

    def test_t_on_the_results_screen_goes_to_the_title(self):  # the other half of the report
        self.play_to_the_end()  # results showing
        self.press(pygame.K_t)  # title
        self.assertEqual(self.app.state, STATE_TITLE)  # back home

    def test_a_letter_with_no_key_code_is_found_by_its_position(self):  # the scancode fallback
        self.play_to_the_end()  # results showing
        self.press(pygame.K_UNKNOWN, pygame.KSCAN_R)  # the layout gave no name, the key position is R
        self.assertEqual(self.app.state, STATE_COUNTDOWN)  # still read as R

    def test_the_layout_name_wins_over_the_position(self):  # the fallback only fills gaps
        self.assertEqual(key_name_for(key_event(pygame.KEYDOWN, pygame.K_d, pygame.KSCAN_F)), "d")  # name says d, so d

    def test_text_input_is_switched_off_at_setup_and_on_focus(self):  # the IME fix itself
        with mock.patch("pygame.key.stop_text_input") as stop:  # spy on it
            self.app._setup()  # setup again, now watched
            self.assertEqual(stop.call_count, 1)  # switched off when the window opens
            self.feed(pygame.event.Event(pygame.WINDOWFOCUSGAINED))  # the player clicks back in
            self.assertEqual(stop.call_count, 2)  # switched off again, SDL turns it on with focus
            self.feed(pygame.event.Event(pygame.TEXTEDITING, text="d", start=0, length=1))  # an input method composing
            self.assertEqual(stop.call_count, 3)  # and again whenever it shows up anyway

    def test_every_lane_press_lights_its_key(self):  # feedback even with no note to hit
        self.press(pygame.K_SPACE)  # start
        self.now += 6_000  # inside the countdown, no note near the line
        self.press(pygame.K_d)  # lane 0
        self.assertIn(0, self.app.presses)  # the key lights up
        self.assertIn(0, self.app.held)  # and stays lit while held
        self.feed(key_event(pygame.KEYUP, pygame.K_d, pygame.KSCAN_D))  # let go
        self.assertNotIn(0, self.app.held)  # the key cap rises again

    def test_losing_focus_releases_held_keys(self):  # the key-up never comes after alt-tab
        self.press(pygame.K_SPACE)  # start
        self.press(pygame.K_f)  # hold lane 1
        self.feed(pygame.event.Event(pygame.WINDOWFOCUSLOST))  # click away
        self.assertEqual(self.app.held, set())  # nothing stuck down

    def test_best_accuracy_is_kept_per_chart(self):  # the title card's BEST
        self.play_to_the_end()  # a run with nothing hit
        title = self.app.chart.title  # scores are per chart
        self.assertEqual(self.app.best_accuracy[title], 0.0)  # the first run sets the best
        self.assertFalse(self.app.new_best)  # nothing to beat the first time
        self.press(pygame.K_r)  # again
        for note in self.app.chart.notes:  # this time hit everything
            self.app.session.register_tap(note.lane, tap_ms=float(note.time_ms))  # dead on
        self.now += 60_000  # past the end
        self.app._update()  # countdown over
        self.app._update()  # run over
        self.assertEqual(self.app.best_accuracy[title], 100.0)  # the new best
        self.assertTrue(self.app.new_best)  # and the results screen says so


    def test_the_digits_only_show_in_the_three_seconds_before_the_first_note(self):  # the stuck-on-3 fix
        # the first note sits at 8 s, so the old gate (remaining > 0) drew the
        # countdown through the whole four bar intro and the digit sat on its
        # 3 clamp for five seconds before the numbers ever moved
        with mock.patch("beatlanes.app.render.draw_countdown") as digits:  # spy on the numbers
            self.press(pygame.K_SPACE)  # start the run, song time is 0
            self.now += 2_000  # two seconds into the intro
            self.app._update()  # a frame at 2 s
            self.app._draw()  # draw that frame
            self.assertEqual(digits.call_count, 0)  # too early: quiet intro, no digit
            self.now += 3_100  # 5.1 s, inside the three second window
            self.app._update()  # a frame at 5.1 s
            self.app._draw()  # draw it
            self.assertEqual(digits.call_count, 1)  # now the digits show
            remaining = digits.call_args[0][1]  # what the renderer was told
            self.assertLessEqual(remaining, 3_000)  # never outside the window again


    # --- week 12: the short guide ------------------------------------------
    def test_g_on_the_title_opens_the_guide_and_g_closes_it(self):  # the way in and the way out
        self.press(pygame.K_g)  # open it
        self.assertEqual(self.app.state, STATE_GUIDE)  # the guide is up
        self.press(pygame.K_g)  # same key again
        self.assertEqual(self.app.state, STATE_TITLE)  # back home

    def test_esc_on_the_guide_goes_back_instead_of_quitting(self):  # ESC quits from the title, not from here
        self.press(pygame.K_g)  # open it
        self.press(pygame.K_ESCAPE)  # back out
        self.assertEqual(self.app.state, STATE_TITLE)  # on the title screen
        self.assertTrue(self.app.running)  # and the game is still running

    def test_the_guide_ignores_the_start_key(self):  # reading the guide should not start a run by accident
        self.press(pygame.K_g)  # open it
        self.press(pygame.K_SPACE)  # space on the guide
        self.assertEqual(self.app.state, STATE_GUIDE)  # still reading
        self.assertIsNone(self.app.session)  # no run was started

    def test_g_is_found_by_its_position_too(self):  # same scancode fallback as the other letters
        self.press(pygame.K_UNKNOWN, pygame.KSCAN_G)  # the layout gave no name
        self.assertEqual(self.app.state, STATE_GUIDE)  # still read as G

    def test_the_guide_draws(self):  # the screen itself, on the dummy window
        self.press(pygame.K_g)  # open it
        self.app._draw()  # one frame
        self.assertEqual(self.app.frame.get_size(), (config.WINDOW_WIDTH, config.WINDOW_HEIGHT))  # drawn on the full frame


class HoldKeyTests(unittest.TestCase):  # week 12: the first time a key-up matters
    """The holds chart's first hold is F (lane 1) from 10000 to 11500 ms."""

    def setUp(self) -> None:  # an app on the holds chart
        self.now = 0  # the fake tick reading, in ms
        self.folder = tempfile.TemporaryDirectory()  # for the tap log
        self.app = BeatLanesApp(  # a plain, non-automatic app
            chart_path=config.CHART_3_PATH,  # the chart with holds
            enable_audio=False,  # no mixer
            ticks_provider=lambda: self.now,  # the test moves time
            mixer_pos_provider=lambda: -1,  # no music
            latency_offset_ms=0.0,  # no calibration file involved
            log_path=Path(self.folder.name) / "taps.csv",  # disposable
        )  # app built
        self.app._setup()  # opens the dummy window
        self.press(pygame.K_SPACE)  # start a run, song time 0
        self.now = 10_000  # the first hold's head
        self.app._update()  # countdown over, the four warm-up taps missed
        self.press(pygame.K_f)  # press the hold on time
        self.hold = self.app.session.notes[4]  # the 10000 ms F hold
        self.assertIs(self.hold.state, NoteState.HOLDING)  # the press started it

    def tearDown(self) -> None:  # close everything
        self.app._teardown()  # log closed, pygame shut down
        self.folder.cleanup()  # temp folder gone

    def feed(self, *events) -> None:  # same helper as above
        with mock.patch("pygame.event.get", return_value=list(events)):  # instead of SDL's queue
            self.app._handle_events()  # the code under test

    def press(self, key: int) -> None:  # one key down
        self.feed(key_event(pygame.KEYDOWN, key))  # through the handler

    def test_letting_go_early_drops_the_hold(self):  # a key-up halfway up the tail
        self.now = 10_600  # 900 ms before the tail
        self.feed(key_event(pygame.KEYUP, pygame.K_f))  # let go
        self.assertIs(self.hold.state, NoteState.MISSED)  # dropped
        self.assertEqual(self.app.session.holds_dropped, 1)  # and counted

    def test_letting_go_near_the_tail_keeps_the_hold(self):  # inside the allowance
        self.now = 11_450  # 50 ms before the tail
        self.feed(key_event(pygame.KEYUP, pygame.K_f))  # let go
        self.assertIs(self.hold.state, NoteState.HIT)  # kept
        self.assertEqual(self.app.session.holds_kept, 1)  # and counted

    def test_holding_through_the_tail_keeps_it_and_bursts_the_key(self):  # no key-up at all
        self.now = 11_500  # the tail reaches the line
        self.app._update()  # one frame
        self.assertIs(self.hold.state, NoteState.HIT)  # kept without a key-up
        self.assertEqual(self.app.flashes.get(1), 11_500)  # the key bursts on the tail

    def test_losing_focus_lets_go_of_the_hold(self):  # alt-tab mid-hold
        self.now = 10_600  # partway through
        self.feed(pygame.event.Event(pygame.WINDOWFOCUSLOST))  # click away
        self.assertIs(self.hold.state, NoteState.MISSED)  # not held while the player is elsewhere
        self.assertEqual(self.app.held, set())  # every key up

    def test_the_hold_draws_while_it_is_held(self):  # the body pinned to the line
        self.now = 10_700  # partway through
        self.app._draw()  # one frame with a held hold on it
        self.assertIn(1, self.app.session.holding)  # still held after drawing


if __name__ == "__main__":  # run directly
    unittest.main()  # run the tests
