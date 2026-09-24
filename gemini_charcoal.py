"""Photo -> artist-quality charcoal drawing via Gemini (``gemini-2.5-flash-image``).

This is the *artistic* engine. It hands Gemini two images:

  1. the user's photo  — the subject to redraw, faithfully;
  2. a bundled reference artwork (``references/charcoal_reference.png``) — the
     *style* to imitate: a detailed, realistic, hand-drawn charcoal-and-graphite
     drawing on warm paper with fine stippling, hatching, a soft smudged
     background and a cast shadow.

The prompt is written to be faithful to the subject (same composition, nothing
invented) while matching the reference's medium and craftsmanship. No SDK
dependency: talks to the REST endpoint with the standard library.
"""

from __future__ import annotations

import base64
import io
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

MODEL = "gemini-2.5-flash-image"
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Longest edge sent for each image. Bounds latency and cost.
_MAX_EDGE = 1536
_REF_MAX_EDGE = 1024
_TIMEOUT = 180

_REFERENCE_PATH = Path(__file__).with_name("references") / "charcoal_reference.png"

# The user-authored charcoal art direction \u2014 the fixed aesthetic every render aims
# for. Applied to the *uploaded* subject (never a new one) so the app stays faithful.
_USER_STYLE = (
    "The illustration must be executed on a sheet of textured, off-white cotton "
    "drawing paper, showing visible paper grain. Apply a style that features rich, "
    "velvety deep blacks and dramatic, high-contrast values on the subject itself. "
    "The background must be PURE WHITE, empty drawing paper. Do NOT add any gray wash, "
    "smudged clouds, atmospheric haze, halo, aura, vignette or gradient in the negative "
    "space around the subject \u2014 the area surrounding the subject must stay clean white, "
    "with at most a faint contact shadow directly beneath it and a few tiny stray "
    "speckles. Keep the deep blacks confined to the subject itself. Define the form with "
    "precise rendering and cross-hatching, accented by "
    "deliberate eraser marks to create highlights. Maintain a raw, biological, "
    "specimen-illustration feel."
)

_PROMPT_HEAD = (
    "You are a master fine artist. Redraw the SUBJECT image (provided below) as a "
    "highly detailed charcoal and graphite illustration of the very same subject \u2014 "
    "never a new or different subject.\n"
    "FIDELITY: reproduce the subject's shapes, pose, proportions, spatial layout and "
    "composition exactly, framed the same way. Do NOT add, remove, relocate or invent "
    "any object, element, text, marking or detail that is not present in the subject "
    "image; keep every real structural detail accurate.\n"
    "STYLE: "
)

_PROMPT_TAIL = (
    "\nAlso match the drawing medium and craftsmanship (mark-making, contrast, "
    "detail) of the STYLE REFERENCE artwork provided, but never copy its subject and "
    "IGNORE its background \u2014 keep the background plain white as instructed. The "
    "result must read as a genuine hand-drawn artwork on textured paper, not a photo "
    "filter. Return only the resulting image, nothing else."
)

_SEPIA_LINE = (
    " Render the subject's charcoal work in warm sepia, umber and soft-brown tones on "
    "aged cream paper (keep the background the clean paper colour)."
)

# key -> (label, emphasis prefix layered onto _USER_STYLE). "chalk" is a full
# override handled in _build_prompt. Order sets the dropdown order.
STYLES: dict[str, tuple[str, str]] = {
    "detailed": ("Detailed (recommended)", ""),
    "bold": (
        "Bold high-contrast",
        "Push the drama further with the deepest, most velvety blacks and starker "
        "high-contrast values. ",
    ),
    "soft": (
        "Soft atmospheric",
        "Lean softer and moodier, with more rubbed, hazy graphite smudging and "
        "gentler lost-and-found edges. ",
    ),
    "sketch": (
        "Loose sketch",
        "Render more loosely, with energetic visible directional strokes and open "
        "cross-hatching, still carefully observed. ",
    ),
    "chalk": ("White chalk on black", "__CHALK__"),
}
DEFAULT_STYLE = "detailed"

_CHALK_STYLE = (
    "Render as white chalk and charcoal on black paper (chiaroscuro): luminous "
    "highlights and light strokes on a dark ground, with the same rich contrast, "
    "precise cross-hatching, atmospheric smudges, scattered speckles and raw "
    "biological specimen-illustration feel."
)


class GeminiError(RuntimeError):
    """Raised when the Gemini request fails or returns no image."""


