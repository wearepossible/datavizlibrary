// ── Data Viz Library ────────────────────────────────────────────────────
//
// Client-side application for the Possible Dataviz Library.
// Loads all record metadata from data.json, renders a card grid, and
// provides instant search with relevancy ranking.  No build step — just
// vanilla JS served as a static file via Netlify.

// All records loaded from data.json (including those without images)
let allRecords = [];

// Subset shown by default: only records that have images, randomly shuffled
// so the grid looks different on each visit.
let defaultRecords = [];

// Fields searched when the user types a query.  image_text contains OCR /
// SVG-extracted text from chart images, enabling full-text search across
// axis labels, titles, and annotations.
const SEARCH_FIELDS = [
  "headline",
  "campaign",
  "tags",
  "relevant_cities",
  "data_source",
  "status",
  "image_text",
];

// ── Init ────────────────────────────────────────────────────────────────

async function init() {
  const resp = await fetch("data.json");
  const data = await resp.json();

  allRecords = data;

  // Default grid shows only records with images, in random order
  defaultRecords = shuffle(data.filter((r) => r.has_images));

  render(defaultRecords);

  // Wire up event listeners
  document.getElementById("search").addEventListener("input", onSearch);
  document.addEventListener("keydown", onKeydown);
  document
    .querySelector(".lightbox-backdrop")
    .addEventListener("click", closeLightbox);
  document
    .querySelector(".lightbox-close")
    .addEventListener("click", closeLightbox);
}

// ── Search ──────────────────────────────────────────────────────────────
//
// Lightweight relevancy search: every query term must appear somewhere
// in the record's searchable fields (AND logic).  Results are scored and
// sorted so that headline matches rank highest, followed by campaign and
// tag matches.  Exact phrase matches get a large bonus.

function onSearch(e) {
  const query = e.target.value.trim().toLowerCase();

  // Empty query → revert to the default randomised grid
  if (!query) {
    render(defaultRecords);
    updateCount(defaultRecords.length, defaultRecords.length);
    return;
  }

  const terms = query.split(/\s+/);

  const scored = allRecords
    .map((record) => {
      let score = 0;
      const headline = (record.headline || "").toLowerCase();
      const campaign = (record.campaign || "").toLowerCase();
      const tags = (record.tags || []).join(" ").toLowerCase();

      // Concatenate all searchable fields into one string for term matching
      const searchable = SEARCH_FIELDS.map((f) => {
        const val = record[f];
        if (Array.isArray(val)) return val.join(" ").toLowerCase();
        return (val || "").toLowerCase();
      }).join(" ");

      // All terms must appear somewhere (AND logic) — reject early if not
      for (const term of terms) {
        if (!searchable.includes(term)) return { record, score: 0 };
      }

      // Exact phrase match bonuses (highest priority)
      if (headline.includes(query)) score += 20;
      if (campaign.includes(query)) score += 15;
      if (tags.includes(query)) score += 10;

      // Per-term field bonuses — headline matches are worth more than
      // campaign matches, which are worth more than tag matches
      for (const term of terms) {
        if (headline.includes(term)) score += 3;
        if (campaign.includes(term)) score += 2;
        if (tags.includes(term)) score += 2;
        score += 1; // Base point for appearing anywhere
      }

      return { record, score };
    })
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score)
    .map((item) => item.record);

  render(scored);
}

// ── Render grid ─────────────────────────────────────────────────────────

/**
 * Render an array of records as cards in the grid.
 * Rebuilds the entire grid innerHTML for simplicity — with ~400 records
 * this is fast enough to feel instant.
 */
