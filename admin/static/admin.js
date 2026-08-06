/*
 * Shared form behaviour for the admin tool.
 *
 * Used by both the add/edit form (form.html) and the batch upload wizard
 * (batch_item.html):
 *   - setupAutocomplete()    suggestions for comma-separated fields
 *   - setupCampaignFilter()  filter box for long campaign checkbox lists
 */

// Autocomplete for a comma-separated input (Tags, Cities).
// Suggestions match the text after the last comma, so several values can be
// entered in one field, and values already entered are excluded.
function setupAutocomplete(inputId, suggestionsId, options) {
  const input = document.getElementById(inputId);
  const container = document.getElementById(suggestionsId);
  if (!input || !container) return;

  input.addEventListener('input', function () {
    const value = this.value;
    const lastComma = value.lastIndexOf(',');
    const current = (lastComma >= 0 ? value.substring(lastComma + 1) : value).trim().toLowerCase();

    container.innerHTML = '';
    if (current.length < 1) return;

    const existing = value.substring(0, lastComma >= 0 ? lastComma : 0)
      .split(',').map(s => s.trim().toLowerCase()).filter(Boolean);

    const matches = options.filter(o =>
      o.toLowerCase().includes(current) && !existing.includes(o.toLowerCase())
    ).slice(0, 8);

    matches.forEach(m => {
      const div = document.createElement('div');
      div.className = 'suggestion';
      div.textContent = m;
      div.addEventListener('mousedown', function (e) {
        e.preventDefault(); // Keep focus on input
        const prefix = lastComma >= 0 ? value.substring(0, lastComma + 1) + ' ' : '';
        input.value = prefix + m + ', ';
        container.innerHTML = '';
        input.focus();
      });
      container.appendChild(div);
    });
  });

  // Hide suggestions on blur, after a delay so clicks register first
  input.addEventListener('blur', function () {
    setTimeout(() => container.innerHTML = '', 200);
  });
}

// Inject a filter box above a campaign checkbox list once it gets long
// enough that scanning it is slower than typing.
function setupCampaignFilter(selectId) {
  const select = document.getElementById(selectId);
  if (!select || select.children.length <= 10) return;

  const filter = document.createElement('input');
  filter.type = 'text';
  filter.placeholder = 'Filter campaigns...';
  filter.className = 'multiselect-filter';
  filter.addEventListener('input', function () {
    const q = this.value.toLowerCase();
    select.querySelectorAll('.checkbox-label').forEach(label => {
      label.style.display = label.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
  });
  select.insertBefore(filter, select.firstChild);
}
