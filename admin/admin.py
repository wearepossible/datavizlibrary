"""
Phase 3b: Admin tool for the Possible Dataviz Library.

A local-only Flask web app that provides a browser-based interface for
managing the dataviz archive.  Features:
  - List/search all records
  - Add new records with image upload (PNG + SVG)
  - Batch upload: drop a pile of PNGs/SVGs, then answer questions about each
    one in turn (files are paired up by filename, allowing for export
    suffixes such as `chart.svg` + `chart@2x.png`)
  - Edit existing records and replace images
  - Delete records (removes images from R2 too)
  - One-click deploy: reads the text out of any chart still missing it,
    then commits + pushes data.json to trigger a Netlify rebuild

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
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import boto3
import requests
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

from scripts.text_utils import (
    extract_text_for_record,
    extraction_unavailable_reason,
    tesseract_status,
)

# ── Config ──────────────────────────────────────────────────────────────

load_dotenv(PROJECT_ROOT / ".env")

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
R2_PUBLIC_URL = "https://pub-083eded00aa04ff4b10dea5e1868aa1a.r2.dev"
# EU jurisdiction requires ".eu." in the S3-compatible endpoint URL
R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.eu.r2.cloudflarestorage.com"

# How long to wait for one image when fetching it back from R2 for text
# extraction.  Generous: these are multi-megabyte chart exports.
IMAGE_FETCH_TIMEOUT = 60

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
# Guards on pairing a PNG with an SVG whose filename isn't identical — see
# stems_look_like_one_chart().  A stem shorter than this is too generic to
# pair on; more than this many extra characters is a different chart, not an
# export suffix.
MIN_PAIR_STEM_CHARS = 6
MAX_PAIR_EXTRA_CHARS = 8

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


def record_needs_text(record):
    """True if a record could still gain searchable text from its images.

    `text_extracted` marks a record the deploy pass has already read, so a
    chart that genuinely has no words in it isn't downloaded and OCR'd again
    on every deploy.
    """
    if record.get("image_text") or record.get("text_extracted"):
        return False
    return bool(record.get("png_urls") or record.get("svg_urls"))


def _fetch_record_images(tmpdir, record, kinds):
    """Download a published record's images from R2 into tmpdir.

    Returns a record-shaped dict naming the files that arrived, ready to hand
    to text_utils.  An image that can't be fetched is skipped rather than
    failing the deploy: the cost is less searchable text, not a lost record.
    """
    staged = {"png_files": [], "svg_files": []}
    for kind in kinds:
        for name, url in zip(record.get(f"{kind}_files", []), record.get(f"{kind}_urls", [])):
            try:
                response = requests.get(url, timeout=IMAGE_FETCH_TIMEOUT)
                response.raise_for_status()
            except requests.RequestException as e:
                print(f"  Could not fetch {url}: {e}")
                continue
            (Path(tmpdir) / name).write_bytes(response.content)
            staged[f"{kind}_files"].append(name)
    return staged


def extract_text_from_urls(record, allow_ocr=True):
    """Read the searchable text out of a published record's images.

    The SVG is fetched first: when a chart's SVG carries <text> elements the
    words come out of the XML in milliseconds, and the PNG — several
    megabytes, and seconds of OCR — never has to be downloaded at all.

    `allow_ocr` is False when the machine has no OCR to offer, in which case
    a chart that needs it is left alone rather than downloaded for nothing.

    Returns (text, reason); `reason` explains an empty result caused by a
    missing tool rather than a wordless chart.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        staged = _fetch_record_images(tmpdir, record, ("svg",))
        if staged["svg_files"]:
            text = extract_text_for_record(staged, tmpdir)
            if text:
                return text, ""

        if not allow_ocr:
            return "", tesseract_status()[1]

        staged["png_files"] = _fetch_record_images(tmpdir, record, ("png",))["png_files"]
        text = extract_text_for_record(staged, tmpdir)
        reason = "" if text else extraction_unavailable_reason(staged, tmpdir)
        return text, reason


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


