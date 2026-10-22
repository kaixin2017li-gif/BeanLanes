# Sharp text on scaled screens.
#
# The week 11 blur. Windows laptops usually run the desktop at 125% or 150%.
# A program that does not say it understands that scale is drawn at 100% and
# then stretched by Windows, so the 960x640 frame became a 1440x960 bitmap
# blown up with a smooth filter: every letter soft, gaps between letters
# uneven (each pixel became one or two). The only real fix is to draw at the
# real size. claim_dpi_awareness tells Windows we do that, and returns the
# scale it is running at; the app then draws everything at that scale.
#
# Nothing here imports pygame, and it is a no-op off Windows, so the tests run
# the same on every machine.

from __future__ import annotations

import math  # floor for the scale steps
import sys  # the platform check

from . import config  # the design size the window is measured against

# Room left for the title bar and the taskbar. A 150% laptop at 1920x1080 has
# 1080 rows; 960 for the frame plus ~46 for the title bar plus ~72 for the
# taskbar does not fit, so the window steps down a little instead.
SCREEN_WIDTH_SHARE = 0.95  # at most this much of the desktop width
SCREEN_HEIGHT_SHARE = 0.87  # at most this much of the desktop height
SCALE_STEP = 0.05  # scales are rounded down to this, so sizes stay tidy


def claim_dpi_awareness() -> float:  # tell Windows we draw at real pixels, get its scale back
    """On Windows: mark the process DPI aware and return the desktop scale
    (1.0 at 100%, 1.5 at 150%). Anywhere else, or if any call fails, 1.0 -
    which is exactly the old behaviour."""
    if sys.platform != "win32":  # macOS and Linux handle this differently
        return 1.0  # unchanged
    import ctypes  # only on Windows, the calls below do not exist anywhere else

    try:  # Windows 8.1 and later
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 2 = per monitor aware; an error code just means it was already set
    except Exception:  # older Windows has no shcore
        try:  # Vista and later
            ctypes.windll.user32.SetProcessDPIAware()  # system aware, good enough
        except Exception:  # nothing worked
            return 1.0  # Windows keeps stretching, same as before
    try:  # Windows 10 1607 and later
        dpi = ctypes.windll.user32.GetDpiForSystem()  # 96 at 100%, 144 at 150%
    except Exception:  # older: ask the screen's device context
        hdc = ctypes.windll.user32.GetDC(0)  # the whole screen
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # 88 = LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)  # give it back
    return max(1.0, dpi / 96.0) if dpi else 1.0  # 96 dpi is 100%


def fit_scale(wanted: float, desktop: tuple[int, int]) -> float:  # the largest scale that still fits the screen
    """wanted capped so the window fits on the desktop, rounded down to a
    SCALE_STEP, never below 1.0 (a 960x640 window is the smallest we make)."""
    width, height = desktop  # the screen in real pixels
    if width <= 0 or height <= 0:  # no answer from the display
        return max(1.0, wanted)  # trust the scale
    room = min(  # the tightest limit wins
        wanted,  # what Windows asked for
        width * SCREEN_WIDTH_SHARE / config.DESIGN_WIDTH,  # what the width allows
        height * SCREEN_HEIGHT_SHARE / config.DESIGN_HEIGHT,  # what the height allows
    )  # limit found
    stepped = math.floor(room / SCALE_STEP + 1e-6) * SCALE_STEP  # round down; the epsilon keeps 1.5 from becoming 1.45
    return max(1.0, round(stepped, 2))  # tidy float, never smaller than the design
