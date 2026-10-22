# allows the user to calibrate with there metronome
# there will be an added latency from the user tapping keys(audio, os and user) this will show as an added time this finds that added time and the clock will use it to compensate.

import json  # this will be stored as json
import statistics  # average and middle of taps
from datetime import datetime  # timestamp of when its stored
from pathlib import Path  # where calibration.json lives
from typing import Optional  # for the maybe-an-offset returns

from . import config  # total beats beat length and allowed error


class CalibrationRun:  # get all tap times while going through a metronome
    def __init__(  # defaults all come from config
        self,  # nothing special, just self
        beats: int = config.CALIBRATION_BEATS,  # how many clicks were played
        beat_ms: float = config.CALIBRATION_BEAT_MS,  # distance between each click
        tolerance_ms: float = config.CALIBRATION_TOLERANCE_MS,  # if they are farther then this its considered a miss fire
    ) -> None:  # stores only, returns nothing
        self.beats = beats  # list of clicks during this session
        self.beat_ms = beat_ms  # time between each click
        self.tolerance_ms = tolerance_ms  # can only be no larger than .5 beat length
        self.offsets: list[float] = []  # stores all good taps as offsets
        self.skipped = 0  # taps that were consider miss fires

    @property  # read like a value
    def duration_ms(self) -> float:  # how long the clicks last
        return self.beats * self.beat_ms  # clicks times spacing

    def beat_time_ms(self, index: int) -> float:  # time a click occurs
        return index * self.beat_ms  # the grid is that simple

    def nearest_beat_index(self, song_ms: float) -> int:  # click your comparing to
        return int(round(song_ms / self.beat_ms))  # round to the closest click

    def beat_phase(self, song_ms: float) -> float:  # from 0-1 on current beat
        if self.beat_ms <= 0:  # prevents beat length of inf  # divide by zero guard
            return 0.0  # sit at the start of a beat
        return (song_ms % self.beat_ms) / self.beat_ms  # how far into the beat, 0 to 1

    def register_tap(self, song_ms: float) -> Optional[float]:  # the run decides if the tap is usable
        # compare a tap to the closest click returns none if it was to high of an offset
        index = self.nearest_beat_index(song_ms)  # which click was this meant to be
        if index < 0 or index >= self.beats:  # if tap is not within a run
            self.skipped += 1  # outside the run, ignore it
            return None  # no offset from this tap
        offset = song_ms - self.beat_time_ms(index)  # - means you were early
        if abs(offset) > self.tolerance_ms:  # most likely doubled tap or compared to the wrong click
            self.skipped += 1  # same ignore as the range check, different reason
            return None  # too far off, don't let it poison the median
        self.offsets.append(offset)  # a usable tap, keep it
        return offset  # handy for the live display

    @property  # how many usable taps so far
    def tap_count(self) -> int:  # amount of taps that can be used
        return len(self.offsets)  # list length

    @property  # enough taps to trust the number
    def finished(self) -> bool:  # minimum amount to get a reliable number
        return self.tap_count >= max(6, self.beats // 2)  # at least half the clicks, and never fewer than 6

    @property  # the average, kept for the summary line
    def mean_offset_ms(self) -> Optional[float]:  # keeps average
        if not self.offsets:  # nothing collected yet
            return None  # no mean without taps
        return statistics.fmean(self.offsets)  # plain average

    @property  # the mean, kept for comparison
    def median_offset_ms(self) -> Optional[float]:  # median, not mean: one
        if not self.offsets:  # distracted tap cannot drag it
            return None  # same empty guard as mean above
        return statistics.median(self.offsets)  # median instead of average in case of 1 off tap

    @property  # how scattered the taps are
    def spread_ms(self) -> Optional[float]:  # standard deviation
        if len(self.offsets) < 2:  # requires at least 2 taps
            return None  # one tap has no spread
        return statistics.pstdev(self.offsets)  # population stddev, the whole run is the population

    @property  # which single number gets saved
    def offset_ms(self) -> Optional[float]:  # value to save
        return self.median_offset_ms  # median wins, one wild tap can't move it

    def summary(self) -> str:  # information about the run
        offset = self.median_offset_ms  # read once
        if offset is None:  # if no good taps were collected
            return f"no usable taps ({self.skipped} ignored)"  # honest empty state
        spread = self.spread_ms  # spread is optional in the text
        spread_text = "" if spread is None else f", spread {spread:.0f} ms"  # only shown when we have it
        return (  # the summary line
            f"median {offset:+.0f} ms from {self.tap_count} taps"  # median leads
            f" (mean {self.mean_offset_ms:+.0f} ms{spread_text}), "  # mean rides along for comparison
            f"{self.skipped} ignored"  # and the reject count
        )  # glued together


def save_calibration(offset_ms: float, taps: int = 0, path: Optional[Path | str] = None) -> Path:  # writes calibration.json
    # sets offset for next run
    target = Path(path) if path is not None else config.CALIBRATION_PATH  # default file from config
    payload = {  # the file is tiny, four keys
        "latency_offset_ms": round(float(offset_ms), 1),  # offset
        "measured_from_taps": taps,  # how many taps to get the offset
        "measured_at": datetime.now().isoformat(timespec="seconds"),  # time
        "note": "subtracted from the tick clock to get song time",  # information about tap
    }  # payload done
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")  # indent so a human can read the file too
    return target  # caller may want to print it


def load_calibration(path: Optional[Path | str] = None) -> float:  # reads it back, zero if anything is off
    # get saved offset will default to 0
    target = Path(path) if path is not None else config.CALIBRATION_PATH  # same default as save, the two mirror each other
    try:  # file could be missing or broken
        data = json.loads(target.read_text(encoding="utf-8"))  # read and parse in one go
    except (FileNotFoundError, json.JSONDecodeError, OSError):  # if there is no offset or corrupt file
        return 0.0  # the no-file case
    try:  # the json might parse but hold the wrong shape
        return float(data["latency_offset_ms"])  # the one key we care about
    except (KeyError, TypeError, ValueError):  # if file is found but not correct format
        return 0.0  # the wrong-shape case
