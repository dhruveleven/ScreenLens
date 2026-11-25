import pytest

from screenlens.geometry import bbox, is_tiny_stroke, pad_and_clamp, place_panel, to_physical

SCREEN = (0, 0, 1280, 720)
PANEL = (440, 400)


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
def test_to_physical_at_windows_scalings(scale):
    pixels = (1920, 1080)
    logical = (1920 / scale, 1080 / scale)
    mapped = to_physical([(0, 0), (logical[0] / 2, logical[1] / 2), (100, 200)], logical, pixels)
    assert mapped[0] == (0, 0)
    assert mapped[1] == pytest.approx((960, 540))
    assert mapped[2] == pytest.approx((100 * scale, 200 * scale))


def test_to_physical_with_rounded_logical_size():
    # 1366x768 at 125% is 1092.8x614.4, which Qt rounds.
    assert to_physical([(1093, 614)], (1093, 614), (1366, 768)) == [(1365, 767)]


def test_to_physical_clamps_points_outside_screen():
    mapped = to_physical([(-50, -5), (5000, 5000)], (1280, 720), (1920, 1080))
    assert mapped == [(0, 0), (1919, 1079)]


def test_bbox():
    assert bbox([(5, 9), (1, 20), (30, 2)]) == (1, 2, 30, 20)


def test_pad_and_clamp_rounds_outwards():
    assert pad_and_clamp((100.4, 100.6, 200.4, 200.6), 40, (1920, 1080)) == (60, 60, 241, 241)


def test_pad_and_clamp_at_corners():
    assert pad_and_clamp((5, 5, 50, 50), 40, (1920, 1080)) == (0, 0, 90, 90)
    assert pad_and_clamp((1900, 1070, 1919, 1079), 40, (1920, 1080)) == (1860, 1030, 1920, 1080)


@pytest.mark.parametrize("points, tiny", [
    ([], True),
    ([(0, 0), (100, 0), (100, 100), (0, 100)], True),          # only 4 points
    ([(i, i) for i in range(5)], True),                        # 4px box
    ([(i * 2.5, 0) for i in range(5)], False),                 # exactly 10px wide
    ([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)], False),
    ([(i * 20, 1) for i in range(10)], False),                 # flat underline is kept
])
def test_is_tiny_stroke(points, tiny):
    assert is_tiny_stroke(points) is tiny


def test_panel_prefers_below_right():
    assert place_panel((100, 100), PANEL, SCREEN) == (116, 116)


def test_panel_flips_at_bottom_right():
    assert place_panel((1270, 710), PANEL, SCREEN) == (1270 - 16 - 440, 710 - 16 - 400)


@pytest.mark.parametrize("x", range(0, 1281, 160))
@pytest.mark.parametrize("y", range(0, 721, 120))
def test_panel_always_fully_on_screen(x, y):
    px, py = place_panel((x, y), PANEL, SCREEN)
    assert 0 <= px <= 1280 - 440 and 0 <= py <= 720 - 400


def test_panel_on_offset_screen_and_oversized():
    px, _ = place_panel((-1800, 100), PANEL, (-1920, 0, 1920, 1080))
    assert -1920 <= px <= -440
    assert place_panel((10, 10), (2000, 2000), SCREEN) == (0, 0)
