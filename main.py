#!/usr/bin/env python3
# Entry point of the BeatLanes prototype.

# This shows the normal way to run the program:
#   python main.py
# These are used while building and testing:
#   python main.py --selftest
#   python main.py --selftest --chart charts/holds_120.json
#   python main.py --screenshot docs/shots/play.png --at 14000

from __future__ import annotations

import argparse  # the command line flags
import sys  # so the package can be found when run from the repo root
from pathlib import Path  # the flag types

# Allow "python main.py" from the repository root without installing anything.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from beatlanes import config  # the window constants
from beatlanes.app import BeatLanesApp  # the game itself
from beatlanes.chart import load_chart  # to read the chart file
from beatlanes.judge import hold_was_kept  # week 12: the tail rule, so the expected counts use the game's own rule

# Timing errors, in ms, applied to each note in turn by --selftest. The list
# lands inside PERFECT, inside GOOD, and outside GOOD, covering all windows.
SELFTEST_ERROR_CYCLE = (0, 5, -5, 30, -40, 15, 60, -70, 0, 110, -115, 130, -125, 0, 10, -20)

# Week 12: when each hold's key comes up, against its tail, applied to the
# holds in turn. 0 and +40 hold past the tail (the tail finishes it first),
# -60 lets go inside the allowance, -400 lets go far too early and drops it.
SELFTEST_RELEASE_CYCLE = (0, -60, 40, -400)


def selftest_plan(chart) -> list[tuple[int, float, float | None]]:  # one (lane, press, release) per note
    # the single place the prepared run is decided, so the taps, the key-ups
    # and the expected counts can never disagree with each other
    plan: list[tuple[int, float, float | None]] = []  # built up here
    holds_seen = 0  # which release offset the next hold gets
    for position, note in enumerate(chart.notes):  # walk the notes in order
        press = note.time_ms + SELFTEST_ERROR_CYCLE[position % len(SELFTEST_ERROR_CYCLE)]  # the press, offset in time
        release = None  # taps have no key-up that matters
        if note.is_hold:  # a hold needs one
            offset = SELFTEST_RELEASE_CYCLE[holds_seen % len(SELFTEST_RELEASE_CYCLE)]  # this hold's key-up against its tail
            release = max(press + 1.0, note.end_ms + offset)  # never before its own press
            holds_seen += 1  # the next hold gets the next offset
        plan.append((note.lane, float(press), release))  # one entry per note
    return plan  # in note order


def build_selftest_taps(chart) -> list[tuple[int, float]]:  # the prepared presses for --selftest
    # one tap per note, shifted by the error cycle
    return [(lane, press) for lane, press, _release in selftest_plan(chart)]  # (lane, press time) pairs


def build_selftest_releases(chart) -> list[tuple[int, float]]:  # week 12: the prepared key-ups
    # only the holds get one; a chart without holds gets an empty list
    return [(lane, release) for lane, _press, release in selftest_plan(chart) if release is not None]  # (lane, key-up time) pairs


def calibration_taps(bias_ms: float = 18.0) -> list[tuple[int, float]]:  # for the calibrate screenshot or recording
    # taps for the metronome screen, slightly off the beat on purpose, so a
    # recorded calibration shows an offset actually being measured
    from beatlanes.calibration import CalibrationRun  # imported here to keep app startup light

    run = CalibrationRun()  # knows the click layout
    return [(0, run.beat_time_ms(index) + bias_ms) for index in range(run.beats)]  # one tap per click


def expected_counts(chart) -> dict[str, int]:  # the numbers --selftest compares against
    # what the session should produce, derived from the same window settings
    counts = {"perfect": 0, "good": 0, "miss": 0, "stray": 0, "holds_kept": 0, "holds_dropped": 0}  # one bucket each
    for note, (_lane, press, release) in zip(chart.notes, selftest_plan(chart)):  # walk every note with its plan
        error = abs(press - note.time_ms)  # its planned error
        if error > config.GOOD_MS:  # outside both windows
            # too far from the note: the press becomes a stray tap and the note
            # is missed when the song passes it. a hold missed this way never
            # starts, so its key-up does nothing either
            counts["miss"] += 1  # the note dies
            counts["stray"] += 1  # and the press counted as a stray
            continue  # nothing else to decide for this note
        if note.is_hold and not hold_was_kept(release, note.end_ms):  # the press landed but the key came up too early
            counts["miss"] += 1  # a dropped hold is a MISS, not a stray
            counts["holds_dropped"] += 1  # and counted as dropped
            continue  # the press verdict never counts
        if note.is_hold:  # held to the tail
            counts["holds_kept"] += 1  # counted as kept, and scored like its press below
        if error <= config.PERFECT_MS:  # inside PERFECT
            counts["perfect"] += 1  # count it
        else:  # inside GOOD
            counts["good"] += 1  # count it
    return counts  # the expectation