# A retina/scale suffix describes the file, not the chart: "chart@2x",
# "chart_3x".  A separator is required before the digits, so a name that just
# happens to end that way (e.g. "co2x") keeps its last word.
EXPORT_SUFFIX_RE = re.compile(r"[-_@\s]+\d+x$", re.I)


def humanize_filename(stem):
    """Turn a filename stem into a plausible headline.

    'co2-emissions_by-country-2024' → 'Co2 Emissions By Country 2024'
    'co2-emissions@2x'              → 'Co2 Emissions'
    """
    words = re.split(r"[-_\s]+", EXPORT_SUFFIX_RE.sub("", stem.strip()))
    return " ".join(w[:1].upper() + w[1:] for w in words if w)


def normalise_stem(stem):
    """Reduce a filename stem to its bare letters and digits, for comparison.

    Lowercases and drops separators and punctuation, so that the same chart
    named 'Car Free Cities', 'car-free-cities' and 'car_free_cities' all
    compare equal.
    """
    return re.sub(r"[^a-z0-9]+", "", stem.lower())


def stems_look_like_one_chart(a, b):
    """True if two normalised stems look like one chart exported twice.

    A chart's PNG often carries a suffix its SVG doesn't — `chart.svg` next
    to `chart@2x.png` — so names are matched by containment rather than
    equality.  Two guards stop that pulling unrelated charts together:
      - the shorter stem must be specific enough to be worth matching on
      - what is left over must look like an export suffix rather than more
        words, so 'ev-sales' doesn't swallow 'ev-sales-by-country'
    """
    shorter, longer = sorted((a, b), key=len)
    if len(shorter) < MIN_PAIR_STEM_CHARS:
        return False
    if len(longer) - len(shorter) > MAX_PAIR_EXTRA_CHARS:
        return False
    return shorter in longer


def pair_groups_by_name(groups):
    """Merge PNG-only and SVG-only groups that name the same chart.

    Runs after files have been grouped by identical filename, and only
    considers groups still missing their other half — so a pair that already
    matched exactly is never broken up or added to.

    Where a PNG could join more than one SVG, the longest (most specific)
    stem wins; an exact tie is left alone, since attaching a chart's image to
    the wrong record is worse than leaving it as its own item.

    Mutates and returns `groups`.
    """
    png_only = [k for k, g in groups.items() if g["png_files"] and not g["svg_files"]]
    svg_only = [k for k, g in groups.items() if g["svg_files"] and not g["png_files"]]
    if not png_only or not svg_only:
        return groups

    merges = {}  # PNG-only key -> the SVG-only key it joins
    for png_key in png_only:
        matches = sorted(
            (k for k in svg_only if stems_look_like_one_chart(png_key, k)),
            key=len,
            reverse=True,
        )
        if not matches:
            continue
        if len(matches) > 1 and len(matches[0]) == len(matches[1]):
            continue  # equally plausible candidates — don't guess
        merges[png_key] = matches[0]

    for png_key, svg_key in merges.items():
        png_group = groups.pop(png_key)
        svg_group = groups[svg_key]
        svg_group["png_files"].extend(png_group["png_files"])
        # Flagged so the form can say the pairing wasn't an exact name match
        svg_group["paired_by_name"] = True
        # Keep the shorter name: exporters add suffixes rather than remove
        # them, so the shorter stem is the chart's own name.
        if len(png_group["stem"]) < len(svg_group["stem"]):
            svg_group["stem"] = png_group["stem"]

    return groups


