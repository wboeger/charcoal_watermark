#!/usr/bin/env python3
"""Build MANUAL.pdf from MANUAL.md (styled HTML -> PDF via LibreOffice).

Usage:  venv/bin/python build_manual.py
Requires: `markdown` (pip) and LibreOffice (soffice) on the system.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
SRC = HERE / "MANUAL.md"
OUT = HERE / "MANUAL.pdf"

_CSS = """
@page { size: A4; margin: 2cm 1.8cm; }
* { font-family: 'Helvetica Neue', Arial, sans-serif; }
body { font-size: 10.5pt; line-height: 1.5; color: #1a1a1a; }
h1 { font-size: 22pt; color: #16304f; border-bottom: 2px solid #2563eb;
     padding-bottom: 4px; margin: 0 0 10px; }
h2 { font-size: 15pt; color: #1d4ed8; margin: 20px 0 6px;
     border-bottom: 1px solid #c7d2fe; padding-bottom: 3px; }
h3 { font-size: 12pt; color: #334155; margin: 14px 0 4px; }
p, li { font-size: 10.5pt; }
code { font-family: 'SFMono-Regular', Consolas, monospace; font-size: 9.5pt;
       background: #eef2ff; padding: 1px 4px; border-radius: 3px; color: #3730a3; }
pre { background: #0f172a; color: #e2e8f0; padding: 10px 12px; border-radius: 6px;
      font-size: 9pt; white-space: pre-wrap; word-wrap: break-word; }
pre code { background: transparent; color: inherit; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9.5pt; }
th, td { border: 1px solid #cbd5e1; padding: 5px 7px; text-align: left;
         vertical-align: top; }
th { background: #e0e7ff; color: #1e293b; }
tr:nth-child(even) td { background: #f8fafc; }
a { color: #2563eb; text-decoration: none; }
blockquote { border-left: 3px solid #93c5fd; margin: 8px 0; padding: 2px 12px;
             color: #475569; background: #f1f5f9; }
hr { border: 0; border-top: 1px solid #e2e8f0; margin: 16px 0; }
"""

_COVER = """
<div style="text-align:center; margin: 120px 0 60px;">
  <div style="font-size:30pt; font-weight:700; color:#16304f;">Chapter Watermarker</div>
  <div style="font-size:14pt; color:#475569; margin-top:10px;">
    Manual do Aplicativo &mdash; Watermark de capítulos &amp; Desenho a carvão</div>
  <div style="font-size:10pt; color:#94a3b8; margin-top:30px;">{date}</div>
</div>
<div style="page-break-after: always;"></div>
"""


def main() -> int:
    if not SRC.exists():
        print(f"ERROR: {SRC} not found", file=sys.stderr)
        return 1

    body = markdown.markdown(
        SRC.read_text(encoding="utf-8"),
        extensions=["tables", "fenced_code", "sane_lists", "toc"],
    )
    from datetime import date
    cover = _COVER.format(date=date.today().isoformat())
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_CSS}</style></head><body>{cover}{body}</body></html>"
    )

    soffice = _resolve_soffice()
    if not soffice:
        print("ERROR: LibreOffice (soffice) not found; cannot build the PDF.", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "MANUAL.html"
        html_path.write_text(html, encoding="utf-8")
        profile = Path(tmp) / "_lo_profile"
        subprocess.run(
            [soffice, "--headless", "--norestore",
             f"-env:UserInstallation=file://{profile}",
             "--convert-to", "pdf", "--outdir", tmp, str(html_path)],
            check=True, capture_output=True, timeout=180,
        )
        pdf = Path(tmp) / "MANUAL.pdf"
        if not pdf.exists():
            print("ERROR: LibreOffice produced no PDF", file=sys.stderr)
            return 3
        OUT.write_bytes(pdf.read_bytes())

    print(f"Wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
    return 0


def _resolve_soffice() -> str | None:
    import shutil
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    if sys.platform == "darwin":
        candidates = ["/Applications/LibreOffice.app/Contents/MacOS/soffice"]
    elif os.name == "nt":
        candidates = [r"C:\Program Files\LibreOffice\program\soffice.exe",
                      r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"]
    else:
        candidates = ["/usr/bin/soffice", "/usr/local/bin/soffice",
                      "/opt/libreoffice/program/soffice"]
    return next((c for c in candidates if os.path.exists(c)), None)


if __name__ == "__main__":
    raise SystemExit(main())
