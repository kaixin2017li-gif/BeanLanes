# Song time for the game.
# Two readings exist in pygame and both are used, but only one is trusted:
# get_ticks() is the clock the judgement runs on, the mixer reading is only
# logged beside it so the two can be compared.
# The clock sources are passed in (not imported) so tests can fake the time.

from __future__ import annotations

from typing import Callable, Optional  # Callable is how the clock sources get injected

TickProvider = Callable[[], int]  # a function that returns a tick count
MixerPosProvider = Callable[[], int]  # a function that returns a mixer position


def _pygame_ticks() -> int:  # the real tick clock
    import pygame  # imported late so this module loads without pygame

    return pygame.time.get_ticks()  # ms since pygame.init(), the machine's own clock


def _pygame_mixer_pos() -> int:  # the mixer's own reading
    import pygame  # same late import as above, same reason

    return pygame.mixer.music.get_pos()  # ms into the current track


class SongClock:  # converts tick readings into song time
    def __init__(  # every knob a test could want is a keyword here
        self,  # nothing special, just self
        latency_offset_ms: float = 0.0,  # the measured calibration offset
        ticks_provider: Optional[TickProvider] = None,  # injectable for tests
        mixer_pos_provider: Optional[MixerPosProvider] = None,  # injectable too
    ) -> None:  # constructor, returns nothing
        self.latency_offset_ms = float(latency_offset_ms)  # subtracted from song time
        self._ticks: TickProvider = ticks_provider or _pygame_ticks  # default to the real clock
        self._mixer_pos: MixerPosProvider = mixer_pos_provider or _pygame_mixer_pos  # same default-or-inject pattern as the ticks line above
        self._start_ticks: Optional[int] = None  # tick reading when the music started
        self.samples: list[tuple[float, Optional[int]]] = []  # (song_ms, mixer_ms) pairs kept for later checks

    def start(self, at_ticks: Optional[int] = None) -> None:  # mark the moment the music started
        self._start_ticks = self._ticks() if at_ticks is None else int(at_ticks)  # tests pass a fixed tick, the game just reads the clock
        self.samples.clear()  # old run's samples belong to that run

    def stop(self) -> None:  # forget the start, the clock reads zero again
        self._start_ticks = None  # no start means song_time_ms sits at zero again

    @property  # reads like a value, no brackets
    def running(self) -> bool:  # True once start() has been called
        return self._start_ticks is not None  # that is the whole definition

    @property  # same property trick, keeps call sites tidy
    def latency_offset(self) -> float:  # read the offset
        return self.latency_offset_ms  # the float the game actually uses

    @latency_offset.setter  # setter so calibration can fix a live clock
    def latency_offset(self, value: float) -> None:  # change it after a calibration run
        self.latency_offset_ms = float(value)  # float again, keep the type honest

    def tick_reading_ms(self) -> int:  # the raw reading, for the debug panel
        return int(self._ticks())  # int, ticks always are

    def song_time_ms(self, at_ticks: Optional[int] = None) -> float:  # the primary reading the whole game runs on
        # the primary clock: (now - start) - offset. This is what the judgement uses.
        if self._start_ticks is None:  # not started yet
            return 0.0  # zero until the music starts
        now = self._ticks() if at_ticks is None else int(at_ticks)  # same injectable reading as start(), one pattern everywhere
        return (now - self._start_ticks) - self.latency_offset_ms  # elapsed minus the calibration offset

    def mixer_position_ms(self) -> Optional[int]:  # the mixer's opinion, or None
        try:  # get_pos can throw if the mixer is dead
            position = int(self._mixer_pos())  # the one line that can blow up
        except Exception:  # a broken mixer must not crash the game
            return None  # swallow it, the game keeps running silent
        return None if position < 0 else position  # -1 means nothing is playing

    def drift_ms(self, at_ticks: Optional[int] = None) -> Optional[float]:  # mixer vs tick clock
        position = self.mixer_position_ms()  # None means nothing usable came back
        if position is None:  # no usable reading
            return None  # no reading, no drift number
        return self.song_time_ms(at_ticks) - position  # positive means the tick clock runs ahead of the audio

    def sample(self, at_ticks: Optional[int] = None) -> tuple[float, Optional[int]]:  # read both clocks and remember the pair
        # read both clocks at once and keep the pair for later comparison
        song_ms = self.song_time_ms(at_ticks)  # both clocks read at the same instant
        position = self.mixer_position_ms()  # second reading, as close as possible to the first
        self.samples.append((song_ms, position))  # kept so the log analyser can graph the drift later
        return song_ms, position  # handy for tests too
