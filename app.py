"""Flask front-end for per-chapter DOCX watermarking.

Upload one .docx plus one or more watermark PNGs. Each ``Heading 1`` chapter
whose name appears in a PNG's file name gets that PNG pinned, semi-transparent,
to the bottom-left of its first page.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
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
try:
    import pymupdf  # PDF rasterization for watermark inputs
except Exception:
    pymupdf = None

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


def _rasterize_watermark(data: bytes, filename: str) -> bytes:
    """Return RGBA PNG bytes for a watermark upload.

    PDFs are rasterized (first page, transparency preserved for vector logos);
    other image types are opened and normalized to RGBA PNG.
    """
    if filename.lower().endswith(".pdf"):
        if pymupdf is None:
            raise RuntimeError("PDF support unavailable (pymupdf not installed).")
        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(3, 3), alpha=True)
            return pix.tobytes("png")
        finally:
            doc.close()
    with Image.open(io.BytesIO(data)) as im:
        buf = io.BytesIO()
        im.convert("RGBA").save(buf, format="PNG")
        return buf.getvalue()


def _resolve_soffice() -> str | None:
    """Find LibreOffice across platforms, incl. default install paths not on PATH."""
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    import sys
    if sys.platform == "darwin":
        candidates = ["/Applications/LibreOffice.app/Contents/MacOS/soffice"]
    elif os.name == "nt":
        candidates = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
    else:
        candidates = ["/usr/bin/soffice", "/usr/local/bin/soffice",
                      "/opt/libreoffice/program/soffice"]
    return next((c for c in candidates if os.path.exists(c)), None)


_SOFFICE = _resolve_soffice()


def _docx_to_pdf_one(path: str, outdir: str, profile: str, timeout: int = 300) -> str:
    """Convert a single .docx to PDF via headless LibreOffice; return the PDF path."""
    if not _SOFFICE:
        raise RuntimeError("PDF export needs LibreOffice (soffice) installed on the server.")
    subprocess.run(
        [_SOFFICE, "--headless", "--norestore",
         f"-env:UserInstallation=file://{profile}",
         "--convert-to", "pdf", "--outdir", outdir, path],
        check=True, capture_output=True, timeout=timeout,
    )
    pdf = os.path.join(outdir, os.path.splitext(os.path.basename(path))[0] + ".pdf")
    if not os.path.exists(pdf):
        raise RuntimeError("LibreOffice produced no PDF")
    return pdf


def _stamp_diagonal(pdf_bytes: bytes, text: str, opacity: float) -> bytes:
    """Overlay a single diagonal, semi-transparent grey text watermark on every page."""
    if pymupdf is None:
        raise RuntimeError("Diagonal watermark needs pymupdf installed.")
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page in doc:
            r = page.rect
            diag = (r.width ** 2 + r.height ** 2) ** 0.5
            unit = pymupdf.get_text_length(text, fontname="helv", fontsize=1) or 1.0
            fontsize = max(12.0, min(400.0, diag * 0.8 / unit))
            width = pymupdf.get_text_length(text, fontname="helv", fontsize=fontsize)
            pivot = pymupdf.Point(r.width / 2, r.height / 2)
            origin = pymupdf.Point(pivot.x - width / 2, pivot.y + fontsize * 0.35)
            page.insert_text(
                origin, text, fontname="helv", fontsize=fontsize,
                color=(0.5, 0.5, 0.5), fill_opacity=opacity,
                morph=(pivot, pymupdf.Matrix(1, 0, 0, 1, 0, 0).prerotate(45)),
            )
        return doc.tobytes()
    finally:
        doc.close()


@app.post("/process")
def process():
    docx_files = [
        f for f in request.files.getlist("docx")
        if f and f.filename and f.filename.lower().endswith(".docx")
    ]
    if not docx_files:
        abort(400, "Please upload at least one .docx file (or a folder of them).")

    accepted = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".pdf")
    marks = [
        f for f in request.files.getlist("watermarks")
        if f and f.filename and f.filename.lower().endswith(accepted)
    ]
    if not marks:
        abort(400, "Please upload at least one watermark (.png, .jpg, .tif or .pdf).")

    opacity = _pct(request.form.get("opacity"), 50)
    width_pct = _pct(request.form.get("width_pct"), 25)
    to_pdf = str(request.form.get("to_pdf", "")).lower() in ("1", "true", "on", "yes")
    diagonal = (request.form.get("diagonal_text") or "").strip()
    diagonal_opacity = _pct(request.form.get("diagonal_opacity"), 25)
    if diagonal:
        to_pdf = True  # the diagonal mark is stamped onto the PDF output

    workdir = tempfile.mkdtemp(prefix="wm_")

    # Rasterize the watermark set once; it is shared across every document.
    pairs: list[tuple[str, str]] = []
    for i, mark in enumerate(marks):
        original = os.path.basename(mark.filename.replace("\\", "/"))
        try:
            png = _rasterize_watermark(mark.read(), original)
        except Exception:
            continue
        stem = secure_filename(os.path.splitext(original)[0]) or f"watermark_{i}"
        path = os.path.join(workdir, f"wm_{i}_{stem}.png")
        with open(path, "wb") as fh:
            fh.write(png)
        pairs.append((path, original))
    if not pairs:
        abort(400, "None of the uploaded watermarks could be read.")

    # Watermark each document with the shared set.
    docs = []
    for j, up in enumerate(docx_files):
        base = os.path.basename(up.filename.replace("\\", "/"))
        docx_name = secure_filename(base) or f"document_{j}.docx"
        in_path = os.path.join(workdir, f"in_{j}_{docx_name}")
        up.save(in_path)
        out_name = f"{os.path.splitext(docx_name)[0]}_watermarked.docx"
        out_path = os.path.join(workdir, f"out_{j}_{out_name}")
        report = wm.process(in_path, pairs, out_path, opacity=opacity, width_pct=width_pct, workdir=workdir)
        with open(out_path, "rb") as fh:
            data = fh.read()
        docs.append({"src": base, "name": out_name, "out_path": out_path,
                     "data": data, "report": report})

    # Optionally convert each watermarked document to PDF and stamp a diagonal mark.
    # Done per file so one slow/failed conversion can't lose the whole batch; a
    # failed file simply keeps its .docx.
    conv_errors: list[tuple[str, str]] = []
    if to_pdf:
        pdfdir = os.path.join(workdir, "pdf")
        os.makedirs(pdfdir, exist_ok=True)
        profile = os.path.join(pdfdir, "_lo_profile")
        for d in docs:
            try:
                pdf_path = _docx_to_pdf_one(d["out_path"], pdfdir, profile)
                with open(pdf_path, "rb") as fh:
                    pdf = fh.read()
                if diagonal:
                    pdf = _stamp_diagonal(pdf, diagonal, diagonal_opacity)
                d["data"] = pdf
                d["name"] = f"{os.path.splitext(d['name'])[0]}.pdf"
            except Exception as exc:
                conv_errors.append((d["src"], str(exc)))  # keep the .docx instead

    # Single document -> the classic per-chapter report page.
    if len(docs) == 1:
        d = docs[0]
        token = uuid.uuid4().hex
        _remember(_OUTPUTS, token, ("bytes", d["data"], d["name"]))
        return render_template(
            "result.html", report=d["report"], token=token, download_name=d["name"],
            opacity_pct=round(opacity * 100), width_pct=round(width_pct * 100),
            pdf_error=(conv_errors[0][1] if conv_errors else None),
        )

    # Multiple documents -> collect all into a "watermarked" folder / zip.
    used: set[str] = set()
    for d in docs:
        d["name"] = _unique_name(d["name"], used)

    token = uuid.uuid4().hex
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in docs:
            zf.writestr(f"watermarked/{d['name']}", d["data"])
    _remember(_OUTPUTS, token, ("bytes", buf.getvalue(), "watermarked.zip"))

    saved_dir = None
    if LOCAL_SAVE_DIR is not None:
        dest_dir = LOCAL_SAVE_DIR / "watermarked"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for d in docs:
            _unique_path(dest_dir, d["name"]).write_bytes(d["data"])
        saved_dir = str(dest_dir)

    files = [
        {"src": d["src"], "name": d["name"],
         "watermarked": d["report"].watermarked, "total": d["report"].total_chapters}
        for d in docs
    ]
    return render_template(
        "result_batch.html",
        token=token, files=files, count=len(docs),
        total_watermarked=sum(d["report"].watermarked for d in docs),
        opacity_pct=round(opacity * 100), width_pct=round(width_pct * 100),
        saved_dir=saved_dir, base=(str(LOCAL_SAVE_DIR) if LOCAL_SAVE_DIR else None),
        conv_errors=conv_errors,
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
    """Return a non-clobbering path in ``directory`` for ``filename``."""
    dest = directory / filename
    stem, ext, n = dest.stem, dest.suffix, 1
    while dest.exists():
        dest = directory / f"{stem}_{n}{ext}"
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


def _find_free_port(start: int, host: str = "127.0.0.1", tries: int = 50) -> int:
    """Return the first free TCP port at or after ``start`` (handles macOS :5000)."""
    import socket
    for candidate in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError:
                continue
    return start


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "on")
    port = _find_free_port(int(os.environ.get("PORT", "5000")))
    url = f"http://127.0.0.1:{port}"
    mode = f"saving to {LOCAL_SAVE_DIR}" if LOCAL_SAVE_DIR else "download mode"
    print(f" * Chapter Watermarker -> {url}  ({mode})")
    # Open the browser once the server is up (skip in debug to avoid the reloader
    # opening it twice; set NO_BROWSER=1 to disable).
    if not debug and os.environ.get("NO_BROWSER", "").lower() not in ("1", "true", "on"):
        import threading
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=port, debug=debug)
