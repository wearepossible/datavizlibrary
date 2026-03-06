"""
Phase 2: Upload all images to Cloudflare R2 and update export.json with public URLs.

Usage:
    python scripts/upload_to_r2.py

Requires .env with R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME.
Also requires the R2 bucket to have public access enabled (r2.dev subdomain or custom domain).
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

# EU jurisdiction endpoint
ENDPOINT = f"https://{ACCOUNT_ID}.eu.r2.cloudflarestorage.com"

DATA_DIR = Path("data")
IMAGE_DIR = DATA_DIR / "images"
EXPORT_FILE = DATA_DIR / "export.json"

# ── Helpers ─────────────────────────────────────────────────────────────────


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name="auto",
    )


def get_content_type(filename):
    """Get the correct content type, especially for SVGs."""
    if filename.endswith(".svg"):
        return "image/svg+xml"
    content_type, _ = mimetypes.guess_type(filename)
    return content_type or "application/octet-stream"


def upload_file(s3, filepath, key):
    """Upload a file to R2 with correct content type."""
    content_type = get_content_type(filepath.name)
    s3.upload_file(
        str(filepath),
        BUCKET,
        key,
        ExtraArgs={"ContentType": content_type},
    )


def get_existing_keys(s3):
    """List all existing keys in the bucket to skip re-uploads."""
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

    # Load records
    with open(EXPORT_FILE) as f:
        data = json.load(f)

    # Get already-uploaded files to skip
    print("Checking existing uploads...")
    existing = get_existing_keys(s3)
    print(f"  {len(existing)} files already in bucket")

    # Collect all files to upload
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
        # Upload
        for i, (filepath, key) in enumerate(to_upload):
            try:
                upload_file(s3, filepath, key)
                print(f"  [{i+1}/{len(to_upload)}] Uploaded {key}")
            except Exception as e:
                print(f"  [{i+1}/{len(to_upload)}] FAILED {key}: {e}")

    # Now update export.json with R2 URL info
    # The actual public URL depends on whether r2.dev or custom domain is set up.
    # We store the R2 key (filename) — the base URL is configured once in the site.
    print(f"\nDone! {len(to_upload)} files uploaded to R2 bucket '{BUCKET}'")
    print(f"\nNext steps:")
    print(f"  1. Enable public access on the bucket (r2.dev subdomain or custom domain)")
    print(f"  2. Run: python scripts/update_urls.py <public-base-url>")


if __name__ == "__main__":
    main()
