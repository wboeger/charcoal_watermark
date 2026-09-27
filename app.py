"""Flask front-end for per-chapter DOCX watermarking.

Upload one .docx plus one or more watermark PNGs. Each ``Heading 1`` chapter
whose name appears in a PNG's file name gets that PNG pinned, semi-transparent,
to the bottom-left of its first page.
"""

from __future__ import annotations

import io
import os
import tempfile
import uuid
import zipfile
from pathlib import Path

from flask import (
    Flask,
    abort,
    render_template,
    request,
    send_file,
)
from werkzeug.utils import secure_filename

import watermarker as wm
import charcoal as ch
import gemini_charcoal as gem
from PIL import Image

# This is a trusted local/self-hosted tool processing the user's own photos, so
# lift Pillow's decompression-bomb guard (large scans/panoramas are legitimate).
Image.MAX_IMAGE_PIXELS = None

# Extra decoders so common phone/web formats import too (HEIC/HEIF from iPhones,
# AVIF). Optional: the app still runs if a plugin is missing.
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass
try:
    import pillow_avif  # noqa: F401  (registers the AVIF opener on import)
except Exception:
    pass

app = Flask(__name__)
# Max upload size per request. Folder uploads of many photos are large, so this
# defaults high and is overridable with MAX_UPLOAD_MB.
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "1024"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


@app.errorhandler(413)
def _too_large(_):
    return (
        f"Upload too large — the limit is {MAX_UPLOAD_MB} MB per upload. "
        "Select fewer or smaller images, or raise MAX_UPLOAD_MB on the server.",
        413,
        {"Content-Type": "text/plain; charset=utf-8"},
    )

# Result store: token -> (kind, ref, name). kind == "path" (temp file on the
# server) or "bytes" (in-memory payload). Output is always delivered to the user
# via download, so nothing needs to persist server-side (Railway's filesystem is
# ephemeral). Bounded so a long-lived instance can't grow without limit.
_OUTPUTS: dict[str, tuple[str, object, str]] = {}
# token -> list[(image_bytes, original_filename)] for the editor/preview/batch.
_UPLOADS: dict[str, list[tuple[bytes, str]]] = {}
# token -> (signature, png_bytes): last Gemini render, so Save reuses exactly
# what the preview showed instead of paying for a second API call.
_GEMINI_CACHE: dict[str, tuple[str, bytes]] = {}
# token -> Gemini API key entered in the form (overrides the server env key).
_KEYS: dict[str, str] = {}
_STORE_CAP = 64

# LOCAL_SAVE_DIR set   -> "local" mode: charcoal writes straight into a chosen
#                         subdirectory on this machine (the user's own computer).
# LOCAL_SAVE_DIR unset -> "download" mode (default, e.g. Railway): charcoal is
#                         returned as a download; a subdirectory is preserved by
#                         packaging the PNG inside a .zip with that folder path.
_local_dir = os.environ.get("LOCAL_SAVE_DIR") or os.environ.get("CHARCOAL_OUTPUT_DIR")
LOCAL_SAVE_DIR = Path(_local_dir).resolve() if _local_dir else None


def _remember(store: dict, token: str, value) -> None:
    """Insert into a bounded token store, evicting the oldest entry when full."""
    if len(store) >= _STORE_CAP:
        store.pop(next(iter(store)))
    store[token] = value


def _pct(value: str | None, default: int) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = default
    return max(1.0, min(100.0, n)) / 100.0


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return "ok", 200


