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


def _smear(field: np.ndarray, tx: np.ndarray, ty: np.ndarray, length: int) -> np.ndarray:
    """Line-integral convolution: average ``field`` along the per-pixel (tx, ty)
    direction, producing strokes that follow the drawing's form."""
    h, w = field.shape
    ys, xs = np.indices((h, w), dtype=np.float32)
    acc = np.zeros_like(field)
    for k in range(-length, length + 1):
        sx = np.clip(xs + tx * k, 0, w - 1).astype(np.int32)
        sy = np.clip(ys + ty * k, 0, h - 1).astype(np.int32)
        acc += field[sy, sx]
    return acc / (2 * length + 1)


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

    # Tonal base: contrast around mid-gray, then deepen shadows by ``depth``.
    tone = np.clip((gray - 128.0) * contrast + 128.0, 0.0, 255.0) / 255.0
    tone = tone ** (1.0 + 0.8 * depth)
    demand = 1.0 - tone                                # how much charcoal a pixel wants

    # Form field: unit tangent (along edges), blended with a diagonal in flat areas
    # so open regions get natural diagonal hatching.
    gyf, gxf = np.gradient(_blur(gray, max(1.0, blur * 0.3)))
    mag = np.sqrt(gxf * gxf + gyf * gyf)
    m = mag / (mag.max() + 1e-6)
    inv = 1.0 / (mag + 1e-6)
    tx, ty = -gyf * inv, gxf * inv
    wgt = np.clip(m * 4.0, 0.0, 1.0)
    tx = tx * wgt + 0.7071 * (1.0 - wgt)
    ty = ty * wgt + 0.7071 * (1.0 - wgt)
    nrm = np.sqrt(tx * tx + ty * ty) + 1e-6
    tx, ty = tx / nrm, ty / nrm

    # Charcoal stroke texture: smear noise along the form (line-integral convolution).
    rng = np.random.default_rng(7)
    length = int(np.clip(round(blur), 5, 18))
    strokes = _smear(rng.standard_normal((h, w)).astype(np.float32), tx, ty, length)
    strokes = (strokes - strokes.mean()) / (strokes.std() + 1e-6)
    strokes = np.clip(strokes * 0.5 + 0.5, 0.0, 1.0)   # 0..1 stroke field

    edge = np.clip(m * 3.5, 0.0, 1.0)                   # crisp contour lines

    # Deposit charcoal: tone demand, textured by strokes, with edges on top.
    deposit = demand * (0.35 + 0.95 * strokes) + 0.45 * edge
    deposit = np.clip(deposit, 0.0, 1.0)
    light = 1.0 - deposit

    # Paper tooth + grain (deterministic, subtle).
    if texture > 0.0:
        fibre = _blur(rng.random((h, w)).astype(np.float32) * 255.0, 1.0) / 255.0
        light -= texture * 0.04 * (fibre - fibre.mean())
        light -= texture * 0.05 * (rng.random((h, w)).astype(np.float32) - 0.5) * deposit

    # Keep the paper clean: force near-white, edge-free areas to pure white (no halo).
    background = (demand < 0.06) & (edge < 0.08)
    light = np.where(background, 1.0, light)

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
