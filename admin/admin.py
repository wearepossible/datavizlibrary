"""
Phase 3b: Admin tool for the Possible Dataviz Library.

A local-only Flask web app that provides a browser-based interface for
managing the dataviz archive.  Features:
  - List/search all records
  - Add new records with image upload (PNG + SVG)
  - Batch upload: drop a pile of PNGs/SVGs, then answer questions about each
    one in turn (files are paired up by filename)
  - Edit existing records and replace images
  - Delete records (removes images from R2 too)
  - One-click deploy (git commit + push to trigger Netlify rebuild)

Images are uploaded directly to Cloudflare R2; record metadata is stored in
site/data.json (the same file the public static site reads).

This app is intended to run locally only — no authentication is needed.

Usage:
    python admin/admin.py
    # Opens at http://localhost:5001
"""

import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

import boto3
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename

# Add project root to path so we can import shared utilities from scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.text_utils import extract_lines_for_record, extract_text_for_record

# ── Config ──────────────────────────────────────────────────────────────

load_dotenv(PROJECT_ROOT / ".env")

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
R2_PUBLIC_URL = "https://pub-083eded00aa04ff4b10dea5e1868aa1a.r2.dev"
# EU jurisdiction requires ".eu." in the S3-compatible endpoint URL
R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.eu.r2.cloudflarestorage.com"

# The single JSON file that holds all record metadata — shared with the
# public static site.  Changes here are immediately visible when serving
# locally; a git push is needed to update the Netlify-hosted site.
DATA_JSON = PROJECT_ROOT / "site" / "data.json"

# Staging area for batch uploads — dropped files live here while the admin
# works through them one by one, then the folder is deleted.  Gitignored.
BATCH_ROOT = PROJECT_ROOT / "data" / "batches"
BATCH_EXTENSIONS = {".png", ".svg"}
# Abandoned batches are cleaned up automatically after this many days
BATCH_MAX_AGE_DAYS = 7

app = Flask(__name__)
# Random secret key — fine for a local-only app, regenerated each launch
app.secret_key = os.urandom(24)
# A batch upload posts every dropped file in one request, so raise Werkzeug's
# default cap of 1000 form parts.  (Ignored by older Flask versions, which
# don't enforce a limit at all.)
app.config["MAX_FORM_PARTS"] = 20000

# ── Helpers ─────────────────────────────────────────────────────────────


def slugify(text):
    """Convert text to a clean filename slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def make_slug(campaign, headline):
    """Generate a slug from campaign + headline (same logic as the migration scripts)."""
    parts = []
    if campaign:
        parts.append(slugify(campaign))
    if headline:
        parts.append(slugify(headline))
    return "-".join(parts) if parts else "untitled"


def load_data():
    """Load all records from site/data.json."""
    if DATA_JSON.exists():
        with open(DATA_JSON) as f:
            return json.load(f)
    return []


def save_data(records):
    """Persist the full record list back to site/data.json."""
    with open(DATA_JSON, "w") as f:
        json.dump(records, f, indent=2)


def get_s3_client():
    """Create a boto3 S3 client pointed at the R2 endpoint."""
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def upload_to_r2(filepath, key):
    """Upload a file to R2 and return its public URL."""
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
    """Ensure slug is unique among existing record IDs by appending -2, -3, etc."""
    if slug not in existing_ids:
        return slug
    counter = 2
    while f"{slug}-{counter}" in existing_ids:
        counter += 1
    return f"{slug}-{counter}"


def parse_comma_list(text):
    """Parse a comma-separated string into a list of stripped, non-empty items."""
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def _stage_images(tmpdir, png_paths, svg_paths):
    """Copy image files into tmpdir and return a record-shaped dict for them."""
    record = {"png_files": [], "svg_files": []}
    for key, paths in (("png_files", png_paths), ("svg_files", svg_paths)):
        for p in paths or []:
            if not p:
                continue
            src = Path(p)
            if not src.exists():
                continue
            (Path(tmpdir) / src.name).write_bytes(src.read_bytes())
            record[key].append(src.name)
    return record


def extract_lines_from_paths(png_paths, svg_paths):
    """Extract text from local image files as separate lines.

    Copies the files into a temp directory and delegates to the shared
    text_utils extraction (SVG XML parsing, then OCR).  Accepts lists of
    paths; SVGs are preferred as they extract fastest.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        record = _stage_images(tmpdir, png_paths, svg_paths)
        return extract_lines_for_record(record, tmpdir)


