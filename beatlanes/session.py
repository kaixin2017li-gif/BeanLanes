# One play-through: which notes are still live, and what each key press scored.
# No pygame objects in here, so the timing rules can be tested without a window.
#
# Week 12 adds hold notes. A hold is still one note with one verdict, it just
# takes two moments to decide: the press is judged like a tap, then the note
# waits in HOLDING until the tail reaches the line (kept, scores the press
# verdict) or the key comes up too early (dropped, scores a MISS).

from dataclasses import dataclass  # LiveNote is a plain data holder
from enum import Enum  # the three note states
from typing import Optional  # for the maybe-a-note returns

from . import config  # the GOOD window decides when a note is missed
from .chart import Chart  # the chart being played
from .judge import Judgement, Verdict, hold_was_kept, judge_tap  # the scoring rules
from .logbook import TapLog  # the CSV log
from .scoring import Score  # combo and totals
from .songclock import SongClock  # the clock


class NoteState(Enum):  # where a note is in its life
    PENDING = "pending"  # still falling, can still be hit
    HOLDING = "holding"  # week 12: a hold whose press landed, key still down
    HIT = "hit"  # was hit inside the window
    MISSED = "missed"  # the song walked past it


@dataclass  # no frozen here, the state field changes during play
class LiveNote:  # a chart note plus its state during play
    index: int  # position in the chart
    lane: int  # which lane, 0..3
    time_ms: int  # target time
    hold_ms: int = 0  # week 12: the tail length, 0 for a tap
    state: NoteState = NoteState.PENDING  # starts as falling
    # a hold's press is kept here until the tail decides whether it counts
    head: Optional[Judgement] = None  # the press verdict and error
    head_tap_ms: Optional[float] = None  # when the press happened, for the log
    head_mixer_ms: Optional[int] = None  # the mixer reading on that press, for the log

    @property  # reads like a flag
    def is_hold(self) -> bool:  # True for a hold note
        return self.hold_ms > 0  # any tail makes it a hold

    @property  # reads like a value
    def end_ms(self) -> int:  # when the note is over
        return self.time_ms + self.hold_ms  # the tail for a hold, the head for a tap

    @property  # reads like a flag
    def live(self) -> bool:  # True while it can still be hit
        return self.state is NoteState.PENDING  # hit and missed notes are done

    def screen_y(self, song_ms: float) -> float:  # where to draw this note right now
        # a note sits on the judgement line at its own target time; the position
        # comes from the clock, not from a per-frame step, so slow frames never
        # change when a note is judged
        px_per_ms = (config.JUDGE_LINE_Y - config.NOTE_SPAWN_Y) / config.APPROACH_MS
        return config.JUDGE_LINE_Y - (self.time_ms - song_ms) * px_per_ms  # same maths as y_for_time in render

    def tail_screen_y(self, song_ms: float) -> float:  # where a hold's tail is right now
        # the tail is just a later moment on the same scroll, so it uses the
        # same subtraction with the end time instead of the start time
        px_per_ms = (config.JUDGE_LINE_Y - config.NOTE_SPAWN_Y) / config.APPROACH_MS  # same speed as the head
        return config.JUDGE_LINE_Y - (self.end_ms - song_ms) * px_per_ms  # higher up the screen than the head