def batch_item_paths(state, item):
    """Return (png_paths, svg_paths) as absolute paths into the staging folder."""
    d = batch_dir(state["id"])
    return (
        [d / name for name in item.get("png_files", [])],
        [d / name for name in item.get("svg_files", [])],
    )


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

    # Clean up temp files now that the upload is done
    for p in [png_tmp_path, svg_tmp_path]:
        if p and Path(p).exists():
            Path(p).unlink()

    # Searchable chart text is left empty here and filled in at deploy time —
    # reading it costs seconds of OCR per image, which shouldn't be spent
    # while someone is waiting on a form.
    record = record_from_form(
        request.form, slug, campaign,
        png_files, png_urls, svg_files, svg_urls, "",
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

    # New images mean the stored text describes a chart that's no longer
    # there — clear it so the next deploy reads the replacement.
    if png_tmp_path or svg_tmp_path:
        record["image_text"] = ""
        record.pop("text_extracted", None)

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

    Files are grouped by filename stem (ignoring case, separators and
    punctuation), so `chart.png` and `chart.svg` become one item.  Leftovers
    are then paired by containment, which catches the common case of a PNG
    exported with a suffix its SVG doesn't have (`chart.svg` + `chart@2x.png`).
    Anything still unpaired becomes an item on its own.

    Returns JSON with the URL of the first item.
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

        # Group on the original filename, not the sanitised one:
        # secure_filename() strips the very characters that mark an export
        # suffix (@, spaces), and the sanitised name is only needed on disk.
        stem = Path(original).stem
        key = normalise_stem(stem) or Path(safe).stem.lower()
        group = groups.setdefault(key, {
            "stem": stem,
            "png_files": [],
            "svg_files": [],
        })
        group["png_files" if ext == ".png" else "svg_files"].append(dest.name)

    if not groups:
        shutil.rmtree(d, ignore_errors=True)
        return jsonify({"error": "None of those files were PNGs or SVGs."}), 400

    pair_groups_by_name(groups)

    items = []
    for key in sorted(groups):
        item = groups[key]
        item["status"] = "pending"      # pending | saved | skipped
        item["record_id"] = None
        item["headline"] = None
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

        # Batch records are saved with no `image_text`: reading the words out
        # of a chart costs seconds per image (OCR), which is the whole time
        # budget of working through a batch.  The trade-off is that these
        # records aren't findable by the text inside the chart — everything
        # else about them is searchable, and records added one at a time on
        # the add form still get their text extracted.
        records.insert(0, record_from_form(
            request.form, slug, campaign,
            png_files, png_urls, svg_files, svg_urls, "",
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


# ── Deploy ──────────────────────────────────────────────────────────────
# Deploying does two things: read the text out of any chart that doesn't
# have it yet, then commit and push data.json for Netlify to rebuild from.
#
# The text is what makes a chart findable by the words printed on it.  Doing
# it here — once, in bulk, at the point where you're already waiting for the
# site to update — keeps it off the path of every form save, where OCR's few
# seconds per image are felt as the tool being slow.
#
# It runs in a background thread so a long pass doesn't hold a request open;
# the browser polls /deploy/status and shows the progress.

_deploy_lock = threading.Lock()
_deploy_state = {"status": "idle", "step": "", "done": 0, "total": 0, "message": ""}


def set_deploy_state(**fields):
    """Update the running deploy's progress."""
    with _deploy_lock:
        _deploy_state.update(fields)


def get_deploy_state():
    """A snapshot of the deploy's progress, safe to hand to another thread."""
    with _deploy_lock:
        return dict(_deploy_state)


def extract_missing_text():
    """Fill in image_text for every record still without it.

    Images are fetched back from R2 rather than kept locally, so this works
    for records added on any machine, and for a batch whose staging folder
    has long since been cleared.

    Returns a one-line summary for the deploy message.
    """
    pending = [r for r in load_data() if record_needs_text(r)]
    if not pending:
        return ""

    set_deploy_state(step="Reading the text in new charts", done=0, total=len(pending))

    # Asked once, not per chart: without OCR, a chart whose text isn't in an
    # SVG can't be read at all, and there's no point downloading its PNG to
    # find that out.  Charts that do have SVG text are still read.
    ocr_ready = tesseract_status()[0]

    extracted = {}
    blocked = ""
    unread = 0
    for i, record in enumerate(pending, start=1):
        text, reason = extract_text_from_urls(record, allow_ocr=ocr_ready)
        if reason:
            # Left unmarked on purpose, so a later deploy — on a machine
            # where the tool is installed — picks it up again.
            blocked = blocked or reason
            unread += 1
        else:
            extracted[record["id"]] = text
        set_deploy_state(done=i)

    if extracted:
        # Re-read before writing: this pass can take minutes, and a record
        # saved in another tab meanwhile mustn't be rolled back by it.
        records = load_data()
        for record in records:
            if record["id"] in extracted and record_needs_text(record):
                record["image_text"] = extracted[record["id"]]
                record["text_extracted"] = True
        save_data(records)

    summary = []
    if extracted:
        found = sum(1 for text in extracted.values() if text)
        summary.append(f"Read {len(extracted)} chart(s), found text in {found}.")
    if blocked:
        summary.append(f"Couldn't read {unread} more. {blocked}")
    return " ".join(summary)


def git_publish():
    """Commit and push site/data.json.  Returns (ok, message)."""
    def run(*args):
        return subprocess.run(args, cwd=str(PROJECT_ROOT), capture_output=True, text=True)

    add = run("git", "add", "site/data.json")
    if add.returncode:
        return False, f"git add failed: {add.stderr.strip()}"

    commit = run("git", "commit", "-m", "Update data.json via admin tool")
    if commit.returncode:
        # Nothing staged isn't a failure — the live site already matches
        if "nothing to commit" in (commit.stdout + commit.stderr).lower():
            return True, "Nothing to deploy — the live site is already up to date."
        return False, f"git commit failed: {(commit.stderr or commit.stdout).strip()}"

    push = run("git", "push")
    if push.returncode:
        return False, f"git push failed: {push.stderr.strip()}"
    return True, "Deployed. Netlify rebuilds the site within a minute or two."


def run_deploy():
    """The background job behind the Deploy button."""
    try:
        note = extract_missing_text()
    except Exception as e:
        # Never let a text problem block the deploy itself — the records are
        # saved either way, and the next deploy will try them again.
        note = f"Couldn't read the chart text ({e}); deploying anyway."

    set_deploy_state(step="Committing and pushing", done=0, total=0)
    ok, message = git_publish()
    set_deploy_state(
        status="done" if ok else "error",
        step="",
        message=" ".join(part for part in (message, note) if part),
    )


@app.route("/deploy", methods=["POST"])
def deploy():
    """Start a deploy, then show its progress."""
    with _deploy_lock:
        already_running = _deploy_state["status"] == "running"
        if not already_running:
            _deploy_state.update(
                status="running", step="Starting", done=0, total=0, message="")

    if not already_running:
        threading.Thread(target=run_deploy, daemon=True).start()

    return redirect(url_for("deploy_progress"))


@app.route("/deploy/progress")
def deploy_progress():
    """Page that follows the deploy along."""
    return render_template("deploy.html", state=get_deploy_state())


@app.route("/deploy/status")
def deploy_status():
    """JSON the progress page polls."""
    return jsonify(get_deploy_state())


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

PORT = 5001
URL = f"http://localhost:{PORT}"

if __name__ == "__main__":
    # Env vars rather than CLI flags, so the Finder launchers can set them:
    #   DATAVIZ_OPEN_BROWSER=1  open a browser tab once the server is up
    #                           ("Dataviz Admin.command" sets this)
    #   DATAVIZ_DEBUG=1         auto-reload on code changes + the Werkzeug
    #                           debugger.  Off by default: this can be left
    #                           running all day via the login item, and an
    #                           always-on debugger is not something to leave
    #                           lying around.
    debug = os.getenv("DATAVIZ_DEBUG") == "1"

    # WERKZEUG_RUN_MAIN is set in the reloader's child process — checking it
    # keeps the tab from opening twice when debug mode is on.
    if os.getenv("DATAVIZ_OPEN_BROWSER") == "1" and not os.getenv("WERKZEUG_RUN_MAIN"):
        import threading
        import webbrowser

        # Give the server a moment to bind the port before pointing a
        # browser at it
        threading.Timer(1.0, lambda: webbrowser.open(URL)).start()

    print("Admin tool starting...")
    print(f"Data file: {DATA_JSON}")
    print(f"Open {URL}")
    app.run(debug=debug, port=PORT)
