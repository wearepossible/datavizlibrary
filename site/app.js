// ── Data Viz Library ────────────────────────────────────────────────────

let allRecords = [];

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

  // Only show records that have images
  allRecords = data.filter((r) => r.has_images);

  render(allRecords);

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

function onSearch(e) {
  const query = e.target.value.trim().toLowerCase();

  if (!query) {
    render(allRecords);
    updateCount(allRecords.length, allRecords.length);
    return;
  }

  const terms = query.split(/\s+/);

  const scored = allRecords
    .map((record) => {
      let score = 0;
      const headline = (record.headline || "").toLowerCase();
      const campaign = (record.campaign || "").toLowerCase();
      const tags = (record.tags || []).join(" ").toLowerCase();
      const searchable = SEARCH_FIELDS.map((f) => {
        const val = record[f];
        if (Array.isArray(val)) return val.join(" ").toLowerCase();
        return (val || "").toLowerCase();
      }).join(" ");

      // All terms must appear somewhere
      for (const term of terms) {
        if (!searchable.includes(term)) return { record, score: 0 };
      }

      // Exact phrase match bonuses (highest priority)
      if (headline.includes(query)) score += 20;
      if (campaign.includes(query)) score += 15;
      if (tags.includes(query)) score += 10;

      // Per-term field bonuses
      for (const term of terms) {
        if (headline.includes(term)) score += 3;
        if (campaign.includes(term)) score += 2;
        if (tags.includes(term)) score += 2;
        score += 1;
      }

      return { record, score };
    })
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score)
    .map((item) => item.record);

  render(scored);
}

// ── Render grid ─────────────────────────────────────────────────────────

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
      const imgUrl = r.png_urls?.[0] || r.svg_urls?.[0] || "";
      const campaign = r.campaign ? `<div class="card-campaign">${esc(r.campaign)}</div>` : "";
      const tags = (r.tags || [])
        .map((t) => `<span class="tag">${esc(t)}</span>`)
        .join("");

      return `
      <div class="card" data-id="${esc(r.id)}">
        <img class="card-img" src="${esc(imgUrl)}" alt="${esc(r.headline)}" loading="lazy">
        <div class="card-body">
          <div class="card-title">${esc(r.headline)}</div>
          ${campaign}
          <div class="card-tags">${tags}</div>
        </div>
      </div>`;
    })
    .join("");

  // Attach click handlers
  grid.querySelectorAll(".card").forEach((card) => {
    card.addEventListener("click", () => {
      const record = records.find((r) => r.id === card.dataset.id);
      if (record) openLightbox(record);
    });
  });
}

// ── Lightbox ────────────────────────────────────────────────────────────

function openLightbox(record) {
  const lb = document.getElementById("lightbox");
  const img = document.getElementById("lightbox-img");
  const title = document.getElementById("lightbox-title");
  const details = document.getElementById("lightbox-details");
  const links = document.getElementById("lightbox-links");

  // Use PNG for display
  img.src = record.png_urls?.[0] || record.svg_urls?.[0] || "";
  title.textContent = record.headline;

  // Build details
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

  // Build download/link buttons
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
  document.body.style.overflow = "hidden";
}

function closeLightbox() {
  document.getElementById("lightbox").classList.add("hidden");
  document.body.style.overflow = "";
}

function onKeydown(e) {
  if (e.key === "Escape") closeLightbox();
}

// ── Helpers ─────────────────────────────────────────────────────────────

function esc(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ── Go ──────────────────────────────────────────────────────────────────

init();