def env_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def available() -> bool:
    """True when a Gemini key is configured on the server (env)."""
    return bool(env_key())


def resolve_style(style: str | None) -> str:
    return style if style in STYLES else DEFAULT_STYLE


def _build_prompt(style: str, detail: str, sepia: bool) -> str:
    style = resolve_style(style)
    emphasis = STYLES[style][1]
    if emphasis == "__CHALK__":
        style_body = _CHALK_STYLE
    else:
        style_body = emphasis + _USER_STYLE + (_SEPIA_LINE if sepia else "")
    prompt = _PROMPT_HEAD + style_body + _PROMPT_TAIL
    detail = (detail or "").strip()
    if detail:
        # User detail may refine medium/tone only, never the content; reaffirm.
        prompt += (
            f"\nAdditional stylistic direction (medium and tone only, do not change "
            f"the subject or add content): {detail}."
        )
    return prompt


def _encode(image_bytes: bytes, max_edge: int) -> str:
    src = Image.open(io.BytesIO(image_bytes))
    src = ImageOps.exif_transpose(src).convert("RGB")
    if max(src.size) > max_edge:
        src.thumbnail((max_edge, max_edge))
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


_ref_cache: str | None = None


def _reference_b64() -> str | None:
    """Base64 PNG of the bundled style reference, or ``None`` if unavailable."""
    global _ref_cache
    if _ref_cache is None and _REFERENCE_PATH.exists():
        _ref_cache = _encode(_REFERENCE_PATH.read_bytes(), _REF_MAX_EDGE)
    return _ref_cache


def _part_text(text: str) -> dict:
    return {"text": text}


def _part_image(b64: str) -> dict:
    return {"inline_data": {"mime_type": "image/png", "data": b64}}


def render(
    image_bytes: bytes,
    style: str = DEFAULT_STYLE,
    detail: str = "",
    sepia: bool = False,
    api_key: str | None = None,
) -> Image.Image:
    """Return an artist-quality charcoal rendering of ``image_bytes`` from Gemini.

    ``api_key`` (from the form) takes precedence over the server environment key.
    Raises ``GeminiError`` on missing key, transport/HTTP failure, or if the model
    returns no image (e.g. a safety refusal) \u2014 never a fabricated result.
    """
    key = (api_key or "").strip() or env_key()
    if not key:
        raise GeminiError(
            "No Gemini API key. Enter one in the form or set GEMINI_API_KEY on the server."
        )

    parts = [_part_text(_build_prompt(style, detail, sepia))]
    parts.append(_part_text("SUBJECT image (redraw this exact scene, faithfully):"))
    parts.append(_part_image(_encode(image_bytes, _MAX_EDGE)))
    ref = _reference_b64()
    if ref:
        parts.append(_part_text(
            "STYLE REFERENCE artwork (imitate ONLY its drawing style, medium, level "
            "of detail, background and paper \u2014 never its subject):"
        ))
        parts.append(_part_image(ref))
    parts.append(_part_text("Now produce the drawing. Return only the image."))

    payload = json.dumps({
        "contents": [{"parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }).encode("utf-8")

    url = _ENDPOINT.format(model=MODEL)
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise GeminiError(f"Gemini API error {exc.code}: {_http_error_detail(exc)}") from exc
    except urllib.error.URLError as exc:
        raise GeminiError(f"Could not reach Gemini API: {exc.reason}") from exc

    return _extract_image(data)


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read())
        return body.get("error", {}).get("message") or str(body)[:300]
    except Exception:
        return exc.reason or "unknown error"


def _extract_image(data: dict) -> Image.Image:
    candidates = data.get("candidates") or []
    if not candidates:
        block = (data.get("promptFeedback") or {}).get("blockReason")
        raise GeminiError(
            f"Gemini returned no candidates (blocked: {block})." if block
            else "Gemini returned no candidates."
        )
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text_bits = []
    for part in parts:
        blob = part.get("inline_data") or part.get("inlineData")
        if blob and blob.get("data"):
            raw = base64.b64decode(blob["data"])
            return Image.open(io.BytesIO(raw)).convert("RGB")
        if part.get("text"):
            text_bits.append(part["text"])
    reason = candidates[0].get("finishReason")
    msg = " ".join(text_bits).strip() or f"no image in response (finishReason={reason})"
    raise GeminiError(f"Gemini did not return an image: {msg}")