def extract_text_from_paths(png_paths, svg_paths):
    """Extract searchable text from local image files as one string."""
    with tempfile.TemporaryDirectory() as tmpdir:
        record = _stage_images(tmpdir, png_paths, svg_paths)
        return extract_text_for_record(record, tmpdir)


def extract_text_from_uploads(png_path, svg_path):
    """Extract searchable text from a single uploaded PNG and/or SVG."""
    return extract_text_from_paths([png_path], [svg_path])


def upload_images_for_slug(slug, png_paths, svg_paths):
    """Upload local image files to R2 under slug-based keys.

    The first file of each type is named `<slug>.png` / `<slug>.svg`; any
    extras get numeric suffixes (`<slug>-2.png`, ...), matching the naming
    convention used by the migration scripts.

    Returns (png_files, png_urls, svg_files, svg_urls).
    """
    results = []
    for paths, ext in ((png_paths, ".png"), (svg_paths, ".svg")):
        names, urls = [], []
        for i, path in enumerate(paths or []):
            if not path or not Path(path).exists():
                continue
            suffix = "" if not names else f"-{len(names) + 1}"
            key = f"{slug}{suffix}{ext}"
            urls.append(upload_to_r2(Path(path), key))
            names.append(key)
        results.append((names, urls))
    (png_files, png_urls), (svg_files, svg_urls) = results
    return png_files, png_urls, svg_files, svg_urls


def form_campaign(form):
    """Join the campaign checkboxes from a submitted form into a string."""
    return ", ".join(c.strip() for c in form.getlist("campaign") if c.strip())


def record_from_form(form, slug, campaign, png_files, png_urls, svg_files, svg_urls, image_text):
    """Build a record dict from submitted form fields plus uploaded image info."""
    return {
        "id": slug,
        "headline": form.get("headline", "").strip(),
        "status": form.get("status", "").strip(),
        "campaign": campaign,
        "relevant_cities": parse_comma_list(form.get("relevant_cities", "")),
        "tags": parse_comma_list(form.get("tags", "")),
        "png_files": png_files,
        "svg_files": svg_files,
        "published_link": form.get("published_link", "").strip(),
        "data_source": form.get("data_source", "").strip(),
        "data_link": form.get("data_link", "").strip(),
        "last_updated": form.get("last_updated", "").strip(),
        "image_text": image_text,
        "has_images": bool(png_files or svg_files),
        "png_urls": png_urls,
        "svg_urls": svg_urls,
    }


# ── Batch upload helpers ────────────────────────────────────────────────
# A "batch" is a folder of dropped files under data/batches/<batch_id>/ plus
# a batch.json describing how they were grouped into records and how far the
# admin has worked through them.  State lives on disk (not in memory) so a
# Flask reload or a closed tab doesn't lose an in-progress batch.

BATCH_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def batch_dir(batch_id):
    """Return the staging folder for a batch, or None if the id is malformed."""
    if not batch_id or not BATCH_ID_RE.match(batch_id):
        return None
    return BATCH_ROOT / batch_id


def load_batch(batch_id):
    """Load a batch's state from disk, or None if it doesn't exist."""
    d = batch_dir(batch_id)
    if not d or not (d / "batch.json").exists():
        return None
    with open(d / "batch.json") as f:
        return json.load(f)


