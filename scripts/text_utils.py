"""
Shared text extraction utilities for the data viz library.

Extracts readable text from data visualisation images so that the static site
can offer full-text search across chart content (axis labels, titles, data
annotations, etc.).

Three extraction strategies are available, tried in order of preference:
  1. SVG XML parsing — fast and accurate when the SVG contains <text> elements
  2. PNG OCR via Tesseract — works for rasterised text
  3. SVG-to-PNG rasterisation + OCR — fallback when the SVG has no text elements
     but does contain visible text rendered as paths

Strategies 2 and 3 depend on tools that may not be installed (Tesseract, and
the cairosvg package).  When one is missing, extraction returns nothing —
which looks exactly like a chart with no words in it.  The status helpers
below (tesseract_status, svg_rasteriser_status, extraction_unavailable_reason)
let callers tell those two cases apart and say so.

Used by:
  - scripts/extract_text.py (batch processing during migration)
  - admin/admin.py (on-the-fly extraction for new uploads)
"""

import html
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytesseract
from PIL import Image

# pytesseract shells out to the `tesseract` binary and, by default, only finds
# it on PATH — which a Finder-launched or LaunchAgent-run process often doesn't
# inherit from a login shell.  Check the usual install locations too, so text
# extraction doesn't quietly come back empty on a machine that has Tesseract.
TESSERACT_PATHS = (
    "/opt/homebrew/bin/tesseract",  # Homebrew on Apple silicon
    "/usr/local/bin/tesseract",     # Homebrew on Intel macOS
    "/opt/local/bin/tesseract",     # MacPorts
    "/usr/bin/tesseract",           # Linux distribution packages
)

_tesseract_cmd = shutil.which("tesseract") or next(
    (p for p in TESSERACT_PATHS if Path(p).exists()), None
)
if _tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = _tesseract_cmd

# Set once Tesseract has been confirmed to run, so the check costs one
# subprocess per process rather than one per image.  Failures are never
# cached: the fix is to install Tesseract, and that shouldn't need a restart
# of a tool that may be running all day as a login item.
_TESSERACT_OK = False

# Fallback for SVGs that a strict XML parser rejects.  Real-world exports
# quite often contain undeclared entities (&nbsp;) or stray markup, which is
# fatal to ElementTree but harmless to us — we only want the words.  Matching
# stops at the first closing text/tspan tag, so nested tspans come out as
# separate lines rather than one run-on string.
SVG_TEXT_RE = re.compile(r"<(?:text|tspan)\b[^>]*>(.*?)</(?:text|tspan)>", re.S | re.I)
SVG_TAG_RE = re.compile(r"<[^>]+>")


def tesseract_status():
    """Check whether the Tesseract OCR binary can actually be run.

    pytesseract only fails at the point of use, and a failure is
    indistinguishable from an image with no text in it — so callers that want
    to explain an empty result ask here first.

    Returns (available, message); message is empty when it is available.
    """
    global _TESSERACT_OK
    if _TESSERACT_OK:
        return True, ""
    try:
        pytesseract.get_tesseract_version()
    except Exception as e:  # TesseractNotFoundError, permission errors, ...
        return False, (
            "Reading text from images needs Tesseract, which isn't available "
            f"to this app ({e}). On macOS: brew install tesseract, then "
            "restart the admin tool."
        )
    _TESSERACT_OK = True
    return True, ""


def svg_rasteriser_status():
    """Check whether SVGs can be rasterised for OCR (needs the cairosvg package).

    Returns (available, message); message is empty when it is available.
    """
    try:
        import cairosvg  # noqa: F401
    except Exception as e:
        return False, (
            "This SVG draws its text as shapes, so it has to be rasterised "
            f"before it can be read — and cairosvg isn't available ({e}). "
            "Install it with: pip install cairosvg"
        )
    return True, ""


