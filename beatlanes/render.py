"""Drawing.

This module knows about rectangles, colours and fonts, and nothing about timing
rules. Keeping it separate means the parts that decide the outcome of a run can
be tested without opening a window.

Anything that needs a song time is given one; nothing here reads a clock. The
few animations on the title screen are driven by an ambient time handed in by
the caller, so a headless screenshot still produces the same frame every time.

The heavier looks - gradient notes, glows, hit bursts, lane beams - are
precomputed sprites stamped with an alpha, not per-frame pixel work. pygame has
no blur, so every glow here is a stack of soft stamped layers built once and
cached.

Week 11 pass: one highway instead of four loose strips, a side HUD so the
playfield stays clear, key caps drawn the same way on every screen, a MISS
popup, and a timing strip on the results screen.

Week 12: hold notes (a body from the head up to the tail, pinned to the line
while the key is down) and the short guide screen.

Every length here is written in design pixels (the 960x640 layout) and goes
through _s() on its way to the screen, so the same code draws a sharp frame at
any UI_SCALE. Font sizes and letter tracking stay in design pixels all the way
into font() and tracked_text(), which scale them themselves. Everything else a
helper takes - positions, rects, radii, line widths - is already in screen
pixels.
"""

from __future__ import annotations  # lets the type hints use modern syntax

import math  # burst maths, arcs, the title pulse
import random  # seeds the burst angles and the background dust
import statistics  # the median on the calibration screen
from typing import Iterable, Optional, Sequence  # type hints

import pygame  # surfaces, drawing, fonts
import pygame.gfxdraw  # anti-aliased circles, the plain draw module has none

from . import __version__, config  # the shared settings, and the build number for the title
from .judge import Verdict  # verdict names
from .session import LiveNote, NoteState, PlaySession  # what a run looks like while playing

Colour = tuple[int, int, int]  # shorter hints below

# One tint per lane, so a note's lane is readable at a glance. The four are
# picked to stay distinct from each other and from the verdict colours.
LANE_TINTS = (
    (126, 196, 255),  # ice blue
    (178, 156, 255),  # violet
    (255, 150, 205),  # pink
    (108, 226, 214),  # mint
)  # one tint per lane, in lane order

VERDICT_COLOURS = {  # the popup words get these
    Verdict.PERFECT: config.PERFECT_COLOUR,  # green
    Verdict.GOOD: config.GOOD_COLOUR,  # amber
    Verdict.MISS: config.MISS_COLOUR,  # red
}  # three entries

# Rank grades on the results screen, coolest to warmest as the run gets worse.
RANK_COLOURS = {
    "S": (255, 214, 102),  # gold, the chase rank
    "A": config.PERFECT_COLOUR,  # green
    "B": config.HIT_FLASH,  # the cyan accent
    "C": config.GOOD_COLOUR,  # amber
    "D": config.MISS_COLOUR,  # red
}  # five ranks

LOGO_GRADIENT = (config.HIT_FLASH, config.ACCENT_VIOLET, config.ACCENT_PINK)  # left to right across the logo

_FONTS: dict[tuple[int, bool, bool], pygame.font.Font] = {}  # the font cache, keyed by size/bold/mono


def _s(value: float) -> int:  # design px -> screen px, whole
    """A design-pixel length in screen pixels. At UI_SCALE 1.0 it is the same number."""
    return int(round(value * config.UI_SCALE))  # rounded, so 1 px at 1.5x becomes 2 and not 1


def _sf(value: float) -> float:  # design px -> screen px, kept fractional
    return value * config.UI_SCALE  # for speeds and eased distances that get rounded later


def set_ui_scale(scale: float) -> None:  # change the scale and drop everything built at the old one
    """Called by the app before the window opens (and by the tests)."""
    changed = float(scale) != config.UI_SCALE  # the tests open many apps at the same scale
    config.apply_ui_scale(scale)  # window, lanes, notes, judgement line
    if not changed:  # everything cached is still the right size
        return  # keep it
    _FONTS.clear()  # font objects are per pixel size
    _TEXT_CACHE.clear()  # rendered strings
    _SPRITES.clear()  # highway, notes, glows, panels

# first one found wins. segoeui is there for Windows, where avenir and
# helvetica do not exist and the old list fell all the way back to arial
UI_FONT = ("avenirnext", "segoeui", "helveticaneue", "helvetica", "arial")  # ui text
MONO_FONT = ("menlo", "consolas", "couriernew")  # mono for numbers and status lines


# one font object per (size, bold, mono), built on first use
def font(size: int, bold: bool = False, mono: bool = False) -> pygame.font.Font:
    """Cached font lookup with a fallback to Pygame's built-in font."""
    key = (size, bold, mono)  # fonts are cached by size/weight
    cached = _FONTS.get(key)  # reuse it if we have it
    if cached is not None:  # cache hit
        return cached  # done
    family = ",".join(MONO_FONT if mono else UI_FONT)  # pick the family list
    try:  # fall back to pygame's built-in font if sys fonts fail
        loaded = pygame.font.SysFont(family, max(1, _s(size)), bold=bold)  # try the system font first, at the scaled size
    except Exception:  # any font problem lands here
        loaded = pygame.font.Font(None, max(1, _s(size)))  # the built-in fallback
    if not _FONTS:  # the first font since pygame started
        # pygame.quit() frees every font, and a cached one used after the next
        # pygame.init() crashes the interpreter (the tests open and close
        # pygame many times). pygame calls each registered quit function once,
        # so it is registered again with the first font of every session.
        pygame.register_quit(_FONTS.clear)  # forget them when pygame shuts down
    _FONTS[key] = loaded  # remember it
    return loaded  # fresh build, now cached


# --- geometry ---------------------------------------------------------------
def lanes_width() -> float:  # total width of the four lanes and their gaps
    return config.LANE_COUNT * config.LANE_WIDTH + (config.LANE_COUNT - 1) * config.LANE_GAP  # lanes plus the gaps between them


def lane_left() -> float:  # x of the first lane, so the block sits centred
    return (config.WINDOW_WIDTH - lanes_width()) / 2.0  # centre the lane block


def lane_rect(index: int) -> pygame.Rect:  # full-height rectangle for one lane
    """Full-height rectangle for one lane."""
    x = lane_left() + index * (config.LANE_WIDTH + config.LANE_GAP)  # lane's left edge
    return pygame.Rect(int(x), 0, config.LANE_WIDTH, config.WINDOW_HEIGHT)  # full height strip


def lane_centre_x(index: int) -> float:  # centre x of one lane
    return lane_left() + index * (config.LANE_WIDTH + config.LANE_GAP) + config.LANE_WIDTH / 2.0  # left edge + half width


HIGHWAY_PAD = 12  # how far the highway sticks out past the outer lanes


def highway_rect() -> pygame.Rect:  # the dark strip all four lanes sit on
    left = int(lane_left()) - _s(HIGHWAY_PAD)  # a little wider than the lanes
    return pygame.Rect(left, 0, int(lanes_width()) + _s(HIGHWAY_PAD) * 2, config.WINDOW_HEIGHT)  # full height


def y_for_time(time_ms: float, song_ms: float) -> float:  # screen y of a moment in the song
    """Screen y of a moment in the song.

    The conversion is the same one LiveNote.screen_y uses, so the beat grid and
    the notes can never disagree about where the music is.
    """
    px_per_ms = (config.JUDGE_LINE_Y - config.NOTE_SPAWN_Y) / config.APPROACH_MS  # scroll speed
    return config.JUDGE_LINE_Y - (time_ms - song_ms) * px_per_ms  # on the line exactly at its target time


def receptor_rect(index: int) -> pygame.Rect:  # the key slot drawn on the line
    """The key slot on the judgement line for one lane."""
    rect = pygame.Rect(0, 0, config.NOTE_WIDTH + _s(8), _s(38))  # slightly wider than a note
    rect.center = (int(lane_centre_x(index)), int(config.JUDGE_LINE_Y))  # centred on the line, so notes land in it
    return rect  # caller may need the area


# --- colour helpers ---------------------------------------------------------
def _lerp_colour(a: Colour, b: Colour, t: float) -> Colour:  # blend two colours, t=0 gives a, t=1 gives b
    """Linear blend between two colours; t = 0 gives a, t = 1 gives b."""
    t = max(0.0, min(1.0, t))  # clamp, callers pass ages that can overshoot
    return (  # blend each channel
        int(a[0] + (b[0] - a[0]) * t),  # red channel
        int(a[1] + (b[1] - a[1]) * t),  # green channel
        int(a[2] + (b[2] - a[2]) * t),  # blue channel
    )  # tuple back together


def _lighten(colour: Colour, amount: float) -> Colour:  # blend towards white
    return _lerp_colour(colour, (255, 255, 255), amount)  # amount 0..1


def _darken(colour: Colour, amount: float) -> Colour:  # blend towards the background
    return _lerp_colour(colour, config.BACKGROUND_BOTTOM, amount)  # amount 0..1


def _gradient_at(stops: Sequence[Colour], t: float) -> Colour:  # a colour along a multi-stop gradient
    t = max(0.0, min(1.0, t))  # clamp
    span = t * (len(stops) - 1)  # which pair of stops we are between
    index = min(len(stops) - 2, int(span))  # the left stop of the pair
    return _lerp_colour(stops[index], stops[index + 1], span - index)  # blend inside the pair


def _vertical_fade(width: int, height: int, colour: Colour, top_alpha: int, bottom_alpha: int) -> pygame.Surface:  # one colour, alpha changing top to bottom
    surface = pygame.Surface((max(1, width), max(1, height)), pygame.SRCALPHA)  # translucent
    for y in range(height):  # one row at a time
        t = y / max(1, height - 1)  # 0 at the top, 1 at the bottom
        alpha = int(top_alpha + (bottom_alpha - top_alpha) * t)  # this row's alpha
        pygame.draw.line(surface, (*colour, alpha), (0, y), (width, y))  # paint the row
    return surface  # caller caches it


