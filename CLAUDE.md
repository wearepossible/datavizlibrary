# CLAUDE.md

## Project Overview

A climate data visualisation archive (~437 records, ~1.6GB of image attachments) migrated from Airtable to a zero-cost static site with client-side filtering. The migration is complete and the site is live on Netlify.

## Architecture

- **Static site**: Vanilla HTML/CSS/JS single-page app with client-side search and filtering — no build step, no framework
- **Data**: `site/data.json` — a single JSON file containing all record metadata (headline, campaign, tags, image URLs, etc.)
- **Image hosting**: Cloudflare R2 (free tier, EU jurisdiction, 10GB storage, zero egress fees)
- **Site hosting**: Netlify (free tier, auto-deploys from Git on push)
- **Admin tool**: Local Flask app (`admin/admin.py`) for adding/editing/deleting records and uploading images to R2
- **Backup**: Google Drive (org already pays for this)

## Current State

All four phases of the migration are complete:

### Phase 1: Export from Airtable — DONE
- Exported 437 records, 430 PNGs, 416 SVGs (~1.4GB)
- Text extracted from images via SVG XML parsing + Tesseract OCR (418/437 records)
- 16 records have no images in Airtable; hidden from default grid via `has_images` flag
- **Note**: Campaign field is a linked record (list type) in Airtable, not a string — the export script normalises it to a comma-joined string

### Phase 2: Upload images to Cloudflare R2 — DONE
- Bucket: `possible-dataviz-library` (EU jurisdiction)
- Public URL: `https://pub-083eded00aa04ff4b10dea5e1868aa1a.r2.dev`
- 846 files uploaded (430 PNGs + 416 SVGs)
- **Note**: EU jurisdiction requires `.eu.` in the S3-compatible endpoint URL

### Phase 3: Build the static browsing interface — DONE
- Vanilla HTML/CSS/JS, no build step
- Client-side search with relevancy scoring (exact phrase match boosting, field-weighted term matching)
- Full-text search across chart content via OCR/SVG-extracted `image_text` field
- Possible brand: `#321D49` (deep purple), `#BF0978` (magenta), Poppins font
- No rounded corners (deliberate design choice)
- Responsive: search wraps to second line on mobile
- Lightbox with PNG/SVG download and metadata
- Client-side password gate (SHA-256 hash, sessionStorage)
- Sort control: random (default), newest first, oldest first — sorts on
  `last_updated` and applies to search results as well as the default grid;
  the choice is remembered in localStorage (`dvl_sort`)

### Phase 3b: Build admin tool — DONE
- Local Flask app at `http://localhost:5001`
- Add/edit/delete records with image upload to R2
- Autocomplete for tags and cities from existing records
- Multi-select campaign checkboxes with filter
- Text extraction from uploaded images (SVG XML or OCR)
- One-click deploy (git commit + push to trigger Netlify rebuild)

### Phase 3c: Batch upload — DONE
- Drop many PNGs/SVGs (or folders) at `/batch`, answer questions about each in turn
- Files are grouped into records by filename stem, case-insensitively — `chart.png` +
  `chart.svg` become one record, unpaired files become records of their own
- Dropped files are staged in `data/batches/<batch_id>/` (gitignored) with a
  `batch.json` holding the grouping and progress; state is on disk, not in memory,
  so a closed tab or Flask reload doesn't lose a half-finished batch
