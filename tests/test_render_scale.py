"""Tests for drawing at a UI scale, and for the cached-image fade.

Written in week 11 after a screenshot from a Windows laptop at 150%: the game
drew 960x640 and Windows stretched it to 1440x960, so all the text was soft.
The game now draws at the real size. These tests check the layout grows with
the scale, that 1.0 is still the old 960x640, that the window picks a scale
that fits the screen, and that a faded word no longer comes back as a box.
"""

from __future__ import annotations  # modern hint syntax

import os  # the SDL driver names
import sys  # to reach the repo root
import unittest  # the runner
from pathlib import Path  # the repo root

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the same line in every test file, tests run from any folder
os.environ["SDL_VIDEODRIVER"] = "dummy"  # no window
os.environ["SDL_AUDIODRIVER"] = "dummy"  # no sound

import pygame  # noqa: E402  # after the driver names, or a window would open

from beatlanes import config, hidpi, render  # noqa: E402  # the things under test

BACKDROP = (10, 10, 30)  # a colour none of the text uses


class ScaleTests(unittest.TestCase):  # the layout follows UI_SCALE
    @classmethod  # runs once for the whole class, fonts are shared
    def setUpClass(cls) -> None:  # fonts need pygame running
        pygame.init()  # dummy driver, nothing opens
        pygame.display.set_mode((16, 16))  # some pygame calls want a display

    @classmethod  # the pair of setUpClass
    def tearDownClass(cls) -> None:  # leave the next test file a clean pygame
        render.set_ui_scale(1.0)  # back to the design
        pygame.quit()  # done

    def tearDown(self) -> None:  # every test leaves the design scale behind
        render.set_ui_scale(1.0)  # other test files assume 960x640

    def test_design_scale_is_the_old_window(self) -> None:  # 1.0 changes nothing
        render.set_ui_scale(1.0)  # the default
        self.assertEqual((config.WINDOW_WIDTH, config.WINDOW_HEIGHT), (960, 640))  # the old window
        self.assertEqual((config.LANE_WIDTH, config.NOTE_HEIGHT, config.JUDGE_LINE_Y), (120, 20, 520.0))  # the old lanes

    def test_one_and_a_half_matches_a_150_percent_screen(self) -> None:  # the user's laptop
        render.set_ui_scale(1.5)  # Windows at 150%
        self.assertEqual((config.WINDOW_WIDTH, config.WINDOW_HEIGHT), (1440, 960))  # the size Windows used to stretch to
        self.assertEqual(config.JUDGE_LINE_Y, 780.0)  # the line moved with the frame
        self.assertEqual(render.receptor_rect(0).height, 57)  # 38 design px
        self.assertEqual(render.highway_rect().width, int(render.lanes_width()) + 2 * 18)  # padding scaled too

    def test_fonts_render_at_the_scaled_size(self) -> None:  # the point of the whole change
        render.set_ui_scale(1.0)  # design size first
        small = render.font(18).get_height()  # line height at 1x
        render.set_ui_scale(2.0)  # then double
        big = render.font(18).get_height()  # same design size, new pixels
        self.assertGreaterEqual(big, small * 2 - 2)  # real glyphs at twice the size, not a stretched bitmap

    def test_rescaling_does_not_compound(self) -> None:  # scale is always from the design
        render.set_ui_scale(1.5)  # up
        render.set_ui_scale(2.0)  # up again
        self.assertEqual(config.LANE_WIDTH, 240)  # 120 x 2, not 120 x 1.5 x 2
        render.set_ui_scale(1.0)  # and back
        self.assertEqual(config.LANE_WIDTH, 120)  # the design value

    def test_note_positions_follow_the_scale(self) -> None:  # notes still land on the line
        from beatlanes.session import LiveNote  # imported here, only this test needs it

        render.set_ui_scale(1.5)  # a scaled frame
        note = LiveNote(index=0, lane=1, time_ms=4000)  # any note
        self.assertAlmostEqual(note.screen_y(4000.0), config.JUDGE_LINE_Y)  # on the scaled line at its time
        self.assertAlmostEqual(note.screen_y(4000.0 - config.APPROACH_MS), config.NOTE_SPAWN_Y)  # and at the top one approach earlier

    def test_every_screen_draws_at_one_and_a_half(self) -> None:  # nothing crashes on a scaled frame
        render.set_ui_scale(1.5)  # the user's scale
        frame = pygame.Surface((config.WINDOW_WIDTH, config.WINDOW_HEIGHT), 0, 24)  # like the app's frame
        render.draw_title(frame, "A chart", 48, 0.0, anim_ms=1234.0, chart_picker=(True, 0, 2), lane_counts=(12, 12, 12, 12))  # title
        render.draw_calibration(frame, 5, 16, [12.0, -8.0], 12.0, 0.4)  # metronome
        render.draw_countdown(frame, 1500.0)  # 3 2 1
        render.draw_go(frame, 400.0)  # GO
        render.draw_guide(frame, 18.0)  # week 12: the short guide
        self.assertEqual(frame.get_size(), (1440, 960))  # drawn at the real size

    def test_hold_notes_draw_at_one_and_a_half(self) -> None:  # week 12: bodies, tails and the held key
        from beatlanes.chart import Chart, ChartNote  # imported here, only this test needs them
        from beatlanes.session import PlaySession  # same
        from beatlanes.songclock import SongClock  # same

        render.set_ui_scale(1.5)  # the user's scale
        frame = pygame.Surface((config.WINDOW_WIDTH, config.WINDOW_HEIGHT), 0, 24)  # like the app's frame
        notes = (ChartNote(0, 1, 1000, 1500), ChartNote(1, 3, 1800, 2000))  # two holds, one gets pressed
        session = PlaySession(Chart("holds", 120.0, notes), SongClock(ticks_provider=lambda: 0, mixer_pos_provider=lambda: -1))  # no log
        session.register_tap(1, tap_ms=1000.0)  # the first one is held
        render.draw_notes(frame, session, 1600.0)  # one held body pinned to the line, one falling
        render.draw_receptors(frame, session, {1: 1000.0}, {1}, {}, 1600.0)  # the held key lit
        session.update(2500.0)  # first tail lands
        render.draw_results(frame, session, "taps.csv", "holds", new_best=False)  # the five-column stats row
        self.assertEqual(frame.get_size(), (1440, 960))  # drawn at the real size