def run_selftest(args) -> int:  # returns 0 on pass, 1 on fail
    # play a whole chart with prepared taps and check the outcome
    chart = load_chart(args.chart)  # the chart to play
    taps = build_selftest_taps(chart)  # the prepared presses
    releases = build_selftest_releases(chart)  # and the key-ups for the holds, if there are any
    app = BeatLanesApp(  # a silent headless app
        chart_path=args.chart,  # from the flags
        song_path=args.song,  # unused here, audio is off
        latency_offset_ms=args.offset if args.offset is not None else 0.0,  # tests run with offset 0
        enable_audio=False,  # headless, no sound
        simulated_taps=taps,  # the prepared presses
        simulated_releases=releases,  # the prepared key-ups
        log_path=args.log,  # where the CSV goes
    )  # app ready
    app.debug = args.debug  # overlay if asked
    app.run()  # play the chart
    report = app.selftest_report()  # what actually happened
    expected = expected_counts(chart)  # what should have happened

    print(f"selftest  chart '{report['chart']}'  {report['notes']} notes  {report['taps_fed']} taps fed  {len(releases)} key-ups fed")  # the header line
    if report["perfect"] is None:  # never reached the results screen
        print("SELFTEST FAILED: the run never reached the results screen")  # say why
        return 1  # failure

    print(f"{'':13} {'expected':>6} {'actual':>9}")  # the column headings
    problems = []  # collected while comparing
    keys = ["perfect", "good", "miss", "stray"]  # every chart has these
    if chart.hold_count:  # week 12: charts with holds also check the tails
        keys += ["holds_kept", "holds_dropped"]  # kept and dropped
    for key in keys:  # compare each count
        wanted = expected[key]  # from the window maths
        got = report[key]  # from the real run
        flag = "" if wanted == got else "   <-- differs"  # marks a mismatch in the printout
        if wanted != got:  # remember it
            problems.append(key)  # for the final verdict
        print(f"{key:13} {wanted:>6} {got:>9}{flag}")  # one row per count
    print(f"{'accuracy':13} {'':>6} {report['accuracy']:>8.1f}%")  # the accuracy row
    print(f"{'best combo':13} {'':>6} {report['best_combo']:>9}")  # the streak
    print(f"log       {report['log']}")  # where the csv went

    if problems:  # any mismatch fails the run
        print(f"SELFTEST FAILED: {', '.join(problems)} did not match")  # name the keys
        return 1  # failure
    print("SELFTEST PASSED")  # every count matched
    return 0  # success


