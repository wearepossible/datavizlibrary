"""
Phase 3b: Admin tool for the Possible Dataviz Library.

Local Flask app for adding/editing records, uploading images to R2,
and updating site/data.json.

Usage:
    python admin/admin.py
    # Opens at http://localhost:5001
"""

import json
import mimetypes
import os
import re
import sys
import tempfile
from pathlib import Path

import boto3
from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

# Add project root to path so we can import scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.text_utils import extract_text_for_record

# ── Config ──────────────────────────────────────────────────────────────

load_dotenv(PROJECT_ROOT / ".env")

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
R2_PUBLIC_URL = "https://pub-083eded00aa04ff4b10dea5e1868aa1a.r2.dev"
R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.eu.r2.cloudflarestorage.com"

DATA_JSON = PROJECT_ROOT / "site" / "data.json"

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ── Helpers ─────────────────────────────────────────────────────────────


def slugify(text):
    """Convert text to a clean filename slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def make_slug(campaign, headline):
    """Generate a slug from campaign + headline."""
    parts = []
    if campaign:
        parts.append(slugify(campaign))
    if headline:
        parts.append(slugify(headline))
    return "-".join(parts) if parts else "untitled"


def load_data():
    """Load records from site/data.json."""
    if DATA_JSON.exists():
        with open(DATA_JSON) as f:
            return json.load(f)
    return []


def save_data(records):
    """Save records to site/data.json."""
    with open(DATA_JSON, "w") as f:
        json.dump(records, f, indent=2)


def get_s3_client():
    """Create a boto3 S3 client for R2."""
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def upload_to_r2(filepath, key):
    """Upload a file to R2 and return the public URL."""
    client = get_s3_client()
    content_type = "image/svg+xml" if key.endswith(".svg") else (
        mimetypes.guess_type(key)[0] or "application/octet-stream"
    )
    client.upload_file(
        str(filepath),
        R2_BUCKET_NAME,
        key,
        ExtraArgs={"ContentType": content_type},
    )
    return f"{R2_PUBLIC_URL}/{key}"


def unique_slug(slug, existing_ids):
    """Ensure slug is unique among existing record IDs."""
    if slug not in existing_ids:
        return slug
    counter = 2
    while f"{slug}-{counter}" in existing_ids:
        counter += 1
    return f"{slug}-{counter}"


def parse_comma_list(text):
    """Parse a comma-separated string into a list of stripped items."""
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def extract_text_from_uploads(png_path, svg_path):
    """Extract searchable text from uploaded image files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        record = {"png_files": [], "svg_files": []}
        if png_path:
            fname = Path(png_path).name
            dest = Path(tmpdir) / fname
            dest.write_bytes(Path(png_path).read_bytes())
            record["png_files"] = [fname]
        if svg_path:
            fname = Path(svg_path).name
            dest = Path(tmpdir) / fname
            dest.write_bytes(Path(svg_path).read_bytes())
            record["svg_files"] = [fname]
        return extract_text_for_record(record, tmpdir)


# ── Routes ──────────────────────────────────────────────────────────────


@app.route("/")
def index():
    """List all records with optional search."""
    records = load_data()
    q = request.args.get("q", "").strip().lower()
    if q:
        terms = q.split()
        filtered = []
        for r in records:
            searchable = " ".join([
                r.get("headline", ""),
                r.get("campaign", ""),
                " ".join(r.get("tags", [])),
                r.get("status", ""),
            ]).lower()
            if all(t in searchable for t in terms):
                filtered.append(r)
        records = filtered
    return render_template("list.html", records=records, query=q)


