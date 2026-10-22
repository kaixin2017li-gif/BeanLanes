#!/usr/bin/env python3
"""Synthesise the backing track.

The song is generated rather than downloaded. Two reasons: the repository then
contains no third party audio at all, so there is no licence to track and no
link that can go dead, and the generator is itself part of the project's code.

The arrangement follows tools/beatplan.py, which is the same file the chart is
generated from, so the drums and the notes share a grid:

    bars 0-3    intro - kick and hats only
    bars 4-7    bass joins
    bars 8-15   lead arpeggio joins
    bar 15      everything, then a fade

    python tools/make_song.py             # -> assets/beatlanes_120.ogg
    python tools/make_song.py --keep-wav  # also keep the intermediate wav
"""

from __future__ import annotations  # modern hint syntax

import argparse  # the flags
import shutil  # to find ffmpeg
import subprocess  # to run ffmpeg
import sys  # so the package can be found
from pathlib import Path  # path handling

import numpy as np  # the maths on samples
import soundfile as sf  # writes the audio files

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # same trick as the tests, run from anywhere

from beatlanes import config  # noqa: E402  # where the song goes
from tools import beatplan  # noqa: E402  # the shared beat plan

SAMPLE_RATE = 44_100  # cd quality
TAIL_MS = 1_000  # room for the last hits to ring out

# One root note per bar, cycling through the progression.
BASS_HZ = (55.00, 55.00, 43.65, 49.00)  # A1 A1 F1 G1
LEAD_HZ = (440.00, 523.25, 587.33, 659.25, 783.99, 659.25, 587.33, 523.25)  # the melody, one octave of A minor walks


# --- the instruments --------------------------------------------------------
def _decay(length: int, tau_seconds: float) -> np.ndarray:
    return np.exp(-np.arange(length) / (SAMPLE_RATE * tau_seconds))  # one pole decay curve
  # exponential fade to zero

def kick(duration: float = 0.30) -> np.ndarray:  # the drum
    """Sine with a fast downward pitch sweep: the usual cheap kick drum."""
    length = int(SAMPLE_RATE * duration)  # how many samples
    t = np.arange(length) / SAMPLE_RATE  # time in seconds
    frequency = 48.0 + 120.0 * np.exp(-t / 0.035)  # starts high, drops fast
    phase = 2.0 * np.pi * np.cumsum(frequency) / SAMPLE_RATE  # phase from the changing pitch
    return np.sin(phase) * _decay(length, 0.075)  # and decays


def hat(duration: float = 0.06, rng: np.random.Generator | None = None) -> np.ndarray:  # the tick on top
    """Filtered noise with a very short decay."""
    length = int(SAMPLE_RATE * duration)  # sample count
    noise = (rng or np.random.default_rng(11)).standard_normal(length)  # plain noise, seeded
    return noise * _decay(length, 0.010) * 0.30  # cut short and quiet


def bass(frequency: float, duration: float = 0.42) -> np.ndarray:  # the low end
    """Sine plus a soft saw, which gives the low end some edge."""
    length = int(SAMPLE_RATE * duration)  # sample count
    t = np.arange(length) / SAMPLE_RATE  # time in seconds
    saw = 2.0 * (t * frequency - np.floor(0.5 + t * frequency))  # a cheap sawtooth
    tone = 0.60 * np.sin(2.0 * np.pi * frequency * t) + 0.25 * saw  # sine body plus saw edge
    envelope = np.minimum(1.0, t / 0.006) * _decay(length, 0.16)  # soft attack, then decay
    return tone * envelope * 0.55  # quiet overall


def lead(frequency: float, duration: float = 0.26) -> np.ndarray:  # the melody on top
    """Triangle wave: quiet, sits above the bass."""
    length = int(SAMPLE_RATE * duration)  # sample count
    t = np.arange(length) / SAMPLE_RATE  # time in seconds
    triangle = 2.0 * np.abs(2.0 * (t * frequency - np.floor(0.5 + t * frequency))) - 1.0  # a triangle wave
    envelope = np.minimum(1.0, t / 0.004) * _decay(length, 0.075)  # fast attack, quick decay
    return triangle * envelope * 0.16  # quiet, it sits above the bass


def place(track: np.ndarray, sound: np.ndarray, at_ms: float, gain: float = 1.0) -> None:  # drop a sound into the track
    """Add a sound into the track at a time in milliseconds."""
    start = int(SAMPLE_RATE * at_ms / 1000.0)  # ms to sample index
    if start >= len(track):  # past the end
        return  # nothing to add
    end = min(len(track), start + len(sound))  # clip at the end of the track
    track[start:end] += sound[: end - start] * gain  # mix it in


