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

Used by:
  - scripts/extract_text.py (batch processing during migration)
  - admin/admin.py (on-the-fly extraction for new uploads)
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytesseract
from PIL import Image

# Point pytesseract to Homebrew's Tesseract binary if it isn't already on PATH
# (common on macOS with Homebrew installs)
TESSERACT_PATH = Path("/opt/homebrew/bin/tesseract")
if TESSERACT_PATH.exists():
    pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_PATH)


def extract_svg_text(svg_path):
    """Extract text from SVG <text> and <tspan> elements.

    Returns the extracted text, or empty string if no text elements found
    or the file can't be parsed.
    """
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
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
        return " ".join(texts)
    except ET.ParseError:
        return ""


def svg_has_text_elements(svg_path):
    """Check if an SVG file contains <text> or <tspan> elements.

    Used as a quick pre-check before attempting the more expensive OCR path.
    """
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag in ("text", "tspan") and elem.text and elem.text.strip():
                return True
        return False
    except ET.ParseError:
        return False


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


def extract_text_for_record(record, image_dir):
    """Extract text from a record's images.

    Tries three strategies in order of preference:
      1. SVG XML text extraction (fast, accurate)
      2. PNG OCR via Tesseract (slower, works for rasterised text)
      3. SVG rasterisation + OCR (slowest, last resort)

    Returns the cleaned text or empty string if nothing could be extracted.
    """
    image_dir = Path(image_dir)

    # Strategy 1: SVG XML extraction — fast and accurate
    for svg_file in record.get("svg_files", []):
        svg_path = image_dir / svg_file
        if svg_path.exists() and svg_has_text_elements(svg_path):
            text = extract_svg_text(svg_path)
            if text:
                return clean_text(text)

    # Strategy 2: PNG OCR via Tesseract
    for png_file in record.get("png_files", []):
        png_path = image_dir / png_file
        if png_path.exists():
            text = ocr_png(png_path)
            if text:
                return clean_text(text)

    # Strategy 3: rasterize SVG and OCR (for SVGs with text-as-paths)
    for svg_file in record.get("svg_files", []):
        svg_path = image_dir / svg_file
        if svg_path.exists():
            text = ocr_svg(svg_path)
            if text:
                return clean_text(text)

    return ""