@app.route("/add", methods=["GET", "POST"])
def add():
    """Add a new record."""
    if request.method == "GET":
        return render_template("form.html", record=None, campaigns=_get_campaigns(),
                               statuses=_get_statuses(), all_tags=_get_tags(), all_cities=_get_cities())

    # Process form
    records = load_data()
    existing_ids = {r["id"] for r in records}

    headline = request.form.get("headline", "").strip()
    if not headline:
        flash("Headline is required.", "error")
        return redirect(url_for("add"))

    campaigns = request.form.getlist("campaign")
    campaign = ", ".join(c.strip() for c in campaigns if c.strip())
    slug = unique_slug(make_slug(campaign, headline), existing_ids)

    # Handle image uploads
    png_files, png_urls = [], []
    svg_files, svg_urls = [], []
    png_tmp_path, svg_tmp_path = None, None

    png_file = request.files.get("png_image")
    if png_file and png_file.filename:
        png_filename = f"{slug}.png"
        png_tmp = Path(tempfile.mktemp(suffix=".png"))
        png_file.save(str(png_tmp))
        png_tmp_path = str(png_tmp)
        url = upload_to_r2(png_tmp, png_filename)
        png_files.append(png_filename)
        png_urls.append(url)

    svg_file = request.files.get("svg_image")
    if svg_file and svg_file.filename:
        svg_filename = f"{slug}.svg"
        svg_tmp = Path(tempfile.mktemp(suffix=".svg"))
        svg_file.save(str(svg_tmp))
        svg_tmp_path = str(svg_tmp)
        url = upload_to_r2(svg_tmp, svg_filename)
        svg_files.append(svg_filename)
        svg_urls.append(url)

    # Extract text from images
    image_text = extract_text_from_uploads(png_tmp_path, svg_tmp_path)

    # Clean up temp files
    for p in [png_tmp_path, svg_tmp_path]:
        if p and Path(p).exists():
            Path(p).unlink()

    # Build record
    record = {
        "id": slug,
        "headline": headline,
        "status": request.form.get("status", "").strip(),
        "campaign": campaign,
        "relevant_cities": parse_comma_list(request.form.get("relevant_cities", "")),
        "tags": parse_comma_list(request.form.get("tags", "")),
        "png_files": png_files,
        "svg_files": svg_files,
        "published_link": request.form.get("published_link", "").strip(),
        "data_source": request.form.get("data_source", "").strip(),
        "data_link": request.form.get("data_link", "").strip(),
        "last_updated": request.form.get("last_updated", "").strip(),
        "image_text": image_text,
        "has_images": bool(png_files or svg_files),
        "png_urls": png_urls,
        "svg_urls": svg_urls,
    }

    records.insert(0, record)
    save_data(records)
    flash(f'Added "{headline}"', "success")
    return redirect(url_for("index"))


@app.route("/edit/<record_id>", methods=["GET", "POST"])
def edit(record_id):
    """Edit an existing record."""
    records = load_data()
    record = next((r for r in records if r["id"] == record_id), None)
    if not record:
        flash("Record not found.", "error")
        return redirect(url_for("index"))

    if request.method == "GET":
        return render_template("form.html", record=record, campaigns=_get_campaigns(),
                               statuses=_get_statuses(), all_tags=_get_tags(), all_cities=_get_cities())

    # Update fields
    record["headline"] = request.form.get("headline", "").strip()
    record["status"] = request.form.get("status", "").strip()
    campaigns = request.form.getlist("campaign")
    record["campaign"] = ", ".join(c.strip() for c in campaigns if c.strip())
    record["relevant_cities"] = parse_comma_list(request.form.get("relevant_cities", ""))
    record["tags"] = parse_comma_list(request.form.get("tags", ""))
    record["published_link"] = request.form.get("published_link", "").strip()
    record["data_source"] = request.form.get("data_source", "").strip()
    record["data_link"] = request.form.get("data_link", "").strip()
    record["last_updated"] = request.form.get("last_updated", "").strip()

    # Handle replacement images
    png_tmp_path, svg_tmp_path = None, None

    png_file = request.files.get("png_image")
    if png_file and png_file.filename:
        png_filename = f"{record_id}.png"
        png_tmp = Path(tempfile.mktemp(suffix=".png"))
        png_file.save(str(png_tmp))
        png_tmp_path = str(png_tmp)
        url = upload_to_r2(png_tmp, png_filename)
        record["png_files"] = [png_filename]
        record["png_urls"] = [url]

    svg_file = request.files.get("svg_image")
    if svg_file and svg_file.filename:
        svg_filename = f"{record_id}.svg"
        svg_tmp = Path(tempfile.mktemp(suffix=".svg"))
        svg_file.save(str(svg_tmp))
        svg_tmp_path = str(svg_tmp)
        url = upload_to_r2(svg_tmp, svg_filename)
        record["svg_files"] = [svg_filename]
        record["svg_urls"] = [url]

    # Re-extract text if new images were uploaded
    if png_tmp_path or svg_tmp_path:
        image_text = extract_text_from_uploads(
            png_tmp_path or None,
            svg_tmp_path or None,
        )
        if image_text:
            record["image_text"] = image_text

    record["has_images"] = bool(record.get("png_files") or record.get("svg_files"))

    # Clean up temp files
    for p in [png_tmp_path, svg_tmp_path]:
        if p and Path(p).exists():
            Path(p).unlink()

    save_data(records)
    flash(f'Updated "{record["headline"]}"', "success")
    return redirect(url_for("index"))


