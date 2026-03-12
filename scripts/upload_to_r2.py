"""
Phase 2: Upload all images from data/images/ to Cloudflare R2.

The script is idempotent — it lists the bucket contents first and skips any
files already present.  Safe to re-run after a partial upload failure.

After uploading, run update_urls.py to generate the final site/data.json
with full public URLs for each image.

Usage:
    python scripts/upload_to_r2.py

Requires .env with R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
R2_BUCKET_NAME.
"""

import json
import mimetypes
import os
from pathlib import Path

import boto3
from dotenv import load_dotenv

# ── Config ──────────────────────────────────────────────────────────────────

load_dotenv()

ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
ACCESS_KEY = os.getenv("R2_ACCESS_KEY_ID")
SECRET_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
BUCKET = os.getenv("R2_BUCKET_NAME")

# The bucket uses EU jurisdiction, which requires ".eu." in the endpoint URL
ENDPOINT = f"https://{ACCOUNT_ID}.eu.r2.cloudflarestorage.com"

DATA_DIR = Path("data")
IMAGE_DIR = DATA_DIR / "images"
EXPORT_FILE = DATA_DIR / "export.json"

# ── Helpers ─────────────────────────────────────────────────────────────────


def get_s3_client():
    """Create a boto3 S3 client pointed at the R2 endpoint."""
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name="auto",
    )


def get_content_type(filename):
    """Get the correct MIME type for a file.

    SVGs need explicit handling because Python's mimetypes module sometimes
    returns the wrong type or misses them entirely.
    """
    if filename.endswith(".svg"):
        return "image/svg+xml"
    content_type, _ = mimetypes.guess_type(filename)
    return content_type or "application/octet-stream"


def upload_file(s3, filepath, key):
    """Upload a single file to R2 with the correct Content-Type header."""
    content_type = get_content_type(filepath.name)
    s3.upload_file(
        str(filepath),
        BUCKET,
        key,
        ExtraArgs={"ContentType": content_type},
    )


def get_existing_keys(s3):
    """List all object keys already in the bucket (for skip-if-exists logic)."""
    keys = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
    return keys


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    print("Phase 2: Upload to Cloudflare R2")
    print("=" * 50)

    if not all([ACCOUNT_ID, ACCESS_KEY, SECRET_KEY, BUCKET]):
        print("ERROR: Missing R2 environment variables. Check your .env file.")
        return

    s3 = get_s3_client()

    # Load records to discover which image files need uploading
    with open(EXPORT_FILE) as f:
        data = json.load(f)

    # Check what's already in the bucket so we can skip re-uploads
    print("Checking existing uploads...")
    existing = get_existing_keys(s3)
    print(f"  {len(existing)} files already in bucket")

    # Build the list of files that still need uploading
    to_upload = []
    for record in data:
        for filename in record.get("png_files", []) + record.get("svg_files", []):
            filepath = IMAGE_DIR / filename
            if filepath.exists() and filename not in existing:
                to_upload.append((filepath, filename))

    print(f"  {len(to_upload)} files to upload\n")

    if not to_upload:
        print("Nothing to upload — all files already in R2.")
    else:
        for i, (filepath, key) in enumerate(to_upload):
            try:
                upload_file(s3, filepath, key)
                print(f"  [{i+1}/{len(to_upload)}] Uploaded {key}")
            except Exception as e:
                print(f"  [{i+1}/{len(to_upload)}] FAILED {key}: {e}")

    print(f"\nDone! {len(to_upload)} files uploaded to R2 bucket '{BUCKET}'")
    print(f"\nNext steps:")
    print(f"  1. Enable public access on the bucket (r2.dev subdomain or custom domain)")
    print(f"  2. Run: python scripts/update_urls.py <public-base-url>")


if __name__ == "__main__":
    main()