# --- arrangement ------------------------------------------------------------
def build_track() -> np.ndarray:  # the whole arrangement
    total = int(SAMPLE_RATE * (beatplan.SONG_MS + TAIL_MS) / 1000.0)  # song length plus the tail
    track = np.zeros(total)  # silence to start
    rng = np.random.default_rng(11)  # seeded, so the hats are identical every build

    for bar in range(beatplan.BARS):  # each bar
        bar_ms = bar * beatplan.BAR_MS  # bar start in ms
        intro = bar < beatplan.INTRO_BARS  # the first four bars

        for beat in range(beatplan.BEATS_PER_BAR):  # each beat
            at_ms = bar_ms + beat * beatplan.BEAT_MS  # beat time in ms
            if beat % 2 == 0:  # kick on the strong beats
                place(track, kick(), at_ms, 0.95)  # the downbeat thump
            place(track, hat(0.07 if beat % 2 == 0 else 0.05, rng), at_ms, 0.50 if intro else 0.75)  # hats every beat, louder once the song starts
            if beat % 2 == 1:  # off-beats
                # extra hat on the off-beat, which is what gives it a pulse
                place(track, hat(0.04, rng), at_ms + beatplan.BEAT_MS / 2.0, 0.40)  # the in-between tick

        if not intro:  # after the intro
            root = BASS_HZ[bar % len(BASS_HZ)]  # this bar's root note
            place(track, bass(root), bar_ms, 0.90)  # bass on beat 1
            place(track, bass(root), bar_ms + 2 * beatplan.BEAT_MS, 0.85)  # and beat 3
            place(track, bass(root * 2.0), bar_ms + 3 * beatplan.BEAT_MS, 0.70)  # an octave up on beat 4

        if bar >= 8:  # the lead joins halfway
            for beat in range(beatplan.BEATS_PER_BAR):  # each beat
                index = (bar * beatplan.BEATS_PER_BAR + beat) % len(LEAD_HZ)  # walk the melody
                place(track, lead(LEAD_HZ[index]), bar_ms + beat * beatplan.BEAT_MS, 0.80)  # one note per beat

    return track  # the raw arrangement


def finalise(track: np.ndarray) -> np.ndarray:  # the finishing pass
    """Normalise, then fade in and out so the file does not start or end with a click."""
    peak = float(np.max(np.abs(track))) if track.size else 0.0  # the loudest sample
    if peak > 0:  # never divide by zero
        track = track / peak * 0.89  # normalise to just under full scale
    fade_in = int(SAMPLE_RATE * 0.04)  # 40 ms fade in
    fade_out = int(SAMPLE_RATE * 0.90)  # most of a second fade out
    track[:fade_in] *= np.linspace(0.0, 1.0, fade_in)  # ramp up
    track[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)  # ramp down
    return track  # the finished track


# --- output -----------------------------------------------------------------
def write_audio(track: np.ndarray, target: Path, keep_wav: bool) -> None:  # get the file onto disk
    """Encode the track. Vorbis is written directly when the local soundfile
    supports it, otherwise a wav is written and ffmpeg does the encoding."""
    target.parent.mkdir(parents=True, exist_ok=True)  # the folder may not exist
    samples = track.astype(np.float32)  # float32 is what soundfile wants

    try:  # direct ogg first
        sf.write(str(target), samples, SAMPLE_RATE, format="OGG", subtype="VORBIS")  # vorbis straight from soundfile
        if keep_wav:  # wav copy wanted too
            sf.write(str(target.with_suffix(".wav")), samples, SAMPLE_RATE)  # write it as well
        return  # done the easy way
    except Exception as exc:  # libsndfile without vorbis support  # this build of libsndfile lacks vorbis
        print(f"soundfile could not write OGG ({exc}); falling back to ffmpeg")  # say so

    wav_path = target.with_suffix(".wav")  # the wav route
    sf.write(str(wav_path), samples, SAMPLE_RATE)  # wav always works
    ffmpeg = shutil.which("ffmpeg")  # find ffmpeg
    if ffmpeg is None:  # no ffmpeg
        print(f"ffmpeg not found: leaving {wav_path} in place, and the game will play the wav")  # the wav still plays fine
        return  # leave the wav, it still plays
    subprocess.run(  # convert with ffmpeg
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(wav_path), "-c:a", "libvorbis", "-q:a", "5", str(target)],  # wav in, vorbis out
        check=True,  # fail loudly if ffmpeg fails
    )  # converted
    if not keep_wav:  # wav not wanted
        wav_path.unlink(missing_ok=True)  # delete it


def main() -> int:  # the entry point
    parser = argparse.ArgumentParser(description="Generate the BeatLanes backing track.")  # the flags
    parser.add_argument("--out", type=Path, default=config.SONG_PATH, help="output file")  # where the ogg goes
    parser.add_argument("--keep-wav", action="store_true", help="keep the intermediate wav too")  # keep the intermediate wav
    args = parser.parse_args()  # parse them

    track = finalise(build_track())  # build then finish
    write_audio(track, Path(args.out), args.keep_wav)  # write it out

    seconds = len(track) / SAMPLE_RATE  # for the printout
    print(  # the summary line
        f"wrote {args.out}  ({seconds:.1f} s, {beatplan.BARS} bars at {beatplan.BPM:.0f} BPM, "  # length and tempo
        f"peak {float(np.max(np.abs(track))):.2f})"  # and the peak
    )  # line done
    return 0  # success


if __name__ == "__main__":  # run directly
    raise SystemExit(main())  # the exit code reaches the shell
