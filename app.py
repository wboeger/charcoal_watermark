"""Flask front-end for per-chapter DOCX figure insertion.

Upload one or more ``.docx`` files (or a whole folder) plus figure images. Each
``Heading 1`` chapter gets the image whose file name shares its name inserted
beside the title (tight wrap, opaque). Optionally add a diagonal, semi-transparent
text watermark on every page. Output is always ``.docx``.
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
from PIL import Image

# Trusted local/self-hosted tool processing the user's own files, so lift
# Pillow's decompression-bomb guard (large scans/figures are legitimate).
Image.MAX_IMAGE_PIXELS = None

try:
    import pymupdf  # rasterizes PDF figure inputs
except Exception:
    pymupdf = None

app = Flask(__name__)
# Max upload size per request. Folder uploads of many documents are large, so
# this defaults high and is overridable with MAX_UPLOAD_MB.
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "1024"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


@app.errorhandler(413)
def _too_large(_):
    return (
        f"Upload too large — the limit is {MAX_UPLOAD_MB} MB per upload. "
        "Select fewer or smaller files, or raise MAX_UPLOAD_MB on the server.",
        413,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


# Result store: token -> (kind, ref, name). kind == "path" (temp file) or
# "bytes" (in-memory payload). Bounded so a long-lived instance can't grow.
_OUTPUTS: dict[str, tuple[str, object, str]] = {}
_STORE_CAP = 64

# LOCAL_SAVE_DIR set   -> "local" mode: results are also written to disk under
#                         <dir>/processadas on this machine.
# LOCAL_SAVE_DIR unset -> "download" mode (default, e.g. Railway).
_local_dir = os.environ.get("LOCAL_SAVE_DIR")
LOCAL_SAVE_DIR = Path(_local_dir).resolve() if _local_dir else None
_OUTPUT_FOLDER = "processadas"


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


def _unique_path(directory: Path, filename: str) -> Path:
    """Return a non-clobbering path in ``directory`` for ``filename``."""
    dest = directory / filename
    stem, ext, n = dest.stem, dest.suffix, 1
    while dest.exists():
        dest = directory / f"{stem}_{n}{ext}"
        n += 1
    return dest


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


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return "ok", 200


def _rasterize_watermark(data: bytes, filename: str) -> bytes:
    """Return RGBA PNG bytes for a figure upload.

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
        abort(400, "Please upload at least one figure (.png, .jpg, .tif or .pdf).")

    width_pct = _pct(request.form.get("width_pct"), 30)
    watermark_text = (request.form.get("diagonal_text") or "").strip()
    watermark_opacity = _pct(request.form.get("diagonal_opacity"), 25)

    workdir = tempfile.mkdtemp(prefix="wm_")

    # Rasterize the figure set once; it is shared across every document.
    pairs: list[tuple[str, str]] = []
    for i, mark in enumerate(marks):
        original = os.path.basename(mark.filename.replace("\\", "/"))
        try:
            png = _rasterize_watermark(mark.read(), original)
        except Exception:
            continue
        stem = secure_filename(os.path.splitext(original)[0]) or f"figure_{i}"
        path = os.path.join(workdir, f"wm_{i}_{stem}.png")
        with open(path, "wb") as fh:
            fh.write(png)
        pairs.append((path, original))
    if not pairs:
        abort(400, "None of the uploaded figures could be read.")

    # Insert the figure beside the matching heading in each document.
    docs = []
    for j, up in enumerate(docx_files):
        base = os.path.basename(up.filename.replace("\\", "/"))
        docx_name = secure_filename(base) or f"document_{j}.docx"
        in_path = os.path.join(workdir, f"in_{j}_{docx_name}")
        up.save(in_path)
        out_name = f"{os.path.splitext(docx_name)[0]}_watermarked.docx"
        out_path = os.path.join(workdir, f"out_{j}_{out_name}")
        report = wm.process(in_path, pairs, out_path, width_pct=width_pct,
                            workdir=workdir, watermark_text=watermark_text,
                            watermark_opacity=watermark_opacity)
        with open(out_path, "rb") as fh:
            data = fh.read()
        docs.append({"src": base, "name": out_name, "out_path": out_path,
                     "data": data, "report": report})

    # Single document -> the per-chapter report page.
    if len(docs) == 1:
        d = docs[0]
        saved_dir = None
        if LOCAL_SAVE_DIR is not None:
            dest_dir = LOCAL_SAVE_DIR / _OUTPUT_FOLDER
            dest_dir.mkdir(parents=True, exist_ok=True)
            saved_path = _unique_path(dest_dir, d["name"])
            saved_path.write_bytes(d["data"])
            d["name"] = saved_path.name
            saved_dir = str(dest_dir)
        token = uuid.uuid4().hex
        if saved_dir:
            _remember(_OUTPUTS, token, ("path", str(Path(saved_dir) / d["name"]), d["name"]))
        else:
            _remember(_OUTPUTS, token, ("bytes", d["data"], d["name"]))
        return render_template(
            "result.html", report=d["report"], token=token, download_name=d["name"],
            width_pct=round(width_pct * 100), saved_dir=saved_dir,
        )

    # Multiple documents -> collect all into a "processadas" folder / zip.
    used: set[str] = set()
    for d in docs:
        d["name"] = _unique_name(d["name"], used)

    token = uuid.uuid4().hex
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in docs:
            zf.writestr(f"{_OUTPUT_FOLDER}/{d['name']}", d["data"])
    _remember(_OUTPUTS, token, ("bytes", buf.getvalue(), f"{_OUTPUT_FOLDER}.zip"))

    saved_dir = None
    if LOCAL_SAVE_DIR is not None:
        dest_dir = LOCAL_SAVE_DIR / _OUTPUT_FOLDER
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
        width_pct=round(width_pct * 100),
        saved_dir=saved_dir, base=(str(LOCAL_SAVE_DIR) if LOCAL_SAVE_DIR else None),
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


@app.get("/manual")
def manual():
    pdf = Path(__file__).with_name("MANUAL.pdf")
    if pdf.exists():
        return send_file(pdf, mimetype="application/pdf", download_name="MANUAL.pdf")
    md = Path(__file__).with_name("MANUAL.md")
    if md.exists():
        return send_file(md, as_attachment=True, download_name="MANUAL.md",
                         mimetype="text/markdown")
    abort(404, "Manual not found.")


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
    if not debug and os.environ.get("NO_BROWSER", "").lower() not in ("1", "true", "on"):
        import threading
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=port, debug=debug)
