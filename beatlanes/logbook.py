# the tap log, csv format, will write each note on one line as the run progresses

import csv  # this is what we will call the log
from datetime import datetime  # filename and what goes in the header
from pathlib import Path  # logs land on disk
from typing import Optional, Union  # Optional for the blank columns, Union for the verdict type

from . import config  # directory that logs will be stored in
from .judge import Verdict  # the name we give our verdicts

HEADER = (  # order they appear in
    "note_index",  # which note in the chart
    "lane",  # which key
    "target_ms",  # when it should be hit
    "tap_ms",  # when it was hit
    "error_ms",  # tap minus target
    "verdict",  # PERFECT GOOD MISS
    "mixer_pos_ms",  # the second clock, for drift checking
    # week 12, added at the end so the older tools that read by column name
    # still work on new logs
    "hold_ms",  # 0 for a tap, the tail length for a hold
    "release_ms",  # when a hold's key went up, blank if it stayed down past the tail
)  # header done

VerdictLike = Union[Verdict, str]  # will take an enum or string


class TapLog:  # takes the taps from one run and puts them in a csv
    def __init__(  # the knobs a caller might want
        self,  # nothing special, just self
        path: Optional[Path | str] = None,  # path to write to, defaults to logs directory
        song: str = "",  # name of song to put in the header
        calibration_ms: float = 0.0,  # what offset was used for the header
    ) -> None:  # opens the file right away
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)  # will create logs dir if it does not exist
        if path is None:  # default name includes the time so runs never overwrite
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")  # datetime in the name so it never overrides a past log
            path = config.LOG_DIR / f"taps-{stamp}.csv"  # the timestamped name
        self.path = Path(path)  # Path either way, str paths get converted
        self._file = self.path.open("w", newline="", encoding="utf-8")  # newline="" stops csv adding blank rows on windows
        self._writer = csv.writer(self._file)  # csv handles the commas and quoting
        # start with some hashes, these will just be strings in excel and you can ignore them with code
        self._writer.writerow(["# BeatLanes tap log"])
        self._writer.writerow([f"# written {datetime.now().isoformat(timespec='seconds')}"])  # when the run's log was started
        if song:  # include the name of the song
            self._writer.writerow([f"# song {song}"])
        self._writer.writerow([f"# calibration_offset_ms {calibration_ms:.0f}"])  # the offset used, so the drift maths can be redone later
        self._writer.writerow(HEADER)  # these are the actual columns

    def write_tap(  # the whole row arrives as arguments
        self,  # nothing special, just self
        note_index: int,  # what note it was
        lane: int,  # what lane it was on
        target_ms: float,  # what time we wanted it hit
        tap_ms: Optional[float],  # when we actually hit it, blank if missed
        error_ms: Optional[float],  # how far off we were, blank if missed
        verdict: VerdictLike,  # PERFECT GOOD or MISS
        mixer_pos_ms: Optional[int] = None,  # clock on the 2nd tick when we tapped
        hold_ms: int = 0,  # week 12: the note's tail, 0 for a tap
        release_ms: Optional[float] = None,  # week 12: when the hold was let go, blank for taps
    ) -> None:  # writes and returns
        # one note judged, with tap info or nothing if missed
        verdict_text = verdict.value if isinstance(verdict, Verdict) else str(verdict)  # enums and plain strings both fine here
        self._writer.writerow(  # the data row
            [  # one cell per column
                note_index,  # straight from the caller
                lane,  # same deal, straight copies
                f"{target_ms:.0f}",  # whole ms is plenty
                "" if tap_ms is None else f"{tap_ms:.0f}",  # blank if missed
                "" if error_ms is None else f"{error_ms:+.0f}",  # negative if early, positive if late
                verdict_text,  # already a string from above
                "" if mixer_pos_ms is None else mixer_pos_ms,  # blank if no sound
                f"{hold_ms:.0f}",  # always filled, so taps and holds can be told apart
                "" if release_ms is None else f"{release_ms:.0f}",  # blank unless the key went up before the tail
            ]  # row done
        )  # row done

    def close(self) -> None:  # make sure everything is flushed and closed
        if not self._file.closed:  # closing twice would throw
            self._file.flush()  # push the buffer to disk first
            self._file.close()  # then let the file go

    def __enter__(self) -> "TapLog":  # allows you to use with
        return self  # with needs the object back

    def __exit__(self, *exc_info) -> None:  # will always close the file
        self.close()  # same close as above, the with block just guarantees it


def read_log(path: Path | str) -> list[dict[str, str]]:  # the reader half of this module
    # will take a log and return it as data to be used by tools and unit tests
    rows: list[dict[str, str]] = []  # each row becomes a dict keyed by column name
    with Path(path).open(encoding="utf-8") as handle:  # with closes it even if parsing blows up
        header: Optional[list[str]] = None  # filled when the column line shows up
        for line in handle:  # one line at a time, logs can get long
            if line.startswith("#"):  # ignore all comment lines
                continue  # on to the next line
            cells = next(csv.reader([line]))  # csv reader wants an iterable of lines
            if header is None:  # the first none comment line will be your columns
                header = cells  # the column names
                continue  # the header is not data, don't store it
            rows.append(dict(zip(header, cells)))  # zip pairs each cell with its column
    return rows  # plain dicts, easy to assert on in tests
