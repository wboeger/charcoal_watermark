"""Photo -> charcoal drawing.

Port of the reference OpenCV sketch (grayscale -> inverted-blur color-dodge ->
charcoal-depth multiply) to Pillow + NumPy, exposed with rendering options.

On save the result is written as PNG with the *original* file name preserved in
the image metadata (tEXt chunks), while the on-disk file name may be changed.
"""

from __future__ import annotations

import io
import os

import numpy as np
from PIL import Image, ImageFilter, ImageOps, PngImagePlugin


def render(
    image_bytes: bytes,
    blur: float = 12.0,
    depth: float = 1.0,
    contrast: float = 1.2,
    invert: bool = False,
) -> Image.Image:
    """Return a single-channel ('L') charcoal rendering of ``image_bytes``.

    - ``blur``     Gaussian radius for the dodge base; higher = softer strokes.
    - ``depth``    0..1 blend toward the darker charcoal-multiply pass.
    - ``contrast`` multiplier around mid-gray (1.0 = unchanged).
    - ``invert``   negative output (white chalk on a black board).
    """
    blur = max(0.1, float(blur))
    depth = max(0.0, min(1.0, float(depth)))
    contrast = max(0.1, float(contrast))

    src = Image.open(io.BytesIO(image_bytes))
    src = ImageOps.exif_transpose(src)  # respect photo orientation
    gray_img = src.convert("L")
    gray = np.asarray(gray_img, dtype=np.float32)

    blurred = np.asarray(
        gray_img.point(lambda v: 255 - v).filter(ImageFilter.GaussianBlur(radius=blur)),
        dtype=np.float32,
    )
    inverted_blur = np.clip(255.0 - blurred, 1.0, 255.0)

    # Color-dodge divide -> line sketch.
    sketch = np.minimum(gray * 256.0 / inverted_blur, 255.0)
    # Charcoal depth: darken by the underlying tone.
    charcoal = sketch * gray / 256.0

    out = sketch * (1.0 - depth) + charcoal * depth
    if contrast != 1.0:
        out = (out - 128.0) * contrast + 128.0
    if invert:
        out = 255.0 - out

    out = np.clip(out, 0.0, 255.0).astype(np.uint8)
    return Image.fromarray(out, mode="L")


def to_png_bytes(image: Image.Image, original_filename: str | None = None) -> bytes:
    """Encode ``image`` as PNG, embedding ``original_filename`` in the metadata.

    The file name is stored as an ``iTXt`` chunk (UTF-8), so accented and other
    non-Latin-1 names survive intact.
    """
    meta = PngImagePlugin.PngInfo()
    if original_filename:
        meta.add_itxt("original_filename", original_filename)
        meta.add_itxt("Title", os.path.splitext(original_filename)[0])
    meta.add_text("Software", "Chapter Watermarker - Charcoal")
    buf = io.BytesIO()
    image.save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()


def read_original_filename(png_bytes: bytes) -> str | None:
    """Read back the embedded original file name from a saved PNG (for tests)."""
    with Image.open(io.BytesIO(png_bytes)) as im:
        return im.info.get("original_filename")
