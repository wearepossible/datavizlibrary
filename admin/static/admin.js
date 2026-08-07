/*
 * Shared form behaviour for the admin tool.
 *
 * Used by both the add/edit form (form.html) and the batch upload wizard
 * (batch_item.html):
 *   - setupAutocomplete()    suggestions for comma-separated fields
 *   - setupCampaignSelect()  filter box + "add a new campaign" for the
 *                            campaign checkbox list
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

// Inject a combined filter/add box above a campaign checkbox list.
//
// The list only ever contains campaigns already used by some record, so
// this is also the only way to introduce a new one: type a name that
// doesn't exist and press Enter (or click Add) and it's added to the list,
// ticked, ready to be saved with the record.
//
// Enter is intercepted here.  Without that, a plain text input inside the
// form means pressing Enter submits the record — which is exactly what it
// used to do to anyone who typed a new campaign name and hit Enter.
function setupCampaignSelect(selectId) {
  const select = document.getElementById(selectId);
  if (!select) return;

  const toolbar = document.createElement('div');
  toolbar.className = 'multiselect-toolbar';

  const input = document.createElement('input');
  input.type = 'text';
  input.placeholder = 'Filter, or type a new campaign';
  input.className = 'multiselect-filter';

  const addButton = document.createElement('button');
  // type="button" matters: a bare <button> in a form defaults to submit
  addButton.type = 'button';
  addButton.className = 'multiselect-add';
  addButton.textContent = '+ Add';

  toolbar.appendChild(input);
  toolbar.appendChild(addButton);
  select.insertBefore(toolbar, select.firstChild);

  /** Find an existing checkbox for this campaign name, ignoring case. */
  function existingCheckbox(name) {
    return Array.from(select.querySelectorAll('input[name="campaign"]'))
      .find(cb => cb.value.toLowerCase() === name.trim().toLowerCase());
  }

  function applyFilter(query) {
    const q = query.toLowerCase();
    select.querySelectorAll('.checkbox-label').forEach(label => {
      label.style.display = label.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
  }

  /** Add a campaign to the list, ticked, at the top where it can be seen. */
  function addCampaign(name) {
    const label = document.createElement('label');
    label.className = 'checkbox-label';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.name = 'campaign';
    checkbox.value = name;
    checkbox.checked = true;

    const flag = document.createElement('span');
    flag.className = 'new-flag';
    flag.textContent = 'new';

    label.appendChild(checkbox);
    label.append(' ' + name + ' ');
    label.appendChild(flag);
    toolbar.insertAdjacentElement('afterend', label);
  }

  // Typing a name that already exists just ticks it, so Enter is safe to
  // lean on whether the campaign is new or not.
  function commit() {
    const name = input.value.trim();
    if (!name) return;

    const existing = existingCheckbox(name);
    if (existing) {
      existing.checked = true;
    } else {
      addCampaign(name);
    }

    input.value = '';
    applyFilter('');   // un-hide everything so the ticked box is visible
    input.focus();
  }

  input.addEventListener('input', function () {
    applyFilter(this.value);
  });

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();   // don't submit the record
      commit();
    }
  });

  addButton.addEventListener('click', commit);
}
