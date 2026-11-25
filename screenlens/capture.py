"""Screenshot plus crop / annotate / encode. Everything but grab() is pure PIL."""
import io
from dataclasses import dataclass

import mss
from PIL import Image, ImageDraw

from screenlens.geometry import bbox, pad_and_clamp

CROP_PADDING = 40
MIN_CROP_SIDE = 300
CONTEXT_MAX_SIDE = 1280
LASSO_WIDTH = 6
LASSO_COLOR = (255, 0, 0)
JPEG_QUALITY = 85


@dataclass(frozen=True)
class EncodedImage:
    data: bytes
    mime_type: str

    @property
    def extension(self):
        return "png" if self.mime_type == "image/png" else "jpg"


def grab(monitor=None) -> Image.Image:
    """Screenshot of a monitor (primary by default) in physical pixels."""
    with mss.MSS() as sct:
        shot = sct.grab(monitor or sct.primary_monitor)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def make_crop(image, points):
    crop = image.crop(pad_and_clamp(bbox(points), CROP_PADDING, image.size))
    if min(crop.size) < MIN_CROP_SIDE:
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
    return crop


def make_context(image, points):
    context = image.copy()
    closed = list(points) + [points[0]]
    ImageDraw.Draw(context).line(closed, fill=LASSO_COLOR, width=LASSO_WIDTH, joint="curve")
    # thumbnail() only ever shrinks and keeps the aspect ratio.
    context.thumbnail((CONTEXT_MAX_SIDE, CONTEXT_MAX_SIDE), Image.Resampling.LANCZOS)
    return context


def encode(image, png=False) -> EncodedImage:
    buffer = io.BytesIO()
    if png:
        image.save(buffer, "PNG")
        return EncodedImage(buffer.getvalue(), "image/png")
    image.save(buffer, "JPEG", quality=JPEG_QUALITY)
    return EncodedImage(buffer.getvalue(), "image/jpeg")


def build_images(image, points, png=False):
    """(context, crop), both encoded, for a lasso in physical pixels."""
    return encode(make_context(image, points), png), encode(make_crop(image, points), png)