class PlaySession:  # owns the note states for one run
    def __init__(  # everything a run needs comes in through here
        self,  # nothing special, just self
        chart: Chart,  # the chart being played
        clock: SongClock,  # the shared clock
        logger: Optional[TapLog] = None,  # None in tests without a log
        calibration_offset_ms: float = 0.0,  # kept for the log header
    ) -> None:  # builds the note list, returns nothing
        self.chart = chart  # the chart being played
        self.clock = clock  # the song clock
        self.logger = logger  # CSV log, may be None in tests
        self.calibration_offset_ms = calibration_offset_ms  # kept for the log header
        self.notes = [LiveNote(note.index, note.lane, note.time_ms, note.hold_ms) for note in chart.notes]  # all notes, in play order
        self.score = Score(chart.note_count)  # combo and totals
        self.last_judgement: Optional[Judgement] = None  # for the on-screen popup
        self.last_judged_song_ms: float = 0.0  # when that popup appeared
        self.last_press_song_ms: dict[int, float] = {}  # lane -> song time of the last press, for the flash
        self.last_miss_song_ms: dict[int, float] = {}  # lane -> song time of its last miss, for the red receptor flash
        self.hit_errors: list[float] = []  # tap - target for every scored hit, the results screen plots these
        # week 12 hold notes
        self.hold_count = chart.hold_count  # how many holds the chart has, the results screen shows kept / this
        self.holding: dict[int, LiveNote] = {}  # lane -> the hold whose key is down right now
        self.holds_kept = 0  # holds that lasted to the tail
        self.holds_dropped = 0  # holds let go too early
        self._kept_since_last_ask: list[tuple[int, float]] = []  # (lane, song ms) of holds that just finished, for the burst

    @property  # the renderer asks for this every frame
    def live_notes(self) -> list[LiveNote]:  # the notes not yet hit or missed
        return [note for note in self.notes if note.live]  # a fresh list each call, cheap at this size

    def nearest_live_note(self, lane: int, at_ms: float) -> Optional[LiveNote]:  # the aiming rule for a press
        # the pending note in this lane closest in time to the press
        best: Optional[LiveNote] = None  # none yet
        for note in self.notes:  # scan them all, the chart is small
            if note.lane != lane or not note.live:  # wrong lane or already used
                continue  # on to the next note
            if best is None or abs(note.time_ms - at_ms) < abs(best.time_ms - at_ms):  # strictly closer wins, ties keep the earlier one
                best = note  # closer than the current best
        return best  # None if nothing was pending

    def register_release(  # week 12: a lane key came up
        self,  # nothing special, just self
        lane: int,  # which lane key was let go
        release_ms: Optional[float] = None,  # key-up time; sampled now if not given
    ) -> Optional[bool]:  # True kept, False dropped, None when no hold was down
        # most key-ups mean nothing: only a lane that is holding a note cares
        note = self.holding.get(lane)  # the hold in this lane, if any
        if note is None:  # a tap's key-up, or a hold that already finished
            return None  # nothing to decide
        if release_ms is None:  # real key-up: use the clock right now
            release_ms = self.clock.song_time_ms()  # the same tick clock the press used
        kept = hold_was_kept(release_ms, note.end_ms)  # close enough to the tail?
        self._finish_hold(note, release_ms, kept)  # score it either way
        return kept  # the app and the tests can look at this

    def pop_kept_holds(self) -> list[tuple[int, float]]:  # holds that finished since the last call
        # the app asks once a frame so it can burst the key when a tail lands;
        # handing the list over and emptying it means each one bursts once
        finished, self._kept_since_last_ask = self._kept_since_last_ask, []  # swap in a fresh list
        return finished  # (lane, song ms) pairs

    def _finish_hold(self, note: LiveNote, release_ms: Optional[float], kept: bool) -> None:  # the second half of a hold
        # release_ms is None when the key was still down as the tail went past
        self.holding.pop(note.lane, None)  # the lane is free again
        head = note.head  # the press, judged earlier
        if head is None:  # cant happen: only a judged press puts a note in HOLDING
            return  # but never crash a run over it
        if kept:  # held to the tail: the press verdict counts now
            note.state = NoteState.HIT  # done, and done well
            self.score.register(head.verdict, early=head.early)  # combo, points, early/late from the press
            self.hit_errors.append(head.error_ms)  # the timing strip only shows scored hits
            self.holds_kept += 1  # for the results screen
            finished_at = note.end_ms if release_ms is None else release_ms  # when the burst should start
            self._kept_since_last_ask.append((note.lane, float(finished_at)))  # the app bursts the key
            self._log(note, note.head_tap_ms, head.error_ms, head.verdict, note.head_mixer_ms, release_ms)  # one row, with the press numbers
        else:  # let go too early
            note.state = NoteState.MISSED  # the hold is dropped
            self.score.register(Verdict.MISS)  # the combo breaks here, not at the press
            self.holds_dropped += 1  # for the results screen
            # the error shown is how early the key came up against the tail
            self.last_judgement = Judgement(error_ms=release_ms - note.end_ms, verdict=Verdict.MISS)  # the popup says MISS
            self.last_judged_song_ms = release_ms  # popup starts on the key-up
            self.last_miss_song_ms[note.lane] = release_ms  # the receptor flashes red
            # the press numbers still go in the log: the press really was on
            # time, it was the tail that failed, and the release column says so
            self._log(note, note.head_tap_ms, head.error_ms, Verdict.MISS, note.head_mixer_ms, release_ms)  # one row

    def update(self, song_ms: float) -> int:  # called every frame with the current song time
        # finish the holds whose tail has reached the line with the key still
        # down, then miss every note the song has passed; returns how many missed
        for note in list(self.holding.values()):  # a copy, _finish_hold edits the dict
            if song_ms >= note.end_ms:  # the tail is on the line
                self._finish_hold(note, None, kept=True)  # no key-up needed, it was held all the way
        missed = 0  # the count the app uses for effects
        for note in self.notes:  # same full scan as nearest_live_note, misses need it too
            if note.live and song_ms - note.time_ms > config.GOOD_MS:  # window is over
                note.state = NoteState.MISSED  # nothing hit it, so it counts against the run
                self.score.register(Verdict.MISS)  # combo break and the miss count
                self._log(note, None, None, Verdict.MISS, None, None)  # logged with empty tap columns
                # until week 11 a miss was silent on screen: the popup only ever
                # showed hits, so a run could fall apart with no feedback at all
                self.last_judgement = Judgement(error_ms=song_ms - note.time_ms, verdict=Verdict.MISS)  # the popup says MISS now
                self.last_judged_song_ms = song_ms  # popup animation starts on this frame
                self.last_miss_song_ms[note.lane] = song_ms  # the receptor flashes red
                missed += 1  # tell the caller
        return missed  # how many died this frame

    def register_tap(  # the whole judging flow starts here
        self,  # nothing special, just self
        lane: int,  # which lane key was pressed
        tap_ms: Optional[float] = None,  # press time; sampled now if not given
        mixer_pos_ms: Optional[int] = None,  # mixer reading for the log
    ) -> Optional[Judgement]:  # None means the press scored nothing
        # judge one key press. Returns a Judgement, or None when no note was
        # close enough to be scored.
        if tap_ms is None:  # real key press: use the clock right now
            tap_ms = self.clock.song_time_ms()  # the tick clock, the one we trust
        self.last_press_song_ms[lane] = tap_ms  # kept for the lane flash

        note = self.nearest_live_note(lane, tap_ms)  # find the target
        if note is None:  # nothing pending in this lane
            self.score.register_stray()  # counted, but harmless
            return None  # no note, no judgement

        judgement = judge_tap(tap_ms, note.time_ms)  # the subtraction
        if judgement.verdict is Verdict.MISS:  # judge_tap can still say MISS
            # too far to be credited: the note stays live and will be missed on
            # its own if nothing better comes along
            self.score.register_stray()  # same stray rule as the empty lane above
            return None  # the note stays live for a better press

        if note.is_hold:  # week 12: the press only starts a hold
            # nothing is scored yet. the popup still shows the press timing,
            # because that is the part the player just did, but the verdict
            # only counts once the tail has reached the line
            note.state = NoteState.HOLDING  # not live any more, not finished either
            note.head = judgement  # kept for when the tail decides
            note.head_tap_ms = tap_ms  # for the log row
            note.head_mixer_ms = mixer_pos_ms  # same
            self.holding[lane] = note  # the key-up for this lane now means something
            self.last_judgement = judgement  # shown by the popup
            self.last_judged_song_ms = tap_ms  # popup animation starts here
            return judgement  # the app still bursts the key

        note.state = NoteState.HIT  # this note is used up
        self.score.register(judgement.verdict, early=judgement.early)  # combo, points, early/late split
        self.last_judgement = judgement  # shown by the popup
        self.last_judged_song_ms = tap_ms  # popup animation starts here
        self.hit_errors.append(judgement.error_ms)  # kept for the timing strip on the results screen
        self._log(note, tap_ms, judgement.error_ms, judgement.verdict, mixer_pos_ms, None)  # one csv row for the hit
        return judgement  # the app uses it for the popup

    def _log(self, note: LiveNote, tap_ms: Optional[float], error_ms: Optional[float], verdict: Verdict, mixer_pos_ms: Optional[int], release_ms: Optional[float]) -> None:  # the underscore means internal, only this file calls it
        # write one row for this note, hit or missed
        if self.logger is None:  # tests run without a log
            return  # nothing to write to
        self.logger.write_tap(  # hand the row over
            note_index=note.index,  # straight from the chart
            lane=note.lane,  # same as above
            target_ms=note.time_ms,  # the note's own time
            tap_ms=tap_ms,  # may be None for a miss
            error_ms=error_ms,  # same, None for a miss
            verdict=verdict,  # the enum itself, logbook turns it into text
            mixer_pos_ms=mixer_pos_ms,  # may be None when there is no sound
            hold_ms=note.hold_ms,  # 0 for a tap
            release_ms=release_ms,  # only a hold that was let go has one
        )  # row written

    @property  # the app polls this to switch screens
    def finished(self) -> bool:  # True once nothing is left to judge
        return self.score.finished  # scoring already knows the answer

    def results_text(self) -> list[str]:  # the lines shown on the results screen
        score = self.score  # shorter lines below
        lines = [  # built top to bottom, same order as the screen
            f"SCORE      {score.points} / {score.max_points}",  # points out of max
            f"RANK       {score.rank}",  # the letter
            f"ACCURACY   {score.accuracy:.1f}%",  # one decimal is enough
            "",  # spacer
            f"PERFECT    {score.perfect}",  # the three counts
            f"GOOD       {score.good}",  # second count
            f"MISS       {score.miss}",  # third count
            "",  # another spacer
            f"BEST COMBO {score.best_combo}",  # the bragging number
            f"EARLY/LATE {score.early_taps} / {score.late_taps}",  # timing habits
            f"STRAY TAPS {score.stray_taps}",  # presses that hit nothing
        ]  # list done
        if self.hold_count:  # only charts with holds get the line
            lines.append(f"HOLDS      {self.holds_kept} / {self.hold_count} kept")  # how many tails were held
        return lines  # renderer prints them as-is