def _blur(image: pygame.Surface, factor: int) -> pygame.Surface:  # a cheap soft blur
    """pygame 2 has no blur, but shrinking an image with smoothscale and
    growing it back averages the pixels, which is what a blur is. Used only
    while building cached sprites, never per frame."""
    width, height = image.get_size()  # the original size
    small = pygame.transform.smoothscale(image, (max(1, width // factor), max(1, height // factor)))  # shrink, pixels get averaged
    small = pygame.transform.smoothscale(small, (max(1, width // (factor * 2)), max(1, height // (factor * 2))))  # once more, for a rounder falloff
    return pygame.transform.smoothscale(small, (width, height))  # back up, now soft


# --- text helpers -----------------------------------------------------------
_TEXT_CACHE: dict[tuple, pygame.Surface] = {}  # rendered strings, so the hud is not re-rendered every frame


def _cache_put(key: tuple, image: pygame.Surface) -> pygame.Surface:  # store with a size cap
    if len(_TEXT_CACHE) > 400:  # scores and clocks change, so the cache would grow forever
        _TEXT_CACHE.clear()  # cheapest possible eviction, it refills in a frame
    _TEXT_CACHE[key] = image  # remember it
    return image  # hand it back


def _place(rect: pygame.Rect, position: tuple[float, float], align: str) -> pygame.Rect:  # anchor a rect at a point
    x, y = int(position[0]), int(position[1])  # whole pixels
    if align == "center":  # x is the middle, y the top
        rect.midtop = (x, y)  # centred
    elif align == "right":  # x is the right edge
        rect.topright = (x, y)  # right aligned
    elif align == "midbottom":  # x is the middle, y the bottom
        rect.midbottom = (x, y)  # grows upwards
    elif align == "middle":  # both centred
        rect.center = (x, y)  # true centre
    else:  # left is the default
        rect.topleft = (x, y)  # left aligned
    return rect  # positioned


def text(  # draw one line of plain text
    surface: pygame.Surface,  # where to draw
    message: str,  # what to say
    position: tuple[float, float],  # anchor point
    size: int = 18,  # px
    colour: Colour = config.TEXT,  # default near-white
    bold: bool = False,  # headings only
    mono: bool = False,  # numbers read better in mono
    align: str = "left",  # left, center, right, midbottom or middle
    alpha: int = 255,  # for fades
) -> pygame.Rect:  # the caller may want to place things under it
    """Draw one line of text and return the rectangle it occupied."""
    key = ("t", message, size, colour, bold, mono)  # everything that shapes the image
    image = _TEXT_CACHE.get(key)  # rendered before?
    if image is None:  # first time this string shows up
        image = _cache_put(key, font(size, bold=bold, mono=mono).render(message, True, colour))  # render once
    rect = _place(image.get_rect(), position, align)  # where it lands
    # The reset after a faded draw has to be set_alpha(255). set_alpha(None)
    # also switches off the per-pixel alpha, so the next full-strength draw of
    # the same cached image came out as a solid box: the PERFECT popup flashed
    # as a green slab for a few frames once it had faded out one time.
    if alpha < 255:  # faded text
        image.set_alpha(alpha)  # applied to the cached image, reset below
        surface.blit(image, rect)  # draw it faded
        image.set_alpha(255)  # back to full strength; set_alpha(None) would also switch off the per-pixel alpha
    else:  # the common case
        surface.blit(image, rect)  # put it on screen
    return rect  # done


def text_width(message: str, size: int, bold: bool = False, mono: bool = False) -> int:  # measure without drawing
    return font(size, bold=bold, mono=mono).size(message)[0]  # pygame measures for us


def tracked_text(  # letter-spaced text, for the small caps labels
    surface: pygame.Surface,  # where to draw
    message: str,  # what to say
    position: tuple[float, float],  # anchor point
    size: int = 12,  # labels are small
    colour: Colour = config.TEXT_MUTED,  # labels are quiet
    tracking: int = 2,  # extra px between letters
    bold: bool = True,  # labels read better bold
    align: str = "left",  # same options as text()
) -> pygame.Rect:  # where it went
    """Text with extra space between the letters. pygame has no tracking, so
    each glyph is rendered on its own and the row is built once and cached."""
    key = ("k", message, size, colour, tracking, bold)  # cache key
    image = _TEXT_CACHE.get(key)  # built before?
    if image is None:  # build the row
        face = font(size, bold=bold)  # one font for the whole row
        glyphs = [face.render(char, True, colour) for char in message]  # one image per letter
        gap = _s(tracking)  # tracking is a design value, like the size
        width = sum(glyph.get_width() for glyph in glyphs) + gap * max(0, len(glyphs) - 1)  # total width
        image = pygame.Surface((max(1, width), face.get_height()), pygame.SRCALPHA)  # transparent row
        x = 0  # pen position
        for glyph in glyphs:  # lay them out
            image.blit(glyph, (x, 0))  # stamp the letter
            x += glyph.get_width() + gap  # advance with the extra gap
        _cache_put(key, image)  # keep it
    rect = _place(image.get_rect(), position, align)  # anchor it
    surface.blit(image, rect)  # draw
    return rect  # done


def fit_text(message: str, max_width: int, size: int, min_size: int = 13, bold: bool = False) -> tuple[str, int]:  # shrink, then cut
    """Pick the largest size that fits, and cut with an ellipsis if even the
    smallest one does not. Long chart titles were running off their panels."""
    for candidate in range(size, min_size - 1, -1):  # biggest first
        if text_width(message, candidate, bold) <= max_width:  # it fits
            return message, candidate  # done
    cut = message  # trim from the end
    while cut and text_width(cut + "...", min_size, bold) > max_width:  # still too wide
        cut = cut[:-1]  # one character less
    return cut.rstrip() + "...", min_size  # the shortened title


def wrap_text(message: str, max_width: int, size: int, bold: bool = False, max_lines: int = 3) -> list[str]:  # word wrap
    lines: list[str] = []  # finished lines
    current = ""  # the line being filled
    for word in message.split():  # one word at a time
        attempt = word if not current else f"{current} {word}"  # try adding it
        if text_width(attempt, size, bold) <= max_width:  # still fits
            current = attempt  # keep it
        else:  # this word starts a new line
            if current:  # do not push an empty line
                lines.append(current)  # line done
            current = word  # carry the word over
    if current:  # the last line
        lines.append(current)  # keep it
    if len(lines) > max_lines:  # too many: cut the last one short
        lines = lines[:max_lines]  # drop the rest
        lines[-1], _ = fit_text(lines[-1] + " ...", max_width, size, size, bold)  # mark that it was cut
    return lines  # one string per line


def glow_text(  # text with a soft halo (stamped copies of itself)
    surface: pygame.Surface,  # where to draw
    message: str,  # what to say
    position: tuple[float, float],  # anchor point
    size: int,  # px, no default here
    colour: Colour,  # the text colour
    glow: Colour = config.HIT_FLASH,  # the halo colour
    bold: bool = True,  # glow text is always bold
    align: str = "center",  # same options as text()
    scale: float = 1.0,  # for pop animations
    alpha: int = 255,  # for fades
) -> pygame.Rect:  # as usual
    """Text with a soft halo: the same glyphs stamped around themselves.

    scale and alpha are applied to the composed stamp, which is what the
    judgement popup and combo counter animate with.
    """
    key = ("g", message, size, colour, glow, bold)  # the unscaled stamp is cached
    composed = _TEXT_CACHE.get(key)  # built before?
    if composed is None:  # build it
        face = font(size, bold=bold).render(message, True, colour)  # the text itself
        pad = _s(max(8, size // 4))  # room for the halo, bigger text gets a wider one
        composed = pygame.Surface((face.get_width() + pad * 2, face.get_height() + pad * 2), pygame.SRCALPHA)  # transparent
        halo = composed.copy()  # same size, the glyphs in the glow colour
        halo.blit(font(size, bold=bold).render(message, True, glow), (pad, pad))  # stamped once
        halo = _blur(halo, max(2, _s(size // 10)))  # softened
        composed.blit(halo, (0, 0))  # the glow
        if size < 48:  # small text needs a second layer to show a glow at all
            composed.blit(halo, (0, 0))  # big text glows enough with one
        else:  # big text: one layer, faded, or the glow swamps the letters
            composed.fill((255, 255, 255, 150), special_flags=pygame.BLEND_RGBA_MULT)  # dim the glow before the face goes on
        composed.blit(face, (pad, pad))  # the text on top
        _cache_put(key, composed)  # keep it
    image = composed  # what gets drawn
    if scale != 1.0:  # pop animation resizes it
        image = pygame.transform.smoothscale(  # smooth resize
            composed,  # the stamped image
            (max(1, int(composed.get_width() * scale)), max(1, int(composed.get_height() * scale))),  # new size, never zero
        )  # resized copy, the cache stays untouched
    rect = _place(image.get_rect(), position, align)  # anchor it
    if alpha < 255:  # fade via alpha
        image.set_alpha(alpha)  # apply the fade
        surface.blit(image, rect)  # draw it
        image.set_alpha(255)  # back to full strength, see text() for why not None
    else:  # no fade
        surface.blit(image, rect)  # draw it
    return rect  # done


def gradient_text(  # the logo: text filled with the accent gradient, plus a glow
    surface: pygame.Surface,  # where to draw
    message: str,  # what to say
    position: tuple[float, float],  # anchor point
    size: int,  # px
    stops: Sequence[Colour] = LOGO_GRADIENT,  # left to right colours
    align: str = "center",  # same options as text()
) -> pygame.Rect:  # where it went
    key = ("grad", message, size, tuple(stops))  # cache key
    image = _TEXT_CACHE.get(key)  # built before?
    if image is None:  # build the logo once
        face = font(size, bold=True).render(message, True, (255, 255, 255))  # white glyphs to multiply against
        width, height = face.get_size()  # the glyph block
        fill = pygame.Surface((width, height), pygame.SRCALPHA)  # the colour ramp
        for x in range(width):  # one column at a time
            pygame.draw.line(fill, (*_gradient_at(stops, x / max(1, width - 1)), 255), (x, 0), (x, height))  # this column's colour
        fill.blit(face, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)  # keep the ramp only where the letters are
        pad = _s(size // 3)  # room for the glow
        image = pygame.Surface((width + pad * 2, height + pad * 2), pygame.SRCALPHA)  # transparent
        halo = image.copy()  # the glow is the same coloured letters, blurred
        halo.blit(fill, (pad, pad))  # stamped once
        halo = _blur(halo, max(3, _s(size // 9)))  # softened
        image.blit(halo, (0, 0))  # the glow
        image.blit(fill, (pad, pad))  # the letters on top
        _cache_put(key, image)  # keep it
    rect = _place(image.get_rect(), position, align)  # anchor it
    surface.blit(image, rect)  # draw
    return rect  # done


# --- cached sprites ---------------------------------------------------------
# Everything in this section is built once and reused. The caches are keyed by
# the values that shape the sprite, so nothing here holds mutable state.

_SPRITES: dict[tuple, pygame.Surface] = {}  # one cache for every built sprite, keys start with the sprite's name


def _background_sprite() -> pygame.Surface:  # gradient, stage glow, dust and vignette, built once
    """Vertical gradient, a soft glow behind the judgement line, a little
    seeded dust, and a vignette, the size of the window."""
    key = ("background", config.WINDOW_WIDTH, config.WINDOW_HEIGHT)  # window-sized
    cached = _SPRITES.get(key)  # already built?
    if cached is not None:  # use the cached one
        return cached  # reuse it

    width, height = config.WINDOW_WIDTH, config.WINDOW_HEIGHT  # unpack
    surface = pygame.Surface((width, height))  # opaque, like the frame it lands on
    for y in range(height):  # one line per pixel row
        pygame.draw.line(  # paint the row
            surface,  # onto the gradient
            _lerp_colour(config.BACKGROUND_TOP, config.BACKGROUND_BOTTOM, (y / max(1, height - 1)) ** 0.8),  # blend top->bottom, darker sooner
            (0, y),  # row start
            (width, y),  # row end
        )  # row painted

    # a wide soft ellipse behind the judgement line, like a stage light
    glow = pygame.Surface((width, height), pygame.SRCALPHA)  # translucent layer
    for step in range(18, 0, -1):  # widest and faintest first
        rect = pygame.Rect(0, 0, _s(60 + step * 52), _s(24 + step * 15))  # grows with each step
        rect.center = (width // 2, int(config.JUDGE_LINE_Y) + _s(30))  # centred under the line
        pygame.draw.ellipse(glow, (*config.ACCENT_VIOLET, 5), rect)  # stacked faint layers read as a blur
    surface.blit(glow, (0, 0))  # stamp the stage light

    # dust: tiny dots, seeded so every run and every screenshot is the same
    rng = random.Random(52685)  # the unit code, any fixed seed works
    for _ in range(90):  # ninety specks
        x, y = rng.randrange(width), rng.randrange(height)  # anywhere
        shade = _lerp_colour(config.BACKGROUND_TOP, config.TEXT_MUTED, rng.uniform(0.15, 0.45))  # mostly faint
        surface.set_at((x, y), shade)  # one pixel
        if rng.random() < 0.15:  # a few bigger ones
            pygame.draw.circle(surface, shade, (x, y), _s(1))  # two pixels across

    # The vignette is four soft edge gradients. Corners are stamped by two of
    # them, which is exactly the extra darkening a vignette wants there.
    overlay = pygame.Surface((width, height), pygame.SRCALPHA)  # drawn on top of the gradient
    edge = _s(150)  # how many rows the top/bottom fade covers
    for i in range(edge):  # top and bottom edges
        alpha = int(70 * (1.0 - i / edge) ** 2)  # fades with distance from the edge
        pygame.draw.line(overlay, (0, 0, 0, alpha), (0, i), (width, i))  # top rows
        pygame.draw.line(overlay, (0, 0, 0, alpha), (0, height - 1 - i), (width, height - 1 - i))  # bottom rows
    side = _s(240)  # how many columns the left/right fade covers
    for i in range(side):  # left and right edges
        alpha = int(60 * (1.0 - i / side) ** 2)  # same idea, slightly weaker
        pygame.draw.line(overlay, (0, 0, 0, alpha), (i, 0), (i, height))  # left columns
        pygame.draw.line(overlay, (0, 0, 0, alpha), (width - 1 - i, 0), (width - 1 - i, height))  # right columns
    surface.blit(overlay, (0, 0))  # stamp the vignette over the gradient

    _SPRITES[key] = surface  # cache it
    return surface  # built once, reused forever


def _highway_sprite() -> pygame.Surface:  # the lane strip with its edges, lane tints and fades
    """The highway: one dark strip, the four lanes, a tint of each lane's
    colour rising from the receptors, glowing side rails, and fades at the top
    and bottom so notes come out of the dark instead of popping in."""
    key = ("highway",)  # one per run, geometry comes from config
    cached = _SPRITES.get(key)  # built before?
    if cached is not None:  # cache hit
        return cached  # reuse

    road = highway_rect()  # where it sits on screen
    surface = pygame.Surface(road.size, pygame.SRCALPHA)  # translucent, so the stage light shows through a little
    surface.fill((*config.HIGHWAY, 228))  # the strip itself
    for index in range(config.LANE_COUNT):  # the four lanes
        lane = lane_rect(index).move(-road.x, 0)  # lane rect, local to the strip
        shade = config.LANE_FILL if index % 2 == 0 else config.LANE_FILL_ALT  # alternate the shade
        pygame.draw.rect(surface, (*shade, 235), lane)  # fill it
        tint = _vertical_fade(lane.width, _s(220), LANE_TINTS[index], 0, 30)  # the lane's colour, strongest at the line
        surface.blit(tint, (lane.x, int(config.JUDGE_LINE_Y) - tint.get_height()))  # sits on the receptors
    for index in range(1, config.LANE_COUNT):  # the separators between lanes
        x = int(lane_rect(index).x - road.x - config.LANE_GAP / 2)  # the middle of the gap
        pygame.draw.line(surface, (*config.LANE_EDGE, 120), (x, 0), (x, road.height), _s(1))  # a thin quiet line

    for x, direction in ((0, 1), (road.width - 1, -1)):  # the two side rails
        rail = _s(8)  # how far the glow reaches in
        for step in range(rail):  # a short glow fading inwards
            alpha = int(90 * (1.0 - step / float(rail)) ** 2)  # strongest at the rail
            pygame.draw.line(surface, (*config.HIT_FLASH, alpha), (x + step * direction, 0), (x + step * direction, road.height))  # one column of glow
        pygame.draw.line(surface, (*_lighten(config.HIT_FLASH, 0.2), 200), (x, 0), (x, road.height), _s(1))  # the rail itself

    top_fade = _vertical_fade(road.width, _s(190), config.BACKGROUND_TOP, 255, 0)  # notes come out of the dark
    surface.blit(top_fade, (0, 0))  # over the top of the strip
    _SPRITES[key] = surface  # cache it
    return surface  # finished strip


def _top_fade_sprite() -> pygame.Surface:  # the same top fade, stamped again over the notes
    key = ("top_fade",)  # one per run
    cached = _SPRITES.get(key)  # built before?
    if cached is None:  # build it
        road = highway_rect()  # same width as the strip
        cached = _vertical_fade(road.width, _s(150), _lerp_colour(config.BACKGROUND_TOP, config.HIGHWAY, 0.3), 235, 0)  # dark at the very top
        _SPRITES[key] = cached  # cache it
    return cached  # done


def _bottom_fade_sprite() -> pygame.Surface:  # darkens the deck under the receptors
    key = ("bottom_fade",)  # one per run
    cached = _SPRITES.get(key)  # built before?
    if cached is None:  # build it
        road = highway_rect()  # same width as the strip
        depth = config.WINDOW_HEIGHT - int(config.JUDGE_LINE_Y) - _s(20)  # from just under the receptors to the bottom
        cached = _vertical_fade(road.width, depth, config.BACKGROUND_BOTTOM, 120, 245)  # notes past the line sink away
        _SPRITES[key] = cached  # cache it
    return cached  # done


def _note_sprite(tint: Colour) -> pygame.Surface:  # one note image per lane tint
    """A rounded, vertically shaded note with a bright core line."""
    key = ("note", config.NOTE_WIDTH, config.NOTE_HEIGHT, tint)  # cache key
    cached = _SPRITES.get(key)  # built before?
    if cached is not None:  # cache hit
        return cached  # done

    width, height = config.NOTE_WIDTH, config.NOTE_HEIGHT  # note size
    gradient = pygame.Surface((width, height), pygame.SRCALPHA)  # per-pixel alpha, so the corners can be cut
    top = _lighten(tint, 0.45)  # top of the shade
    bottom = _lerp_colour(tint, (10, 14, 24), 0.3)  # bottom of the shade
    for y in range(height):  # shade row by row
        pygame.draw.line(gradient, (*_lerp_colour(top, bottom, y / max(1, height - 1)), 255), (0, y), (width, y))  # this row's shade

    # Round the corners by multiplying against a rounded mask.
    mask = pygame.Surface((width, height), pygame.SRCALPHA)  # a rounded mask
    pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=_s(7))  # rounded shape
    gradient.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)  # multiply keeps only the rounded part

    # the core: a white line through the middle, the part the eye tracks
    pygame.draw.line(gradient, (255, 255, 255, 235), (_s(10), height // 2), (width - _s(11), height // 2), _s(2))  # bright core
    pygame.draw.line(gradient, (*_lighten(tint, 0.8), 200), (_s(6), _s(1)), (width - _s(7), _s(1)), _s(1))  # glossy top edge
    _SPRITES[key] = gradient  # cache it
    return gradient  # the finished note image


def _note_glow(tint: Colour) -> pygame.Surface:  # the halo under a note
    """A soft halo a little larger than the note, stamped underneath it."""
    pad = _s(16)  # how far the halo sticks out
    key = ("note_glow", config.NOTE_WIDTH, config.NOTE_HEIGHT, tint)  # cache key
    cached = _SPRITES.get(key)  # built before?
    if cached is not None:  # same cache check as the note sprite
        return cached  # reuse it

    width, height = config.NOTE_WIDTH + pad * 2, config.NOTE_HEIGHT + pad * 2  # halo size
    glow = pygame.Surface((width, height), pygame.SRCALPHA)  # translucent
    body = pygame.Rect(pad - _s(2), pad - _s(2), config.NOTE_WIDTH + _s(4), config.NOTE_HEIGHT + _s(4))  # a little bigger than the note
    pygame.draw.rect(glow, (*tint, 120), body, border_radius=body.height // 2)  # a pill in the lane colour
    glow = _blur(glow, _s(5))  # softened into a halo
    _SPRITES[key] = glow  # cache it
    return glow  # finished halo


HOLD_BODY_SHARE = 0.42  # a hold's body is this much of a note's width, so the head still reads as the thing to hit


def _hold_body_sprite(tint: Colour, lit: bool) -> pygame.Surface:  # the column between a hold's head and its tail
    """One column of a hold body, the full window tall. Every row is the same,
    so a body of any length is just the top part of this sprite, blitted with
    an area - no per-frame drawing and no resizing."""
    width = max(_s(6), int(config.NOTE_WIDTH * HOLD_BODY_SHARE))  # narrower than the head
    key = ("hold_body", width, config.WINDOW_HEIGHT, tint, lit)  # cache key
    cached = _SPRITES.get(key)  # built before?
    if cached is not None:  # the usual cache check
        return cached  # reuse
    body = pygame.Surface((width, config.WINDOW_HEIGHT), pygame.SRCALPHA)  # translucent column
    fill = _lighten(tint, 0.25) if lit else _darken(tint, 0.15)  # a held body brightens
    body.fill((*fill, 150 if lit else 95))  # see-through, so the lane still shows behind it
    edge = (*_lighten(tint, 0.55), 230 if lit else 150)  # the two rails
    pygame.draw.line(body, edge, (0, 0), (0, config.WINDOW_HEIGHT), _s(2))  # left rail
    pygame.draw.line(body, edge, (width - _s(2), 0), (width - _s(2), config.WINDOW_HEIGHT), _s(2))  # right rail
    if lit:  # a bright core while it is held, like the core line on a note
        pygame.draw.line(body, (255, 255, 255, 170), (width // 2, 0), (width // 2, config.WINDOW_HEIGHT), _s(2))  # the core
    _SPRITES[key] = body  # cache it
    return body  # finished column


def _beam_sprite(lane: int) -> pygame.Surface:  # the column of light above a pressed key
    key = ("beam", lane)  # one per lane
    cached = _SPRITES.get(key)  # built before?
    if cached is None:  # build it
        cached = _vertical_fade(config.LANE_WIDTH, _s(240), _lighten(LANE_TINTS[lane], 0.15), 0, 110)  # brightest at the key
        _SPRITES[key] = cached  # cache it
    return cached  # alpha is set per frame by the caller


def _spark_sprite(tint: Colour) -> pygame.Surface:  # one particle of a hit burst
    key = ("spark", tint)  # one per tint
    cached = _SPRITES.get(key)  # built before?
    if cached is not None:  # the same two lines as every builder here
        return cached  # reuse
    side = _s(12)  # the dot's box
    spark = pygame.Surface((side, side), pygame.SRCALPHA)  # small translucent dot
    pygame.draw.circle(spark, (*tint, 70), (side // 2, side // 2), _s(5))  # soft outer
    pygame.draw.circle(spark, (*_lighten(tint, 0.7), 240), (side // 2, side // 2), _s(2))  # bright centre
    _SPRITES[key] = spark  # cache it
    return spark  # finished dot


def _ring_sprite(tint: Colour) -> pygame.Surface:  # the ring of a hit burst
    key = ("ring", tint)  # one per tint
    cached = _SPRITES.get(key)  # built before?
    if cached is not None:  # cache check again, same reason
        return cached  # reuse it
    side = _s(128)  # the sprite's box; it is resized per frame, this only sets how sharp it stays
    middle = (side // 2, side // 2)  # its centre
    ring = pygame.Surface((side, side), pygame.SRCALPHA)  # translucent
    pygame.draw.circle(ring, (*tint, 150), middle, _s(56), width=_s(8))  # a thick ring
    ring = _blur(ring, _s(4))  # softened into a glow
    pygame.draw.circle(ring, (*_lighten(tint, 0.6), 240), middle, _s(54), width=_s(2))  # crisp ring on top
    _SPRITES[key] = ring  # cache it
    return ring  # finished ring


def _panel_sprite(width: int, height: int, radius: int, alpha: int, edge: Colour) -> pygame.Surface:  # translucent rounded panel
    """A translucent rounded panel with a lighter top edge, so screens can sit
    over a live background and still read as a card."""
    key = ("panel", width, height, radius, alpha, edge)  # cache key
    cached = _SPRITES.get(key)  # built before?
    if cached is not None:  # the usual cache check
        return cached  # reuse
    image = pygame.Surface((width, height), pygame.SRCALPHA)  # translucent
    rect = image.get_rect()  # the whole sprite
    pygame.draw.rect(image, (*config.PANEL, alpha), rect, border_radius=radius)  # tinted fill
    sheen = _vertical_fade(width, min(height, _s(60)), (255, 255, 255), 10, 0)  # a faint light from above
    mask = pygame.Surface(sheen.get_size(), pygame.SRCALPHA)  # keep the sheen inside the corners
    pygame.draw.rect(mask, (255, 255, 255, 255), rect, border_radius=radius)  # same rounded shape
    sheen.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)  # trimmed to the panel
    image.blit(sheen, (0, 0))  # on top of the fill
    pygame.draw.rect(image, (*edge, min(255, alpha)), rect, width=_s(1), border_radius=radius)  # a 1 px edge
    _SPRITES[key] = image  # cache it
    return image  # finished panel


def make_icon() -> pygame.Surface:  # the window icon, four lane bars
    """The window icon: four bars in the lane colours on a dark tile. Without
    it the title bar showed pygame's snake."""
    icon = pygame.Surface((32, 32), pygame.SRCALPHA)  # 32 px is what every OS accepts
    pygame.draw.rect(icon, (*config.HIGHWAY, 255), icon.get_rect(), border_radius=7)  # the tile
    for lane, height in enumerate((14, 22, 18, 26)):  # one bar per lane, like an equaliser
        bar = pygame.Rect(5 + lane * 6, 28 - height, 4, height)  # left to right
        pygame.draw.rect(icon, (*LANE_TINTS[lane], 255), bar, border_radius=2)  # in the lane colour
    return icon  # app.py hands it to the window


# Sparks of a hit burst. The angles and speeds are fixed per lane from a seeded
# generator, so a burst is a pure function of its age: same age, same frame,
# which keeps headless screenshots deterministic.
_SPARK_COUNT = 12  # particles per burst
_SPARKS: dict[int, list[tuple[float, float, float, float]]] = {}  # per-lane burst layouts


def _sparks_for_lane(lane: int) -> list[tuple[float, float, float, float]]:  # burst angles fixed per lane, so the same age draws the same frame
    cached = _SPARKS.get(lane)  # laid out before?
    if cached is not None:  # same cache pattern as the sprites
        return cached  # reuse the layout
    rng = random.Random(1000 + lane)  # seeded, so every run is identical
    sparks = []  # the layout being built
    for _ in range(_SPARK_COUNT):  # one entry per particle
        angle = rng.uniform(-math.pi, 0.0) + rng.uniform(-0.5, 0.5)  # an upwards fan with some spread
        speed = rng.uniform(34.0, 96.0)  # px over the burst
        delay = rng.uniform(0.0, 0.25)  # staggered starts
        sparks.append((math.cos(angle), math.sin(angle), speed, delay))  # direction + speed + delay
    _SPARKS[lane] = sparks  # cache it
    return sparks  # the frozen layout


# --- shared widgets ---------------------------------------------------------
def panel(  # a rounded card
    surface: pygame.Surface,  # where to draw
    rect: pygame.Rect,  # the box
    alpha: int = 225,  # how solid the card is
    radius: Optional[int] = None,  # corner rounding in screen px, None for the usual 14 design px
    edge: Colour = config.LANE_EDGE,  # outline colour
) -> None:  # draws, returns nothing
    radius = _s(14) if radius is None else radius  # the default card corner
    surface.blit(_panel_sprite(rect.width, rect.height, radius, alpha, edge), rect.topleft)  # stamped from cache


def _keycap_width(label: str, size: int) -> int:  # how wide a key cap will be
    return max(_s(size * 2 + 6), text_width(label, size, bold=True) + _s(20))  # never narrower than a square-ish cap


def keycap(  # one keyboard key, drawn the same on every screen
    surface: pygame.Surface,  # where to draw
    label: str,  # the key name
    topleft: tuple[int, int],  # where it goes
    size: int = 13,  # label size
    tint: Optional[Colour] = None,  # lane keys get their lane colour
    lit: float = 0.0,  # 0 resting, 1 pressed
    dim: bool = False,  # secondary caps sit darker so their letters still read over busy backgrounds
) -> pygame.Rect:  # where it went
    width = _keycap_width(label, size)  # fitted to the label
    height = _s(size * 2 + 6)  # tall enough for the label and the shadow
    rect = pygame.Rect(int(topleft[0]), int(topleft[1]), width, height)  # the cap
    accent = tint or config.TEXT_MUTED  # outline colour
    pygame.draw.rect(surface, _darken(accent, 0.8), rect.move(0, _s(3)), border_radius=_s(7))  # the shadow under the cap
    pressed = rect.move(0, int(_sf(2) * lit))  # a pressed key sinks a little
    base = config.HIGHWAY if dim else config.RECEPTOR_FILL  # secondary caps use the darkest panel colour
    face = _lerp_colour(base, _darken(accent, 0.35), lit)  # the cap top
    pygame.draw.rect(surface, face, pressed, border_radius=_s(7))  # the cap
    pygame.draw.rect(surface, _lerp_colour(_darken(accent, 0.35), accent, max(lit, 0.4)), pressed, width=_s(1), border_radius=_s(7))  # its outline
    label_colour = _lerp_colour(_lighten(accent, 0.4), (255, 255, 255), lit)  # the letter
    text(surface, label, pressed.center, size=size, colour=label_colour, bold=True, align="middle")  # centred on the cap
    return rect  # for layout


def hint_row(  # a centred row of [KEY] action pairs
    surface: pygame.Surface,  # where to draw
    items: Sequence[tuple[Sequence[str], str]],  # (keys, action) pairs
    centre_x: float,  # the row is centred here
    y: int,  # top of the caps
    size: int = 13,  # label size
    alpha: int = 255,  # the action words can pulse
    dim: bool = False,  # secondary rows read better dark with the brighter word colour
) -> None:  # draws, returns nothing
    gap_keys, gap_label, gap_items = _s(4), _s(9), _s(34)  # spacing between caps, before the word, between pairs
    widths = []  # each pair's width, measured before drawing so the row can centre
    for keys, action in items:  # measure
        caps = sum(_keycap_width(key, size) for key in keys) + gap_keys * (len(keys) - 1)  # the caps
        widths.append(caps + gap_label + text_width(action, size + 1, bold=True))  # caps plus the word
    x = centre_x - (sum(widths) + gap_items * (len(items) - 1)) / 2.0  # start so the whole row is centred
    word_colour = config.TEXT_MUTED if dim else config.TEXT  # the quieter colour for the secondary row
    for (keys, action), width in zip(items, widths):  # draw
        cursor = x  # pen for this pair
        for key in keys:  # one cap per key
            cursor = keycap(surface, key, (int(cursor), y), size=size, dim=dim).right + gap_keys  # next cap goes after this one
        word_y = y + (_s(size * 2 + 6) - font(size + 1, bold=True).get_height()) // 2  # vertically centred on the caps
        text(surface, action, (cursor - gap_keys + gap_label, word_y), size=size + 1, colour=word_colour, bold=True, alpha=alpha)  # the word, beside the caps
        x += width + gap_items  # next pair


def chip(  # a small pill with a coloured outline
    surface: pygame.Surface,  # where to draw
    message: str,  # what it says
    centre: tuple[float, float],  # where its middle goes
    colour: Colour,  # outline and text colour
    size: int = 12,  # text size
    filled: bool = False,  # a solid chip for the badges
) -> pygame.Rect:  # where it went
    width = text_width(message, size, bold=True) + _s(22)  # padded
    rect = pygame.Rect(0, 0, width, _s(size + 14))  # pill size
    rect.center = (int(centre[0]), int(centre[1]))  # centred
    if filled:  # badge style
        pygame.draw.rect(surface, colour, rect, border_radius=rect.height // 2)  # solid
        text(surface, message, rect.center, size=size, colour=config.BACKGROUND_BOTTOM, bold=True, align="middle")  # dark text on it
    else:  # outline style
        pygame.draw.rect(surface, _darken(colour, 0.82), rect, border_radius=rect.height // 2)  # a dim fill
        pygame.draw.rect(surface, _darken(colour, 0.3), rect, width=_s(1), border_radius=rect.height // 2)  # the outline
        text(surface, message, rect.center, size=size, colour=colour, bold=True, align="middle")  # the words
    return rect  # for layout


def _soft_ring(surface: pygame.Surface, centre: tuple[int, int], radius: int, colour: Colour, width: int, alpha: int) -> None:  # an anti-aliased ring
    """Drawn at twice the size and shrunk with smoothscale, which smooths the
    edges. Stacking gfxdraw.aacircle per pixel of width left a dotted moire."""
    if alpha <= 0:  # invisible
        return  # nothing to do
    half = radius + width + 2  # half the layer size at 1x
    layer = pygame.Surface((half * 4, half * 4), pygame.SRCALPHA)  # the 2x layer
    pygame.draw.circle(layer, (*colour, max(0, min(255, alpha))), (half * 2, half * 2), (radius + width // 2) * 2, width * 2)  # the ring at 2x
    layer = pygame.transform.smoothscale(layer, (half * 2, half * 2))  # back to 1x, edges averaged
    surface.blit(layer, layer.get_rect(center=centre))  # onto the frame


def _thick_arc(surface: pygame.Surface, centre: tuple[int, int], radius: int, fraction: float, colour: Colour, width: int) -> None:  # a smooth arc from 12 o'clock
    """pygame.draw.arc leaves gaps at thick widths, so the arc is a chain of
    small filled circles instead, one every degree or so."""
    steps = int(360 * max(0.0, min(1.0, fraction)))  # one dot per degree of the sweep
    for step in range(steps + 1):  # walk round
        angle = -math.pi / 2 + 2 * math.pi * step / 360.0  # from the top, clockwise
        x = int(round(centre[0] + math.cos(angle) * radius))  # point on the circle
        y = int(round(centre[1] + math.sin(angle) * radius))  # same
        pygame.gfxdraw.filled_circle(surface, x, y, width // 2, colour)  # one dot of the stroke
        pygame.gfxdraw.aacircle(surface, x, y, width // 2, colour)  # with a smooth rim


# --- play screen ------------------------------------------------------------
def draw_background(surface: pygame.Surface) -> None:  # blit the cached gradient, glow and vignette
    surface.blit(_background_sprite(), (0, 0))  # covers the whole window


def draw_beat_grid(  # faint lines showing where the beats are
    surface: pygame.Surface,  # where to draw
    song_ms: float,  # now
    bpm: float,  # the grid speed
    beats_per_bar: int = 4,  # bar lines every 4
) -> None:  # draws, returns nothing
    """Faint lines showing where the beats are.

    This is the visual proof that the notes follow the song clock: the lines and
    the notes are placed with the same function. Since week 11 they stay inside
    the highway; the old ones ran past it and read as stray blue lines.
    """
    if bpm <= 0:  # no bpm, no grid
        return  # nothing to draw
    beat_ms = 60_000.0 / bpm  # ms per beat
    px_per_ms = (config.JUDGE_LINE_Y - config.NOTE_SPAWN_Y) / config.APPROACH_MS  # the same px per ms as the notes
    earliest = song_ms - (config.WINDOW_HEIGHT - config.JUDGE_LINE_Y) / px_per_ms  # first beat that could be visible
    latest = song_ms + config.APPROACH_MS  # last beat that could be visible
    road = highway_rect()  # the grid spans the highway only
    for beat in range(int(earliest // beat_ms), int(latest // beat_ms) + 2):  # each beat in range
        if beat < 0:  # song has not started
            continue  # skip it
        y = int(y_for_time(beat * beat_ms, song_ms))  # same placement as the notes
        if y < -2 or y > config.WINDOW_HEIGHT + 2:  # off screen
            continue  # skip
        bar_line = beat % beats_per_bar == 0  # start of a bar?
        colour = _lerp_colour(config.BEAT_GRID, config.JUDGE_LINE, 0.35) if bar_line else config.BEAT_GRID  # bar lines a touch stronger
        pygame.draw.line(surface, colour, (road.x + _s(2), y), (road.right - _s(3), y), _s(2) if bar_line else _s(1))  # the grid line


def _press_strength(lane: int, presses: dict[int, float], held: Iterable[int], song_ms: float, fade_ms: float) -> float:  # 0..1 for a key's light
    if lane in held:  # still held down
        return 1.0  # fully lit while the finger is on the key
    at = presses.get(lane)  # when it was last pressed
    if at is None:  # never pressed
        return 0.0  # resting
    age = song_ms - at  # how long ago
    if 0 <= age < fade_ms:  # still fading
        return 1.0 - age / fade_ms  # linear fade
    return 0.0  # faded out


def draw_lane_beams(  # the light that rises from a pressed key
    surface: pygame.Surface,  # where to draw
    presses: dict[int, float],  # lane -> song time of its last press, scored or not
    held: Iterable[int],  # lanes whose key is down right now
    song_ms: float,  # to age the presses
) -> None:  # draws, returns nothing
    """Every press lights its lane, scored or not. Before week 11 only a
    scored hit lit anything, so a press that found no note looked exactly like
    a key that was never read - which is the first thing to rule out when a
    player says the keys stopped working."""
    held = set(held)  # read more than once
    for lane in range(config.LANE_COUNT):  # each lane
        strength = _press_strength(lane, presses, held, song_ms, 180.0)  # how lit
        if strength <= 0.0:  # dark
            continue  # nothing to draw
        beam = _beam_sprite(lane)  # cached column of light
        beam.set_alpha(int(255 * strength))  # this frame's strength
        surface.blit(beam, (lane_rect(lane).x, int(config.JUDGE_LINE_Y) - beam.get_height()))  # stands on the key
        beam.set_alpha(255)  # leave the cache at full strength, see text()


def draw_combo(surface: pygame.Surface, session: PlaySession, song_ms: float) -> None:  # the big combo behind the notes
    score = session.score  # the totals
    if score.combo < 2:  # combo only shows from 2 up
        return  # nothing to show
    judgement = session.last_judgement  # what happened last
    age = song_ms - session.last_judged_song_ms  # pops with each judged note
    pop = 1.0 + 0.18 * max(0.0, 1.0 - age / 140.0) if judgement is not None and judgement.verdict is not Verdict.MISS else 1.0  # a small bump per hit
    if score.combo >= 30:  # big combo goes green
        colour = config.PERFECT_COLOUR  # like a PERFECT
    elif score.combo >= 10:  # medium combo goes cyan
        colour = config.HIT_FLASH  # the accent
    else:  # small combo stays white
        colour = config.TEXT  # plain
    centre_x = config.WINDOW_WIDTH / 2.0  # over the middle of the highway
    glow_text(surface, str(score.combo), (centre_x, _s(214)), size=70, colour=colour, glow=colour, align="midbottom", scale=pop, alpha=120)  # quiet, the notes go over it
    tracked_text(surface, "COMBO", (centre_x, _s(204)), size=12, colour=_darken(colour, 0.35), tracking=5, align="center")  # the label under it


def _draw_hold_body(surface: pygame.Surface, note: LiveNote, song_ms: float) -> None:  # one hold's body and tail cap
    """The body runs from the tail down to the head. While the key is down the
    head is pinned to the judgement line, so the body is eaten from below as
    the tail comes down to meet it."""
    holding = note.state is NoteState.HOLDING  # the key is down on it
    head_y = config.JUDGE_LINE_Y if holding else note.screen_y(song_ms)  # pinned to the line while held
    tail_y = note.tail_screen_y(song_ms)  # always above the head
    top = max(0, int(tail_y))  # clipped to the window
    bottom = min(config.WINDOW_HEIGHT, int(head_y))  # same
    if bottom <= top:  # nothing of it on screen
        return  # done
    tint = LANE_TINTS[note.lane]  # lane colour
    body = _hold_body_sprite(tint, holding)  # cached column
    x = int(lane_centre_x(note.lane) - body.get_width() / 2)  # centred in the lane
    surface.blit(body, (x, top), area=pygame.Rect(0, 0, body.get_width(), bottom - top))  # only as tall as the hold is now
    if tail_y >= -_s(8):  # the tail cap is on screen
        cap = pygame.Rect(0, 0, body.get_width() + _s(10), _s(8))  # a little wider than the body
        cap.center = (int(lane_centre_x(note.lane)), int(tail_y))  # on the tail time
        pygame.draw.rect(surface, _lighten(tint, 0.5 if holding else 0.2), cap, border_radius=_s(4))  # the end of the hold


def draw_notes(surface: pygame.Surface, session: PlaySession, song_ms: float) -> None:  # the falling notes
    """Live notes, positioned from the song clock. Hold bodies go first, so
    every head sits on top of every body."""
    for note in session.notes:  # week 12: the bodies
        if note.is_hold and note.state in (NoteState.PENDING, NoteState.HOLDING):  # falling or being held
            _draw_hold_body(surface, note, song_ms)  # body and tail cap
    for note in session.notes:  # every note in the chart
        if note.state is NoteState.HOLDING:  # the head sits on the line while the key is down
            tint = LANE_TINTS[note.lane]  # lane colour
            sprite = _note_sprite(tint)  # same head image
            note_rect = sprite.get_rect(center=(int(lane_centre_x(note.lane)), int(config.JUDGE_LINE_Y)))  # pinned
            glow = _note_glow(tint)  # its halo
            surface.blit(glow, glow.get_rect(center=note_rect.center))  # halo first
            surface.blit(sprite, note_rect)  # head on top
            continue  # nothing else to do for this one
        if note.state is not NoteState.PENDING:  # hit or missed already
            continue  # next note
        y = note.screen_y(song_ms)  # where it is now
        if y < -config.NOTE_HEIGHT * 2 or y > config.WINDOW_HEIGHT + config.NOTE_HEIGHT:  # off screen
            continue  # second skip reason: not on screen
        tint = LANE_TINTS[note.lane]  # lane colour
        sprite = _note_sprite(tint)  # the cached note image
        note_rect = sprite.get_rect(center=(int(lane_centre_x(note.lane)), int(y)))  # centred in its lane
        glow = _note_glow(tint)  # its halo
        surface.blit(glow, glow.get_rect(center=note_rect.center))  # halo first
        surface.blit(sprite, note_rect)  # note on top
        # notes that are very close to their moment get a brighter edge
        distance = abs(note.time_ms - song_ms)  # how close to its moment
        if distance <= config.GOOD_MS:  # inside GOOD: highlight it
            edge = _lerp_colour(config.NOTE_EDGE, tint, distance / config.GOOD_MS)  # brighter as it nears
            pygame.draw.rect(surface, edge, note_rect.inflate(_s(2), _s(2)), width=_s(2), border_radius=_s(8))  # the highlight edge


def _draw_hit_burst(surface: pygame.Surface, lane: int, age_ms: float) -> None:  # ring + sparks out of the receptor
    """Ring and sparks out of the receptor, a pure function of the flash's age."""
    if not 0 <= age_ms < 420:  # burst lasts 420 ms
        return  # nothing to burst
    progress = age_ms / 420.0  # 0..1 through the burst
    ease = 1.0 - (1.0 - progress) ** 3  # ease-out
    tint = LANE_TINTS[lane]  # the lane's colour
    centre = receptor_rect(lane).center  # burst origin

    ring = _ring_sprite(tint)  # the ring sprite
    radius = _s(24) + int(_sf(54) * ease)  # expands out
    scaled = pygame.transform.smoothscale(ring, (radius * 2, radius * 2))  # scaled to the radius
    scaled.set_alpha(int(230 * (1.0 - progress)))  # fades as it expands
    surface.blit(scaled, scaled.get_rect(center=centre))  # centred on the receptor

    spark = _spark_sprite(tint)  # the particle sprite
    for dx, dy, speed, delay in _sparks_for_lane(lane):  # each particle
        local = progress - delay  # staggered starts
        if local <= 0:  # not launched yet
            continue  # not drawn
        distance = _sf(speed) * (1.0 - (1.0 - min(1.0, local / (1.0 - delay))) ** 3)  # eased travel
        drop = _sf(26.0) * progress * progress  # sparks fall back like embers
        spark.set_alpha(int(255 * (1.0 - progress)))  # fade out
        surface.blit(spark, spark.get_rect(center=(int(centre[0] + dx * distance), int(centre[1] + dy * distance + drop))))  # draw it
    spark.set_alpha(255)  # leave the cache at full strength, see text()


def _judge_glow_sprite() -> pygame.Surface:  # the halo under the judgement line
    key = ("judge_glow",)  # one per run
    cached = _SPRITES.get(key)  # built before?
    if cached is None:  # build it
        road = highway_rect()  # the line spans the highway
        height = _s(24)  # how tall the halo is
        cached = pygame.Surface((road.width, height), pygame.SRCALPHA)  # translucent strip
        for row in range(height):  # strongest in the middle row
            alpha = int(70 * (1.0 - abs(row - (height - 1) / 2.0) / (height / 2.0)) ** 2)  # falls off both ways
            pygame.draw.line(cached, (*config.HIT_FLASH, alpha), (0, row), (road.width, row))  # one row
        _SPRITES[key] = cached  # cache it
    return cached  # done


def draw_receptors(  # the judgement line and the four keys on it
    surface: pygame.Surface,  # where to draw
    session: Optional[PlaySession],  # for the red miss flash, None on screens with no run
    presses: dict[int, float],  # lane -> last press time, any press
    held: Iterable[int],  # lanes held down now
    hits: dict[int, float],  # lane -> last scored hit time, for the burst
    song_ms: float,  # to age all of the above
) -> None:  # draws, returns nothing
    road = highway_rect()  # the line spans the highway
    y = int(config.JUDGE_LINE_Y)  # the line's height
    halo = _judge_glow_sprite()  # the soft strip under the line
    surface.blit(halo, (road.x, y - halo.get_height() // 2))  # halo first
    pygame.draw.line(surface, _lighten(config.HIT_FLASH, 0.35), (road.x + _s(1), y), (road.right - _s(2), y), _s(2))  # the line itself

    held = set(held)  # read more than once
    misses = session.last_miss_song_ms if session is not None else {}  # lane -> last miss time
    for lane in range(config.LANE_COUNT):  # the four keys
        slot = receptor_rect(lane)  # where it sits
        tint = LANE_TINTS[lane]  # the lane's colour
        lit = _press_strength(lane, presses, held, song_ms, 160.0)  # 0 resting, 1 pressed
        holding = session is not None and lane in session.holding  # week 12: a hold is down in this lane
        if holding:  # the key stays fully lit for the whole hold
            lit = 1.0  # even if the key-down light has faded
        miss_at = misses.get(lane)  # when this lane last missed
        miss = 0.0  # 0 no flash, 1 just missed
        if miss_at is not None and 0 <= song_ms - miss_at < 300:  # a recent miss
            miss = 1.0 - (song_ms - miss_at) / 300.0  # fades over 300 ms

        pygame.draw.rect(surface, _darken(tint, 0.85), slot.move(0, _s(4)), border_radius=_s(10))  # the shadow under the key
        cap = slot.move(0, int(_sf(3) * lit))  # a pressed key sinks a little
        face = _lerp_colour(config.RECEPTOR_FILL, _darken(tint, 0.45), lit)  # the key top fills with colour
        face = _lerp_colour(face, _darken(config.MISS_COLOUR, 0.6), miss)  # and goes red on a miss
        pygame.draw.rect(surface, face, cap, border_radius=_s(10))  # the key top
        pygame.draw.line(surface, _lighten(face, 0.12), (cap.x + _s(9), cap.y + _s(2)), (cap.right - _s(10), cap.y + _s(2)), _s(1))  # a thin highlight along the top
        edge = _lerp_colour(_lerp_colour(config.RECEPTOR_EDGE, tint, 0.45), _lighten(tint, 0.3), lit)  # outline blends to the lane colour
        edge = _lerp_colour(edge, config.MISS_COLOUR, miss)  # red on a miss
        pygame.draw.rect(surface, edge, cap, width=_s(2), border_radius=_s(10))  # the outline
        if holding:  # a second, softer outline so a held lane reads from the corner of the eye
            pulse = 0.5 + 0.5 * math.sin(song_ms / 90.0)  # a quick shimmer, from the song clock so captures stay fixed
            ring = cap.inflate(_s(6), _s(6))  # just outside the key
            pygame.draw.rect(surface, _lerp_colour(tint, (255, 255, 255), 0.3 * pulse), ring, width=_s(2), border_radius=_s(13))  # the shimmer
        label = _lerp_colour(_lerp_colour(config.TEXT_MUTED, tint, 0.5), (255, 255, 255), lit)  # the letter brightens when pressed
        text(surface, config.LANE_LABELS[lane], cap.center, size=17, colour=label, bold=True, align="middle")  # the key name

        at = hits.get(lane)  # the last scored hit in this lane
        if at is not None:  # there was one
            _draw_hit_burst(surface, lane, song_ms - at)  # ring + sparks over the key


def draw_highway(surface: pygame.Surface) -> None:  # the strip, from cache
    surface.blit(_highway_sprite(), highway_rect().topleft)  # one blit for all the lanes


def draw_highway_fades(surface: pygame.Surface) -> None:  # fades stamped over the notes
    road = highway_rect()  # same width as the strip
    surface.blit(_top_fade_sprite(), road.topleft)  # notes come out of the dark at the top
    surface.blit(_bottom_fade_sprite(), (road.x, int(config.JUDGE_LINE_Y) + _s(20)))  # and sink under the keys


def draw_judgement_popup(  # PERFECT / GOOD / MISS word
    surface: pygame.Surface,  # where to draw
    verdict: Verdict,  # which word
    error_text: str,  # the ms reading, ignored for a MISS
    age_ms: float,  # drives the whole animation
) -> None:  # draws, returns nothing
    """The word that flashes up after a note is judged.

    It opens slightly overscaled, settles, rises a touch, then fades - the pop
    is what makes a PERFECT feel different from reading the same word flat.
    """
    if not 0 <= age_ms < 600:  # popup lasts 600 ms
        return  # nothing to show
    fade = 1.0 - age_ms / 600.0  # fades over its life
    pop = 1.0 + 0.24 * max(0.0, 1.0 - age_ms / 150.0)  # opens big, settles
    rise = _sf(10.0) * min(1.0, age_ms / 180.0)  # drifts up a little
    centre_x = config.WINDOW_WIDTH / 2.0  # centred
    colour = VERDICT_COLOURS[verdict]  # its colour
    alpha = int(255 * min(1.0, fade * 1.6))  # fade, but stay readable longer
    # anchored by its bottom edge, so the opening pop grows upwards and never
    # covers the error reading underneath
    glow_text(surface, verdict.value, (centre_x, config.JUDGE_LINE_Y - _s(96) - rise), size=34, colour=colour, glow=colour, align="midbottom", scale=pop, alpha=alpha)  # the word
    if verdict is not Verdict.MISS:  # a miss has no tap, so no reading
        text(surface, error_text, (centre_x, config.JUDGE_LINE_Y - _s(90)), size=13, colour=config.TEXT_MUTED, mono=True, align="center", alpha=int(255 * fade))  # the ms reading


def draw_countdown(surface: pygame.Surface, remaining_ms: float) -> None:  # the 3, 2, 1 before the first note
    """The 3, 2, 1 before the first note.

    remaining_ms counts down to the first note. Each number sits in a ring that
    empties over its second, so the wait reads as time and not just a digit.
    """
    number = max(1, min(3, int(remaining_ms // 1000) + 1))  # which number is showing
    age_in_second = 1000.0 - (remaining_ms % 1000.0)  # how long this number has been up
    pop = 1.0 + 0.30 * max(0.0, 1.0 - age_in_second / 160.0)  # pops on arrival
    fade = min(1.0, remaining_ms / 350.0)  # fades near the end
    centre = (int(config.WINDOW_WIDTH / 2), _s(262))  # over the highway
    side = _s(180)  # the backdrop's box
    backdrop = pygame.Surface((side, side), pygame.SRCALPHA)  # a dark disc so the number reads over notes
    pygame.gfxdraw.filled_circle(backdrop, side // 2, side // 2, _s(66), (*config.BACKGROUND_BOTTOM, int(170 * fade)))  # the disc
    surface.blit(backdrop, backdrop.get_rect(center=centre))  # behind the ring
    _thick_arc(surface, centre, _s(62), 1.0, _darken(config.LANE_EDGE, 1.0 - fade), _s(5))  # the empty track
    _thick_arc(surface, centre, _s(62), 1.0 - age_in_second / 1000.0, _darken(config.HIT_FLASH, 1.0 - fade), _s(5))  # the part of the second that is left
    glow_text(surface, str(number), (centre[0], centre[1] + _s(4)), size=76, colour=config.TEXT, glow=config.HIT_FLASH, align="middle", scale=pop, alpha=int(255 * fade))  # the digit
    tracked_text(surface, "GET READY", (centre[0], centre[1] - _s(104)), size=13, colour=config.HIT_FLASH, tracking=6, align="center")  # the label above


def draw_go(surface: pygame.Surface, remaining_ms: float) -> None:  # GO, as the first note lands
    """GO, drawn over the same spot once the countdown has run out."""
    if remaining_ms <= 0:  # time is up
        return  # nothing left to show
    age = 800.0 - remaining_ms  # 0 to 800 ms
    fade = min(1.0, remaining_ms / 320.0)  # fades out through its life
    pop = 1.0 + 0.20 * max(0.0, 1.0 - age / 160.0)  # same pop idea
    centre = (int(config.WINDOW_WIDTH / 2), _s(262))  # same spot as the numbers
    _soft_ring(surface, centre, _s(62) + int(_sf(70) * min(1.0, age / 500.0)), config.PERFECT_COLOUR, _s(3), int(200 * fade))  # a ring that opens outwards
    glow_text(surface, "GO!", (centre[0], centre[1] + _s(4)), size=84, colour=config.PERFECT_COLOUR, glow=config.PERFECT_COLOUR, align="middle", scale=pop, alpha=int(255 * fade))  # the word


def _ms(value: float) -> str:  # -0.4 -> "+0 ms", never "-0 ms"
    return f"{round(value):+d} ms"  # rounding first drops the sign of a value that rounds to zero


def _clock_text(ms: float) -> str:  # 12345 ms -> "0:12"
    seconds = max(0, int(ms // 1000))  # whole seconds, never negative
    return f"{seconds // 60}:{seconds % 60:02d}"  # minutes:seconds


def _live_accuracy(session: PlaySession) -> Optional[float]:  # accuracy over the notes judged so far
    score = session.score  # the totals
    if score.judged == 0:  # nothing judged yet
        return None  # the hud shows a dash
    return 100.0 * score.points / (score.judged * config.POINTS_PERFECT)  # points against a perfect run so far


def draw_play_hud(  # the two side columns
    surface: pygame.Surface,  # where to draw
    session: PlaySession,  # the live run
    song_ms: float,  # now
    chart_title: str,  # for the left column
    show_hints: bool = True,  # off while the debug panel covers that corner
) -> None:  # draws, returns nothing
    """Song on the left, score on the right, the highway left alone.

    The old HUD put a long chart title, the song clock and the fps over the
    top of the lanes; the fps now lives in the TAB panel only.
    """
    score = session.score  # the totals
    column = highway_rect().x - _s(24) - _s(24)  # room between the window edge and the highway
    left, right = _s(24), config.WINDOW_WIDTH - _s(24)  # the two margins

    # --- left: what is playing, and how far through it we are
    tracked_text(surface, "NOW PLAYING", (left, _s(26)), size=11, tracking=3)  # the label
    y = _s(44)  # pen
    for line in wrap_text(chart_title, column, 16, bold=True):  # the title, wrapped to the column
        text(surface, line, (left, y), size=16, colour=config.TEXT, bold=True)  # one line
        y += _s(21)  # next line
    text(surface, f"{session.chart.bpm:.0f} BPM  ·  {score.note_count} NOTES", (left, y + _s(4)), size=12, colour=config.TEXT_MUTED, bold=True)  # the meta line

    length = max(1.0, float(session.chart.length_ms) + 1200.0)  # the run ends 1.2 s after the last note
    fraction = min(1.0, max(0.0, song_ms / length))  # 0..1 through the song
    bar = pygame.Rect(left, y + _s(40), column, _s(5))  # the progress bar
    pygame.draw.rect(surface, config.LANE_EDGE, bar, border_radius=_s(3))  # the track
    if fraction > 0.0:  # something has played
        filled = bar.copy()  # the played part
        filled.width = max(_s(5), int(bar.width * fraction))  # grows with the song
        pygame.draw.rect(surface, config.HIT_FLASH, filled, border_radius=_s(3))  # the fill
        pygame.gfxdraw.filled_circle(surface, filled.right - _s(2), bar.centery, _s(4), _lighten(config.HIT_FLASH, 0.4))  # the playhead
    text(surface, _clock_text(song_ms), (left, bar.bottom + _s(8)), size=13, colour=config.TEXT, mono=True)  # elapsed
    text(surface, _clock_text(length), (bar.right, bar.bottom + _s(8)), size=13, colour=config.TEXT_MUTED, mono=True, align="right")  # total

    if show_hints:  # the two keys that matter mid-run
        keycap(surface, "TAB", (left, config.WINDOW_HEIGHT - _s(88)), size=11)  # debug key
        text(surface, "timing overlay", (left + _s(52), config.WINDOW_HEIGHT - _s(83)), size=12, colour=config.TEXT_MUTED, bold=True)  # what it does
        keycap(surface, "ESC", (left, config.WINDOW_HEIGHT - _s(52)), size=11)  # quit key
        text(surface, "end the run", (left + _s(52), config.WINDOW_HEIGHT - _s(47)), size=12, colour=config.TEXT_MUTED, bold=True)  # what it does

    # --- right: score, accuracy, and the three counts
    tracked_text(surface, "SCORE", (right, _s(26)), size=11, tracking=3, align="right")  # the label
    glow_text(surface, f"{score.points:,}", (right + _s(6), _s(36)), size=34, colour=config.TEXT, glow=config.JUDGE_LINE, align="right")  # the number, with thousands commas
    tracked_text(surface, "ACCURACY", (right, _s(100)), size=11, tracking=3, align="right")  # the label
    accuracy = _live_accuracy(session)  # None before the first judged note
    text(surface, "--" if accuracy is None else f"{accuracy:.1f}%", (right, _s(116)), size=22, colour=config.TEXT, bold=True, align="right")  # the value
    rows = (("PERFECT", score.perfect, config.PERFECT_COLOUR), ("GOOD", score.good, config.GOOD_COLOUR), ("MISS", score.miss, config.MISS_COLOUR))  # the counts
    for row, (label, value, colour) in enumerate(rows):  # one row each
        row_y = _s(166 + row * 24)  # spaced rows
        pygame.gfxdraw.filled_circle(surface, right - column + _s(4), row_y + _s(8), _s(4), colour)  # a coloured dot
        text(surface, label, (right - column + _s(16), row_y), size=13, colour=config.TEXT_MUTED, bold=True)  # the word
        text(surface, str(value), (right, row_y), size=13, colour=colour, bold=True, mono=True, align="right")  # the count
    text(surface, f"{score.judged} / {score.note_count} judged", (right, _s(246)), size=12, colour=config.TEXT_DIM, mono=True, align="right")  # progress through the chart


def draw_debug_panel(  # the two clocks side by side
    surface: pygame.Surface,  # where to draw
    song_ms: float,  # the trusted clock
    ticks_ms: Optional[int],  # the raw reading
    mixer_pos_ms: Optional[int],  # the mixer reading
    drift_ms: Optional[float],  # the gap between them
    latency_offset_ms: float,  # the offset in use
    fps: Optional[float],  # None in headless runs
) -> None:  # draws, returns nothing
    """Second reading and calibration state.

    The mixer reading is shown next to the tick clock so the two can be
    compared live. This is the panel that made it obvious the mixer could not be
    the primary clock. Labels on the left, values on the right, so it fits in
    the left column instead of covering the first lane.
    """
    rows = [  # (label, value) pairs
        ("tick clock", f"{song_ms:.1f} ms"),  # the trusted number
        ("raw ticks", "-" if ticks_ms is None else f"{ticks_ms}"),  # ticks before the offset
        ("mixer get_pos", "-" if mixer_pos_ms is None else f"{mixer_pos_ms}"),  # the second clock
        ("drift", "-" if drift_ms is None else f"{drift_ms:+.0f} ms"),  # trusted minus mixer
        ("offset", f"{latency_offset_ms:+.0f} ms"),  # what calibration decided
        ("fps", "-" if fps is None else f"{fps:.1f}"),  # a dash when headless
    ]  # rows done
    width = highway_rect().x - _s(24) - _s(16)  # fits the left column
    rect = pygame.Rect(_s(24), config.WINDOW_HEIGHT - _s(40 + 20 * len(rows) + 24), width, _s(40 + 20 * len(rows)))  # bottom left
    panel(surface, rect, alpha=235, radius=_s(10), edge=config.JUDGE_LINE)  # a blue edge marks it as a dev tool
    tracked_text(surface, "DEBUG", (rect.x + _s(12), rect.y + _s(10)), size=11, colour=config.HIT_FLASH, tracking=3)  # the title
    text(surface, "TAB", (rect.right - _s(12), rect.y + _s(10)), size=11, colour=config.TEXT_DIM, bold=True, align="right")  # how to hide it
    for row, (label, value) in enumerate(rows):  # each row
        y = rect.y + _s(32 + row * 20)  # row position
        text(surface, label, (rect.x + _s(12), y), size=12, colour=config.TEXT_MUTED, mono=True)  # left
        text(surface, value, (rect.right - _s(12), y), size=12, colour=config.TEXT, mono=True, align="right")  # right


# --- other screens ----------------------------------------------------------
def _draw_ambient_notes(surface: pygame.Surface, anim_ms: float) -> None:  # demo notes drifting behind the title
    """Demo notes drifting down the lanes behind the title content."""
    span = config.WINDOW_HEIGHT + _s(120)  # travel range, a little past both edges
    for i in range(12):  # twelve drifting notes
        lane = i % config.LANE_COUNT  # spread across the lanes
        y = _sf(anim_ms * 0.085 + i * 157.0) % span - _sf(60.0)  # steady fall, staggered start
        sprite = _note_sprite(LANE_TINTS[lane])  # cached image
        sprite.set_alpha(30)  # very faint
        surface.blit(sprite, sprite.get_rect(center=(int(lane_centre_x(lane)), int(y))))  # in its lane
        sprite.set_alpha(255)  # the cache goes back to full strength, see text()


def _draw_equaliser(surface: pygame.Surface, centre_x: float, y: int, anim_ms: Optional[float]) -> None:  # bars under the logo
    count, bar_w, gap = 33, _s(6), _s(4)  # thirty-three thin bars
    left = centre_x - (count * bar_w + (count - 1) * gap) / 2.0  # centred row
    for index in range(count):  # each bar
        phase = (anim_ms or 0.0) / 260.0  # zero in captures, so the frame is fixed
        level = 0.5 + 0.5 * math.sin(index * 0.55 + phase) * math.cos(index * 0.23 - phase * 0.6)  # a slow wave
        height = _s(3) + int(_sf(15) * level)  # 3 to 18 design px
        colour = _gradient_at(LOGO_GRADIENT, index / (count - 1))  # same ramp as the logo
        bar = pygame.Rect(int(left + index * (bar_w + gap)), y - height, bar_w, height)  # grows upwards from y
        pygame.draw.rect(surface, _darken(colour, 0.35), bar, border_radius=_s(2))  # muted, it is decoration


def _draw_arrow(surface: pygame.Surface, centre: tuple[int, int], direction: int, colour: Colour) -> None:  # a chart picker arrow
    x, y = centre  # unpack
    half_w, half_h = _s(7), _s(11)  # the triangle's half sizes
    points = [(x - half_w * direction, y - half_h), (x + half_w * direction, y), (x - half_w * direction, y + half_h)]  # a triangle pointing left or right
    pygame.gfxdraw.aapolygon(surface, points, colour)  # smooth edge
    pygame.gfxdraw.filled_polygon(surface, points, colour)  # filled


def draw_title(  # the waiting screen
    surface: pygame.Surface,  # where to draw
    chart_title: str,  # the picked chart
    note_count: int,  # shown in the card
    calibration_ms: float,  # shown as a chip
    last_accuracy: Optional[float] = None,  # the last run on this chart, if any
    anim_ms: Optional[float] = None,  # ambient clock, None in captures
    chart_picker: tuple[bool, int, int] | None = None,  # (shown, index, count) when there is a choice
    best_accuracy: Optional[float] = None,  # best run on this chart this session
    chart_bpm: float = 120.0,  # for the meta line
    chart_length_ms: float = 0.0,  # for the meta line
    lane_counts: Sequence[int] = (),  # notes per lane, drawn as small bars
    status_message: str = "",  # e.g. 'no audio device'
    hold_count: int = 0,  # week 12: how many of the notes are holds
) -> None:  # draws, returns nothing
    """The waiting screen.

    anim_ms is an ambient clock from the caller, used only to drift the
    background notes, move the equaliser and pulse the start hint. Headless
    captures pass None and get the same still frame every time.

    The old layout drew the chart picker, the last run line and the status line
    on top of each other once all three were showing; every line now has its
    own row.
    """
    draw_background(surface)  # the gradient
    if anim_ms is not None:  # headless passes None
        _draw_ambient_notes(surface, anim_ms)  # the drifting notes
    centre_x = config.WINDOW_WIDTH / 2.0  # used a lot here

    _draw_equaliser(surface, centre_x, _s(70), anim_ms)  # decoration above the logo
    gradient_text(surface, "BEATLANES", (centre_x, _s(128)), size=76, align="middle")  # the logo
    tracked_text(surface, "FOUR LANE KEYBOARD RHYTHM PROTOTYPE", (centre_x, _s(176)), size=12, colour=config.TEXT_MUTED, tracking=5, align="center")  # the tagline

    card = pygame.Rect(int(centre_x) - _s(300), _s(214), _s(600), _s(166))  # the chart card
    panel(surface, card, alpha=232)  # translucent, so the notes show
    picker = chart_picker is not None and chart_picker[0]  # more than one chart installed
    label = f"CHART {chart_picker[1] + 1} / {chart_picker[2]}" if picker else "CHART"  # which one of how many
    tracked_text(surface, label, (card.x + _s(28), card.y + _s(22)), size=11, colour=config.HIT_FLASH, tracking=3)  # top left
    shown, title_size = fit_text(chart_title, card.width - _s(56) - _s(150), 24, 15, bold=True)  # the title, fitted to the space left of the best score
    text(surface, shown, (card.x + _s(28), card.y + _s(42)), size=title_size, colour=config.TEXT, bold=True)  # the chart name
    holds = f"   ·   {hold_count} HOLDS" if hold_count else ""  # only charts that have holds say so
    meta = f"{chart_bpm:.0f} BPM   ·   {note_count} NOTES{holds}   ·   {_clock_text(chart_length_ms + 1200.0)}"  # tempo, size, length
    text(surface, meta, (card.x + _s(28), card.y + _s(78)), size=13, colour=config.TEXT_MUTED, bold=True)  # under the title

    if lane_counts:  # notes per lane, so two charts can be told apart at a glance
        most = max(1, max(lane_counts))  # the busiest lane sets the scale
        for lane, count in enumerate(lane_counts):  # one block per lane
            x = card.x + _s(28 + lane * 132)  # four columns
            cap = keycap(surface, config.LANE_LABELS[lane], (x, card.y + _s(112)), size=11, tint=LANE_TINTS[lane])  # the key
            track = pygame.Rect(cap.right + _s(8), cap.centery - _s(2), _s(64), _s(4))  # the bar's track
            pygame.draw.rect(surface, config.LANE_EDGE, track, border_radius=_s(2))  # background
            pygame.draw.rect(surface, LANE_TINTS[lane], pygame.Rect(track.x, track.y, max(_s(4), int(track.width * count / most)), track.height), border_radius=_s(2))  # share of the notes
            text(surface, str(count), (track.right + _s(6), cap.centery - _s(8)), size=12, colour=config.TEXT_MUTED, mono=True)  # the number

    tracked_text(surface, "BEST", (card.right - _s(28), card.y + _s(22)), size=11, tracking=3, align="right")  # top right
    best_text = "--" if best_accuracy is None else f"{best_accuracy:.1f}%"  # nothing until a run
    text(surface, best_text, (card.right - _s(28), card.y + _s(38)), size=26, colour=config.HIT_FLASH if best_accuracy is not None else config.TEXT_DIM, bold=True, align="right")  # the best score
    if last_accuracy is not None:  # only after a run
        text(surface, f"last {last_accuracy:.1f}%", (card.right - _s(28), card.y + _s(76)), size=12, colour=config.TEXT_MUTED, mono=True, align="right")  # the last run

    if picker:  # arrows either side of the card
        bob = 0 if anim_ms is None else int(_sf(3) * math.sin(anim_ms / 300.0))  # a small nudge outwards and back
        _draw_arrow(surface, (card.x - _s(30) - bob, card.centery), -1, config.HIT_FLASH)  # left
        _draw_arrow(surface, (card.right + _s(30) + bob, card.centery), 1, config.HIT_FLASH)  # right

    chips = (  # the timing rules, at a glance
        (f"PERFECT ±{config.PERFECT_MS:.0f} ms", config.PERFECT_COLOUR),  # tight window
        (f"GOOD ±{config.GOOD_MS:.0f} ms", config.GOOD_COLOUR),  # loose window
        (f"OFFSET {calibration_ms:+.0f} ms", config.HIT_FLASH),  # the saved calibration
    )  # three chips
    widths = [text_width(message, 12, bold=True) + _s(22) for message, _ in chips]  # measured first so the row centres, same padding as chip()
    x = centre_x - (sum(widths) + _s(12) * (len(chips) - 1)) / 2.0  # start of the row
    for (message, colour), width in zip(chips, widths):  # draw them
        chip(surface, message, (x + width / 2.0, _s(408)), colour)  # one pill
        x += width + _s(12)  # next one

    if status_message:  # e.g. 'calibration saved' or 'no audio device'
        text(surface, status_message, (centre_x, _s(438)), size=13, colour=config.TEXT_MUTED, mono=True, align="center")  # its own row now

    pulse = 255 if anim_ms is None else int(190 + 65 * math.sin(anim_ms / 380.0))  # the start hint breathes
    hint_row(surface, ((("SPACE",), "START"), (("C",), "CALIBRATE"), (("ESC",), "QUIT")), centre_x, _s(486), size=13, alpha=pulse)  # the main keys
    extra = [(("TAB",), "timing overlay in play"), (("G",), "guide")]  # always true
    if picker:  # only worth saying when there is a choice
        extra.insert(0, (("LEFT", "RIGHT"), "change chart"))  # arrows first
    # the second row used to be 10px caps in the dimmest colour, sitting right
    # on the drifting notes and the key beams - on a real screen the words
    # vanished. now 12px on a darker cap with a strip of highway behind the
    # whole row, so the letters always have a quiet background to sit on
    row_sizes = [text_width(action, 13, bold=True) + sum(_keycap_width(key, 12) for key in keys) + _s(4) * (len(keys) - 1) + _s(9) for keys, action in extra]  # measure before the strip, same gaps as hint_row
    row_width = sum(row_sizes) + _s(34) * (len(extra) - 1)  # the whole row
    strip = pygame.Rect(int(centre_x - row_width / 2.0) - _s(16), _s(544), int(row_width) + _s(32), _s(38))  # behind the caps and words
    panel(surface, strip, alpha=205, radius=_s(10))  # the quiet strip
    hint_row(surface, extra, centre_x, _s(549), size=12, dim=True)  # the redesigned second row
    # the week 11 reminder from class, in the professor's words: the point of
    # the prototype is to learn, not to ship a commercial product
    text(surface, '"BUILDING TO LEARN"', (centre_x, _s(606)), size=11, colour=config.TEXT_MUTED, bold=True, align="center")  # the phrase itself
    text(surface, "the prototype is for learning, not for shipping", (centre_x, _s(624)), size=10, colour=config.TEXT_DIM, align="center")  # what it means
    text(surface, f"v{__version__}  ·  week 12 build", (config.WINDOW_WIDTH - _s(16), config.WINDOW_HEIGHT - _s(24)), size=11, colour=config.TEXT_DIM, align="right")  # build tag, bottom right


def _stat_block(surface: pygame.Surface, label: str, value: str, centre_x: float, y: int, colour: Colour = config.TEXT) -> None:  # a small label over a number
    tracked_text(surface, label, (centre_x, y), size=10, tracking=2, align="center")  # the label
    text(surface, value, (centre_x, y + _s(15)), size=20, colour=colour, bold=True, align="center")  # the value


def _timing_axis(  # an early/late axis with the windows shaded, shared by calibration and results
    surface: pygame.Surface,  # where to draw
    rect: pygame.Rect,  # the axis area, the line runs through its middle
    errors: Iterable[float],  # ms values to plot
    reach_ms: float,  # the axis runs from -reach to +reach
) -> None:  # draws, returns nothing
    mid_y = rect.centery  # the axis line
    scale = rect.width / (2.0 * reach_ms)  # px per ms

    def x_for(ms: float) -> int:  # ms -> screen x, clamped to the axis
        return int(rect.centerx + max(-reach_ms, min(reach_ms, ms)) * scale)  # clamped so wild taps sit on the ends

    good = pygame.Rect(x_for(-config.GOOD_MS), rect.y, x_for(config.GOOD_MS) - x_for(-config.GOOD_MS), rect.height)  # the GOOD window
    pygame.draw.rect(surface, _darken(config.GOOD_COLOUR, 0.88), good, border_radius=_s(4))  # faint amber
    perfect = pygame.Rect(x_for(-config.PERFECT_MS), rect.y, x_for(config.PERFECT_MS) - x_for(-config.PERFECT_MS), rect.height)  # the PERFECT window
    pygame.draw.rect(surface, _darken(config.PERFECT_COLOUR, 0.82), perfect, border_radius=_s(4))  # faint green
    pygame.draw.line(surface, config.LANE_EDGE, (rect.x, mid_y), (rect.right, mid_y), _s(1))  # the axis
    pygame.draw.line(surface, config.TEXT_MUTED, (rect.centerx, rect.y - _s(3)), (rect.centerx, rect.bottom + _s(3)), _s(1))  # zero
    for value in errors:  # one mark per tap
        colour = config.PERFECT_COLOUR if abs(value) <= config.PERFECT_MS else config.GOOD_COLOUR if abs(value) <= config.GOOD_MS else config.MISS_COLOUR  # by window
        pygame.draw.line(surface, colour, (x_for(value), rect.y + _s(3)), (x_for(value), rect.bottom - _s(3)), _s(2))  # a tick
    text(surface, "EARLY", (rect.x, rect.bottom + _s(6)), size=10, colour=config.TEXT_DIM, bold=True)  # left end
    text(surface, "LATE", (rect.right, rect.bottom + _s(6)), size=10, colour=config.TEXT_DIM, bold=True, align="right")  # right end
    text(surface, "0", (rect.centerx, rect.bottom + _s(6)), size=10, colour=config.TEXT_DIM, bold=True, align="center")  # the middle


def draw_calibration(  # the metronome screen
    surface: pygame.Surface,  # where to draw
    beat_index: int,  # the click we are on
    total_beats: int,  # clicks planned
    offsets: Iterable[float],  # usable taps so far
    current_offset_ms: Optional[float],  # the newest tap
    beat_phase: float,  # 0 to 1 through the beat
) -> None:  # draws, returns nothing
    """Tap-to-metronome screen.

    beat_phase is 0..1 through the current beat, used to pulse the ring so the
    player has something to aim at. The dots round the ring are the sixteen
    clicks, so the length of the test is visible from the first one.
    """
    draw_background(surface)  # the gradient
    centre_x = config.WINDOW_WIDTH / 2.0  # used a lot
    gradient_text(surface, "CALIBRATION", (centre_x, _s(66)), size=40, align="middle")  # the heading
    text(surface, "Tap any lane key on every click. The median of your taps is saved as the offset.", (centre_x, _s(100)), size=14, colour=config.TEXT_MUTED, align="center")  # the instruction

    centre = (int(centre_x), _s(250))  # the metronome
    pulse = 1.0 - beat_phase  # 1 at the click, 0 at the next
    pygame.gfxdraw.filled_circle(surface, centre[0], centre[1], _s(80), config.RECEPTOR_FILL)  # the dark disc
    _soft_ring(surface, centre, _s(80), config.LANE_EDGE, _s(2), 255)  # its rim
    _soft_ring(surface, centre, _s(80) + int(_sf(22) * pulse), config.HIT_FLASH, _s(3), int(40 + 180 * pulse))  # snaps wide on the click and closes in
    for beat in range(total_beats):  # one dot per click
        angle = -math.pi / 2 + 2 * math.pi * beat / max(1, total_beats)  # clockwise from the top
        x = int(centre[0] + math.cos(angle) * _s(112))  # on a ring outside the disc
        y = int(centre[1] + math.sin(angle) * _s(112))  # same
        if beat < beat_index:  # already played
            pygame.gfxdraw.filled_circle(surface, x, y, _s(4), config.HIT_FLASH)  # lit
        elif beat == beat_index:  # the current click
            pygame.gfxdraw.filled_circle(surface, x, y, _s(5) + int(_sf(2) * pulse), _lighten(config.HIT_FLASH, 0.5))  # bigger, pulsing
        else:  # still to come
            pygame.gfxdraw.aacircle(surface, x, y, _s(4), config.LANE_EDGE)  # an empty ring
    text(surface, str(min(beat_index + 1, total_beats)), (centre[0], centre[1] - _s(8)), size=44, colour=config.TEXT, bold=True, align="middle")  # the click number
    text(surface, f"of {total_beats}", (centre[0], centre[1] + _s(26)), size=13, colour=config.TEXT_MUTED, bold=True, align="middle")  # out of how many

    collected = list(offsets)  # the taps so far
    axis = pygame.Rect(int(centre_x) - _s(220), _s(396), _s(440), _s(22))  # the tap strip
    _timing_axis(surface, axis, collected, config.CALIBRATION_TOLERANCE_MS)  # one tick per tap, the windows shaded

    median = statistics.median(collected) if collected else None  # the number that will be saved
    _stat_block(surface, "TAPS", str(len(collected)), centre_x - _s(150), _s(452))  # left
    _stat_block(surface, "MEDIAN", "--" if median is None else _ms(median), centre_x, _s(452), config.HIT_FLASH)  # middle, the one that matters
    _stat_block(surface, "LAST TAP", "--" if current_offset_ms is None else _ms(current_offset_ms), centre_x + _s(150), _s(452))  # right

    hint_row(surface, ((config.LANE_LABELS, "tap"), (("ESC",), "cancel")), centre_x, _s(548), size=12)  # the keys


def draw_results(  # the results screen
    surface: pygame.Surface,  # where to draw
    session: PlaySession,  # the finished run
    log_name: str,  # for the footer
    chart_title: str,  # under the heading
    new_best: bool = False,  # beat the best accuracy for this chart this session
) -> None:  # draws, returns nothing
    """The results card. No beat grid behind it any more: the old one showed
    through the translucent panel as a blue line across the stats."""
    draw_background(surface)  # the gradient
    centre_x = config.WINDOW_WIDTH / 2.0  # used a lot here, same as the other screens
    score = session.score  # the run's totals
    rank_colour = RANK_COLOURS.get(score.rank, config.HIT_FLASH)  # colour for this rank

    tracked_text(surface, "RUN COMPLETE", (centre_x, _s(26)), size=12, colour=config.HIT_FLASH, tracking=6, align="center")  # the label
    shown, size = fit_text(chart_title, _s(700), 22, 15, bold=True)  # the chart name, fitted
    text(surface, shown, (centre_x, _s(46)), size=size, colour=config.TEXT, bold=True, align="center")  # under it

    card = pygame.Rect(_s(110), _s(92), _s(740), _s(352))  # the results card
    panel(surface, card, alpha=238)  # nearly solid

    # left: rank badge with an accuracy ring
    badge = (card.x + _s(140), card.y + _s(128))  # badge centre
    side = _s(220)  # the glow's box
    halo = pygame.Surface((side, side), pygame.SRCALPHA)  # a soft glow behind it
    for step in range(6, 0, -1):  # widest first
        pygame.gfxdraw.filled_circle(halo, side // 2, side // 2, _s(74 + step * 5), (*rank_colour, 6))  # stacked faint discs
    surface.blit(halo, halo.get_rect(center=badge))  # behind the badge
    pygame.gfxdraw.filled_circle(surface, badge[0], badge[1], _s(76), config.RECEPTOR_FILL)  # the disc
    _thick_arc(surface, badge, _s(76), 1.0, config.LANE_EDGE, _s(7))  # the empty track
    _thick_arc(surface, badge, _s(76), score.accuracy / 100.0, rank_colour, _s(7))  # fills with accuracy
    glow_text(surface, score.rank, (badge[0], badge[1] + _s(2)), size=88, colour=rank_colour, glow=rank_colour, align="middle")  # the letter
    text(surface, f"{score.accuracy:.1f}%", (badge[0], card.y + _s(222)), size=30, colour=config.TEXT, bold=True, align="center")  # accuracy under the badge
    text(surface, f"{score.points:,} / {score.max_points:,} pts", (badge[0], card.y + _s(260)), size=13, colour=config.TEXT_MUTED, mono=True, align="center")  # points under that

    badges = []  # the little achievement pills
    if score.miss == 0 and score.judged == score.note_count and score.note_count > 0:  # nothing missed
        badges.append(("FULL COMBO", RANK_COLOURS["S"]))  # gold
    if new_best:  # better than this session's earlier runs
        badges.append(("NEW BEST", config.HIT_FLASH))  # cyan
    for index, (message, colour) in enumerate(badges):  # stacked under the points
        chip(surface, message, (badge[0], card.y + _s(298 + index * 28)), colour, size=11, filled=True)  # one pill each

    pygame.draw.line(surface, config.LANE_EDGE, (card.x + _s(280), card.y + _s(26)), (card.x + _s(280), card.bottom - _s(26)), _s(1))  # divider

    # right: the three counts with bars
    left, right = card.x + _s(312), card.right - _s(32)  # the right section's edges
    rows = (("PERFECT", score.perfect, config.PERFECT_COLOUR), ("GOOD", score.good, config.GOOD_COLOUR), ("MISS", score.miss, config.MISS_COLOUR))  # the counts
    for row, (label, value, colour) in enumerate(rows):  # one row each
        y = card.y + _s(26 + row * 50)  # spaced rows
        text(surface, label, (left, y + _s(4)), size=15, colour=colour, bold=True)  # the word
        text(surface, str(value), (right, y), size=22, colour=colour, bold=True, align="right")  # the count
        track = pygame.Rect(left, y + _s(32), right - left, _s(4))  # the bar track
        pygame.draw.rect(surface, config.LANE_EDGE, track, border_radius=_s(2))  # background
        if value > 0:  # something to fill
            pygame.draw.rect(surface, colour, pygame.Rect(left, track.y, max(_s(4), int(track.width * value / max(1, score.note_count))), track.height), border_radius=_s(2))  # share of the chart

    stats = [("MAX COMBO", str(score.best_combo)), ("EARLY", str(score.early_taps)), ("LATE", str(score.late_taps)), ("STRAY", str(score.stray_taps))]  # the habits
    if session.hold_count:  # week 12: only charts with holds get a fifth column
        stats.append(("HOLDS", f"{session.holds_kept}/{session.hold_count}"))  # tails held out of tails played
    step = (right - left) / len(stats)  # four or five equal columns
    for index, (label, value) in enumerate(stats):  # one block each
        _stat_block(surface, label, value, left + step * (index + 0.5), card.y + _s(186))  # centred in its column

    errors = session.hit_errors  # tap - target for every hit
    tracked_text(surface, "TIMING", (left, card.y + _s(256)), size=10, tracking=2)  # the label
    if errors:  # only with hits to describe
        text(surface, f"mean {_ms(statistics.fmean(errors))}   median {_ms(statistics.median(errors))}", (right, card.y + _s(254)), size=12, colour=config.TEXT_MUTED, mono=True, align="right")  # the two numbers the calibration story is about
    _timing_axis(surface, pygame.Rect(left, card.y + _s(278), right - left, _s(22)), errors, config.GOOD_MS + 20)  # one tick per hit

    text(surface, f"tap log  {log_name}", (centre_x, card.bottom + _s(16)), size=12, colour=config.TEXT_DIM, mono=True, align="center")  # which csv
    hint_row(surface, ((("R",), "RETRY"), (("T",), "TITLE"), (("ESC",), "QUIT")), centre_x, _s(540), size=13)  # the keys


def _draw_mini_hold(surface: pygame.Surface, topleft: tuple[int, int], tint: Colour) -> pygame.Rect:  # a small hold note for the guide
    """A hold note drawn small enough to sit next to a line of text: tail cap
    on top, body, head at the bottom. Same parts as the real one in play."""
    x, y = topleft  # where the icon starts
    head = pygame.Rect(x, y + _s(26), _s(40), _s(10))  # the head, at the bottom like a note on the line
    body = pygame.Rect(head.centerx - _s(8), y + _s(2), _s(16), head.y - y - _s(2))  # the body above it, about the same share of the head as in play
    pygame.draw.rect(surface, _darken(tint, 0.35), body)  # body fill
    pygame.draw.rect(surface, _lighten(tint, 0.4), body, width=_s(1))  # body rails
    cap = pygame.Rect(body.x - _s(4), y, body.width + _s(8), _s(4))  # the tail cap
    pygame.draw.rect(surface, _lighten(tint, 0.3), cap, border_radius=_s(2))  # on top of the body
    pygame.draw.rect(surface, _lighten(tint, 0.3), head, border_radius=_s(4))  # the head
    pygame.draw.line(surface, (255, 255, 255), (head.x + _s(6), head.centery), (head.right - _s(7), head.centery), _s(1))  # its core line
    return pygame.Rect(x, y, head.width, head.bottom - y)  # the whole icon


def draw_guide(  # the week 12 short guide: one screen, everything a first run needs
    surface: pygame.Surface,  # where to draw
    calibration_ms: float,  # the saved offset, for the calibration row
) -> None:  # draws, returns nothing
    """The short guide from the week 12 plan. One screen, six short rows, no
    scrolling: what the keys are, when to press, what the windows mean and
    what a MISS is, how a hold note works, why calibration exists, and which
    keys change the chart and open the two-clock overlay.

    The first version had five rows on a 76 px pitch, which left a hole under
    the one-line windows row. The rows are now evenly packed and every row has
    a heading line and a detail line."""
    draw_background(surface)  # the gradient
    centre_x = config.WINDOW_WIDTH / 2.0  # same as the other screens

    tracked_text(surface, "HOW TO PLAY", (centre_x, _s(18)), size=13, colour=config.HIT_FLASH, tracking=6, align="center")  # the label
    gradient_text(surface, "BEATLANES", (centre_x, _s(66)), size=40, align="middle")  # the small logo, its middle sits at 66

    card = pygame.Rect(_s(110), _s(112), _s(740), _s(420))  # the guide card
    panel(surface, card, alpha=238)  # nearly solid, the text has to read

    left = card.x + _s(36)  # the text column
    row = card.y + _s(30)  # the first row's top, moves down as rows are drawn
    gap = _s(63)  # row pitch: six rows of heading + detail fill the card evenly

    def detail(message: str, x: int, y: int) -> None:  # the quieter second line of a row
        # fitted to the card, so a long line (or a big offset number) shrinks
        # a size or two instead of running off the right edge
        shown, size = fit_text(message, card.right - _s(36) - x, 13, 11)  # shrink a little rather than overflow
        text(surface, shown, (x, y), size=size, colour=config.TEXT_MUTED)  # muted, under the heading

    # row 1: the four lanes and the one rule
    cursor = left  # the key caps draw left to right
    for lane in range(config.LANE_COUNT):  # one cap per lane
        cap = keycap(surface, config.LANE_LABELS[lane], (int(cursor), row - _s(6)), size=13, tint=LANE_TINTS[lane])  # the lane's colour
        cursor = cap.right + _s(6)  # next cap beside it
    text(surface, "one key per lane", (cursor + _s(10), row + _s(2)), size=14, colour=config.TEXT_MUTED, bold=True)  # the caption
    text(surface, "press the key as the note meets the glowing line", (left, row + _s(30)), size=15, colour=config.TEXT, bold=True)  # the one rule

    # row 2: the two windows, as the chips on the title screen show them, and what a MISS is
    row += gap  # next row down
    chips = (  # the same two windows the title screen shows
        (f"PERFECT ±{config.PERFECT_MS:.0f} ms", config.PERFECT_COLOUR),  # the tight window
        (f"GOOD ±{config.GOOD_MS:.0f} ms", config.GOOD_COLOUR),  # the loose window
    )  # two chips
    chip_x = left  # they start at the text column
    for message, colour in chips:  # draw them left to right
        width = text_width(message, 12, bold=True) + _s(22)  # chip() pads by this much
        chip(surface, message, (chip_x + width // 2, row + _s(8)), colour)  # centred where we want it
        chip_x += width + _s(10)  # next one beside it
    text(surface, "the closer to the line, the better the judgement", (chip_x + _s(10), row), size=13, colour=config.TEXT_MUTED)  # what they mean
    detail(f"no press within {config.GOOD_MS:.0f} ms and the note is a MISS; a press with no note near it is only a stray tap", left, row + _s(30))  # the other two outcomes

    # row 3: hold notes, new in week 12
    row += gap  # next row down
    icon = _draw_mini_hold(surface, (left, row - _s(6)), LANE_TINTS[1])  # a small hold, in the F lane colour
    heading = text(surface, "hold notes", (icon.right + _s(16), row), size=15, colour=config.TEXT, bold=True)  # the heading
    text(surface, "press on the head, keep the key down until the tail reaches the line", (heading.right + _s(10), row + _s(2)), size=13, colour=config.TEXT_MUTED)  # the rule
    detail(f"let go more than {config.HOLD_RELEASE_MS:.0f} ms before the tail and the hold is dropped: it counts as a MISS", icon.right + _s(16), row + _s(26))  # the catch

    # row 4: the countdown
    row += gap  # next row down
    text(surface, "the countdown lands on the first note", (left, row), size=15, colour=config.TEXT, bold=True)  # the heading
    detail("3, 2, 1 sit on the three seconds before it, GO when it reaches the line", left, row + _s(26))  # the detail

    # row 5: calibration
    row += gap  # next row down
    keycap(surface, "C", (left, row - _s(6)), size=13)  # the key
    text(surface, "calibration", (left + _s(48), row + _s(2)), size=15, colour=config.TEXT, bold=True)  # the heading
    detail(f"tap a key on every metronome click; the measured delay ({calibration_ms:+.0f} ms) is subtracted from the clock", left, row + _s(30))  # the why

    # row 6: charts, the overlay, and the tap log
    row += gap  # next row down
    cursor = left  # the arrow caps draw first
    for key in ("LEFT", "RIGHT"):  # one cap each
        cursor = keycap(surface, key, (int(cursor), row - _s(6)), size=12, dim=True).right + _s(4)  # beside each other
    text(surface, "change chart", (cursor + _s(8), row), size=13, colour=config.TEXT_MUTED)  # the caption
    tab_x = cursor + _s(8) + text_width("change chart", 13) + _s(40)  # the TAB pair starts after the arrows' caption
    tab = keycap(surface, "TAB", (int(tab_x), row - _s(6)), size=12, dim=True)  # the overlay key
    text(surface, "the two-clock timing overlay", (tab.right + _s(8), row), size=13, colour=config.TEXT_MUTED)  # the caption
    detail("every run also writes a csv tap log, one row per note, for the drift analysis", left, row + _s(30))  # the log line

    hint_row(surface, ((("G",), "BACK"), (("ESC",), "TITLE")), centre_x, _s(566), size=13)  # how to leave