@app.post("/process")
def process():
    upload = request.files.get("docx")
    if not upload or not upload.filename.lower().endswith(".docx"):
        abort(400, "Please upload a .docx file.")

    pngs = [f for f in request.files.getlist("watermarks") if f and f.filename.lower().endswith(".png")]
    if not pngs:
        abort(400, "Please upload at least one .png watermark.")

    opacity = _pct(request.form.get("opacity"), 50)
    width_pct = _pct(request.form.get("width_pct"), 25)

    workdir = tempfile.mkdtemp(prefix="wm_")
    docx_name = secure_filename(upload.filename) or "document.docx"
    docx_path = os.path.join(workdir, docx_name)
    upload.save(docx_path)

    pairs: list[tuple[str, str]] = []
    for i, png in enumerate(pngs):
        original = png.filename  # match on the ORIGINAL name (accents/spaces intact)
        safe = secure_filename(png.filename) or f"watermark_{i}.png"
        path = os.path.join(workdir, f"{i}_{safe}")  # index-prefixed: no disk collisions
        png.save(path)
        pairs.append((path, original))

    stem = os.path.splitext(docx_name)[0]
    download_name = f"{stem}_watermarked.docx"
    output_path = os.path.join(workdir, download_name)

    report = wm.process(docx_path, pairs, output_path, opacity=opacity, width_pct=width_pct, workdir=workdir)

    token = uuid.uuid4().hex
    _remember(_OUTPUTS, token, ("path", output_path, download_name))

    return render_template(
        "result.html",
        report=report,
        token=token,
        opacity_pct=round(opacity * 100),
        width_pct=round(width_pct * 100),
    )


@app.get("/download/<token>")
def download(token: str):
    entry = _OUTPUTS.get(token)
    if not entry:
        abort(404, "This result has expired. Please run it again.")
    kind, ref, name = entry
    if kind == "path":
        if not os.path.exists(ref):
            abort(404, "This result has expired. Please run it again.")
        return send_file(ref, as_attachment=True, download_name=name)
    return send_file(io.BytesIO(ref), as_attachment=True, download_name=name)


def _charcoal_opts(src) -> dict:
    def num(key: str, default: float) -> float:
        try:
            return float(src.get(key, default))
        except (TypeError, ValueError):
            return default

    return {
        "blur": max(0.1, min(50.0, num("blur", 12))),
        "depth": max(0.0, min(1.0, num("depth", 100) / 100.0)),  # UI is 0..100 %
        "contrast": max(0.1, min(3.0, num("contrast", 1.2))),
        "invert": str(src.get("invert", "")).lower() in ("1", "true", "on", "yes"),
    }


def _sepia_flag(src) -> bool:
    return str(src.get("sepia", "")).lower() in ("1", "true", "on", "yes")


def _gemini_signature(src) -> str:
    style = gem.resolve_style(src.get("style"))
    detail = (src.get("detail") or "").strip()
    return f"gemini|{style}|{int(_sepia_flag(src))}|{detail}"


def _render_png(data: bytes, original: str, src, api_key: str | None = None) -> bytes:
    """Render one charcoal PNG for the requested engine (no caching).

    ``engine=gemini`` -> artist-quality Gemini render (faithful to the subject),
    using ``api_key`` if given, else the server env key.
    Anything else -> deterministic local filter (no hallucination possible).
    Raises ``gem.GeminiError`` if a Gemini render fails.
    """
    engine = (src.get("engine") or "classic").lower()
    if engine == "gemini":
        image = gem.render(
            data,
            style=gem.resolve_style(src.get("style")),
            detail=src.get("detail", ""),
            sepia=_sepia_flag(src),
            api_key=api_key,
        )
        return ch.to_png_bytes(image, original)
    image = ch.render(data, **_charcoal_opts(src))
    return ch.to_png_bytes(image, original)


def _charcoal_png(token: str, data: bytes, original: str, src, api_key: str | None = None) -> bytes:
    """Single-image render with a per-token Gemini cache, so Save reuses the exact
    image the preview produced instead of paying for a second API call."""
    if (src.get("engine") or "classic").lower() == "gemini":
        signature = _gemini_signature(src)
        cached = _GEMINI_CACHE.get(token)
        if cached and cached[0] == signature:
            return cached[1]
        png = _render_png(data, original, src, api_key)
        _remember(_GEMINI_CACHE, token, (signature, png))
        return png
    return _render_png(data, original, src, api_key)


def _clean_subpath(raw: str | None) -> str:
    """Sanitize a user subdirectory into a safe relative posix path ('' if none)."""
    parts = [secure_filename(p) for p in (raw or "").replace("\\", "/").split("/")]
    return "/".join(p for p in parts if p)