def save_batch(state):
    """Persist a batch's state to disk."""
    d = batch_dir(state["id"])
    with open(d / "batch.json", "w") as f:
        json.dump(state, f, indent=2)


def list_batches():
    """List unfinished batches, newest first, for the batch landing page."""
    batches = []
    if not BATCH_ROOT.exists():
        return batches
    for d in BATCH_ROOT.iterdir():
        if not d.is_dir():
            continue
        state = load_batch(d.name)
        if not state:
            continue
        items = state.get("items", [])
        batches.append({
            "id": state["id"],
            "created": state.get("created", ""),
            "total": len(items),
            "pending": sum(1 for i in items if i["status"] == "pending"),
            "saved": sum(1 for i in items if i["status"] == "saved"),
            "skipped": sum(1 for i in items if i["status"] == "skipped"),
        })
    return sorted(batches, key=lambda b: b["created"], reverse=True)


def prune_old_batches():
    """Delete staging folders left behind more than BATCH_MAX_AGE_DAYS ago."""
    if not BATCH_ROOT.exists():
        return
    cutoff = time.time() - BATCH_MAX_AGE_DAYS * 86400
    for d in BATCH_ROOT.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)


def humanize_filename(stem):
    """Turn a filename stem into a plausible headline.

    'co2-emissions_by-country-2024' → 'Co2 Emissions By Country 2024'
    """
    words = re.split(r"[-_\s]+", stem.strip())
    return " ".join(w[:1].upper() + w[1:] for w in words if w)


def batch_item_paths(state, item):
    """Return (png_paths, svg_paths) as absolute paths into the staging folder."""
    d = batch_dir(state["id"])
    return (
        [d / name for name in item.get("png_files", [])],
        [d / name for name in item.get("svg_files", [])],
    )


def pick_chart_title(lines):
    """Pick the line of a chart most likely to be its title.

    Charts put their title first, so this takes the first line that reads
    like a phrase rather than an axis label, a number or a source note.
    Returns "" if nothing looks title-ish.
    """
    for line in lines:
        candidate = line.strip(" .:;-—_|")
        if not 8 <= len(candidate) <= 120:
            continue
        if len(candidate.split()) < 3:
            continue
        # Axis ticks and data labels are mostly digits and symbols
        if sum(c.isalpha() for c in candidate) < len(candidate) * 0.5:
            continue
        if candidate.lower().startswith(("source", "note", "notes", "chart by", "graphic")):
            continue
        return candidate
    return ""


def batch_item_analysis(state, item):
    """Get the item's extracted text and suggested title, caching the result.

    Extraction can be slow (OCR), so both are worked out in one pass and
    stored in batch.json — revisiting an item never re-runs it.
    """
    if item.get("image_text") is None:
        png_paths, svg_paths = batch_item_paths(state, item)
        lines = extract_lines_from_paths(png_paths, svg_paths)
        item["image_text"] = " ".join(lines)
        item["suggested_title"] = pick_chart_title(lines)
        save_batch(state)
    return item["image_text"], item.get("suggested_title", "")


def find_possible_duplicate(records, item):
    """Find an existing record that looks like this item has already been added.

    Matches either on filename (files re-dropped after a previous upload keep
    their slug-based names) or on the slugified filename stem matching a
    record id.  Catches the common case of dropping a folder twice.
    """
    names = {n.lower() for n in item.get("png_files", []) + item.get("svg_files", [])}
    stem_slug = slugify(item["stem"])
    for r in records:
        existing = {n.lower() for n in r.get("png_files", []) + r.get("svg_files", [])}
        if names & existing:
            return r
        if stem_slug and r.get("id", "").lower() == stem_slug:
            return r
    return None


# ── Routes ──────────────────────────────────────────────────────────────


