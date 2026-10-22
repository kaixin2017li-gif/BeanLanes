"""The plan the song and the chart are both built from.

The audio generator and the chart generator read this one file, which is how
the notes and the drums stay together: they are placed on the same beat grid
rather than lined up by ear afterwards.

Tempo is 120 BPM, so one beat is 500 ms and one 4/4 bar is 2000 ms. The first
four bars are an intro with the drums only, which gives the player time to hear
the beat before the first note arrives.
"""

from __future__ import annotations  # modern hint syntax

BPM = 120.0  # the tempo
BEAT_MS = 60_000.0 / BPM  # 500 ms  # one beat
BEATS_PER_BAR = 4  # 4/4
BAR_MS = BEAT_MS * BEATS_PER_BAR  # 2000 ms  # one bar
BARS = 16  # bars in the song
SONG_MS = BARS * BAR_MS  # 32000 ms  # song length

INTRO_BARS = 4  # drums only, no notes

# Lane patterns, one per bar, read as "which lane on beat 1, beat 2, beat 3, beat 4".
# Lanes are 0..3 = D F J K. The cycle repeats down the chart.
BAR_PATTERNS = (
    (0, 1, 2, 3),  # up the lanes
    (3, 2, 1, 0),  # back down
    (0, 2, 1, 3),  # zig zag
    (1, 3, 0, 2),  # inner pair first
    (0, 1, 0, 3),  # double tap on beat 3
    (2, 3, 2, 1),  # the busiest bar
)  # six bars, then it repeats


def note_times() -> list[tuple[float, int]]:  # the single source of note times
    """(time_ms, lane) for every note in the chart, in play order."""
    notes: list[tuple[float, int]] = []  # the list being built
    for bar in range(BARS):  # every bar
        if bar < INTRO_BARS:  # the intro
            continue  # drums only, skip it
        pattern = BAR_PATTERNS[(bar - INTRO_BARS) % len(BAR_PATTERNS)]  # the pattern for this bar
        for beat, lane in enumerate(pattern):  # each beat of the bar
            notes.append((bar * BAR_MS + beat * BEAT_MS, lane))  # time is bar start plus the beat offset
    return notes  # in play order


def note_count() -> int:  # how many notes the chart gets
    return len(note_times())  # just the list length
