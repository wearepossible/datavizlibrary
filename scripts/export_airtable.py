"""
Phase 1: Export all records from Airtable and download image attachments.

Connects to the Airtable API, paginates through every record in the configured
table, and saves cleaned metadata to data/export.json.  Image attachments (PNG
and SVG) are downloaded to data/images/ with human-readable slug filenames
derived from each record's campaign and headline.

Note: Airtable attachment URLs expire after a few hours, so images must be
downloaded in the same run as the record fetch.

Usage:
    python scripts/export_airtable.py

Requires .env with AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME.
"""

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

# ── Config ──────────────────────────────────────────────────────────────────

load_dotenv()

API_KEY = os.getenv("AIRTABLE_API_KEY")
BASE_ID = os.getenv("AIRTABLE_BASE_ID")
TABLE_NAME = os.getenv("AIRTABLE_TABLE_NAME")

BASE_URL = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_NAME}"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

DATA_DIR = Path("data")
IMAGE_DIR = DATA_DIR / "images"
EXPORT_FILE = DATA_DIR / "export.json"

# ── Helpers ─────────────────────────────────────────────────────────────────


def slugify(text):
    """Convert text to a clean filename slug.

    Example: "CO2 Emissions by Country (2024)" -> "co2-emissions-by-country-2024"
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)   # Strip punctuation
    text = re.sub(r"[\s_]+", "-", text)     # Spaces/underscores -> hyphens
    text = re.sub(r"-+", "-", text)         # Collapse consecutive hyphens
    return text.strip("-")


def make_image_slug(campaign, headline):
    """Generate a slug from campaign + headline, e.g. carfreecities-co2-emissions."""
    parts = []
    if campaign:
        parts.append(slugify(campaign))
    if headline:
        parts.append(slugify(headline))
    return "-".join(parts) if parts else "untitled"


def unique_path(path):
    """If path exists, append a numeric suffix to make it unique."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        new_path = parent / f"{stem}-{counter}{suffix}"
        if not new_path.exists():
            return new_path
        counter += 1


def download_image(url, dest_path):
    """Download a file from url to dest_path."""
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(resp.content)
    return dest_path


# ── Fetch all records ───────────────────────────────────────────────────────


def fetch_all_records():
    """Paginate through the Airtable API and return all records.

    Airtable returns up to 100 records per page.  Each response includes an
    "offset" token when more pages remain; we keep fetching until it's absent.
    """
    records = []
    offset = None

    while True:
        params = {}
        if offset:
            params["offset"] = offset

        resp = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        records.extend(data.get("records", []))
        offset = data.get("offset")

        print(f"  Fetched {len(records)} records so far...")

        if not offset:
            break

        time.sleep(0.2)  # Rate-limit: Airtable allows 5 req/s on free tier

    return records


# ── Process records ─────────────────────────────────────────────────────────


def process_records(raw_records):
    """Convert raw Airtable records into our clean format and download images.

    For each record we:
      1. Extract and normalise the metadata fields
      2. Generate a human-readable slug for image filenames
      3. Download attached PNG and SVG images to data/images/
      4. Build a clean dict ready for export.json
    """
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    processed = []
    used_slugs = set()

    for i, record in enumerate(raw_records):
        fields = record.get("fields", {})
        headline = fields.get("Headline", "").strip()

        # Campaign is a linked record in Airtable, so it arrives as a list
        # of strings rather than a plain string.  Normalise to comma-joined.
        raw_campaign = fields.get("Campaign", "")
        if isinstance(raw_campaign, list):
            campaign = ", ".join(raw_campaign).strip()
        elif isinstance(raw_campaign, str):
            campaign = raw_campaign.strip()
        else:
            campaign = ""

        if not headline:
            print(f"  Skipping record {record['id']}: no headline")
            continue

        base_slug = make_image_slug(campaign, headline)

        # Handle slug collisions by appending a numeric suffix (-2, -3, ...)
        slug = base_slug
        counter = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        used_slugs.add(slug)

        # Download PNG and SVG attachments.  Each field can hold multiple
        # files; we number them with a suffix (-2, -3, ...) when > 1.
        png_files = []
        svg_files = []

        for attachment in fields.get("PNG image", []):
            url = attachment.get("url")
            if not url:
                continue
            idx = len(png_files) + 1
            suffix = f"-{idx}" if idx > 1 else ""
            filename = f"{slug}{suffix}.png"
            dest = IMAGE_DIR / filename
            try:
                download_image(url, dest)
                png_files.append(filename)
                print(f"  [{i+1}/{len(raw_records)}] Downloaded {filename}")
            except Exception as e:
                print(f"  [{i+1}/{len(raw_records)}] FAILED {filename}: {e}")

        for attachment in fields.get("SVG image", []):
            url = attachment.get("url")
            if not url:
                continue
            idx = len(svg_files) + 1
            suffix = f"-{idx}" if idx > 1 else ""
            filename = f"{slug}{suffix}.svg"
            dest = IMAGE_DIR / filename
            try:
                download_image(url, dest)
                svg_files.append(filename)
                print(f"  [{i+1}/{len(raw_records)}] Downloaded {filename}")
            except Exception as e:
                print(f"  [{i+1}/{len(raw_records)}] FAILED {filename}: {e}")

        # Build clean record — the slug becomes the record ID used everywhere
        entry = {
            "id": slug,
            "headline": headline,
            "status": fields.get("Status", ""),
            "campaign": campaign,
            "relevant_cities": fields.get("Relevant Cities", ""),
            "tags": fields.get("Tags", []),
            "png_files": png_files,
            "svg_files": svg_files,
            "published_link": fields.get("Published Link", ""),
            "data_source": fields.get("Data Source", ""),
            "data_link": fields.get("Data Link", ""),
            "last_updated": fields.get("Last Updated", ""),
        }
        processed.append(entry)

    return processed


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    print("Phase 1: Airtable Export")
    print("=" * 50)

    if not all([API_KEY, BASE_ID, TABLE_NAME]):
        print("ERROR: Missing environment variables. Check your .env file.")
        return

    print(f"\nFetching records from '{TABLE_NAME}'...")
    raw_records = fetch_all_records()
    print(f"\nTotal records fetched: {len(raw_records)}")

    print(f"\nProcessing records and downloading images...")
    processed = process_records(raw_records)

    # Write the intermediate export file (consumed by later pipeline steps)
    EXPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EXPORT_FILE, "w") as f:
        json.dump(processed, f, indent=2)

    print(f"\nDone! Exported {len(processed)} records to {EXPORT_FILE}")

    # Summary
    total_png = sum(len(r["png_files"]) for r in processed)
    total_svg = sum(len(r["svg_files"]) for r in processed)
    print(f"  PNG images: {total_png}")
    print(f"  SVG images: {total_svg}")
    print(f"  Images saved to: {IMAGE_DIR}/")


if __name__ == "__main__":
    main()