class FadeTests(unittest.TestCase):  # the PERFECT popup that turned into a green box
    @classmethod  # runs once for the whole class
    def setUpClass(cls) -> None:  # fonts need pygame running
        pygame.init()  # dummy driver
        pygame.display.set_mode((16, 16))  # some pygame calls want a display

    @classmethod  # the pair of setUpClass
    def tearDownClass(cls) -> None:  # leave the next test file a clean pygame
        pygame.quit()  # done

    def corner_after(self, draw) -> tuple[int, int, int]:  # top-left pixel of what was drawn, never a glyph
        surface = pygame.Surface((400, 200))  # a plain backdrop
        surface.fill(BACKDROP)  # known colour
        rect = draw(surface)  # draw once
        return tuple(surface.get_at((rect.x + 1, rect.y + 1)))[:3]  # the colour in the corner

    def test_text_drawn_after_a_fade_is_not_a_box(self) -> None:  # the hint row pulses
        draw = lambda alpha: (lambda s: render.text(s, "START", (50, 50), size=14, bold=True, alpha=alpha))  # same string, same cache entry
        self.corner_after(draw(200))  # faded once
        self.assertEqual(self.corner_after(draw(255)), BACKDROP)  # full strength again, still see-through

    def test_popup_drawn_after_a_fade_is_not_a_box(self) -> None:  # the green slab
        def popup(alpha):  # the judgement word, as draw_judgement_popup draws it once the pop has settled
            return lambda s: render.glow_text(s, "PERFECT", (200, 150), size=34, colour=config.PERFECT_COLOUR, glow=config.PERFECT_COLOUR, align="midbottom", alpha=alpha)  # the same cached stamp at this fade

        self.corner_after(popup(120))  # the first popup fading out
        self.assertEqual(self.corner_after(popup(255)), BACKDROP)  # the next popup, before the fix a solid box


class FitScaleTests(unittest.TestCase):  # the window has to fit on the screen
    def test_150_percent_on_a_tall_screen_keeps_one_and_a_half(self) -> None:  # e.g. 2560x1600
        self.assertEqual(hidpi.fit_scale(1.5, (2560, 1600)), 1.5)  # 1440x960 fits

    def test_150_percent_on_1080p_steps_down_to_fit(self) -> None:  # 960 rows + title bar + taskbar is too tall
        scale = hidpi.fit_scale(1.5, (1920, 1080))  # the common laptop
        self.assertLess(scale, 1.5)  # had to shrink
        self.assertLessEqual(config.DESIGN_HEIGHT * scale, 1080 * hidpi.SCREEN_HEIGHT_SHARE)  # and now it fits
        self.assertGreater(scale, 1.3)  # but only a little

    def test_100_percent_stays_at_the_design(self) -> None:  # nothing changes for most desktops
        self.assertEqual(hidpi.fit_scale(1.0, (1920, 1080)), 1.0)  # 960x640

    def test_never_smaller_than_the_design(self) -> None:  # a tiny screen still gets the full layout
        self.assertEqual(hidpi.fit_scale(1.5, (1024, 600)), 1.0)  # the old size, Windows can deal with the rest

    def test_off_windows_nothing_is_claimed(self) -> None:  # macOS and Linux keep the old behaviour
        if sys.platform == "win32":  # this machine really is Windows
            self.skipTest("only meaningful off Windows")  # the real claim runs in the app
        self.assertEqual(hidpi.claim_dpi_awareness(), 1.0)  # no change


if __name__ == "__main__":  # run this file on its own
    unittest.main()  # the usual runner
