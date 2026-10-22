# we need sound. a mixer, click and song
# report any errors that happen, dont raise. can play with out sound

import numpy as np  # used when creating the click sample
import pygame  # the mixer lives in pygame
from pathlib import Path  # what kind of path to use for the song

from . import config  # path to the song

MIXER_SETTINGS = {"frequency": 44100, "size": -16, "channels": 2, "buffer": 512}  # use default settings


def init_mixer(**overrides) -> bool:  # bool on purpose: false means run silent
    # open sound device, false will allow you to run with no sound
    settings = dict(MIXER_SETTINGS)  # a copy, so the default dict never gets mutated
    settings.update(overrides)  # used so tests can override
    try:  # init is the part that fails on machines with no sound card
        pygame.mixer.init(**settings)  # this is the line that can fail
    except pygame.error:  # no sound card on pc or a server
        return False  # the documented no-sound path
    return pygame.mixer.get_init() is not None  # double check it really opened


def mixer_ready() -> bool:  # returns true if mixer was able to open
    return pygame.mixer.get_init() is not None  # same check as init, just on demand


def make_click(frequency: float = 1320.0, duration_ms: float = 26.0, volume: float = 0.45):  # builds the metronome blip from scratch
    # a short sine blip for the calibration metronome
    if not mixer_ready():  # no mixer, no click
        return None  # callers must handle no click
    rate, _, channels = pygame.mixer.get_init()  # make it the same format as our mixer
    frames = int(rate * duration_ms / 1000.0)  # amount of samples in our click
    if frames <= 0:  # guard against a zero length click
        return None  # nothing to play
    t = np.arange(frames) / rate  # this will be in seconds
    envelope = np.exp(-t / (duration_ms / 3000.0))  # we want our click to die off fast
    wave = np.sin(2.0 * np.pi * frequency * t) * envelope * volume  # sine at the pitch, shaped and quietened
    samples = np.int16(np.clip(wave, -1.0, 1.0) * 32767)  # since our mixer uses 16 bit
    if channels == 2:  # copy over to make it stereo
        samples = np.repeat(samples[:, None], 2, axis=1)  # same samples left and right
    try:  # making a sound can still fail on odd mixer formats
        return pygame.sndarray.make_sound(np.ascontiguousarray(samples))  # hand the finished array to pygame
    except (pygame.error, ValueError):  # if your mixer format is weird
        return None  # every failure path here returns None, that's the deal


def start_song(path: Path | str | None = None, volume: float = 0.75) -> bool:  # one shot: load, volume, play
    # loads song and starts playing it. false means no music just a clock
    target = Path(path) if path is not None else config.SONG_PATH  # default song comes from config
    if not mixer_ready() or not target.exists():  # couldnt find device or file
        return False  # silence beats a crash
    try:  # a corrupt or missing file throws here
        pygame.mixer.music.load(str(target))  # load before play, the mixer wants it in that order
        pygame.mixer.music.set_volume(volume)  # songs are mastered loud, keep it under control
        pygame.mixer.music.play()  # songclock.start() goes right after this
    except pygame.error:  # file could not be read
        return False  # same deal as above, run without music
    return True  # music is playing


def stop_song() -> None:  # if the music is playing stop it
    if mixer_ready():  # no mixer, nothing to stop
        try:  # see init_mixer, same no-sound-card story
            pygame.mixer.music.stop()  # the actual stop
        except pygame.error:  # already stopped
            pass  # if it throws here the mixer already died, nothing to do


def music_position_ms() -> int:  # will return mixers position or -1
    if not mixer_ready():  # same answer as stop_song, no mixer means no position
        return -1  # -1 is pygame's own no-sound value, keep it
    try:  # the reading can throw if the mixer died between the check and here
        return pygame.mixer.music.get_pos()  # the reading itself
    except pygame.error:  # mixer died while playing
        return -1  # same fallback again, a dead mixer reads as stopped