@app.route("/delete/<record_id>", methods=["POST"])
def delete(record_id):
    """Delete a record and its images from R2."""
    records = load_data()
    record = next((r for r in records if r["id"] == record_id), None)
    if not record:
        flash("Record not found.", "error")
        return redirect(url_for("index"))

    headline = record.get("headline", record_id)

    # Delete images from R2
    r2_keys = record.get("png_files", []) + record.get("svg_files", [])
    if r2_keys:
        try:
            client = get_s3_client()
            for key in r2_keys:
                client.delete_object(Bucket=R2_BUCKET_NAME, Key=key)
        except Exception as e:
            flash(f"Warning: could not delete images from R2: {e}", "error")

    records = [r for r in records if r["id"] != record_id]
    save_data(records)
    flash(f'Deleted "{headline}" and its images', "success")
    return redirect(url_for("index"))


@app.route("/api/autocomplete")
def autocomplete():
    """Return all unique tags, cities, and campaigns for autocomplete."""
    records = load_data()
    tags = set()
    cities = set()
    for r in records:
        for t in r.get("tags", []):
            if t.strip():
                tags.add(t.strip())
        rc = r.get("relevant_cities", [])
        if isinstance(rc, list):
            for c in rc:
                if c.strip():
                    cities.add(c.strip())
    return jsonify({"tags": sorted(tags), "cities": sorted(cities)})


@app.route("/deploy", methods=["POST"])
def deploy():
    """Git commit and push to trigger Netlify deploy."""
    import subprocess

    try:
        subprocess.run(
            ["git", "add", "site/data.json"],
            cwd=str(PROJECT_ROOT),
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Update data.json via admin tool"],
            cwd=str(PROJECT_ROOT),
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push"],
            cwd=str(PROJECT_ROOT),
            check=True, capture_output=True,
        )
        flash("Deployed! Pushed to Git.", "success")
    except subprocess.CalledProcessError as e:
        flash(f"Deploy failed: {e.stderr.decode() if e.stderr else str(e)}", "error")

    return redirect(url_for("index"))


# ── Helpers for form dropdowns ──────────────────────────────────────────


def _get_campaigns():
    """Get sorted unique campaigns from existing records."""
    records = load_data()
    campaigns = set()
    for r in records:
        c = r.get("campaign", "").strip()
        if c:
            for part in c.split(","):
                part = part.strip()
                if part:
                    campaigns.add(part)
    return sorted(campaigns)


def _get_statuses():
    """Get sorted unique statuses from existing records."""
    records = load_data()
    statuses = set()
    for r in records:
        s = r.get("status", "").strip()
        if s:
            statuses.add(s)
    return sorted(statuses)


def _get_tags():
    """Get sorted unique tags from existing records."""
    records = load_data()
    tags = set()
    for r in records:
        for t in r.get("tags", []):
            if t.strip():
                tags.add(t.strip())
    return sorted(tags)


def _get_cities():
    """Get sorted unique cities from existing records."""
    records = load_data()
    cities = set()
    for r in records:
        rc = r.get("relevant_cities", [])
        if isinstance(rc, list):
            for c in rc:
                if c.strip():
                    cities.add(c.strip())
    return sorted(cities)


# ── Main ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Admin tool starting...")
    print(f"Data file: {DATA_JSON}")
    print(f"Open http://localhost:5001")
    app.run(debug=True, port=5001)
