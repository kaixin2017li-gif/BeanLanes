#!/usr/bin/env python3
"""Read a tap log and report on the timing.

This is the tool behind the calibration step in the plan. A finished run writes
a CSV with one row per note, and the questions worth asking of it are:

    how did the judgements come out?
    what did the errors look like - a constant shift, or random scatter?
    were taps early or late, and by how much on average?
    did the mixer reading agree with the tick clock?

A constant shift is what the calibration offset is for: if every tap is 40 ms
late, nothing is wrong with the playing, the folder offset is wrong. Scatter
with no shift means the player, not the machine.

    python tools/analyse_log.py logs/taps-20260916-095210.csv
    python tools/analyse_log.py logs/taps-*.csv --plot docs/shots/error-histogram.png
"""

from __future__ import annotations  # modern hint syntax

import argparse  # the command line flags
import statistics  # mean, median, spread
import sys  # so the package can be found
from pathlib import Path  # path handling

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # same trick as the tests, run the tool from anywhere

from beatlanes import config  # noqa: E402  # the tolerance numbers for context
from beatlanes.logbook import read_log  # noqa: E402  # the csv reader


def load_rows(path: Path) -> list[dict[str, str]]:  # load with a friendly error
    rows = read_log(path)  # the shared reader
    if not rows:  # empty file
        raise SystemExit(f"{path} has no rows")  # say so and stop
    return rows  # the rows


def errors_from(rows: list[dict[str, str]]) -> list[float]:  # just the error column
    return [float(row["error_ms"]) for row in rows if row.get("error_ms")]  # blank rows (misses) do not count


def mixer_differences(rows: list[dict[str, str]]) -> list[float]:  # the two clocks compared
    """tick clock song time minus the mixer reading, per tap.

    For a log written by real key presses this difference is the input and audio
    delay that the calibration offset has to absorb. For an automatic run the
    mixer is read on the frame the tap was released, so the figure carries up to
    one frame of the feed delay on top.
    """
    differences: list[float] = []  # the differences, one per tap
    for row in rows:  # each row
        if row.get("error_ms") and row.get("mixer_pos_ms"):  # only rows with both readings
            differences.append(float(row["tap_ms"]) - float(row["mixer_pos_ms"]))  # tap time minus mixer time
    return differences  # the list


def describe(values: list[float]) -> str:  # one line of stats
    if not values:  # no values
        return "none"  # say none
    return (  # the stats line
        f"mean {statistics.fmean(values):+7.1f} ms   "  # the average
        f"median {statistics.median(values):+7.1f} ms   "  # the middle
        f"spread {statistics.pstdev(values):5.1f} ms   "  # the spread
        f"range {min(values):+.0f} to {max(values):+.0f} ms"  # and the range
    )  # glued together


def verdict_tally(rows: list[dict[str, str]]) -> dict[str, int]:  # count each verdict
    tally = {"PERFECT": 0, "GOOD": 0, "MISS": 0}  # one bucket each
    for row in rows:  # every row
        tally[row["verdict"]] = tally.get(row["verdict"], 0) + 1  # add one, tolerating surprise verdicts
    return tally  # the counts


def hold_tally(rows: list[dict[str, str]]) -> dict[str, int]:  # week 12: what happened to the hold notes
    # logs from before week 12 have no hold_ms column, so they count no holds
    tally = {"holds": 0, "kept": 0, "dropped": 0, "never_pressed": 0}  # one bucket each
    for row in rows:  # every row
        if int(row.get("hold_ms") or 0) <= 0:  # a tap, or an old log
            continue  # not a hold
        tally["holds"] += 1  # one more hold
        if row["verdict"] != "MISS":  # scored, so it was held to the tail
            tally["kept"] += 1  # kept
        elif row.get("tap_ms"):  # a MISS with a press means the key came up too early
            tally["dropped"] += 1  # dropped
        else:  # a MISS with no press at all
            tally["never_pressed"] += 1  # missed at the head, like a tap
    return tally  # the counts