- Per item: image preview, headline pre-filled from the filename (or one click to
  use the chart's own title from the extracted text), all other fields optional,
  and a Skip button
- Campaign and status carry over to the next item; tags/cities/data source/data
  link/date get "copy previous" buttons instead (deliberate — those vary per chart)
- Likely duplicates are flagged (filename match, or filename slug matching a
  record id) but never auto-skipped
- Text extraction runs async after render and is cached in `batch.json`, so a slow
  OCR pass never blocks the form
- Abandoned staging folders are pruned after 7 days

### Phase 4: Netlify deployment — DONE
- Git repo with site files + JSON data (images on R2, not in repo)
- Netlify auto-deploys on push to main

## Tech Stack

- **Migration scripts**: Python (requests, python-dotenv, pytesseract, Pillow, cairosvg, boto3)
- **Static site**: Vanilla HTML/CSS/JS (no build step — keeps it simple and long-lived)
- **Admin tool**: Python Flask (local only, no auth needed)
- **Image hosting**: Cloudflare R2 via S3-compatible API (boto3)
- **Deployment**: Netlify (auto-deploy on push)

## Key Constraints

- Total cost must be strictly zero (this is a charity — no "almost free" services)
- Prefer established, long-lived services over startups
- The archive is low-traffic but must be reliable for years
- One sole maintainer — keep tooling simple
- Image filenames are clean slugs from campaign + headline (e.g. `carfreecities-co2-emissions-by-country-2024.png`)
- Slug collisions are resolved by appending numeric suffixes (-2, -3, ...)
- The browsing experience should feel instant (client-side filtering of ~400 records works fine)

## Data Flow

```
Airtable → export_airtable.py → data/export.json + data/images/
                                        ↓
                              extract_text.py (adds image_text)
                                        ↓
                              upload_to_r2.py → Cloudflare R2
                                        ↓
                              update_urls.py → site/data.json
                                        ↓
                              Netlify serves site/ directory
```

For ongoing use, the admin tool handles the full loop:
```
Admin form → upload image to R2 → extract text → save to site/data.json → git push → Netlify rebuilds
```

## Environment Variables

Create a `.env` file in the project root (gitignored):

- `AIRTABLE_API_KEY` — Airtable personal access token (only for re-export)
- `AIRTABLE_BASE_ID` — ID of the Airtable base (only for re-export)
- `AIRTABLE_TABLE_NAME` — Name of the table to export (only for re-export)
- `R2_ACCOUNT_ID` — Cloudflare account ID
- `R2_ACCESS_KEY_ID` — R2 API access key
- `R2_SECRET_ACCESS_KEY` — R2 API secret key
- `R2_BUCKET_NAME` — R2 bucket name (`possible-dataviz-library`)

## File Structure

```
project/
├── CLAUDE.md               # This file — project context for AI assistants
├── README.md               # Human-readable project documentation
├── .env                    # API keys (gitignored)
├── .gitignore              # Ignores .env, data/images/, __pycache__/
├── requirements.txt        # Python deps: requests, python-dotenv, pytesseract, Pillow, cairosvg, boto3, flask
├── Possible_Logo_White.png # Source logo file
├── scripts/
│   ├── export_airtable.py  # Phase 1: fetch records from Airtable + download images
│   ├── text_utils.py       # Shared text extraction (SVG XML parsing + Tesseract OCR)
│   ├── extract_text.py     # Batch text extraction — adds image_text to export.json
│   ├── upload_to_r2.py     # Phase 2: upload images to Cloudflare R2 (idempotent)
│   └── update_urls.py      # Phase 2b: generate site/data.json with public R2 URLs
├── admin/
│   ├── admin.py            # Local Flask admin tool (add/edit/delete/batch/deploy)
│   ├── static/
│   │   ├── admin.css       # Admin interface styles
│   │   ├── admin.js        # Shared form JS (autocomplete + campaign filter)
│   │   └── logo.png        # Logo for admin header
│   └── templates/
│       ├── base.html       # Shared layout (header + flash messages)
│       ├── list.html       # Record list with live search + deploy button
│       ├── form.html       # Add/edit form with autocomplete + image upload
│       ├── batch.html      # Batch upload drop zone + unfinished batches
│       ├── batch_item.html # One dropped item: preview + questions + skip
│       └── batch_done.html # Batch summary + deploy button
├── site/
│   ├── index.html          # Main browsing interface (password-gated)
│   ├── style.css           # Public site styles (Possible brand)
│   ├── app.js              # Client-side search, grid rendering, lightbox
│   ├── logo.png            # White Possible logo on transparent
│   ├── serve.py            # Simple local dev server (port 8080)
│   └── data.json           # All record metadata with R2 image URLs (committed)
└── data/
    ├── export.json          # Raw Airtable export (intermediate, not committed)
    └── images/              # Downloaded images (intermediate, not committed)
```

## Search Implementation

The static site uses a lightweight client-side relevancy search:
- All query terms must appear somewhere in the record (AND logic)
- Fields searched: headline, campaign, tags, cities, data source, status, image_text
- Scoring: exact phrase in headline (+20), campaign (+15), tags (+10); per-term bonuses weighted by field importance
- Results sorted by score descending
- No external search library — just ~40 lines of JS, fast enough for ~400 records

## Notes

- Airtable attachment URLs are temporary (expire after a few hours) — the export script downloads them immediately
- SVG files are served with `image/svg+xml` content type from R2 (explicitly set during upload)
- Some records have multiple attachments per field — handled with numeric suffixes
- The password gate on the public site is a SHA-256 client-side check — a lightweight barrier, not a security boundary
- The admin tool is local-only (Flask dev server) — never deployed publicly
- The default grid shuffles records randomly on each page load so the library feels fresh
- The shuffle happens once per page load, so switching the sort control back to
  "Random order" restores that page's order rather than reshuffling
- 8 of 439 records have no `last_updated`; they sort to the end in both date
  directions (unknown date ≠ old)
