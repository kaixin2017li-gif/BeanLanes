#!/usr/bin/env python3
"""Write the chart JSON from the beat plan.

The chart is hand-made in the sense that the lanes and the bar patterns were
chosen by hand in beatplan.py; this script only turns those choices into the
file the game reads, so the note times cannot disagree with the song.

    python tools/make_chart.py
"""

from __future__ import annotations  # modern hint syntax

import json  # the chart is json
import sys  # so the package can be found
from pathlib import Path  # path handling

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # same trick as the tests, run from anywhere

from beatlanes import config  # noqa: E402  # where the chart goes
from tools import beatplan  # noqa: E402  # the shared beat plan


def build_chart() -> dict:  # turn the plan into the dict
    notes = [  # one entry per note
        {"time": int(round(time_ms)), "lane": int(lane)}  # whole ms times, int lanes
        for time_ms, lane in beatplan.note_times()  # from the plan
    ]  # notes done
    return {  # the file contents
        "title": "BeatLanes 120 (generated from the beat plan)",  # says where it came from
        "bpm": beatplan.BPM,  # the tempo
        "bars": beatplan.BARS,  # bars in the song
        "intro_bars": beatplan.INTRO_BARS,  # intro length
        "note_count": len(notes),  # handy for the printout
        "generated_by": "tools/make_chart.py",  # provenance
        "notes": notes,  # the notes themselves
    }  # chart done


def main() -> int:  # the entry point
    chart = build_chart()  # build it
    config.CHART_DIR.mkdir(parents=True, exist_ok=True)  # the folder may not exist
    target = config.CHART_PATH  # the standard spot
    target.write_text(json.dumps(chart, indent=2) + "\n", encoding="utf-8")  # indent so a human can read it
    print(f"wrote {target}  ({chart['note_count']} notes, {chart['bars']} bars at {chart['bpm']:.0f} BPM)")  # say what was written
    return 0  # success


if __name__ == "__main__":  # run directly
    raise SystemExit(main())  # the exit code reaches the shell
