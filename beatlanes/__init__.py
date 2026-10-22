"""BeatLanes - a four lane keyboard rhythm prototype.

Built for 52685 Creative Coding, Assessment 2.

The package is split so the timing rules can be tested without opening a window:

    config      shared settings and geometry
    judge       error = tap - target, and the PERFECT / GOOD / MISS windows
    chart       loading hand-made charts from JSON, taps and hold notes
    songclock   the song clock, and the mixer reading used to check it
    scoring     combo, score and accuracy
    session     one play-through: note states, taps, and hold key-ups
    logbook     the CSV tap log
    calibration the tap-to-metronome offset
    audio       mixer setup, the metronome click and the song
    render      drawing, and nothing else
    hidpi       the Windows display scale, so text is drawn sharp
    app         states, events and the frame loop
"""

__version__ = "0.10.0"  # bumped when something visible changes (0.10: week 12, hold notes and the guide)