def print_report(path: Path, rows: list[dict[str, str]]) -> None:  # the whole console report
    tally = verdict_tally(rows)  # verdict counts
    errors = errors_from(rows)  # the errors
    early = [value for value in errors if value < 0]  # negative side
    late = [value for value in errors if value > 0]  # positive side

    print(f"log        {path}")  # which log
    print(f"notes      {len(rows)}")  # how many notes
    print(  # the verdict line, split for width
        f"verdicts   PERFECT {tally['PERFECT']}   GOOD {tally['GOOD']}   MISS {tally['MISS']}"  # the three buckets
    )  # printed
    holds = hold_tally(rows)  # week 12
    if holds["holds"]:  # only logs with holds get the line
        print(  # the hold line, split for width
            f"holds      {holds['holds']}   kept {holds['kept']}   dropped {holds['dropped']}   never pressed {holds['never_pressed']}"  # what happened to the tails
        )  # printed
    print()  # spacer
    print(f"all taps   {describe(errors)}")  # everything
    print(f"early      {describe(early)}   ({len(early)} taps)")  # the early taps
    print(f"late       {describe(late)}   ({len(late)} taps)")  # the late taps

    if errors:  # the calibration suggestion
        mean = statistics.fmean(errors)  # the mean
        median = statistics.median(errors)  # and the median
        print()  # spacer
        print(f"suggested calibration offset  {median:+.0f} ms  (median of the taps)")  # the median is what gets saved
        print(  # the suggestion, split for width
            "  a large mean and median the same way round means the offset is "  # the shift vs scatter rule
            f"wrong, not the player. mean {mean:+.0f} ms against median {median:+.0f} ms"  # both numbers for comparison
        )  # printed

    differences = mixer_differences(rows)  # the mixer column
    print()  # spacer
    if differences:  # readings exist
        print(f"tick clock vs mixer        {describe(differences)}")  # the comparison line
        print("  each figure is tap_ms - mixer_pos_ms for the same tap")  # what the figure means
    else:  # none exist
        print("tick clock vs mixer        no mixer readings in this log")  # say so
        print("  the mixer column is only filled when the music is actually playing")  # and why


def main() -> int:  # the tool entry point
    parser = argparse.ArgumentParser(description="Analyse a BeatLanes tap log.")  # the flags
    parser.add_argument("logs", type=Path, nargs="+")  # one or more logs
    parser.add_argument("--plot", type=Path, default=None, help="write an error histogram to this PNG")  # optional histogram
    args = parser.parse_args()  # parse them

    for path in args.logs:  # each log
        rows = load_rows(path)  # load it
        print_report(path, rows)  # print the report
        print()  # spacer between logs

    if args.plot is not None and args.logs:  # the plot, if asked
        rows = load_rows(args.logs[0])  # first log only
        write_plot(errors_from(rows), args.plot, args.logs[0].name)  # write it
    return 0  # success


def write_plot(errors: list[float], target: Path, title: str) -> None:  # the histogram writer
    """A histogram of the tap errors, with the judgement windows marked."""  # the picture version of the report
    try:  # matplotlib is optional
        import matplotlib  # import it

        matplotlib.use("Agg")  # headless backend, no window
        import matplotlib.pyplot as plt  # the plotting part
    except ImportError:  # not installed
        print("matplotlib is not installed, so no plot was written")  # say so
        return  # carry on

    if not errors:  # no errors to plot
        print("no scored taps in this log, so no plot was written")  # say so
        return  # carry on

    target.parent.mkdir(parents=True, exist_ok=True)  # the folder may not exist
    figure, axes = plt.subplots(figsize=(7.2, 3.6), dpi=140)  # one wide figure
    span = max(200.0, max(abs(value) for value in errors) * 1.2)  # x range covers the worst tap
    axes.hist(errors, bins=24, range=(-span, span), color="#4d7fd0", edgecolor="#1a2a44")  # the histogram itself
    for edge, colour, label in (  # the window edges
        (-config.GOOD_MS, "#e07070", f"GOOD -{config.GOOD_MS:.0f} ms"),  # good edge, early side
        (-config.PERFECT_MS, "#76e6be", f"PERFECT -{config.PERFECT_MS:.0f} ms"),  # perfect edge, early side
        (config.PERFECT_MS, "#76e6be", f"PERFECT +{config.PERFECT_MS:.0f} ms"),  # perfect edge, late side
        (config.GOOD_MS, "#e07070", f"GOOD +{config.GOOD_MS:.0f} ms"),  # good edge, late side
    ):  # four edges, in order
        axes.axvline(edge, color=colour, linewidth=1.2, linestyle="--", label=label)  # one dashed line each
    axes.axvline(0, color="#1a2a44", linewidth=1.0)  # zero error, solid
    axes.set_xlabel("tap error (ms)   negative = early, positive = late")  # the x label
    axes.set_ylabel("notes")  # the y label
    axes.set_title(f"BeatLanes tap errors - {title}")  # which log it came from
    axes.legend(fontsize=7, frameon=False)  # small legend
    figure.tight_layout()  # fit everything in
    figure.savefig(target)  # write the png
    print(f"wrote {target}")  # where it went


if __name__ == "__main__":  # run directly
    raise SystemExit(main())  # the exit code reaches the shell
