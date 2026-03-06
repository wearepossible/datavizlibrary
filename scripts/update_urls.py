"""
Phase 2b: Update export.json with R2 public URLs and write site/data.json.

Usage:
    python scripts/update_urls.py <base-url>

Example:
    python scripts/update_urls.py https://pub-abc123.r2.dev

This reads data/export.json, adds full R2 URLs to each record's image fields,
and writes the final site/data.json for the static site.
"""

import json
import sys
from pathlib import Path

EXPORT_FILE = Path("data/export.json")
SITE_DIR = Path("site")
SITE_DATA = SITE_DIR / "data.json"


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/update_urls.py <base-url>")
        print("Example: python scripts/update_urls.py https://pub-abc123.r2.dev")
        sys.exit(1)

    base_url = sys.argv[1].rstrip("/")
    print(f"Base URL: {base_url}")

    with open(EXPORT_FILE) as f:
        data = json.load(f)

    for record in data:
        # Build full URLs from filenames
        record["png_urls"] = [f"{base_url}/{f}" for f in record.get("png_files", [])]
        record["svg_urls"] = [f"{base_url}/{f}" for f in record.get("svg_files", [])]

    # Write site data
    SITE_DIR.mkdir(exist_ok=True)
    with open(SITE_DATA, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Written {len(data)} records to {SITE_DATA}")
    print(f"  with image URLs prefixed: {base_url}/")


if __name__ == "__main__":
    main()