def _svg_text_by_regex(svg_path):
    """Pull text out of an SVG that couldn't be parsed as XML.

    Less precise than the XML path (it can't tell a real <text> element from
    one inside a comment), but a malformed SVG is common and losing the whole
    file's text to one bad entity is worse.
    """
    try:
        markup = Path(svg_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    texts = []
    for match in SVG_TEXT_RE.findall(markup):
        # Strip any nested markup, then turn entities back into characters
        t = html.unescape(SVG_TAG_RE.sub(" ", match)).strip()
        if t:
            texts.append(t)
    return texts


def svg_text_elements(svg_path):
    """List the text of each <text>/<tspan> element in an SVG, in document order.

    Document order matters: the chart's title is almost always the first
    text element, which the admin tool uses to suggest a headline.

    Returns a list of strings (empty if the file holds no text at all).
    """
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
    except (ET.ParseError, OSError):
        return _svg_text_by_regex(svg_path)

    texts = []
    for elem in root.iter():
        # SVG elements may have an XML namespace prefix like
        # "{http://www.w3.org/2000/svg}text" — strip it to compare the
        # local tag name.
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag in ("text", "tspan") and elem.text:
            t = elem.text.strip()
            if t:
                texts.append(t)
    return texts


def extract_svg_text(svg_path):
    """Extract text from SVG <text> and <tspan> elements.

    Returns the extracted text, or empty string if no text elements found
    or the file can't be read.
    """
    return " ".join(svg_text_elements(svg_path))


def svg_has_text_elements(svg_path):
    """Check if an SVG file contains readable <text>/<tspan> content.

    Used as a quick pre-check before attempting the more expensive OCR path.
    """
    return bool(svg_text_elements(svg_path))


def ocr_png(png_path):
    """Run Tesseract OCR on a PNG file and return the recognised text."""
    try:
        image = Image.open(png_path)
        text = pytesseract.image_to_string(image)
        return text.strip()
    except Exception as e:
        print(f"  OCR failed for {png_path.name}: {e}")
        return ""


def ocr_svg(svg_path):
    """Rasterize an SVG to PNG in memory via cairosvg, then OCR.

    This is the slowest extraction path — only used when the SVG has no
    native <text> elements but we still want to try reading visible text.
    """
    try:
        import cairosvg
        from io import BytesIO

        png_data = cairosvg.svg2png(url=str(svg_path))
        image = Image.open(BytesIO(png_data))
        text = pytesseract.image_to_string(image)
        return text.strip()
    except Exception as e:
        print(f"  SVG OCR failed for {svg_path.name}: {e}")
        return ""


def clean_text(raw_text):
    """Normalize extracted text for search indexing.

    Strips non-ASCII characters and collapses whitespace so the resulting
    string can be stored compactly in data.json and matched during search.
    """
    if not raw_text:
        return ""
    # Remove non-printable characters (keep newlines temporarily)
    text = re.sub(r"[^\x20-\x7E\n]", " ", raw_text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_lines_for_record(record, image_dir):
    """Extract text from a record's images as separate lines.

    Same strategies as extract_text_for_record() below, but preserving the
    boundaries between SVG <text> elements / OCR lines.  Callers wanting one
    searchable blob should use extract_text_for_record(); the line structure
    is what lets the admin tool guess a chart's title (which is the first
    line of nearly every chart).

    Returns a list of cleaned, non-empty lines.
    """
    image_dir = Path(image_dir)

    def cleaned(raw_lines):
        return [line for line in (clean_text(l) for l in raw_lines) if line]

    # Strategy 1: SVG XML extraction — fast and accurate
    for svg_file in record.get("svg_files", []):
        svg_path = image_dir / svg_file
        if svg_path.exists():
            lines = cleaned(svg_text_elements(svg_path))
            if lines:
                return lines

    # Strategy 2: PNG OCR via Tesseract
    for png_file in record.get("png_files", []):
        png_path = image_dir / png_file
        if png_path.exists():
            lines = cleaned(ocr_png(png_path).splitlines())
            if lines:
                return lines

    # Strategy 3: rasterize SVG and OCR (for SVGs with text-as-paths)
    for svg_file in record.get("svg_files", []):
        svg_path = image_dir / svg_file
        if svg_path.exists():
            lines = cleaned(ocr_svg(svg_path).splitlines())
            if lines:
                return lines

    return []


def extraction_unavailable_reason(record, image_dir):
    """Explain why extraction came back empty, if a missing tool is to blame.

    Call this only after extraction has returned nothing.  It answers the
    question the admin tool can't otherwise answer: is this chart wordless,
    or is the machine missing the thing that reads the words?

    Returns a message to show the user, or "" when the tooling is all present
    and the images genuinely had no readable text.
    """
    image_dir = Path(image_dir)
    pngs = [p for p in (image_dir / n for n in record.get("png_files", [])) if p.exists()]
    svgs = [p for p in (image_dir / n for n in record.get("svg_files", [])) if p.exists()]

    # Strategy 1 needs no tooling, so if we got here the SVG (if any) had no
    # <text> elements — everything left depends on Tesseract.
    available, message = tesseract_status()
    if not available:
        return message

    # With no PNG to OCR, the only route left is rasterising the SVG, which
    # additionally needs cairosvg.  (With a PNG present, OCR did run and found
    # nothing — that's a real answer, not a missing tool.)
    if svgs and not pngs:
        available, message = svg_rasteriser_status()
        if not available:
            return message

    return ""


def extract_text_for_record(record, image_dir):
    """Extract text from a record's images as one searchable string.

    Tries three strategies in order of preference:
      1. SVG XML text extraction (fast, accurate)
      2. PNG OCR via Tesseract (slower, works for rasterised text)
      3. SVG rasterisation + OCR (slowest, last resort)

    Returns the cleaned text or empty string if nothing could be extracted.
    """
    return " ".join(extract_lines_for_record(record, image_dir))
