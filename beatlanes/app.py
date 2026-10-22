# The game itself: states, events, the frame loop.
#
# States: TITLE (waiting) -> CALIBRATE (metronome test) -> COUNTDOWN (3,2,1,GO)
# -> PLAY (song running, taps judged) -> RESULTS (run over, log written).
# GUIDE (week 12) is a side trip from the title screen and straight back.
#
# Week 12 also adds hold notes, which is the first time a key-up matters: in
# PLAY a lane key coming up is handed to the session, which decides whether a
# hold in that lane was kept to its tail or dropped.
#
# The loop is deliberately plain: cap the frame rate, read the clock, handle
# events, update the session, draw. Notes are never moved by a per-frame step -
# their positions come from the song clock - so a slow frame changes how smooth
# the game looks, never when a note is judged.

from __future__ import annotations

import os  # to set the SDL driver names for headless runs
from pathlib import Path  # chart, song and log paths come in as Path or str
from typing import Callable, Optional, Sequence  # Callable is the injectable clock, Optional the maybes

import pygame  # window, input, clock

from . import audio, config, hidpi, render  # sound, settings, screen scale, drawing
from .calibration import CalibrationRun, load_calibration, save_calibration  # the metronome test
from .chart import Chart, load_chart  # the chart being played
from .judge import Verdict  # verdict names
from .logbook import TapLog  # the CSV log
from .session import PlaySession  # one play-through
from .songclock import SongClock  # the song clock

STATE_TITLE = "title"  # waiting for the player
STATE_GUIDE = "guide"  # the week 12 short guide
STATE_CALIBRATE = "calibrate"  # the metronome test
STATE_COUNTDOWN = "countdown"  # 3, 2, 1 before the first note
STATE_PLAY = "play"  # the song is running
STATE_RESULTS = "results"  # the run is over

COUNTDOWN_WINDOW_MS = 3000.0  # the digits only cover the three seconds before the first note

# Every key name the game reacts to. Anything else that arrives is checked
# against the physical key positions below before it is ignored.
COMMAND_KEYS = {"escape", "space", "tab", "left", "right", "c", "r", "t", "g"}  # the non-lane keys
KNOWN_KEYS = COMMAND_KEYS | set(config.LANE_KEY_TO_INDEX)  # lanes too

# Physical key positions (scancodes) for the letter keys. pygame.key.name()
# reports what the active keyboard layout calls the key; the scancode is where
# the key sits on the board. On a layout where D F J K are not D F J K the
# name misses, and the position still finds the right lane.
SCANCODE_NAMES = {  # scancode -> the name the rest of the game uses
    pygame.KSCAN_D: "d",  # lane 0
    pygame.KSCAN_F: "f",  # lane 1
    pygame.KSCAN_J: "j",  # lane 2
    pygame.KSCAN_K: "k",  # lane 3
    pygame.KSCAN_C: "c",  # calibrate
    pygame.KSCAN_G: "g",  # guide
    pygame.KSCAN_R: "r",  # retry
    pygame.KSCAN_T: "t",  # title
}  # the letters only, named keys like escape never change with the layout


def key_name_for(event) -> str:  # the name _handle_key should see for one key event
    name = pygame.key.name(event.key).lower()  # what the layout calls it
    if name in KNOWN_KEYS:  # the normal case
        return name  # done
    return SCANCODE_NAMES.get(getattr(event, "scancode", None), name)  # fall back to the key's position


class FakeTicks:  # a tick source that advances by a fixed step per frame
    # song time comes from ticks, so faking the ticks makes a headless run
    # deterministic and independent of the machine's speed

    def __init__(self, step_ms: float) -> None:
        self.value = 0.0  # the fake time, in ms
        self.step_ms = float(step_ms)  # how far it moves per frame

    def __call__(self) -> int:  # the clock interface the rest of the code sees
        return int(self.value)  # int, the rest of the code expects ticks

    def advance(self) -> None:  # called once per frame
        self.value += self.step_ms  # one frame of fake time


class SimulatedTaps:  # feeds a prepared list of taps in at the right song times
    def __init__(self, taps: Sequence[tuple[int, float]]) -> None:  # taps are (lane, song_ms) pairs
        self.taps = sorted(taps, key=lambda item: item[1])  # earliest first
        self.next_index = 0  # which tap is next

    def due(self, song_ms: float) -> list[tuple[int, float]]:  # called once per frame
        # the taps whose time has arrived by now
        ready: list[tuple[int, float]] = []  # what we hand back this frame
        while self.next_index < len(self.taps) and self.taps[self.next_index][1] <= song_ms:  # walk forward while the next tap is due
            ready.append(self.taps[self.next_index])  # take it
            self.next_index += 1  # so it never fires twice
        return ready  # possibly empty, that is normal