@app.route("/")
def index():
    """List all records, optionally filtered by a search query (?q=...)."""
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
    """Add a new record.

    GET:  render the empty form with dropdowns populated from existing data.
    POST: validate, upload images to R2, extract text, save to data.json.
    """
    if request.method == "GET":
        return render_template("form.html", record=None, campaigns=_get_campaigns(),
                               statuses=_get_statuses(), all_tags=_get_tags(), all_cities=_get_cities())

    # ── Process form submission ──
    records = load_data()
    existing_ids = {r["id"] for r in records}

    headline = request.form.get("headline", "").strip()
    if not headline:
        flash("Headline is required.", "error")
        return redirect(url_for("add"))

    # Campaign comes from checkboxes (multi-select), joined with commas
    campaign = form_campaign(request.form)
    slug = unique_slug(make_slug(campaign, headline), existing_ids)

    # ── Upload images to R2 ──
    # Files are saved to a temp location, uploaded, then cleaned up.
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

    # Extract searchable text from the uploaded images (for full-text search)
    image_text = extract_text_from_uploads(png_tmp_path, svg_tmp_path)

    # Clean up temp files now that upload + text extraction are done
    for p in [png_tmp_path, svg_tmp_path]:
        if p and Path(p).exists():
            Path(p).unlink()

    # Build the new record and prepend it (newest first)
    record = record_from_form(
        request.form, slug, campaign,
        png_files, png_urls, svg_files, svg_urls, image_text,
    )

    records.insert(0, record)
    save_data(records)
    flash(f'Added "{headline}"', "success")
    return redirect(url_for("index"))


@app.route("/edit/<record_id>", methods=["GET", "POST"])
def edit(record_id):
    """Edit an existing record.

    GET:  render the form pre-filled with the record's current values.
    POST: update metadata; if new images are uploaded, replace the old ones
          in R2 and re-extract searchable text.
    """
    records = load_data()
    record = next((r for r in records if r["id"] == record_id), None)
    if not record:
        flash("Record not found.", "error")
        return redirect(url_for("index"))

    if request.method == "GET":
        return render_template("form.html", record=record, campaigns=_get_campaigns(),
                               statuses=_get_statuses(), all_tags=_get_tags(), all_cities=_get_cities())

    # ── Update text metadata ──
    record["headline"] = request.form.get("headline", "").strip()
    record["status"] = request.form.get("status", "").strip()
    record["campaign"] = form_campaign(request.form)
    record["relevant_cities"] = parse_comma_list(request.form.get("relevant_cities", ""))
    record["tags"] = parse_comma_list(request.form.get("tags", ""))
    record["published_link"] = request.form.get("published_link", "").strip()
    record["data_source"] = request.form.get("data_source", "").strip()
    record["data_link"] = request.form.get("data_link", "").strip()
    record["last_updated"] = request.form.get("last_updated", "").strip()

    # ── Replace images if new files were uploaded ──
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
    """Delete a record and remove its images from R2."""
    records = load_data()
    record = next((r for r in records if r["id"] == record_id), None)
    if not record:
        flash("Record not found.", "error")
        return redirect(url_for("index"))

    headline = record.get("headline", record_id)

    # Delete the associated image files from the R2 bucket
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


# ── Batch upload ────────────────────────────────────────────────────────
# Drop a pile of PNGs/SVGs, they get paired up by filename, then the admin
# is asked about each pairing in turn (with the option to skip).


@app.route("/batch")
def batch():
    """Landing page: the drop zone, plus any unfinished batches to resume."""
    prune_old_batches()
    return render_template("batch.html", batches=list_batches())


