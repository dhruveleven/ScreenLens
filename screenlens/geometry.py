"""Pure coordinate math. No Qt, no PIL, so it is trivially testable.

Points are (x, y) tuples; boxes are (left, top, right, bottom).
"""
import math

MIN_STROKE_POINTS = 5
MIN_STROKE_SIZE = 10


def bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def is_tiny_stroke(points):
    """Accidental click or twitch. A flat underline (wide, not tall) is kept."""
    if len(points) < MIN_STROKE_POINTS:
        return True
    left, top, right, bottom = bbox(points)
    return max(right - left, bottom - top) < MIN_STROKE_SIZE


def to_physical(points, logical_size, pixel_size):
    """Map Qt logical points to screenshot pixels.

    Uses the actual size ratio rather than devicePixelRatio, so rounding of the
    logical size at fractional scaling (125%, 150%) cannot introduce drift.
    """
    sx = pixel_size[0] / logical_size[0]
    sy = pixel_size[1] / logical_size[1]
    max_x, max_y = pixel_size[0] - 1, pixel_size[1] - 1
    return [(min(max(x * sx, 0), max_x), min(max(y * sy, 0), max_y)) for x, y in points]


def pad_and_clamp(box, pad, size):
    """Grow box by pad, round outwards to whole pixels, keep inside (0, 0, *size)."""
    left, top, right, bottom = box
    return (
        max(0, math.floor(left - pad)),
        max(0, math.floor(top - pad)),
        min(size[0], math.ceil(right + pad)),
        min(size[1], math.ceil(bottom + pad)),
    )


def place_panel(anchor, panel_size, screen, gap=16):
    """Top-left for the panel: below-right of anchor, flipped or clamped to fit.

    screen is (x, y, width, height).
    """

    def axis(pos, length, start, extent):
        spot = pos + gap
        if spot + length > start + extent:
            spot = pos - gap - length  # flip to the other side of the anchor
        # Final clamp. start wins if the panel is larger than the screen.
        return max(start, min(spot, start + extent - length))

    sx, sy, sw, sh = screen
    return axis(anchor[0], panel_size[0], sx, sw), axis(anchor[1], panel_size[1], sy, sh)
