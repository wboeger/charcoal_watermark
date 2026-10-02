"""Per-chapter DOCX figure insertion.

A "chapter" is a ``Heading 1`` paragraph. A figure image is bound to a chapter
when they share a significant word (token match), so numbered headings like
"1 Chordata" still match "Chordata.png". The figure is inserted, fully opaque,
floating to the right of the chapter title with tight text wrap, on the first
page where that chapter name occurs.
"""

from __future__ import annotations

import os
import re
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
    '<wp:anchor distT="0" distB="0" distL="114300" distR="114300" simplePos="0"'
    ' relativeHeight="251658240" behindDoc="0" locked="0" layoutInCell="1"'
    ' allowOverlap="1">'
    '<wp:simplePos x="0" y="0"/>'
    '<wp:positionH relativeFrom="margin"><wp:align>right</wp:align></wp:positionH>'
    '<wp:positionV relativeFrom="paragraph"><wp:posOffset>0</wp:posOffset></wp:positionV>'
    '<wp:extent cx="{cx}" cy="{cy}"/>'
    '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
    '<wp:wrapTight wrapText="left">'
    '<wp:wrapPolygon edited="0">'
    '<wp:start x="0" y="0"/>'
    '<wp:lineTo x="0" y="21600"/>'
    '<wp:lineTo x="21600" y="21600"/>'
    '<wp:lineTo x="21600" y="0"/>'
    '<wp:lineTo x="0" y="0"/>'
    '</wp:wrapPolygon>'
    '</wp:wrapTight>'
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


_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)  # letter-only runs, length >= 2
_STOP_WORDS = {"final", "fig", "figure", "img", "image", "vol", "volume", "the", "and"}


def _tokens(text: str) -> set[str]:
    """Significant lowercase word tokens (>= 4 letters, no digits/stopwords)."""
    return {
        w for w in _WORD_RE.findall((text or "").lower())
        if len(w) >= 4 and w not in _STOP_WORDS
    }


