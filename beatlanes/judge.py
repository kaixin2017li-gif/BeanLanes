# Judgement: checks if a keypress is a hit.
# separated from pygame to allow for easier testing.
# all of it can be summed up to error = tap time - target time. we have two windows.

from __future__ import annotations

from dataclasses import dataclass  # gives Judgement its attributes
from enum import Enum  # corresponding to each verdict

from . import config  # window sizes go here


class Verdict(Enum):  # can return good, late or miss
    PERFECT = "PERFECT"  # the tight window, the one worth chasing
    GOOD = "GOOD"  # the loose window
    MISS = "MISS"  # not inside either window


@dataclass(frozen=True)  # immutable so you cant mess up your judgement
class Judgement:  # represents a single tap that hit a note
    error_ms: float  # can be negative (early) or positive (late)
    verdict: Verdict  # how far off you were

    @property  # read like a value instead of a method call
    def early(self) -> bool:  # if error is negative we know your early
        return self.error_ms < 0  # a plain sign check

    @property  # same trick as early, it just reads nicer at the call site
    def offset_text(self) -> str:  # displayable version of your error
        if self.error_ms == 0:  # "+0 ms late" didnt look as cool
            return "+0 ms on time"  # the special case text
        direction = "early" if self.early else "late"  # based on if your late or early
        return f"{self.error_ms:+.0f} ms {direction}"  # the normal case, sign included


def verdict_for_error(error_ms: float) -> Verdict:  # the one place that maps error to a verdict
    # we compare the absolute value of our error to these 2 windows in our config
    error = abs(error_ms)  # early or late should not matter, only how far
    if error <= config.PERFECT_MS:  # within 50 milliseconds of the target time
        return Verdict.PERFECT  # done, nothing looser gets a look in
    if error <= config.GOOD_MS:  # within 120 milliseconds of the target time
        return Verdict.GOOD  # good enough, but not perfect
    return Verdict.MISS  # if your not within these windows you missed


def judge_tap(tap_ms: float, target_ms: float) -> Judgement:  # puts the two steps together
    # error = tap time - target time, then judge the windows
    error_ms = float(tap_ms) - float(target_ms)  # float so the log never stores ints by accident
    return Judgement(error_ms=error_ms, verdict=verdict_for_error(error_ms))  # both halves travel together in the dataclass


def is_inside_good_window(error_ms: float) -> bool:  # a yes/no version for the session loop
    # this will be used to determine if the key press should even be scored
    return abs(error_ms) <= config.GOOD_MS  # the loose window, same number as the verdict


def hold_was_kept(release_ms: float, end_ms: float) -> bool:  # week 12: the second half of a hold note
    # a hold is judged twice: the press goes through judge_tap like any tap,
    # then this decides the tail. letting go at end - HOLD_RELEASE_MS or later
    # counts as holding to the end, anything earlier drops the hold
    return float(release_ms) >= float(end_ms) - config.HOLD_RELEASE_MS  # the same kind of allowance the press gets