def _unique_path(directory: Path, filename: str) -> Path:
    """Return a non-clobbering path in ``directory`` for ``filename`` (.png)."""
    dest = directory / filename
    stem, n = dest.stem, 1
    while dest.exists():
        dest = directory / f"{stem}_{n}.png"
        n += 1
    return dest


@app.get("/charcoal")
def charcoal_page():
    return render_template("charcoal_upload.html")


_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".webp", ".gif", ".bmp",
    ".tif", ".tiff", ".heic", ".heif", ".avif", ".jp2", ".j2k", ".jpf", ".ico",
}


def _collect_images(files) -> list[tuple[bytes, str]]:
    """Return [(bytes, basename)] for every valid image upload, skipping the rest.

    Accepts both multi-file and folder (webkitdirectory) selections; non-image and
    unreadable files are silently ignored so a whole folder can be dropped in.
    """
    items: list[tuple[bytes, str]] = []
    for f in files:
        if not f or not f.filename:
            continue
        name = os.path.basename(f.filename.replace("\\", "/"))
        if os.path.splitext(name)[1].lower() not in _IMAGE_EXTS:
            continue
        data = f.read()
        if not data:
            continue
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
        except Exception:
            continue
        items.append((data, name))
    return items


def _session_key(token: str, src) -> str | None:
    """Persist an API key submitted via the form and return the session key."""
    submitted = (src.get("api_key") or "").strip()
    if submitted:
        _remember(_KEYS, token, submitted)
    return _KEYS.get(token) or None


@app.post("/charcoal/edit")
def charcoal_edit():
    items = _collect_images(request.files.getlist("photo"))
    if not items:
        abort(400, "Please upload at least one image.")
    token = uuid.uuid4().hex
    _remember(_UPLOADS, token, items)
    session_key = _session_key(token, request.form)
    first_name = items[0][1]
    stem = os.path.splitext(first_name)[0]
    return render_template(
        "charcoal_edit.html",
        token=token,
        original=first_name,
        count=len(items),
        names=[n for _, n in items],
        default_name=f"{stem}char",
        gemini_available=gem.available() or bool(session_key),
        has_env_key=gem.available(),
        styles=gem.STYLES,
        default_style=gem.DEFAULT_STYLE,
    )


@app.get("/charcoal/preview/<token>")
def charcoal_preview(token: str):
    entry = _UPLOADS.get(token)
    if not entry:
        abort(404)
    data, original = entry[0]
    try:
        png = _charcoal_png(token, data, original, request.args, _KEYS.get(token) or None)
    except gem.GeminiError as exc:
        return str(exc), 502, {"Content-Type": "text/plain; charset=utf-8"}
    return send_file(io.BytesIO(png), mimetype="image/png")


@app.post("/charcoal/save")
def charcoal_save():
    up_token = request.form.get("token", "")
    entry = _UPLOADS.get(up_token)
    if not entry:
        abort(404, "This editing session expired. Please re-upload the photo.")
    data, original = entry[0]

    key = _session_key(up_token, request.form)
    try:
        png = _charcoal_png(up_token, data, original, request.form, key)
    except gem.GeminiError as exc:
        abort(502, str(exc))

    requested = (request.form.get("filename") or "").strip() or f"{os.path.splitext(original)[0]}char"
    safe = secure_filename(requested) or "charcoal"
    if not safe.lower().endswith(".png"):
        safe += ".png"
    subpath = _clean_subpath(request.form.get("subdir"))

    token = uuid.uuid4().hex
    if LOCAL_SAVE_DIR is not None:
        # Local mode: write straight to disk on the user's own machine.
        dest_dir = LOCAL_SAVE_DIR / subpath if subpath else LOCAL_SAVE_DIR
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = _unique_path(dest_dir, safe)
        dest.write_bytes(png)
        _remember(_OUTPUTS, token, ("path", str(dest), dest.name))
        return render_template(
            "charcoal_saved.html", mode="local", token=token, original=original,
            filename=dest.name, saved_path=str(dest),
            rel_path=os.path.relpath(dest, LOCAL_SAVE_DIR), base=str(LOCAL_SAVE_DIR), subpath=subpath,
        )

    # Download mode: deliver to the user's computer. Preserve a subdirectory by
    # shipping a .zip that contains <subpath>/<file>.png.
    if subpath:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{subpath}/{safe}", png)
        payload, dl_name = buf.getvalue(), f"{safe[:-4]}.zip"
    else:
        payload, dl_name = png, safe
    _remember(_OUTPUTS, token, ("bytes", payload, dl_name))
    return render_template(
        "charcoal_saved.html", mode="download", token=token, original=original,
        filename=dl_name, saved_path=None,
        rel_path=(f"{subpath}/{safe}" if subpath else safe), base=None, subpath=subpath,
    )


