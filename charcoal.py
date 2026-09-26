"""Photo -> charcoal drawing (deterministic local filter, no AI).

An expressive multi-layer charcoal look built with Pillow + NumPy:
  * a color-dodge line pass for crisp detail,
  * a gradient (Sobel) pass for bold structural strokes,
  * a smudged tonal pass that fills shadows like rubbed charcoal, and
  * procedural paper grain.
The passes are combined subtractively (each can only darken) over white paper.
Fully deterministic: same input + params -> same output.

On save the result is written as PNG with the *original* file name preserved in
the image metadata, while the on-disk file name may be changed.
"""

from __future__ import annotations

import io
import os

import numpy as np
from PIL import Image, ImageFilter, ImageOps, PngImagePlugin


def _blur(arr: np.ndarray, radius: float) -> np.ndarray:
    """Gaussian-blur a float (0..255) array via Pillow and return it as float."""
    im = Image.fromarray(np.clip(arr, 0.0, 255.0).astype(np.uint8), "L")
    return np.asarray(im.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)


def render(
    image_bytes: bytes,
    blur: float = 12.0,
    depth: float = 1.0,
    contrast: float = 1.2,
    invert: bool = False,
    texture: float = 0.6,
    max_edge: int = 2400,
) -> Image.Image:
    """Return a single-channel ('L') artistic charcoal rendering of ``image_bytes``.

    - ``blur``     softness of strokes/smudging; higher = softer, broader.
    - ``depth``    0..1 amount of tonal charcoal shading (0 = pure line sketch).
    - ``contrast`` tonal contrast around mid-gray (1.0 = unchanged).
    - ``invert``   negative output (white chalk on a black board).
    - ``texture``  0..1 strength of paper grain.
    - ``max_edge`` longest edge worked on (caps memory/time on huge inputs).
    """
    blur = max(0.1, float(blur))
    depth = max(0.0, min(1.0, float(depth)))
    contrast = max(0.1, float(contrast))
    texture = max(0.0, min(1.0, float(texture)))

    src = Image.open(io.BytesIO(image_bytes))
    src = ImageOps.exif_transpose(src)  # respect photo orientation
    gray_img = src.convert("L")
    if max_edge and max(gray_img.size) > max_edge:
        gray_img.thumbnail((max_edge, max_edge))  # bound arrays before numpy work
    gray = np.asarray(gray_img, dtype=np.float32)
    h, w = gray.shape

    # 1) Fine line pass: inverted-blur color dodge -> crisp detail lines on white.
    inv_blur = _blur(255.0 - gray, blur)
    denom = np.clip(255.0 - inv_blur, 1.0, 255.0)
    sketch = np.minimum(gray * 255.0 / denom, 255.0) / 255.0  # ~1 white, dips at lines

    # 2) Structural stroke pass: gradient magnitude -> bold darkened edges.
    gy, gx = np.gradient(_blur(gray, max(0.6, blur * 0.2)))
    grad = np.sqrt(gx * gx + gy * gy)
    grad /= grad.max() + 1e-6
    edge_light = 1.0 - np.clip(grad * 3.5, 0.0, 1.0)  # 0 on strong edges .. 1

    # 3) Tonal shading pass: contrast-stretched, shadow-deepened, then smudged.
    tone = np.clip((gray - 128.0) * contrast + 128.0, 0.0, 255.0) / 255.0
    tone = tone ** (1.0 + 0.8 * depth)                 # deepen darks with depth
    shade = np.minimum(tone, _blur(tone * 255.0, blur * 0.4) / 255.0)  # rubbed

    # Combine subtractively over white paper (each layer only darkens).
    light = sketch
    light *= 1.0 - depth * (1.0 - shade)               # tonal charcoal fill
    light *= 1.0 - 0.55 * (1.0 - edge_light)           # bold strokes

    # 4) Paper grain: subtle fibre + speckle, deterministic.
    if texture > 0.0:
        rng = np.random.default_rng(12345)
        grain = rng.standard_normal((h, w)).astype(np.float32)
        fibre = _blur(rng.random((h, w)).astype(np.float32) * 255.0, 1.2) / 255.0
        light -= texture * 0.05 * grain * (1.0 - light)          # grain within strokes
        light -= texture * 0.03 * (fibre - fibre.mean())         # faint paper tooth

    out = np.clip(light * 255.0, 0.0, 255.0)
    if invert:
        out = 255.0 - out
    return Image.fromarray(out.astype(np.uint8), mode="L")


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
