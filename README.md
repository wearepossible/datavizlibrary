# Possible Dataviz Library

A searchable archive of climate data visualisations for [Possible](https://www.wearepossible.org/), migrated from Airtable to a zero-cost static site.

## What this is

An internal tool for browsing, searching, and downloading ~400 data visualisations (PNG + SVG) produced across Possible's campaigns. The public-facing site is a single-page HTML/JS app with client-side search; images are hosted on Cloudflare R2. A local Flask admin tool lets you add, edit, and delete records without touching JSON by hand.

The grid is in random order by default (so the library feels fresh on each visit); the sort control beside the search box switches to newest or oldest first by last-updated date, and that choice sticks between visits.

## Architecture

| Component | Service | Cost |
|-----------|---------|------|
| Static site | Netlify (free tier) | Free |
| Images | Cloudflare R2 (free tier, EU jurisdiction) | Free |
| Admin tool | Local Flask app (localhost:5001) | Free |
| Data | JSON file in this repo (`site/data.json`) | Free |

## Quick start

### Prerequisites

- Python 3.9+
- A `.env` file with the required environment variables (see below)
- System dependencies for text extraction: `tesseract` (OCR), `cairo` (SVG rasterisation)

### Install Python dependencies

```bash
pip install -r requirements.txt
```

### View the site locally

```bash
python site/serve.py
# Open http://localhost:8080
```

### Run the admin tool

**Without a terminal** — double-click either of these in Finder:

- **`Dataviz Admin.command`** — starts the admin tool and opens it in your browser. Keep the Terminal window it opens; closing that window stops the tool. If it's already running, it just opens the tab.
- **`Start Admin at Login.command`** — sets the admin tool to start automatically at every login and stay running, so http://localhost:5001 is just a bookmark and there's nothing to launch. Double-click it again to turn that off.

Either one installs the Python packages for you on first run.

**From a terminal**, if you prefer:

```bash
python admin/admin.py
# Open http://localhost:5001

# With auto-reload on code changes (for development):
DATAVIZ_DEBUG=1 python admin/admin.py
```

The server only listens on `127.0.0.1`, so it's never reachable from other machines. When it's set to start at login, its output goes to `~/Library/Logs/datavizadmin.log`.

The admin tool lets you add/edit/delete records and upload images. Changes are saved to `site/data.json`. Click "Deploy" in the admin to commit and push, triggering a Netlify rebuild.

The campaign list on the add/edit form only contains campaigns already in use. **To add a new one**, type its name in the box above the checkboxes and press Enter (or click **+ Add**) — it appears ticked at the top of the list, marked `NEW`, and becomes a normal campaign once you save the record. Typing a name that already exists just ticks it instead of duplicating it.

### Adding a lot of charts at once

**Batch Upload** (in the admin header) takes a pile of files in one go:

1. Drag PNGs and SVGs — or whole folders — onto the drop zone.
2. Files sharing a filename (`chart.png` + `chart.svg`) become one record; anything unpaired becomes a record on its own. Non-image files are ignored.
3. You're then asked about each one in turn, with the image on screen: headline (pre-filled from the filename, or one click to use the chart's own title), campaign, tags, and the rest. **Skip** moves on without saving, so anything you can't answer for can wait.
4. Campaign and status carry over to the next item; tags, cities, data source, data link and date each get a "copy previous" button.
5. A summary at the end lists what was saved and what wasn't, with a Deploy button.

Dropped files are staged in `data/batches/` (gitignored) until you clear them, so a half-finished batch survives closing the tab — unfinished batches are listed on the Batch Upload page to resume. `Ctrl`/`⌘` + `Enter` saves the current item and moves on.

## Project structure

```
project/
├── CLAUDE.md               # AI assistant context (project history + decisions)
├── README.md               # This file
├── .env                    # API keys (gitignored)
├── .gitignore
├── requirements.txt        # Python deps
├── Dataviz Admin.command           # Double-click in Finder to start the admin tool
├── Start Admin at Login.command    # Double-click to run it at login (toggle)
├── scripts/                # One-time migration scripts (Phases 1 & 2)
│   ├── export_airtable.py  # Export records + download images from Airtable
│   ├── text_utils.py       # Shared text extraction (SVG XML + Tesseract OCR)
│   ├── extract_text.py     # Batch text extraction into export.json
│   ├── upload_to_r2.py     # Upload images to Cloudflare R2
│   └── update_urls.py      # Generate site/data.json with public image URLs
├── admin/                  # Local admin tool (Phase 3b)
│   ├── admin.py            # Flask app: add/edit/delete records, batch upload, upload to R2
│   ├── static/
│   │   ├── admin.css
│   │   ├── admin.js        # Shared form behaviour (autocomplete, campaign filter)
│   │   └── logo.png
│   └── templates/
│       ├── base.html       # Shared layout
│       ├── list.html       # Record list with search + deploy button
│       ├── form.html       # Add/edit form with autocomplete
│       ├── batch.html      # Batch upload drop zone
│       ├── batch_item.html # One-at-a-time questions for each dropped file
│       └── batch_done.html # Batch summary + deploy
├── site/                   # Public static site (Phase 3) — deployed to Netlify
│   ├── index.html          # Single-page app with password gate
│   ├── style.css           # Possible brand styles
│   ├── app.js              # Search, grid rendering, lightbox
│   ├── logo.png            # White Possible logo
│   ├── serve.py            # Local dev server (python site/serve.py)
│   └── data.json           # All record metadata (committed to repo)
└── data/                   # Intermediate files (not committed)
    ├── export.json         # Raw Airtable export
    ├── images/             # Downloaded images (uploaded to R2, not in repo)
    └── batches/            # Staged files for in-progress batch uploads
```

## Migration pipeline

The initial migration from Airtable was a four-step pipeline. These scripts have already been run and are kept for reference:

1. **`export_airtable.py`** — Fetches all records via the Airtable API, downloads PNG/SVG attachments to `data/images/`, saves metadata to `data/export.json`.

2. **`extract_text.py`** — Extracts readable text from each image (SVG XML parsing or Tesseract OCR) and adds an `image_text` field to each record for full-text search.

3. **`upload_to_r2.py`** — Uploads all images to Cloudflare R2. Idempotent (skips files already in the bucket).

4. **`update_urls.py`** — Converts bare filenames to full public URLs and writes the final `site/data.json`.

## Environment variables

Create a `.env` file in the project root:

```env
# Airtable (only needed for re-running the export)
AIRTABLE_API_KEY=pat_xxxxx
AIRTABLE_BASE_ID=appXXXXX
AIRTABLE_TABLE_NAME=TableName

# Cloudflare R2 (needed for the admin tool)
R2_ACCOUNT_ID=xxxxx
R2_ACCESS_KEY_ID=xxxxx
R2_SECRET_ACCESS_KEY=xxxxx
R2_BUCKET_NAME=possible-dataviz-library
```

## Brand

- **Primary colour**: `#321D49` (deep purple)
- **Accent colour**: `#BF0978` (magenta)
- **Font**: Poppins (Google Fonts)
- **Design**: No rounded corners (deliberate choice)

## Deployment

The `site/` directory is deployed to Netlify. Any push to the main branch triggers an automatic rebuild. The admin tool's "Deploy" button automates this: it runs `git add site/data.json && git commit && git push`.

Images are served directly from Cloudflare R2 (`pub-083eded00aa04ff4b10dea5e1868aa1a.r2.dev`) and are not stored in the Git repo.
