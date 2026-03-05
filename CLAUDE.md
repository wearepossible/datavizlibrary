# CLAUDE.md

## Project Overview

Migrating a climate data visualization archive (~400 entries, ~1.6GB of image attachments) from Airtable to a free-hosted static site with client-side filtering.

## Architecture

- **Static site**: HTML/JS single-page app with client-side filtering by tag and campaign
- **Data**: JSON file containing all record metadata (headline, campaign, tags, data source, last updated, links, etc.)
- **Image hosting**: Cloudflare R2 (free tier, 10GB storage, zero egress fees)
- **Site hosting**: Netlify (free tier, auto-deploys from Git)
- **CMS (optional)**: Decap CMS for form-based editing via Git
- **Backup**: Google Drive (org already pays for this)

## Airtable Source Structure

The Airtable base contains ~400 records with ~1.6GB total attachments. Exact columns:

- **Headline** — text, used to generate image slugs
- **Status** — e.g. draft/published
- **Campaign** — which campaign the viz belongs to
- **Relevant Cities** — cities the viz relates to
- **Tags** — multi-select
- **PNG image** — attachment
- **SVG image** — attachment
- ~~Flourish Link~~ — **discarded, do not migrate**
- **Published Link** — URL where the viz was published
- **Data Source** — text describing where the data came from
- **Data Link** — URL to the source data
- **Last Updated** — date

## Migration Plan

### Phase 1: Export from Airtable
- Use the Airtable API to fetch all records with metadata
- Download all image attachments (Airtable API provides temporary download URLs)
- Save metadata as structured JSON
- Organise downloaded images into a sensible folder structure

### Phase 2: Upload images to Cloudflare R2
- Create an R2 bucket
- Upload all images with predictable URL paths
- Update JSON metadata to reference R2 URLs instead of Airtable URLs

### Phase 3: Build the static browsing interface
- Single-page app loading the JSON data file
- Universal search: single text input that searches across all fields (headline, campaign, relevant cities, tags, data source, status) and shows results filtered by relevancy — no dropdowns or separate filter controls, just type and find
- Image preview with click-to-enlarge, download as PNG or SVG
- Clean, fast, minimal design suitable for a professional org
- Mobile-friendly

### Phase 3b: Build an upload/edit form
- A separate page or interface for admin to add new records
- Form fields for all metadata: Headline, Status, Campaign, Relevant Cities, Tags, Published Link, Data Source, Data Link, Last Updated
- Image upload for PNG and SVG
- Should feel pleasant and polished to use — comparable in ease to Airtable
- Uploads images to R2 and appends to the JSON data file
- This can be a local tool or a protected web page

### Phase 4: Set up Netlify deployment
- Git repo with site files + JSON data (images are on R2, not in repo)
- Netlify auto-deploy on push
- Optional: Decap CMS config for browser-based editing

## Tech Stack

- **Migration scripts**: Python (requests, airtable API)
- **Static site**: Vanilla HTML/CSS/JS (no build step preferred — keep it simple and long-lived)
- **Image hosting**: Cloudflare R2 via S3-compatible API (boto3)
- **Deployment**: Netlify

## Key Constraints

- Total cost must be strictly zero (this is a charity — no "almost free" services)
- Prefer established, long-lived services over startups
- The archive is low-traffic but must be reliable for years
- One sole maintainer — keep tooling simple
- Image filenames should be clean slugs generated from the campaign and headline (e.g. "Car Free Cities" and "CO2 Emissions by Country 2024" → `carfreecities-co2-emissions-by-country-2024.png`) — human-readable and predictable
- The browsing experience should feel instant (client-side filtering of ~400 records is fine)

## Environment Variables Needed

- `AIRTABLE_API_KEY` — Airtable personal access token
- `AIRTABLE_BASE_ID` — ID of the Airtable base
- `AIRTABLE_TABLE_NAME` — Name of the table to export
- `R2_ACCOUNT_ID` — Cloudflare account ID
- `R2_ACCESS_KEY_ID` — R2 API access key
- `R2_SECRET_ACCESS_KEY` — R2 API secret key
- `R2_BUCKET_NAME` — R2 bucket name

## File Structure (Target)

```
project/
├── CLAUDE.md
├── .env                    # API keys (gitignored)
├── scripts/
│   ├── export_airtable.py  # Phase 1: fetch records + download images
│   ├── upload_to_r2.py     # Phase 2: upload images to R2
│   └── update_urls.py      # Phase 2: rewrite JSON with R2 URLs
├── admin/
│   └── admin.py            # Local web app for adding/editing records
│                           # (runs on localhost, provides a nice form UI,
│                           #  uploads images to R2, updates data.json,
│                           #  and optionally git commits + pushes to trigger deploy)
├── site/
│   ├── index.html          # Main browsing interface
│   ├── style.css
│   ├── app.js
│   └── data.json           # Record metadata with R2 image URLs
└── data/
    ├── export.json          # Raw Airtable export (intermediate)
    └── images/              # Downloaded images (intermediate, not committed)
```

## Notes

- Airtable attachment URLs are temporary (expire after a few hours) — the export script must download them promptly after fetching records
- SVG files should be served with correct content-type from R2
- Some records may have multiple attachments per field — handle gracefully
- Tags and campaigns should be normalised for consistent filtering
- Slug generation: if two headlines produce the same slug, append a numeric suffix (e.g. `co2-emissions-by-country-2024-2.png`)
- The admin tool is a local Flask app — it never needs to be deployed publicly, so there are no auth concerns
- The search should use a lightweight relevancy approach (e.g. count matching terms across all fields, rank by match density) — no need for a full search engine for 400 records