@app.route("/batch/upload", methods=["POST"])
def batch_upload():
    """Receive dropped files, group them into items, and start a batch.

    Files are grouped by filename stem (case-insensitive), so `chart.png` and
    `chart.svg` become one item.  Anything unpaired becomes an item on its
    own.  Returns JSON with the URL of the first item.
    """
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        return jsonify({"error": "No files received."}), 400

    batch_id = uuid.uuid4().hex[:12]
    d = BATCH_ROOT / batch_id
    d.mkdir(parents=True, exist_ok=True)

    groups = {}
    ignored = []
    for f in files:
        # Folder drops send paths like "subdir/chart.png" — keep the leaf only
        original = Path(f.filename.replace("\\", "/")).name
        ext = Path(original).suffix.lower()
        if ext not in BATCH_EXTENSIONS:
            ignored.append(original)
            continue

        safe = secure_filename(original) or f"file{ext}"
        if not safe.lower().endswith(ext):
            safe += ext
        # Two files can sanitise to the same name — keep both on disk
        dest = d / safe
        counter = 2
        while dest.exists():
            dest = d / f"{Path(safe).stem}-{counter}{ext}"
            counter += 1
        f.save(str(dest))

        key = Path(safe).stem.lower()
        group = groups.setdefault(key, {
            "stem": Path(original).stem,
            "png_files": [],
            "svg_files": [],
        })
        group["png_files" if ext == ".png" else "svg_files"].append(dest.name)

    if not groups:
        shutil.rmtree(d, ignore_errors=True)
        return jsonify({"error": "None of those files were PNGs or SVGs."}), 400

    items = []
    for key in sorted(groups):
        item = groups[key]
        item["status"] = "pending"      # pending | saved | skipped
        item["record_id"] = None
        item["headline"] = None
        item["image_text"] = None       # filled in lazily by batch_item_text()
        items.append(item)

    state = {
        "id": batch_id,
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "ignored": ignored,
        "items": items,
        # Values carried over from the last saved item, so a batch that
        # shares a campaign/status doesn't need retyping every time
        "prev": {},
    }
    save_batch(state)

    return jsonify({
        "redirect": url_for("batch_item", batch_id=batch_id, index=0),
        "count": len(items),
        "ignored": len(ignored),
    })


@app.route("/batch/<batch_id>")
def batch_resume(batch_id):
    """Jump to the first item of a batch that still needs an answer."""
    state = load_batch(batch_id)
    if not state:
        flash("That batch no longer exists.", "error")
        return redirect(url_for("batch"))
    for i, item in enumerate(state["items"]):
        if item["status"] == "pending":
            return redirect(url_for("batch_item", batch_id=batch_id, index=i))
    return redirect(url_for("batch_done", batch_id=batch_id))


