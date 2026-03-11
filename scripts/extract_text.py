"""
Batch text extraction: reads every record in data/export.json, extracts
readable text from its images (via SVG parsing or OCR), and stores the result
in each record's `image_text` field.  This text powers the full-text search
on the static site.

The script is resumable — records that already have a non-empty `image_text`
are skipped.  Progress is saved to disk every SAVE_INTERVAL records so that
a crash doesn't lose all work.

Usage:
    python scripts/extract_text.py

Must be run from the project root (paths are relative).
"""

import json
from pathlib import Path

from text_utils import extract_text_for_record

DATA_DIR = Path("data")
IMAGE_DIR = DATA_DIR / "images"
EXPORT_FILE = DATA_DIR / "export.json"

# Write progress to disk every N records to guard against crashes
SAVE_INTERVAL = 50


def main():
    print("Text Extraction")
    print("=" * 50)

    with open(EXPORT_FILE) as f:
        data = json.load(f)

    total = len(data)
    skipped = 0
    processed = 0
    svg_extracted = 0
    ocr_extracted = 0

    for i, record in enumerate(data):
        # Skip records that already have extracted text (resumable)
        if record.get("image_text"):
            skipped += 1
            continue

        text = extract_text_for_record(record, IMAGE_DIR)
        record["image_text"] = text
        processed += 1

        # Track extraction method (informational only)
        method = "SVG" if any(
            (IMAGE_DIR / f).exists()
            for f in record.get("svg_files", [])
        ) and text and not any(
            (IMAGE_DIR / f).exists()
            for f in record.get("png_files", [])
        ) else "OCR" if text else "empty"

        chars = len(text)
        label = f"{chars} chars" if chars else "no text found"
        print(f"  [{i+1}/{total}] {record['id'][:60]}: {label}")

        # Checkpoint: save progress periodically
        if processed % SAVE_INTERVAL == 0:
            with open(EXPORT_FILE, "w") as f:
                json.dump(data, f, indent=2)
            print(f"  -- Saved progress ({processed} processed)")

    # Final save
    with open(EXPORT_FILE, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\nDone!")
    print(f"  Processed: {processed}")
    print(f"  Skipped (already done): {skipped}")
    print(f"  Total records: {total}")

    # Summary statistics
    with_text = sum(1 for r in data if r.get("image_text"))
    avg_len = (
        sum(len(r.get("image_text", "")) for r in data) // max(with_text, 1)
    )
    print(f"  Records with text: {with_text}/{total}")
    print(f"  Average text length: {avg_len} chars")


if __name__ == "__main__":
    main()
