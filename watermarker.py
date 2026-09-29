"""Per-chapter DOCX watermarking.

A "chapter" is a ``Heading 1`` paragraph; its name is the heading text. A
watermark PNG is bound to a chapter when the PNG file name contains the chapter
name (case-insensitive substring). The watermark is placed as a semi-transparent
floating image pinned to the bottom-left corner of the chapter's first page,
behind the text.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from docx import Document
from docx.shared import Emu, Inches
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from PIL import Image as PILImage

# Fallbacks for sections that don't declare page geometry (rare).
_DEFAULT_PAGE_W = Inches(8.5)
_DEFAULT_PAGE_H = Inches(11)
_DEFAULT_MARGIN = Inches(1)

_ANCHOR_XML = (
    '<w:r {ns}>'
    '<w:drawing>'
    '<wp:anchor distT="0" distB="0" distL="0" distR="0" simplePos="0"'
    ' relativeHeight="251658240" behindDoc="1" locked="0" layoutInCell="1"'
    ' allowOverlap="1">'
    '<wp:simplePos x="0" y="0"/>'
    '<wp:positionH relativeFrom="page"><wp:posOffset>{hoff}</wp:posOffset></wp:positionH>'
    '<wp:positionV relativeFrom="page"><wp:posOffset>{voff}</wp:posOffset></wp:positionV>'
    '<wp:extent cx="{cx}" cy="{cy}"/>'
    '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
    '<wp:wrapNone/>'
    '<wp:docPr id="{pid}" name="{name}"/>'
    '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
    '<a:graphic>'
    '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
    '<pic:pic>'
    '<pic:nvPicPr><pic:cNvPr id="{pid}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
    '<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
    '<pic:spPr>'
    '<a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
    '</pic:spPr>'
    '</pic:pic>'
    '</a:graphicData>'
    '</a:graphic>'
    '</wp:anchor>'
    '</w:drawing>'
    '</w:r>'
)


@dataclass
class ChapterResult:
    chapter: str
    status: str  # "watermarked" | "no_match" | "ambiguous" | "duplicate"
    png: str | None = None
    candidates: list[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.status == "watermarked"


@dataclass
class Report:
    chapters: list[ChapterResult] = field(default_factory=list)
    unused_pngs: list[str] = field(default_factory=list)
    watermarked: int = 0

    @property
    def total_chapters(self) -> int:
        return len(self.chapters)


def _is_heading1(paragraph) -> bool:
    style = paragraph.style
    if style is None:
        return False
    name = (style.name or "").strip().lower()
    style_id = (getattr(style, "style_id", "") or "").strip().lower()
    return name == "heading 1" or style_id == "heading1"


def _make_translucent(src: str, opacity: float, dst: str) -> tuple[int, int]:
    """Write a copy of ``src`` with its alpha scaled by ``opacity`` to ``dst``.

    Returns the pixel (width, height) of the image.
    """
    with PILImage.open(src) as im:
        im = im.convert("RGBA")
        alpha = im.getchannel("A").point(lambda v: int(v * opacity))
        im.putalpha(alpha)
        im.save(dst, format="PNG")
        return im.width, im.height


def _match_pngs(chapter: str, pngs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return every (path, name) whose *original* file name contains ``chapter``.

    Matching is case-insensitive substring on the uploaded file name (never a
    sanitized on-disk name), so accented/spaced chapter titles still match.
    """
    needle = chapter.strip().lower()
    if not needle:
        return []
    return [(path, name) for path, name in pngs if needle in name.lower()]


def _first_section_geometry(document):
    section = document.sections[0]
    page_w = section.page_width or _DEFAULT_PAGE_W
    page_h = section.page_height or _DEFAULT_PAGE_H
    left = section.left_margin or _DEFAULT_MARGIN
    bottom = section.bottom_margin or _DEFAULT_MARGIN
    top = section.top_margin or _DEFAULT_MARGIN
    return int(page_w), int(page_h), int(left), int(bottom), int(top)


def process(
    docx_path: str,
    pngs: list[tuple[str, str]],
    output_path: str,
    opacity: float = 0.5,
    width_pct: float = 0.25,
    workdir: str | None = None,
) -> Report:
    """Watermark matched chapters of ``docx_path`` and save to ``output_path``.

    ``pngs`` is a list of ``(disk_path, original_name)``; matching uses the
    original name. ``opacity`` and ``width_pct`` are fractions in (0, 1].
    """
    opacity = max(0.0, min(1.0, opacity))
    width_pct = max(0.01, min(1.0, width_pct))
    workdir = workdir or os.path.dirname(output_path) or "."

    document = Document(docx_path)
    page_w, page_h, left_margin, bottom_margin, top_margin = _first_section_geometry(document)
    usable_h = max(1, page_h - top_margin - bottom_margin)

    report = Report()
    used_pngs: set[str] = set()
    # Cache of (src_png, opacity) -> preprocessed translucent PNG path so a
    # watermark reused across chapters is embedded once (get_or_add_image dedupes).
    processed: dict[str, tuple[str, int, int]] = {}
    pic_id = 1000

    for paragraph in document.paragraphs:
        if not _is_heading1(paragraph):
            continue
        chapter = paragraph.text.strip()
        matches = _match_pngs(chapter, pngs)
        if not matches:
            report.chapters.append(ChapterResult(chapter=chapter, status="no_match"))
            continue

        png_path, png_name = matches[0]
        if png_path in used_pngs:
            # Name already placed earlier — only the first page where it occurs
            # gets the figure; later occurrences are reported, not re-stamped.
            report.chapters.append(
                ChapterResult(chapter=chapter, status="duplicate", png=png_name)
            )
            continue
        if png_path not in processed:
            dst = os.path.join(workdir, f"_wm_{len(processed)}.png")
            px_w, px_h = _make_translucent(png_path, opacity, dst)
            processed[png_path] = (dst, px_w, px_h)
        translucent_path, px_w, px_h = processed[png_path]

        cx = int(page_w * width_pct)
        cy = int(cx * px_h / px_w)
        if cy > usable_h:  # keep the image inside the page body
            cy = usable_h
            cx = int(cy * px_w / px_h)

        h_off = left_margin
        v_off = max(0, page_h - bottom_margin - cy)

        rid, _image = document.part.get_or_add_image(translucent_path)
        pic_id += 1
        run_xml = _ANCHOR_XML.format(
            ns=nsdecls("w", "wp", "a", "pic", "r"),
            hoff=h_off,
            voff=v_off,
            cx=cx,
            cy=cy,
            pid=pic_id,
            name=escape(f"Watermark {chapter}"),
            rid=rid,
        )
        paragraph._p.append(parse_xml(run_xml))

        used_pngs.add(png_path)
        report.chapters.append(ChapterResult(chapter=chapter, status="watermarked", png=png_name))
        report.watermarked += 1

    report.unused_pngs = [name for path, name in pngs if path not in used_pngs]
    document.save(output_path)
    return report
