# Used to keep track of your combo score and accuracy during a run.

from . import config  # will hold the constants for scoring
from .judge import Verdict  # our different verdicts


class Score:  # used to keep track of your score through a run
    def __init__(self, note_count: int) -> None:  # the total comes from the chart
        self.note_count = note_count  # amount of notes in the song
        self.perfect = 0  # amount of PERFECT notes hit
        self.good = 0  # amount of GOOD notes hit
        self.miss = 0  # amount of notes missed
        self.combo = 0  # how many notes you have hit without missing in a row
        self.best_combo = 0  # highest combo achieved during the run
        self.stray_taps = 0  # clicks that were to far away to be considered a tap on a note
        self.early_taps = 0  # how many notes you have tapped early on
        self.late_taps = 0  # how many notes you have tapped late on

    def register(self, verdict: Verdict, early: bool = False) -> None:  # take a note that was judged and update combo
        if verdict is Verdict.PERFECT:  # will add all points and keep your combo going
            self.perfect += 1  # one more perfect
            self.combo += 1  # combo keeps going
        elif verdict is Verdict.GOOD:  # will add half the points and keep combo going
            self.good += 1  # one more good
            self.combo += 1  # same combo rule as perfect, good doesn't break it either
        else:  # resets your combo
            self.miss += 1  # one more miss
            self.combo = 0  # and the streak dies
        if verdict is not Verdict.MISS:  # will only be true if the press is on a note
            if early:  # split early and late for the stats panel
                self.early_taps += 1  # counted as early
            else:  # not early means late
                self.late_taps += 1  # counted as late
        self.best_combo = max(self.best_combo, self.combo)  # keep track of your best combo

    def register_stray(self) -> None:  # click that did not land on any notes
        self.stray_taps += 1  # still registers as a click but will not consume a note nor affect your combo

    @property  # read like a value, same pattern for every stat below
    def judged(self) -> int:  # amount of notes you have completed or missed
        return self.perfect + self.good + self.miss  # the three buckets cover everything

    @property  # same property pattern, one stat per read
    def remaining(self) -> int:  # how many notes you have left
        return max(0, self.note_count - self.judged)  # max so a weird count never goes negative

    @property  # no brackets, it reads like a flag
    def finished(self) -> bool:  # True if you have finished all the notes in the song
        return self.judged >= self.note_count  # everything got a verdict

    @property  # computed on demand, nothing to keep in sync
    def points(self) -> int:  # current amount of points you have
        return (  # three lines of points, one per verdict
            self.perfect * config.POINTS_PERFECT  # perfect pays full
            + self.good * config.POINTS_GOOD  # good pays half
            + self.miss * config.POINTS_MISS  # miss pays almost nothing
        )  # added up

    @property  # the best case, for the percentage
    def max_points(self) -> int:  # what you would have gotten if you got them all
        return self.note_count * config.POINTS_PERFECT  # every note a perfect

    @property  # the number on the results screen
    def accuracy(self) -> float:  # current score / max possible score
        if self.max_points == 0:  # if there is no notes dont divide by 0  # empty chart guard
            return 0.0  # zero notes, zero percent
        return 100.0 * self.points / self.max_points  # plain percentage

    @property  # turns accuracy into a letter
    def rank(self) -> str:  # give yourself a general idea of how well you did for the ending screen
        accuracy = self.accuracy  # read once, used five times
        if accuracy >= 98:  # S is the chase rank
            return "S"  # top of the ladder
        if accuracy >= 92:  # A, a solid run
            return "A"  # still strong
        if accuracy >= 80:  # B, decent
            return "B"  # average and up
        if accuracy >= 65:  # C, getting there
            return "C"  # below this it's a D
        return "D"  # the floor rank

    def summary(self) -> str:  # one line summary of all stats used in printing and unit testing
        return (  # f-string pieces glued together
            f"PERFECT {self.perfect}  GOOD {self.good}  MISS {self.miss}  "  # counts first
            f"best combo {self.best_combo}  accuracy {self.accuracy:.1f}%"  # then combo and accuracy
        )  # one line