function render(records) {
  const grid = document.getElementById("grid");
  const empty = document.getElementById("empty");

  if (records.length === 0) {
    grid.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }

  empty.classList.add("hidden");

  grid.innerHTML = records
    .map((r) => {
      // Prefer PNG for thumbnail display; fall back to SVG
      const imgUrl = r.png_urls?.[0] || r.svg_urls?.[0] || "";
      const imgHtml = imgUrl
        ? `<img class="card-img" src="${esc(imgUrl)}" alt="${esc(r.headline)}" loading="lazy">`
        : `<div class="card-img card-img-placeholder"></div>`;
      const campaign = r.campaign ? `<div class="card-campaign">${esc(r.campaign)}</div>` : "";
      const tags = (r.tags || [])
        .map((t) => `<span class="tag">${esc(t)}</span>`)
        .join("");

      return `
      <div class="card" data-id="${esc(r.id)}">
        ${imgHtml}
        <div class="card-body">
          <div class="card-title">${esc(r.headline)}</div>
          ${campaign}
          <div class="card-tags">${tags}</div>
        </div>
      </div>`;
    })
    .join("");

  // Attach click handlers to open the lightbox detail view
  grid.querySelectorAll(".card").forEach((card) => {
    card.addEventListener("click", () => {
      const record = records.find((r) => r.id === card.dataset.id);
      if (record) openLightbox(record);
    });
  });
}

// ── Lightbox ────────────────────────────────────────────────────────────
//
// Full-screen overlay showing the image at a larger size along with all
// metadata and download links.  Closed by clicking the backdrop, the X
// button, or pressing Escape.

function openLightbox(record) {
  const lb = document.getElementById("lightbox");
  const img = document.getElementById("lightbox-img");
  const title = document.getElementById("lightbox-title");
  const details = document.getElementById("lightbox-details");
  const links = document.getElementById("lightbox-links");

  // Use PNG for display (better browser support); fall back to SVG
  img.src = record.png_urls?.[0] || record.svg_urls?.[0] || "";
  title.textContent = record.headline;

  // Build metadata lines, skipping empty fields
  const parts = [];
  if (record.campaign) {
    parts.push(`<span class="detail-label">Campaign:</span> ${esc(record.campaign)}`);
  }
  if (record.relevant_cities) {
    const cities = Array.isArray(record.relevant_cities)
      ? record.relevant_cities.join(", ")
      : record.relevant_cities;
    if (cities) parts.push(`<span class="detail-label">Cities:</span> ${esc(cities)}`);
  }
  if (record.tags && record.tags.length) {
    parts.push(`<span class="detail-label">Tags:</span> ${record.tags.map((t) => esc(t)).join(", ")}`);
  }
  if (record.data_source) {
    parts.push(`<span class="detail-label">Data source:</span> ${esc(record.data_source)}`);
  }
  if (record.last_updated) {
    parts.push(`<span class="detail-label">Last updated:</span> ${esc(record.last_updated)}`);
  }
  if (record.status) {
    parts.push(`<span class="detail-label">Status:</span> ${esc(record.status)}`);
  }
  details.innerHTML = parts.join("<br>");

  // Build action buttons: download links + external links
  const btns = [];
  if (record.png_urls?.[0]) {
    btns.push(`<a href="${esc(record.png_urls[0])}" download class="btn-primary">Download PNG</a>`);
  }
  if (record.svg_urls?.[0]) {
    btns.push(`<a href="${esc(record.svg_urls[0])}" download class="btn-primary">Download SVG</a>`);
  }
  if (record.published_link) {
    btns.push(`<a href="${esc(record.published_link)}" target="_blank" class="btn-secondary">Published link</a>`);
  }
  if (record.data_link) {
    btns.push(`<a href="${esc(record.data_link)}" target="_blank" class="btn-secondary">Data source</a>`);
  }
  links.innerHTML = btns.join("");

  lb.classList.remove("hidden");
  document.body.style.overflow = "hidden"; // Prevent background scrolling
}

function closeLightbox() {
  document.getElementById("lightbox").classList.add("hidden");
  document.body.style.overflow = ""; // Restore scrolling
}

function onKeydown(e) {
  if (e.key === "Escape") closeLightbox();
}

// ── Helpers ─────────────────────────────────────────────────────────────

/** Fisher-Yates shuffle — returns a new array in random order. */
function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/** HTML-escape a string to prevent XSS when inserting into innerHTML. */
function esc(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ── Go ──────────────────────────────────────────────────────────────────

init();