class BeatLanesApp:  # the whole game, wired together
    def __init__(  # every knob is a keyword so a test can flip one at a time
        self,  # nothing special, just self
        chart_path: Optional[Path | str] = None,  # chart file
        song_path: Optional[Path | str] = None,  # song file
        latency_offset_ms: Optional[float] = None,  # calibration offset
        ticks_provider: Optional[Callable[[], int]] = None,  # injectable clock
        mixer_pos_provider: Optional[Callable[[], int]] = None,  # injectable mixer
        enable_audio: bool = True,  # False for silent runs
        simulated_taps: Optional[Sequence[tuple[int, float]]] = None,  # automatic run
        simulated_releases: Optional[Sequence[tuple[int, float]]] = None,  # week 12: key-ups for the holds in an automatic run
        screenshot_path: Optional[Path | str] = None,  # save one frame
        screenshot_at_ms: float = 12_000.0,  # when to take it
        screenshot_state: str = STATE_PLAY,  # which screen
        windowed: bool = False,  # keep a real window in an automatic run
        record_path: Optional[Path | str] = None,  # write an mp4
        record_fps: int = config.FPS,  # recording frame rate
        record_scale: int = 1,  # recording pixel scale
        log_path: Optional[Path | str] = None,  # where the CSV goes
        ui_scale: Optional[float] = None,  # None: follow the Windows display scale (1.0 elsewhere)
    ) -> None:  # stores settings, opens nothing yet
        # chart selection: the title screen cycles config.CHARTS with LEFT/RIGHT
        self.chart_paths = list(config.CHARTS)  # the charts the player can pick
        if chart_path is not None:  # an explicit --chart overrides the menu
            self.chart_paths = [Path(chart_path)]  # single-chart mode, the picker hides itself
        self.chart_index = 0  # which entry of chart_paths is selected
        self.chart: Chart = load_chart(self.chart_paths[self.chart_index])  # read and check it
        self.song_path = song_path  # the backing track  # None means the default from config
        self.enable_audio = enable_audio  # False for silent runs
        self.log_path = log_path  # where the tap log goes  # None means the timestamped default
        self.ui_scale = ui_scale  # a forced scale from --scale, or None to ask the screen

        # An automatic run is one nobody sits in front of: taps are supplied, or
        # it only exists to capture a frame. Automatic runs go headless unless
        # --windowed keeps a real window (and real mixer readings) alive.
        self.automatic = bool(simulated_taps) or screenshot_path is not None or record_path is not None
        self.headless = self.automatic and not windowed  # automatic usually means invisible too
        self.screenshot_path = Path(screenshot_path) if screenshot_path else None  # str or Path, stored as Path
        self.record_path = Path(record_path) if record_path else None  # same conversion as the screenshot path
        self.record_fps = int(record_fps)  # footage frame rate
        self.record_scale = max(1, int(record_scale))  # 2 gives 1920x1280 footage
        self._recorder = None  # the ffmpeg process, when recording
        self.screenshot_at_ms = float(screenshot_at_ms)  # when to capture
        self.screenshot_state = screenshot_state  # which screen to capture
        self._screenshot_written = False  # so only one frame is saved

        # headless runs must not open a window or a speaker; setting the driver
        # names before pygame starts is what keeps them quiet
        if self.headless:
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")  # dummy video: no window at all
            os.environ.setdefault("SDL_AUDIODRIVER", "dummy")  # same for sound, both drivers have to be silenced

        self.fake_ticks = FakeTicks(1000.0 / config.FPS) if self.headless else None  # the fake clock
        if ticks_provider is not None:  # an injected clock wins
            ticks = ticks_provider  # use it as-is
        elif self.fake_ticks is not None:  # headless uses the fake one
            ticks = self.fake_ticks  # deterministic time for the run
        else:  # a plain run
            ticks = None  # SongClock falls back to the real pygame clock
        if mixer_pos_provider is not None:  # injected mixer wins
            mixer_pos = mixer_pos_provider  # injected first, same rule as the ticks above
        elif self.headless:  # headless has no mixer at all
            mixer_pos = lambda: -1  # no audio device in these runs
        else:  # anything else: the real mixer
            mixer_pos = None  # use the real mixer  # the real mixer reading in a real run

        if latency_offset_ms is None:  # no offset given: load the saved one
            latency_offset_ms = load_calibration()  # the json saved by the last calibration
        self.latency_offset = float(latency_offset_ms)  # the offset this run uses
        self.clock = SongClock(  # the song clock
            latency_offset_ms=self.latency_offset,  # the offset this run uses
            ticks_provider=ticks,  # real, fake or injected
            mixer_pos_provider=mixer_pos,  # same trio of choices for the mixer
        )  # clock ready

        # run-time state
        self.state = STATE_TITLE  # the game starts on the title screen
        self.running = True  # False ends the loop
        self.exit_code = 0  # what main() returns
        self.countdown_start_ms: Optional[float] = None  # countdown anchor, in song time
        # automatic runs get a frame budget: no player means no one to close the
        # window, so a stall should fail loudly instead of spinning
        self.frame_budget: Optional[int] = 200_000 if self.automatic else None
        self.debug = False  # TAB toggles the debug overlay
        self.session: Optional[PlaySession] = None  # created when a run starts
        self.log: Optional[TapLog] = None  # the CSV log for this run
        self.last_log_path: Optional[Path] = None  # shown on the results screen
        self.auto_taps: Optional[SimulatedTaps] = (  # the feeder for prepared taps
            SimulatedTaps(simulated_taps) if simulated_taps else None  # built now if we have them
        )  # feeder ready
        self.simulated_taps = list(simulated_taps) if simulated_taps else []  # kept for restarts
        # key-ups work the same way as the taps: a sorted list fed in by song
        # time. only holds need them, a tap's key-up never decides anything
        self.simulated_releases = list(simulated_releases) if simulated_releases else []  # kept for restarts too
        self.auto_releases: Optional[SimulatedTaps] = (  # the feeder for prepared key-ups
            SimulatedTaps(self.simulated_releases) if self.simulated_releases else None  # same class, different meaning
        )  # feeder ready
        self.last_accuracy: dict[str, float] = {}  # chart title -> last run's accuracy, shown on the title screen
        self.best_accuracy: dict[str, float] = {}  # chart title -> best accuracy this session
        self.new_best = False  # the results screen shows a NEW BEST pill
        self.status_message = ""  # one-line message under the title panel
        self.flashes: dict[int, float] = {}  # lane -> song time of the last scored hit, drives the burst
        self.presses: dict[int, float] = {}  # lane -> song time of the last press, scored or not, lights the key
        self.held: set[int] = set()  # lanes whose key is down right now

        # calibration state
        self.calibration_run: Optional[CalibrationRun] = None
        self.calibration_last_beat = -1  # so each click only plays once
        self.calibration_last_offset: Optional[float] = None  # shown under the circle

        # pygame objects, created in _setup
        self.screen: Optional[pygame.Surface] = None  # the window
        # everything is drawn onto this offscreen frame first, then blitted to
        # the window. It must have no alpha mask: on macOS the window surface
        # carries one, and screenshots taken from it come out transparent. It
        # was 24-bit for that reason; it is now 32-bit with the alpha mask set
        # to 0, which is just as opaque, and pygame blends translucent sprites
        # onto 32-bit pixels about five times faster. That matters once the
        # frame is 1440x960 on a 150% screen instead of 960x640.
        self.frame: Optional[pygame.Surface] = None
        self.frame_clock = pygame.time.Clock()  # caps the frame rate
        self.click = None  # the metronome click sound
        self.audio_playing = False  # True while the song plays
        self.mixer_ok = False  # False means 'no audio device'

    # --- lifecycle --------------------------------------------------------
    def _setup(self) -> None:  # open the window, and the mixer when wanted
        real_window = os.environ.get("SDL_VIDEODRIVER") != "dummy"  # tests and headless runs have no screen to match
        # must happen before pygame opens the display: Windows decides at the
        # first window whether to stretch it. See hidpi.py for the blur story.
        desktop_scale = hidpi.claim_dpi_awareness() if real_window else 1.0  # 1.5 on a 150% Windows screen
        pygame.init()  # start pygame itself
        if self.ui_scale is not None:  # --scale wins
            scale = self.ui_scale  # as asked
        elif real_window:  # match the screen, but keep the window on it
            sizes = pygame.display.get_desktop_sizes()  # real pixels once the process is DPI aware
            scale = hidpi.fit_scale(desktop_scale, sizes[0] if sizes else (0, 0))  # capped to the first screen
        else:  # headless: the design size, so captures and tests do not depend on the machine
            scale = 1.0  # 960x640
        render.set_ui_scale(scale)  # window, lanes and fonts all follow, caches rebuilt
        pygame.display.set_caption(config.CAPTION)  # the window title
        pygame.display.set_icon(render.make_icon())  # before set_mode, some platforms only read it then
        # no SCALED flag: the frame is already drawn at the screen's real size,
        # so nothing should stretch it again
        self.screen = pygame.display.set_mode((config.WINDOW_WIDTH, config.WINDOW_HEIGHT))  # the window, size from config
        self._disable_text_input()  # see the note on the method, this is the week 11 key fix
        self.frame = pygame.Surface(  # the offscreen frame everything is drawn on
            (config.WINDOW_WIDTH, config.WINDOW_HEIGHT), 0, 32, (0xFF0000, 0x00FF00, 0x0000FF, 0)  # 32-bit, alpha mask 0: opaque
        )  # sized like the window
        if self.enable_audio and not self.headless:  # only real runs open the mixer
            self.mixer_ok = audio.init_mixer()  # may come back False
            if self.mixer_ok:  # the click needs a mixer
                self.click = audio.make_click()  # the calibration metronome
        if not self.mixer_ok:  # tell the player instead of failing
            self.status_message = self.status_message or "no audio device: running on the tick clock only"  # keep any earlier message

    def _teardown(self) -> None:  # stop recording, close the log, shut pygame down
        self._stop_recording()  # ffmpeg first, it is still eating frames
        if self.log is not None:  # flush the CSV
            self.log.close()  # flush the csv to disk
            self.log = None  # and forget it
        audio.stop_song()  # music off
        pygame.quit()  # last, everything pygame owns

    def run(self) -> int:  # the frame loop, until the game ends
        self._setup()  # window and mixer
        if self.record_path is not None:  # start the recorder first
            self._start_recording()  # before the first frame exists
        if self.automatic and self.screenshot_state == STATE_CALIBRATE:  # the calibrate capture wants the metronome
            self._start_calibration()  # go straight to the metronome  # the capture target is the metronome screen
        elif self.automatic and self.screenshot_state == STATE_GUIDE:  # the guide capture wants the guide
            self.state = STATE_GUIDE  # no set up, it is a static screen
        elif self.automatic and self.screenshot_state != STATE_TITLE:  # any other capture starts from play
            self._start_play()  # no player, so skip the title screen  # straight into the song
        try:  # the loop, with cleanup guaranteed
            while self.running:  # one pass is one frame
                if self.frame_budget is not None:  # only automatic runs have one
                    if self.frame_budget <= 0:  # stalled: fail loudly
                        print("automatic run hit its frame budget without finishing")  # loud on purpose
                        self.exit_code = 1  # non-zero so scripts notice
                        break  # stop the loop here
                    self.frame_budget -= 1  # one frame used
                self._cap_frame_rate()  # hold 60 fps in a real run
                self._handle_events()  # keyboard and window events
                self._update()  # game logic
                self._draw()  # draw the frame
                pygame.display.flip()  # show it  # put the finished frame on screen
                if self.fake_ticks is not None:  # move the fake clock
                    self.fake_ticks.advance()  # next frame, next fake time
                self._write_frame()  # feed the recorder
                if self._screenshot_written:  # capture done, that was the point
                    break  # no need to keep looping after the capture
        finally:  # runs even if something raised
            self._teardown()  # always clean up
        return self.exit_code  # 0 or 1

    def _cap_frame_rate(self) -> None:  # hold the frame rate
        # tick(0) means 'do not wait', which is what a headless run wants
        self.frame_clock.tick(0 if self.headless else config.FPS)

    # --- events -----------------------------------------------------------
    def _disable_text_input(self) -> None:  # turn off text input, and with it the input method (IME)
        # The week 11 key bug. pygame 2 starts with text input switched on, and
        # SDL re-enables it every time the window gets keyboard focus. On
        # Windows that attaches the input method to the game window. With a
        # Chinese IME in Chinese mode every letter key is sent to the IME as
        # VK_PROCESSKEY, and SDL swallows those instead of making a KEYDOWN
        # (SDL 2.28, SDL_windowskeyboard.c, IME_HandleMessage). Keys the IME
        # does not want - ESC, SPACE, TAB, the arrows - still arrive. So the
        # run started, D F J K stopped scoring, and on the results screen R and
        # T did nothing while ESC still quit.
        # Stopping text input detaches the IME from the window. The pygame docs
        # (pygame.key.stop_text_input) say the same: text input is on by
        # default and should be switched off when no text is being typed.
        # It only takes effect while the window has keyboard focus, so it is
        # called again on every focus gain as well.
        try:  # never let this stop the game from starting
            pygame.key.stop_text_input()  # detaches the IME, harmless where there is none
        except pygame.error:  # e.g. no video system
            pass  # nothing to switch off

    def _handle_events(self) -> None:  # read the event queue
        for event in pygame.event.get():  # pygame queues them, we drain them
            if event.type == pygame.QUIT:  # the X button
                self.running = False  # ends the loop on the next check
            elif event.type == pygame.WINDOWFOCUSGAINED:  # back in the window
                self._disable_text_input()  # SDL turned text input back on, turn it off again
            elif event.type == pygame.WINDOWFOCUSLOST:  # clicked away
                # the key-ups go to the other window, so forget what was held. a
                # hold that was down is let go now: without this it would be
                # counted as held all the way while the player is somewhere else
                if self.state == STATE_PLAY and self.session is not None:  # only a run has holds
                    for lane in list(self.session.holding):  # a copy, releasing edits it
                        self.session.register_release(lane)  # judged on this moment
                self.held.clear()  # every key cap rises
            elif event.type in (pygame.TEXTEDITING, pygame.TEXTINPUT):  # only an input method makes these now
                self._disable_text_input()  # something switched it back on, switch it off again
            elif event.type == pygame.KEYDOWN:  # keys are the only input
                self._handle_key(key_name_for(event))  # key name, lower case, with the scancode fallback
            elif event.type == pygame.KEYUP:  # a key let go
                lane = config.LANE_KEY_TO_INDEX.get(key_name_for(event))  # was it a lane key?
                if lane is not None:  # yes
                    self.held.discard(lane)  # the key cap rises again
                    if self.state == STATE_PLAY and self.session is not None:  # week 12: a key-up can end a hold
                        self.session.register_release(lane)  # sampled on the clock now; does nothing for a tap

    def _handle_key(self, name: str) -> None:  # one key press, handled by state
        lane = config.LANE_KEY_TO_INDEX.get(name)  # is it a lane key?

        if self.state == STATE_TITLE:  # waiting
            if name == "escape":  # quit
                self.running = False  # same as the X button
            elif name == "space":  # start a run
                self._start_play()  # space means go
            elif name == "c":  # calibration
                self._start_calibration()  # c for calibrate
            elif name == "g":  # the short guide
                self.state = STATE_GUIDE  # a new screen, nothing to set up
            elif name == "left" or name == "right":  # pick a chart
                step = -1 if name == "left" else 1  # left goes back, right goes forward
                self.chart_index = (self.chart_index + step) % len(self.chart_paths)  # modulo wraps around the ends
                self.chart = load_chart(self.chart_paths[self.chart_index])  # load the newly picked one

        elif self.state == STATE_GUIDE:  # the short guide
            if name in ("escape", "g"):  # either key goes back
                self.state = STATE_TITLE  # back to waiting

        elif self.state == STATE_CALIBRATE:  # the metronome test
            if name == "escape":  # back out
                self.state = STATE_TITLE  # back out, nothing saved
            elif lane is not None:  # any lane key counts as a tap
                self._calibration_tap()  # same handler a real run uses

        elif self.state == STATE_COUNTDOWN:  # during the 3, 2, 1
            if name == "escape":  # bail out of the run
                self._finish_play()  # counts as ending the run
            elif name == "tab":  # the overlay can go on before the notes
                self.debug = not self.debug  # toggle
            elif lane is not None:  # warming up on the keys
                self._light_key(lane)  # lights up, nothing is judged yet

        elif self.state == STATE_PLAY:  # the song is running
            if name == "escape":  # end the run early
                self._finish_play()  # same as bailing from the countdown
            elif name == "tab":  # toggle the debug overlay
                self.debug = not self.debug  # toggle
            elif lane is not None:  # a lane key: judge it
                self._light_key(lane)  # every press lights its key, scored or not
                # the mixer is sampled on the same press, so the log has both
                # clock readings for the same moment
                judgement = self.session.register_tap(  # the press goes to the session
                    lane, mixer_pos_ms=self.clock.mixer_position_ms()  # mixer read on this exact press
                )  # a judgement comes back, or None
                if judgement is not None:  # a note was scored: burst on the key
                    self.flashes[lane] = self.clock.song_time_ms()  # render reads this dict for the burst

        elif self.state == STATE_RESULTS:  # the run is over
            if name == "escape":  # quit
                self.running = False  # same quit as the title screen
            elif name == "r":  # play again
                self._start_play()  # r for rematch
            elif name == "t":  # back to the title
                self.state = STATE_TITLE  # t for title

    def _light_key(self, lane: int) -> None:  # a lane key went down: show it
        self.presses[lane] = self.clock.song_time_ms()  # the beam and the key cap fade from here
        self.held.add(lane)  # stays lit until the key-up

    # --- state changes ----------------------------------------------------
    def _start_play(self) -> None:  # begin a run
        self.session = PlaySession(  # fresh note states and score  # fresh everything
            self.chart,  # the picked chart
            self.clock,  # the shared clock
            logger=None,  # attached right after
            calibration_offset_ms=self.latency_offset,  # the saved offset
        )  # session ready
        self.log = TapLog(path=self.log_path, song=self.chart.title, calibration_ms=self.latency_offset)  # a new csv file per run
        self.session.logger = self.log  # the session writes rows through this
        self.auto_taps = SimulatedTaps(self.simulated_taps) if self.simulated_taps else None  # rebuilt so an R restart replays the same taps
        self.auto_releases = SimulatedTaps(self.simulated_releases) if self.simulated_releases else None  # and the same key-ups
        self.held.clear()  # a fresh run starts with every key up
        self.flashes.clear()  # no lanes lit at the start
        self.presses.clear()  # same for the key lights
        self.new_best = False  # nothing beaten yet
        self.audio_playing = bool(self.enable_audio and not self.headless and audio.start_song(self.song_path))  # False means a silent run, the clock still works
        # the clock starts now; the calibration offset is what absorbs the audio
        # buffer and the input path, so the clock does not guess either
        self.clock.start()
        # the countdown shows in real runs and recorded demos, but not in
        # headless test runs - a test should not wait for the numbers
        show_countdown = not self.headless or self.record_path is not None
        if not show_countdown:  # tests should not wait for numbers
            self.countdown_start_ms = None  # no countdown at all
            self.state = STATE_PLAY  # straight into the song
        else:  # a real run or a recording
            # anchored to the first note: 3, 2, 1 land on the three seconds
            # before it, GO when it reaches the judgement line
            first_note_ms = self.chart.notes[0].time_ms if self.chart.notes else 0
            self.countdown_start_ms = max(0.0, first_note_ms - COUNTDOWN_WINDOW_MS)  # clamped so a tiny chart never goes negative
            self.state = STATE_COUNTDOWN  # numbers first, notes after

    def _finish_play(self) -> None:  # end the run, close the log, show results
        if self.session is not None:  # only if a run happened
            accuracy = self.session.score.accuracy  # this run
            title = self.chart.title  # scores are kept per chart
            previous = self.best_accuracy.get(title)  # the one to beat
            self.new_best = previous is not None and accuracy > previous  # only counts once there was something to beat
            if previous is None or accuracy > previous:  # first run, or better
                self.best_accuracy[title] = accuracy  # remember it
            self.last_accuracy[title] = accuracy  # shown on the title card
        self.held.clear()  # no keys lit on the results screen
        if self.log is not None:  # flush the CSV
            self.last_log_path = self.log.path  # the results screen shows the name
            self.log.close()  # flush before showing results
            self.log = None  # done with it
        audio.stop_song()  # music off
        self.audio_playing = False  # and the flag to match
        self.state = STATE_RESULTS  # the end screen
        if self.automatic:  # nobody to press R or ESC
            self.running = False  # automatic runs end here

    def _start_calibration(self) -> None:  # begin the metronome test
        self.calibration_run = CalibrationRun()  # defaults come from config
        self.calibration_last_beat = -1  # no click has played yet
        self.calibration_last_offset = None  # nothing tapped yet
        self.clock.start()  # the test runs on the same clock
        self.state = STATE_CALIBRATE  # switch screens

    def _calibration_tap(self) -> None:  # one tap during the test
        if self.calibration_run is None:  # not calibrating
            return  # nothing to tap into
        offset = self.calibration_run.register_tap(self.clock.song_time_ms())  # same clock as the game
        if offset is not None:  # a usable tap: show it
            self.calibration_last_offset = offset  # the circle shows it

    def _finish_calibration(self) -> None:  # save the offset, or say why not
        run = self.calibration_run  # shorter lines below
        if run is not None and run.offset_ms is not None and run.finished:  # all three have to hold to save
            self.latency_offset = float(run.offset_ms)  # use the median
            self.clock.latency_offset = self.latency_offset  # live, no restart needed
            save_calibration(self.latency_offset, taps=run.tap_count)  # keep it for next time
            self.status_message = f"calibration saved - {run.summary()}"  # the title screen shows it
        elif run is not None:  # not enough usable taps
            self.status_message = f"calibration skipped - {run.summary()}"  # same place, different news
        self.calibration_run = None  # done either way
        self.state = STATE_TITLE  # back home

    # --- update -----------------------------------------------------------
    def _update(self) -> None:  # game logic, once per frame
        if self.state == STATE_COUNTDOWN and self.session is not None:  # the countdown block
            song_ms = self.clock.song_time_ms()  # the tick clock again
            # the countdown ends at the first note, when keys start to matter
            first_note_ms = self.chart.notes[0].time_ms if self.chart.notes else 0
            if song_ms >= first_note_ms:  # keys start to matter here
                self.state = STATE_PLAY  # countdown over

        elif self.state == STATE_PLAY and self.session is not None:  # the play block
            song_ms = self.clock.song_time_ms()  # same reading for taps and misses
            if self.auto_taps is not None:  # prepared taps, if any
                # a prepared run: judgement uses the tap's own time, so the
                # result does not depend on the frame it lands in. The mixer is
                # read on that frame, which is how an automatic log gets second
                # clock readings; a real press reads it in the key handler.
                for lane, tap_ms in self.auto_taps.due(song_ms):  # everything due by now
                    self.presses[lane] = tap_ms  # the key lights like a real press, there is just no key-up
                    judgement = self.session.register_tap(  # judged at its own time
                        lane, tap_ms=tap_ms, mixer_pos_ms=self.clock.mixer_position_ms()  # mixer sampled on this frame
                    )  # same call the key handler makes
                    if judgement is not None:  # a note scored: light the lane
                        self.flashes[lane] = tap_ms  # same flash rule as a real press
                    if lane in self.session.holding:  # that press started a hold
                        self.held.add(lane)  # keep the key down until its prepared key-up
            if self.auto_releases is not None:  # prepared key-ups, after the presses of the same frame
                for lane, release_ms in self.auto_releases.due(song_ms):  # everything due by now
                    self.held.discard(lane)  # the key cap rises
                    self.session.register_release(lane, release_ms=release_ms)  # judged at its own time, like the taps
            self.session.update(song_ms)  # finish the held tails, miss the notes the song passed
            for lane, at in self.session.pop_kept_holds():  # a tail that landed with the key still down
                self.flashes[lane] = at  # burst the key again, the hold is what scored
            if song_ms > self.chart.length_ms + 1200 and self.session.finished:  # 1.2 s past the last note
                self._finish_play()  # everything judged, plus a little tail
            elif song_ms > self.chart.length_ms + 5000:  # stuck (unjudgeable notes)
                self._finish_play()  # end anyway

        elif self.state == STATE_CALIBRATE and self.calibration_run is not None:  # the calibration block
            run = self.calibration_run  # shorter lines below
            song_ms = self.clock.song_time_ms()  # same clock as a run
            if self.auto_taps is not None:  # prepared taps again
                # prepared taps stand in for a player tapping the metronome
                for _lane, tap_ms in self.auto_taps.due(song_ms):  # the lane is irrelevant here
                    offset = run.register_tap(tap_ms)  # the run decides if the tap is usable
                    if offset is not None:  # a usable tap
                        self.calibration_last_offset = offset  # show the newest one
            beat = int(song_ms // run.beat_ms)  # which click we are on
            if beat != self.calibration_last_beat and 0 <= beat < run.beats:  # a new click, and inside the run
                self.calibration_last_beat = beat  # remember, so it plays once
                if self.click is not None:  # no mixer, no click
                    self.click.play()  # the metronome click
            if song_ms > run.duration_ms + 400:  # run over, plus a little tail
                self._finish_calibration()  # save it or explain why not
                if self.automatic:  # nobody to press anything after
                    self.running = False  # headless calibrations end here too

        # drop flashes that have finished, so the dictionaries cannot grow forever
        song_ms = self.clock.song_time_ms()  # one reading for all lanes
        for lights in (self.flashes, self.presses):  # hits and presses age the same way
            for lane in [lane for lane, at in lights.items() if song_ms - at > 450]:  # collect the expired ones first
                lights.pop(lane, None)  # then remove, popping mid-loop breaks the scan

    # --- draw -------------------------------------------------------------
    def _draw(self) -> None:  # draw one frame, by state
        surface = self.frame  # drawn offscreen, then blitted to the window
        if surface is None:  # not set up yet
            return  # nothing to draw on yet

        if self.state == STATE_TITLE:  # the title screen
            render.draw_title(  # the title card
                surface,  # draw onto the frame
                self.chart.title,  # of the picked chart
                self.chart.note_count,  # notes shown under the title
                self.latency_offset,  # the saved offset
                last_accuracy=self.last_accuracy.get(self.chart.title),  # the last run on this chart, if any
                hold_count=self.chart.hold_count,  # week 12: the meta line says how many are holds
                anim_ms=None if self.headless else pygame.time.get_ticks(),  # None keeps captures identical
                chart_picker=(len(self.chart_paths) > 1, self.chart_index, len(self.chart_paths)),  # the picker only shows with 2 or more charts
                best_accuracy=self.best_accuracy.get(self.chart.title),  # best this session
                chart_bpm=self.chart.bpm,  # meta line
                chart_length_ms=self.chart.length_ms,  # meta line
                lane_counts=self.chart.lane_counts,  # the per-lane bars
                status_message=self.status_message,  # e.g. 'no audio device', now drawn on its own row
            )  # title screen done

        elif self.state == STATE_GUIDE:  # the short guide
            render.draw_guide(  # one screen, six rows
                surface,  # same frame
                self.latency_offset,  # the saved offset, shown in the calibration row
            )  # guide done

        elif self.state == STATE_CALIBRATE and self.calibration_run is not None:  # the metronome screen
            run = self.calibration_run  # shorter lines below
            song_ms = self.clock.song_time_ms()  # for the pulse
            render.draw_calibration(  # the metronome screen
                surface,  # same frame
                max(0, self.calibration_last_beat),  # clicks done
                run.beats,  # clicks total
                run.offsets,  # taps so far
                self.calibration_last_offset,  # the last tap
                run.beat_phase(song_ms),  # pulses the circle
            )  # metronome screen done

        elif self.state in (STATE_COUNTDOWN, STATE_PLAY) and self.session is not None:  # one screen, the countdown draws over it
            # the play screen; the countdown draws over it until the first note
            song_ms = self.clock.song_time_ms()  # one reading per frame
            render.draw_background(surface)  # gradient and vignette first
            render.draw_highway(surface)  # the lane strip
            render.draw_beat_grid(surface, song_ms, self.chart.bpm)  # faint lines on the beat
            render.draw_lane_beams(surface, self.presses, self.held, song_ms)  # light over pressed keys
            render.draw_combo(surface, self.session, song_ms)  # big and quiet, behind the notes
            render.draw_notes(surface, self.session, song_ms)  # the falling notes
            render.draw_highway_fades(surface)  # notes come out of the dark and sink under the keys
            # the receptors need the song time to age the presses and hit
            # bursts; with raw timestamps the flash never fired at all
            render.draw_receptors(surface, self.session, self.presses, self.held, self.flashes, song_ms)  # line, keys, bursts
            render.draw_play_hud(surface, self.session, song_ms, self.chart.title, show_hints=not self.debug)  # the side columns

            judgement = self.session.last_judgement  # the popup word
            if judgement is not None:  # only after a first press
                age = song_ms - self.session.last_judged_song_ms  # for its animation
                render.draw_judgement_popup(  # PERFECT or GOOD over the line
                    surface, judgement.verdict, judgement.offset_text, age  # word, offset, age for the pop
                )  # popup done

            if self.debug:  # the two clocks side by side
                render.draw_debug_panel(  # the two-clock panel
                    surface,  # same frame
                    song_ms,  # first row of the panel
                    self.clock.tick_reading_ms(),  # raw ticks
                    self.clock.mixer_position_ms(),  # the mixer reading
                    self.clock.drift_ms(),  # the gap between them
                    self.latency_offset,  # the offset in use
                    self.reported_fps(),  # the last row
                )  # panel done

            # the countdown over the play screen, then GO over the first beat
            if self.countdown_start_ms is not None:
                first_note_ms = self.chart.notes[0].time_ms if self.chart.notes else 0  # same anchor as _start_play
                remaining = first_note_ms - song_ms  # time before the first note
                if remaining <= 0:  # the note is here or just past
                    if remaining > -800:  # the 0.8 s of GO
                        render.draw_go(surface, 800 + remaining)  # age 0 to 800 ms
                elif remaining <= COUNTDOWN_WINDOW_MS:  # only the last three seconds
                    # this week's stuck-on-3 bug: the gate used to be just
                    # remaining > 0, so with the first note eight seconds in the
                    # digit sat on its 3 clamp for the whole intro. the intro
                    # stays quiet instead - you hear the beat, no digit yet
                    render.draw_countdown(surface, remaining)  # 3, 2, 1

        elif self.state == STATE_RESULTS and self.session is not None:  # the end screen
            log_name = self.last_log_path.name if self.last_log_path else "not written"  # only the file name fits
            render.draw_results(surface, self.session, log_name, self.chart.title, new_best=self.new_best)  # the end screen

        if self.screen is not None:  # show the finished frame
            self.screen.blit(surface, (0, 0))  # frame onto the window

        if self.screenshot_path is not None and not self._screenshot_written:  # one capture per run
            if self._screenshot_ready():  # the asked-for screen is on show
                self.screenshot_path.parent.mkdir(parents=True, exist_ok=True)  # the folder may not exist
                pygame.image.save(surface, str(self.screenshot_path))  # the frame is opaque
                self._screenshot_written = True  # never save twice
                self.exit_code = 0  # a capture is success

    # --- recording -------------------------------------------------------
    def _start_recording(self) -> None:  # one ffmpeg process for the whole run
        # pipe raw frames straight into ffmpeg; pygame has no video writer, and
        # dumping PNGs first would waste disk for no reason
        import shutil  # to find ffmpeg on PATH
        import subprocess  # to run it

        if self.record_path is None:  # not recording
            return  # nothing to start
        ffmpeg = shutil.which("ffmpeg")  # the full path, or None
        if ffmpeg is None:  # no ffmpeg: say so, carry on without video
            print("ffmpeg not found, so no video was recorded")  # visible in the console
            self.record_path = None  # skip every later recording step
            return  # carry on without video
        self.record_path.parent.mkdir(parents=True, exist_ok=True)  # same folder guard as screenshots
        self._recorder = subprocess.Popen(  # raw RGB frames in, h264 out  # stdin pipe is how frames get in
            [  # the ffmpeg command
                ffmpeg, "-y", "-loglevel", "error",  # overwrite, quiet output
                "-f", "rawvideo", "-pix_fmt", "rgb24",  # input is raw rgb frames
                "-s", f"{config.WINDOW_WIDTH * self.record_scale}x{config.WINDOW_HEIGHT * self.record_scale}",  # frame size, scaled up
                "-r", str(self.record_fps),  # input frame rate
                "-i", "-",  # frames come from stdin
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",  # no audio, h264, sane quality
                str(self.record_path),  # the output file
            ],  # command done
            stdin=subprocess.PIPE,  # we write frames to this pipe
        )  # process started

    def _write_frame(self) -> None:  # hand one frame to ffmpeg
        if self._recorder is None or self.frame is None:  # nothing recording, or nothing drawn
            return  # skip
        frame = self.frame  # the finished frame
        if self.record_scale != 1:  # upscale for HD footage
            frame = pygame.transform.smoothscale(  # scale up for the mp4
                frame,  # the bigger size
                (config.WINDOW_WIDTH * self.record_scale, config.WINDOW_HEIGHT * self.record_scale),  # scaled
            )  # scaled to the record size
        try:  # the pipe can be dead
            self._recorder.stdin.write(pygame.image.tobytes(frame, "RGB"))  # raw bytes, row by row
        except (BrokenPipeError, AttributeError):  # ffmpeg died: stop feeding it
            self._recorder = None  # stop trying on every frame

    def _stop_recording(self) -> None:  # close ffmpeg cleanly
        if self._recorder is None:  # never started, or it died
            return  # nothing to close
        self._recorder.stdin.close()  # tells ffmpeg the video ends here
        self._recorder.wait()  # let it write the file out
        print(f"wrote {self.record_path}")  # where to find it
        self._recorder = None  # clean handle

    def reported_fps(self) -> Optional[float]:  # for the HUD; headless has none
        if self.headless:  # no real clock, no fps
            return None  # the hud skips the fps row
        return self.frame_clock.get_fps()  # pygame's smoothed number

    def _screenshot_ready(self) -> bool:  # is the asked-for screen on show?
        if self.state != self.screenshot_state:  # wrong screen
            return False  # keep looping
        if self.state in (STATE_PLAY, STATE_CALIBRATE, STATE_COUNTDOWN):  # timed screens
            return self.clock.song_time_ms() >= self.screenshot_at_ms  # wait for the moment
        return True  # title and results are static screens

    # --- headless reporting ----------------------------------------------
    def selftest_report(self) -> dict:  # the numbers a --selftest run produced
        score = self.session.score if self.session is not None else None  # None when no run happened
        return {  # the dict main() prints
            "chart": self.chart.title,  # which chart ran
            "notes": self.chart.note_count,  # how many notes
            "taps_fed": len(self.simulated_taps),  # how many we fed
            "perfect": None if score is None else score.perfect,  # each verdict
            "good": None if score is None else score.good,  # second
            "miss": None if score is None else score.miss,  # third
            "stray": None if score is None else score.stray_taps,  # presses that hit nothing
            "holds_kept": None if self.session is None else self.session.holds_kept,  # week 12: tails held to the end
            "holds_dropped": None if self.session is None else self.session.holds_dropped,  # and tails let go too early
            "accuracy": None if score is None else round(score.accuracy, 1),  # one decimal
            "best_combo": None if score is None else score.best_combo,  # the streak
            "log": str(self.last_log_path) if self.last_log_path else None,  # where the csv went
        }  # report done