def _match_pngs(chapter: str, pngs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return every (path, name) sharing a significant word with ``chapter``.

    Token overlap (not substring) so a numbered heading like "1 Chordata" matches
    "Chordata.png", and "Coleoptera I" matches "coleoptera.png". Matching uses the
    original file name (accents/spaces intact), extension stripped.
    """
    wanted = _tokens(chapter)
    if not wanted:
        return []
    out = []
    for path, name in pngs:
        stem = os.path.splitext(name)[0]
        if wanted & _tokens(stem):
            out.append((path, name))
    return out


def _first_section_geometry(document):
    section = document.sections[0]
    page_w = section.page_width or _DEFAULT_PAGE_W
    page_h = section.page_height or _DEFAULT_PAGE_H
    left = section.left_margin or _DEFAULT_MARGIN
    bottom = section.bottom_margin or _DEFAULT_MARGIN
    top = section.top_margin or _DEFAULT_MARGIN
    return int(page_w), int(page_h), int(left), int(bottom), int(top)


# A Word (VML) diagonal text watermark placed in a header so it repeats on every
# page, behind the text, semi-transparent — the native "washout" watermark.
_WATERMARK_PICT = (
    '<w:p {ns}><w:r><w:rPr><w:noProof/></w:rPr><w:pict>'
    '<v:shapetype id="_x0000_t136" coordsize="21600,21600" o:spt="136" adj="10800"'
    ' path="m@7,l@8,m@5,21600l@6,21600e">'
    '<v:formulas>'
    '<v:f eqn="sum #0 0 10800"/><v:f eqn="prod #0 2 1"/><v:f eqn="sum 21600 0 @1"/>'
    '<v:f eqn="sum 0 0 @2"/><v:f eqn="sum 21600 0 @3"/><v:f eqn="if @0 @3 0"/>'
    '<v:f eqn="if @0 21600 @1"/><v:f eqn="if @0 0 @2"/><v:f eqn="if @0 @4 21600"/>'
    '<v:f eqn="mid @5 @6"/><v:f eqn="mid @8 @5"/><v:f eqn="mid @7 @8"/>'
    '<v:f eqn="mid @6 @7"/><v:f eqn="sum @6 0 @5"/>'
    '</v:formulas>'
    '<v:path textpathok="t" o:connecttype="custom"'
    ' o:connectlocs="@9,0;@10,10800;@11,21600;@12,10800" o:connectangles="270,180,90,0"/>'
    '<v:textpath on="t" fitshape="t"/>'
    '</v:shapetype>'
    '<v:shape id="PowerPlusWaterMarkObject" type="#_x0000_t136"'
    ' style="position:absolute;margin-left:0;margin-top:0;width:468pt;height:234pt;'
    'rotation:315;z-index:-251658240;mso-position-horizontal:center;'
    'mso-position-horizontal-relative:margin;mso-position-vertical:center;'
    'mso-position-vertical-relative:margin" fillcolor="silver" stroked="f">'
    '<v:fill opacity="{opacity}"/>'
    '<v:textpath style="font-family:&quot;Calibri&quot;;font-size:1pt" string="{text}"/>'
    '</v:shape>'
    '</w:pict></w:r></w:p>'
)


_WATERMARK_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:o="urn:schemas-microsoft-com:office:office"'
)


def _add_text_watermark(document, text: str, opacity: float) -> None:
    """Add a diagonal, semi-transparent text watermark to every page (all sections)."""
    xml = _WATERMARK_PICT.format(
        ns=_WATERMARK_NS,
        opacity=f"{max(0.0, min(1.0, opacity)):.2f}",
        text=escape(text),
    )
    for section in document.sections:
        header = section.header
        header.is_linked_to_previous = False
        header._element.append(parse_xml(xml))


def process(
    docx_path: str,
    pngs: list[tuple[str, str]],
    output_path: str,
    width_pct: float = 0.30,
    workdir: str | None = None,
    watermark_text: str = "",
    watermark_opacity: float = 0.25,
) -> Report:
    """Insert matched figures beside chapter headings and save to ``output_path``.

    ``pngs`` is a list of ``(disk_path, original_name)``; matching uses the
    original name. ``width_pct`` is a fraction in (0, 1]. If ``watermark_text`` is
    given, a diagonal semi-transparent text watermark is added to every page.
    """
    width_pct = max(0.01, min(1.0, width_pct))
    workdir = workdir or os.path.dirname(output_path) or "."

    document = Document(docx_path)
    page_w, page_h, left_margin, bottom_margin, top_margin = _first_section_geometry(document)
    usable_h = max(1, page_h - top_margin - bottom_margin)

    report = Report()
    used_pngs: set[str] = set()
    # Cache of png_path -> (pixel_w, pixel_h). The image is embedded once
    # (get_or_add_image dedupes identical files).
    processed: dict[str, tuple[int, int]] = {}
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
            with PILImage.open(png_path) as im:
                processed[png_path] = im.size
        px_w, px_h = processed[png_path]

        cx = int(page_w * width_pct)              # ~30% of page width
        cy = int(cx * px_h / px_w)
        if cy > usable_h:                          # keep the figure within the body
            cy = usable_h
            cx = int(cy * px_w / px_h)

        # Embed the image at full opacity (no alpha scaling); a transparent PNG
        # keeps its removed background, otherwise it shows on white.
        rid, _image = document.part.get_or_add_image(png_path)
        pic_id += 1
        run_xml = _ANCHOR_XML.format(
            ns=nsdecls("w", "wp", "a", "pic", "r"),
            cx=cx, cy=cy, pid=pic_id,
            name=escape(f"Figure {chapter}"), rid=rid,
        )
        paragraph._p.append(parse_xml(run_xml))

        used_pngs.add(png_path)
        report.chapters.append(ChapterResult(chapter=chapter, status="watermarked", png=png_name))
        report.watermarked += 1

    report.unused_pngs = [name for path, name in pngs if path not in used_pngs]
    if watermark_text.strip():
        _add_text_watermark(document, watermark_text.strip(), watermark_opacity)
    document.save(output_path)
    return report
