/**
 * Slash-command autocomplete module.
 *
 * Provides `/skill:` autocomplete for the chat input. Instantiated by
 * `initAutocomplete(inputEl, onEnter)` — the caller supplies the
 * `<input>`/`<textarea>` element and a function to call when Enter is
 * pressed without an active dropdown (typically `sendTextChatMessage`).
 *
 * This module is a singleton — it manages its own dropdown DOM. There is
 * no need to call it more than once per input.
 *
 * @module autocomplete
 */

const AUTOCOMPLETE_ENDPOINT = 'http://127.0.0.1:13900/faust/autocomplete';

let acDropdown = null;
let acItems = [];
let acIndex = -1;
let acPending = null;

/* ── helpers ───────────────────────────────────────────────────── */

function acRemoveDropdown() {
  if (acDropdown) { acDropdown.remove(); acDropdown = null; }
  acItems = [];
  acIndex = -1;
}

async function acFetch(text, cursor) {
  try {
    const resp = await fetch(AUTOCOMPLETE_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, cursor }),
    });
    if (!resp.ok) return [];
    const data = await resp.json();
    return data.items || [];
  } catch { return []; }
}

function acTrigger(inputEl) {
  clearTimeout(acPending);
  acPending = setTimeout(async () => {
    acPending = null;
    const items = await acFetch(inputEl.value, inputEl.selectionStart || inputEl.value.length);
    acRender(items, inputEl);
  }, 200);
}

function acRender(items, inputEl) {
  acRemoveDropdown();
  if (!items.length || !inputEl) return;
  acItems = items;
  acIndex = -1;
  const rect = inputEl.getBoundingClientRect();
  const width = Math.min(390, Math.max(280, rect.width));
  acDropdown = document.createElement('div');
  acDropdown.className = 'autocomplete-dropdown';
  acDropdown.style.width = width + 'px';
  acDropdown.style.left = Math.round(Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))) + 'px';
  acDropdown.style.top = Math.round(rect.bottom + 4) + 'px';

  const title = document.createElement('div');
  title.className = 'ac-title';
  title.textContent = '命令补全';
  acDropdown.append(title);

  items.forEach((item, i) => {
    const div = document.createElement('div');
    div.className = 'ac-item';
    div.dataset.index = i;
    const labelSpan = document.createElement('span');
    labelSpan.className = 'ac-key';
    labelSpan.textContent = item.label;
    div.appendChild(labelSpan);
    if (item.detail) {
      const detailSpan = document.createElement('span');
      detailSpan.className = 'ac-desc';
      detailSpan.textContent = item.detail;
      div.appendChild(detailSpan);
    }
    div.addEventListener('click', () => acSelect(i, inputEl));
    div.addEventListener('mousedown', (e) => e.preventDefault());
    acDropdown.appendChild(div);
  });

  document.body.appendChild(acDropdown);

  // 输入栏贴近窗口底部时向上弹，避免下拉被裁掉
  const height = acDropdown.offsetHeight;
  const spaceBelow = window.innerHeight - rect.bottom - 8;
  if (height > spaceBelow && rect.top > height + 8) {
    acDropdown.style.top = Math.round(rect.top - height - 6) + 'px';
  }

  acHighlight(0);
}

function acHighlight(idx) {
  if (!acDropdown) return;
  const items = acDropdown.querySelectorAll('.ac-item');
  items.forEach((el, i) => {
    el.classList.toggle('is-on', i === idx);
  });
  acIndex = idx;
  if (idx >= 0 && items[idx]) items[idx].scrollIntoView({ block: 'nearest' });
}

function acSelect(idx, inputEl) {
  if (idx < 0 || idx >= acItems.length || !inputEl) return;
  const item = acItems[idx];
  inputEl.value = item.insert_text;
  acRemoveDropdown();
  inputEl.focus();
  const len = item.insert_text.length;
  inputEl.setSelectionRange(len, len);
}

/* ── public API ────────────────────────────────────────────────── */

/**
 * Enable slash-command autocomplete on an input element.
 *
 * @param {HTMLInputElement|HTMLTextAreaElement|null} inputEl
 * @param {() => void} [onEnter]  Called when Enter is pressed and no
 *   dropdown is active.  Typically `sendTextChatMessage`.
 */
export function initAutocomplete(inputEl, onEnter) {
  if (!inputEl) return;

  // ── input event: trigger autocomplete when value starts with / ──
  inputEl.addEventListener('input', () => {
    const val = inputEl.value;
    if (!val.startsWith('/')) { acRemoveDropdown(); return; }
    acTrigger(inputEl);
  });

  // ── keydown: dropdown navigation and Enter send ──
  inputEl.addEventListener('keydown', (e) => {
    if (e.isComposing) return;
    if (acDropdown) {
      if (e.key === 'ArrowDown') { e.preventDefault(); acHighlight(Math.min(acIndex + 1, acItems.length - 1)); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); acHighlight(Math.max(acIndex - 1, 0)); return; }
      if (e.key === 'Tab') {
        e.preventDefault();
        acSelect(acIndex >= 0 ? acIndex : 0, inputEl);
        return;
      }
      if (e.key === 'Enter') {
        if (acIndex >= 0 && acIndex < acItems.length) {
          e.preventDefault();
          acSelect(acIndex, inputEl);
          return;
        }
      }
      if (e.key === 'Escape') { acRemoveDropdown(); return; }
    }
    if (e.key === 'Tab' && (inputEl.value || '').startsWith('/')) {
      e.preventDefault();
      acTrigger(inputEl);
      return;
    }
    // Enter send (dropdown hidden or no selection)
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (typeof onEnter === 'function') onEnter();
    }
  });

  // ── blur: dismiss dropdown ──
  inputEl.addEventListener('blur', () => {
    setTimeout(acRemoveDropdown, 200);
  });
}