@app.route("/batch/<batch_id>/item/<int:index>", methods=["GET", "POST"])
def batch_item(batch_id, index):
    """Ask about one item in the batch.

    GET:  show the image(s) alongside a pre-filled form.
    POST: save it as a record (uploading to R2), or skip it, then move on to
          the next item.
    """
    state = load_batch(batch_id)
    if not state:
        flash("That batch no longer exists.", "error")
        return redirect(url_for("batch"))
    if not 0 <= index < len(state["items"]):
        return redirect(url_for("batch_resume", batch_id=batch_id))

    item = state["items"][index]
    next_url = (
        url_for("batch_item", batch_id=batch_id, index=index + 1)
        if index + 1 < len(state["items"])
        else url_for("batch_done", batch_id=batch_id)
    )

    if request.method == "POST":
        if request.form.get("action") == "skip":
            item["status"] = "skipped"
            save_batch(state)
            return redirect(next_url)

        headline = request.form.get("headline", "").strip()
        if not headline:
            flash("Headline is required — use Skip if there's no good answer.", "error")
            return redirect(url_for("batch_item", batch_id=batch_id, index=index))

        records = load_data()
        campaign = form_campaign(request.form)
        slug = unique_slug(make_slug(campaign, headline), {r["id"] for r in records})

        png_paths, svg_paths = batch_item_paths(state, item)
        png_files, png_urls, svg_files, svg_urls = upload_images_for_slug(
            slug, png_paths, svg_paths
        )

        # The form carries text extracted in the background; fall back to
        # extracting now if that request hadn't finished before submit.
        image_text = request.form.get("image_text", "").strip()
        if not image_text:
            image_text, _ = batch_item_analysis(state, item)

        records.insert(0, record_from_form(
            request.form, slug, campaign,
            png_files, png_urls, svg_files, svg_urls, image_text,
        ))
        save_data(records)

        item["status"] = "saved"
        item["record_id"] = slug
        item["headline"] = headline
        # Campaign and status are pre-filled on the next item (they rarely
        # change within a batch); the rest are remembered only so the next
        # item can offer a "copy previous" button.
        state["prev"] = {
            "campaign": request.form.getlist("campaign"),
            "status": request.form.get("status", "").strip(),
            "tags": request.form.get("tags", "").strip(),
            "relevant_cities": request.form.get("relevant_cities", "").strip(),
            "data_source": request.form.get("data_source", "").strip(),
            "data_link": request.form.get("data_link", "").strip(),
            "last_updated": request.form.get("last_updated", "").strip(),
        }
        save_batch(state)
        return redirect(next_url)

    records = load_data()
    return render_template(
        "batch_item.html",
        state=state,
        item=item,
        index=index,
        total=len(state["items"]),
        done_count=sum(1 for i in state["items"] if i["status"] != "pending"),
        saved_count=sum(1 for i in state["items"] if i["status"] == "saved"),
        next_url=next_url,
        prev_url=url_for("batch_item", batch_id=batch_id, index=index - 1) if index else None,
        suggested_headline=humanize_filename(item["stem"]),
        duplicate=find_possible_duplicate(records, item),
        prev=state.get("prev", {}),
        campaigns=_get_campaigns(),
        statuses=_get_statuses(),
        all_tags=_get_tags(),
        all_cities=_get_cities(),
    )


@app.route("/batch/<batch_id>/item/<int:index>/text")
def batch_item_text_api(batch_id, index):
    """JSON endpoint for an item's extracted text and suggested headline.

    Called by the form after it renders, so a slow OCR pass doesn't hold up
    the page.  The result is cached in batch.json.
    """
    state = load_batch(batch_id)
    if not state or not 0 <= index < len(state["items"]):
        return jsonify({"text": "", "title": ""}), 404
    text, title = batch_item_analysis(state, state["items"][index])
    return jsonify({"text": text, "title": title})


@app.route("/batch/<batch_id>/file/<path:filename>")
def batch_file(batch_id, filename):
    """Serve a staged file so the form can preview it."""
    d = batch_dir(batch_id)
    if not d or not d.exists():
        abort(404)
    return send_from_directory(d, filename)


@app.route("/batch/<batch_id>/done")
def batch_done(batch_id):
    """Summary of a finished batch, with a deploy button."""
    state = load_batch(batch_id)
    if not state:
        flash("That batch no longer exists.", "error")
        return redirect(url_for("batch"))
    return render_template("batch_done.html", state=state)


@app.route("/batch/<batch_id>/discard", methods=["POST"])
def batch_discard(batch_id):
    """Delete a batch's staging folder.

    Records already saved from the batch are untouched — this only clears
    the dropped files and the batch's progress.
    """
    d = batch_dir(batch_id)
    if d and d.exists():
        shutil.rmtree(d, ignore_errors=True)
        flash("Cleared the dropped files.", "success")
    target = "index" if request.form.get("next") == "index" else "batch"
    return redirect(url_for(target))


@app.route("/api/autocomplete")
def autocomplete():
    """JSON endpoint returning all unique tags and cities for form autocomplete."""
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
    """One-click deploy: git add + commit + push site/data.json.

    Netlify is configured to auto-deploy on push, so this is all that's
    needed to publish changes to the live site.
    """
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
# These scan existing records to populate select/autocomplete options in
# the add/edit form, so the admin doesn't have to retype common values.


def _get_campaigns():
    """Get sorted unique campaigns from existing records.

    Campaigns can be comma-separated (multi-campaign records), so we split
    and deduplicate individual campaign names.
    """
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