def main(argv: list[str] | None = None) -> int:  # argv override for tests
    # the command line flags
    parser = argparse.ArgumentParser(  # the flag parser
        prog="beatlanes",  # the name in help text
        description="A four lane keyboard rhythm prototype: D F J K.",  # one line about the game
    )  # parser made
    parser.add_argument("--chart", type=Path, default=None, help="chart JSON (default: charts/beatlanes_120.json)")  # which chart file
    parser.add_argument("--song", type=Path, default=None, help="song file (default: assets/beatlanes_120.ogg)")  # which song file
    parser.add_argument("--offset", type=float, default=None, help="latency offset in ms (default: calibration.json)")  # overrides calibration.json
    parser.add_argument("--no-audio", action="store_true", help="run on the tick clock without opening the mixer")  # silent mode
    parser.add_argument("--log", type=Path, default=None, help="where to write the CSV tap log")  # where the csv goes
    parser.add_argument("--debug", action="store_true", help="start with the debug overlay visible")  # overlay on from the start
    parser.add_argument("--selftest", action="store_true", help="play a prepared run headlessly and check it")  # the headless check run
    parser.add_argument("--screenshot", type=Path, default=None, help="save one frame to this PNG and exit")  # capture one frame
    parser.add_argument("--at", type=float, default=12_000.0, help="song time for --screenshot, in ms")  # when to capture
    parser.add_argument(  # another flag
        "--shot-state",  # which screen --screenshot should capture
        choices=("title", "guide", "countdown", "play", "results", "calibrate"),  # the screens a capture can wait for
        default="play",  # play is the usual shot
        help="which screen to capture or record",  # shown by --help
    )  # flag done
    parser.add_argument(  # another flag
        "--shot-taps",  # so the frame shows a real run, not an empty board
        action="store_true",  # a switch, no value
        help="feed the selftest taps while taking a screenshot",  # shown by --help
    )  # flag done
    parser.add_argument(  # another flag
        "--autoplay",  # the game plays itself, for demos and logs
        action="store_true",  # a switch, no value
        help="play the chart automatically",  # shown by --help
    )  # flag done
    parser.add_argument(  # another flag
        "--windowed",  # keep the real window even for an automatic run
        action="store_true",  # a switch, no value
        help="keep a real window and audio device even for an automatic run",  # shown by --help
    )  # flag done
    parser.add_argument(  # another flag
        "--scale",  # draw everything bigger, at real pixels
        type=float,  # 1.5 means 1440x960
        default=None,  # None: follow the Windows display scale, 1.0 elsewhere
        help="UI scale, e.g. 1.5 for a 1440x960 window (default: match the Windows display scale)",  # shown by --help
    )  # flag done
    parser.add_argument(  # another flag
        "--record",  # write the run to an mp4
        type=Path,  # a path, not a string
        default=None,  # off by default
        help="record the run to this mp4 (needs ffmpeg on the path)",  # shown by --help
    )  # flag done
    parser.add_argument("--record-fps", type=int, default=60, help="frame rate for --record")  # footage frame rate
    parser.add_argument(  # another flag
        "--record-scale",  # 2 gives 1920x1280 footage from the 960x640 window
        type=int,  # whole number
        default=2,  # whole number
        help="pixel scale for --record",  # hd by default
    )  # shown by --help
    args = parser.parse_args(argv)  # flag done

    if args.scale is not None and not 0.5 <= args.scale <= 4.0:  # a typo like 15 would open a giant window
        parser.error("--scale must be between 0.5 and 4")  # exits with a usage message

    if args.selftest:  # the headless test run
        return run_selftest(args)  # the headless run, exit code decides

    # some modes need the prepared taps: autoplay shows a real run, results and
    # record need notes actually scored
    auto_taps = None  # the prepared presses, if this mode needs them
    auto_releases = None  # week 12: the prepared key-ups for the holds
    if args.shot_state == "calibrate":  # the calibration screen uses its own taps
        auto_taps = calibration_taps()  # the metronome taps
    else:  # every other mode
        needs_taps = args.autoplay or args.shot_taps or (  # which modes want taps
            args.screenshot is not None and args.shot_state == "results"  # a results shot needs scored notes
        ) or args.record is not None  # recordings always do
        if needs_taps:  # build them once
            chart = load_chart(args.chart)  # the chart that will be played
            auto_taps = build_selftest_taps(chart)  # the same error cycle as --selftest
            auto_releases = build_selftest_releases(chart)  # and the same key-ups

    app = BeatLanesApp(  # the game, wired to the flags
        chart_path=args.chart,  # straight from the flags from here down
        song_path=args.song,  # same
        latency_offset_ms=args.offset,  # same
        enable_audio=not args.no_audio,  # flipped flag
        simulated_taps=auto_taps,  # may be None
        simulated_releases=auto_releases,  # may be None, always None for charts without holds
        windowed=args.windowed,  # keep a window
        record_path=args.record,  # may be None
        record_fps=args.record_fps,  # footage rate
        record_scale=args.record_scale,  # hd factor
        screenshot_path=args.screenshot,  # may be None
        screenshot_at_ms=args.at,  # when to capture
        screenshot_state=args.shot_state,  # which screen
        log_path=args.log,  # may be None
        ui_scale=args.scale,  # None asks the screen
    )  # app ready
    app.debug = args.debug  # overlay if asked
    return app.run()  # the game loop, its return is the exit code


if __name__ == "__main__":  # run the game when called directly
    raise SystemExit(main())  # the exit code reaches the shell
