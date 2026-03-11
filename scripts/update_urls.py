"""
Phase 2b: Generate the final site/data.json from the intermediate export.

Reads data/export.json (which stores bare image filenames), prepends the R2
public base URL to each filename to produce full URLs, and writes the result
to site/data.json — the file loaded by the static browsing interface.

This is the bridge between the migration scripts (which work with local files)
and the static site (which loads images over HTTP from R2).

Usage:
    python scripts/update_urls.py <base-url>

Example:
    python scripts/update_urls.py https://pub-abc123.r2.dev
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

    # Convert bare filenames (e.g. "slug.png") into full public URLs
    for record in data:
        record["png_urls"] = [f"{base_url}/{f}" for f in record.get("png_files", [])]
        record["svg_urls"] = [f"{base_url}/{f}" for f in record.get("svg_files", [])]

    # Write the file that the static site's app.js fetches at load time
    SITE_DIR.mkdir(exist_ok=True)
    with open(SITE_DATA, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Written {len(data)} records to {SITE_DATA}")
    print(f"  with image URLs prefixed: {base_url}/")


if __name__ == "__main__":
    main()