_BATCH_FOLDER = "charcoal"


def _unique_name(name: str, used: set[str]) -> str:
    """Return ``name`` (or a ``_n`` variant) not already in ``used``; records it."""
    if name not in used:
        used.add(name)
        return name
    stem, ext = os.path.splitext(name)
    n = 1
    while f"{stem}_{n}{ext}" in used:
        n += 1
    unique = f"{stem}_{n}{ext}"
    used.add(unique)
    return unique


@app.post("/charcoal/batch")
def charcoal_batch():
    """Render every uploaded image with the chosen protocol and collect the PNGs
    together in a ``charcoal/`` folder (on disk in local mode, else a .zip)."""
    up_token = request.form.get("token", "")
    entry = _UPLOADS.get(up_token)
    if not entry:
        abort(404, "This editing session expired. Please re-upload the photos.")
    key = _session_key(up_token, request.form)

    rendered: list[tuple[str, bytes]] = []
    errors: list[tuple[str, str]] = []
    used: set[str] = set()
    for data, original in entry:
        stem = secure_filename(os.path.splitext(original)[0]) or "image"
        try:
            png = _render_png(data, original, request.form, key)
        except gem.GeminiError as exc:
            errors.append((original, str(exc)))
            continue
        rendered.append((_unique_name(f"{stem}char.png", used), png))

    if not rendered:
        detail = "; ".join(f"{n}: {e}" for n, e in errors) or "no valid images."
        abort(502, f"Every image failed to render. {detail}")

    token = uuid.uuid4().hex
    if LOCAL_SAVE_DIR is not None:
        dest_dir = LOCAL_SAVE_DIR / _BATCH_FOLDER
        dest_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for name, png in rendered:
            dest = _unique_path(dest_dir, name)
            dest.write_bytes(png)
            saved.append(dest.name)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, png in rendered:
                zf.writestr(f"{_BATCH_FOLDER}/{name}", png)
        _remember(_OUTPUTS, token, ("bytes", buf.getvalue(), f"{_BATCH_FOLDER}.zip"))
        return render_template(
            "charcoal_batch.html", mode="local", token=token,
            count=len(rendered), names=saved, errors=errors,
            folder=str(dest_dir), base=str(LOCAL_SAVE_DIR),
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, png in rendered:
            zf.writestr(f"{_BATCH_FOLDER}/{name}", png)
    _remember(_OUTPUTS, token, ("bytes", buf.getvalue(), f"{_BATCH_FOLDER}.zip"))
    return render_template(
        "charcoal_batch.html", mode="download", token=token,
        count=len(rendered), names=[n for n, _ in rendered], errors=errors,
        folder=f"{_BATCH_FOLDER}/", base=None,
    )


@app.post("/charcoal/key")
def charcoal_key():
    """Store or clear the Gemini API key for a session (used by the live preview)."""
    token = request.form.get("token", "")
    if token not in _UPLOADS:
        abort(404)
    key = (request.form.get("api_key") or "").strip()
    if key:
        _remember(_KEYS, token, key)
    else:
        _KEYS.pop(token, None)
    return "", 204


@app.get("/manual")
def manual():
    path = Path(__file__).with_name("MANUAL.md")
    if not path.exists():
        abort(404, "Manual not found.")
    return send_file(
        path, as_attachment=True, download_name="MANUAL.md", mimetype="text/markdown"
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "on")
    app.run(host="0.0.0.0", port=port, debug=debug)
