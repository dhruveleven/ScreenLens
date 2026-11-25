import io

import pytest
from PIL import Image

from screenlens.capture import build_images, encode, make_context, make_crop

SQUARE = [(800, 400), (1200, 400), (1200, 800), (800, 800), (800, 420)]


@pytest.fixture
def screen():
    return Image.effect_noise((1920, 1080), 60).convert("RGB")


def test_crop_has_padding_and_no_upscale_when_large(screen):
    assert make_crop(screen, SQUARE).size == (480, 480)


def test_small_crop_is_upscaled_2x(screen):
    points = [(500, 500), (600, 500), (600, 550), (500, 550), (500, 505)]
    assert make_crop(screen, points).size == ((100 + 80) * 2, (50 + 80) * 2)


def test_crop_upscaled_when_only_one_side_is_small(screen):
    points = [(100, 500), (900, 500), (900, 550), (100, 550), (100, 505)]
    assert make_crop(screen, points).size == (880 * 2, 130 * 2)


def test_crop_of_exactly_300_is_not_upscaled(screen):
    points = [(500, 500), (720, 500), (720, 720), (500, 720), (500, 510)]
    assert make_crop(screen, points).size == (300, 300)


def test_crop_clamped_at_corner(screen):
    points = [(0, 0), (400, 0), (400, 400), (0, 400), (0, 10)]
    assert make_crop(screen, points).size == (440, 440)


def test_crop_is_not_annotated():
    crop = make_crop(Image.new("RGB", (1920, 1080), "white"), SQUARE)
    assert crop.getcolors() == [(480 * 480, (255, 255, 255))]


def test_context_is_downscaled_and_outlined_in_red():
    original = Image.new("RGB", (1920, 1080), "white")
    context = make_context(original, SQUARE)
    assert context.size == (1280, 720)
    factor = 1280 / 1920
    for x, y in [(1000, 400), (1200, 600), (1000, 800), (800, 600), (800, 410)]:  # 800,410: closing edge
        r, g, b = context.getpixel((round(x * factor), round(y * factor)))
        assert r > 200 and g < 120 and b < 120
    assert context.getpixel((round(1000 * factor), round(600 * factor))) == (255, 255, 255)
    assert original.getcolors() == [(1920 * 1080, (255, 255, 255))]  # input untouched


def test_context_portrait_and_never_upscaled():
    assert max(make_context(Image.new("RGB", (1080, 1920)), SQUARE).size) == 1280
    assert make_context(Image.new("RGB", (800, 600)), SQUARE).size == (800, 600)


def test_jpeg_is_smaller_than_png(screen):
    jpeg, png = encode(screen), encode(screen, png=True)
    assert jpeg.mime_type == "image/jpeg" and jpeg.data[:2] == b"\xff\xd8"
    assert png.mime_type == "image/png" and png.data[:4] == b"\x89PNG"
    assert len(jpeg.data) < len(png.data)


def test_build_images_order_and_upload_size(screen):
    context, crop = build_images(screen, SQUARE)
    assert Image.open(io.BytesIO(context.data)).size == (1280, 720)
    assert Image.open(io.BytesIO(crop.data)).size == (480, 480)
    assert len(context.data) + len(crop.data) < 2_000_000  # even for pure noise
