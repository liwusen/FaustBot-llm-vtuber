/**
 * Chat composer: 多行自适应、附件(路径制)、剪贴板集成。
 * 由 app.js 调用 initChatComposer() 装配;单实例管理自身 DOM。
 * @module chat-composer
 */

const MAX_ATTACHMENTS = 10;
const MAX_LINES = 6;
const LINE_HEIGHT = 24;
const CLIP_DEDUP_MS = 30000;

function isImagePath(p) {
  return /\.(png|jpe?g|gif|webp|bmp)$/i.test(String(p || ''));
}

export function initChatComposer(opts) {
  const {
    textarea,
    chipContainer,
    barElement,
    pickButton,
    getAutoAttachEnabled,
    onHeightChange,
    toast,
  } = opts;

  const attachments = []; // { path, isImage }
  let lastClipSignature = null;
  let lastClipAt = 0;

  /* ── autogrow ── */
  function autogrow() {
    textarea.style.height = 'auto';
    const maxH = MAX_LINES * LINE_HEIGHT;
    const next = Math.min(textarea.scrollHeight, maxH);
    textarea.style.height = next + 'px';
    textarea.style.overflowY = textarea.scrollHeight > maxH ? 'auto' : 'hidden';
    if (onHeightChange) onHeightChange();
  }

  /* ── chips ── */
  function renderChips(highlight) {
    chipContainer.textContent = '';
    attachments.forEach((a, i) => {
      const chip = document.createElement('span');
      chip.className = 'composer-chip' + (a.isImage ? ' is-image' : '');
      if (highlight && i === attachments.length - 1) chip.classList.add('just-added');
      const name = a.path.replace(/[\\/]+$/, '').split(/[\\/]/).pop();
      const label = document.createElement('span');
      label.className = 'composer-chip-label';
      label.textContent = (a.isImage ? '🖼 ' : '📄 ') + name;
      label.title = a.path;
      const x = document.createElement('span');
      x.className = 'composer-chip-x';
      x.textContent = '✕';
      x.addEventListener('click', () => {
        attachments.splice(i, 1);
        renderChips(false);
      });
      chip.append(label, x);
      chipContainer.appendChild(chip);
    });
    chipContainer.style.display = attachments.length ? 'flex' : 'none';
    if (onHeightChange) onHeightChange();
  }

  /* ── attachments ── */
  function addAttachments(paths, { highlight = false } = {}) {
    let added = 0;
    for (const raw of paths || []) {
      const p = String(raw || '').trim();
      if (!p) continue;
      if (attachments.some((a) => a.path === p)) continue;
      if (attachments.length >= MAX_ATTACHMENTS) {
        if (toast) toast(`附件最多 ${MAX_ATTACHMENTS} 个`);
        break;
      }
      attachments.push({ path: p, isImage: isImagePath(p) });
      added++;
    }
    if (added) renderChips(highlight);
    return added;
  }

  function clear() {
    attachments.length = 0;
    renderChips(false);
  }

  /* ── clipboard ── */
  async function attachClipboardImage() {
    if (!window.api || !window.api.readClipboardImage) return false;
    const img = await window.api.readClipboardImage().catch(() => null);
    if (!img || !img.path) return false;
    if (img.path === lastClipSignature && Date.now() - lastClipAt < CLIP_DEDUP_MS) return false;
    lastClipSignature = img.path;
    lastClipAt = Date.now();
    return addAttachments([img.path], { highlight: true }) > 0;
  }

  async function attachClipboardFilePaths() {
    if (!window.api || !window.api.readClipboardFilePaths) return false;
    const paths = await window.api.readClipboardFilePaths().catch(() => []);
    return addAttachments(paths, { highlight: true }) > 0;
  }

  /* ── events ── */
  textarea.addEventListener('paste', async (e) => {
    const items = Array.from((e.clipboardData && e.clipboardData.items) || []);
    const hasImage = items.some((it) => it.kind === 'file' && it.type.startsWith('image/'));
    if (!hasImage) return; // 文本走默认粘贴
    e.preventDefault();
    const ok = await attachClipboardImage();
    if (!ok && toast) toast('剪贴板中没有可用的图片');
  });

  textarea.addEventListener('focus', async () => {
    try {
      if (typeof getAutoAttachEnabled === 'function' && !getAutoAttachEnabled()) return;
      await attachClipboardImage();
      await attachClipboardFilePaths();
    } catch (e) { /* 静默:自动附加失败不打扰 */ }
  });

  if (pickButton) {
    pickButton.addEventListener('click', async () => {
      if (!window.api || !window.api.pickAttachments) return;
      const paths = await window.api.pickAttachments().catch(() => []);
      addAttachments(paths, { highlight: true });
      textarea.focus();
    });
  }

  if (barElement) {
    barElement.addEventListener('dragover', (e) => { e.preventDefault(); });
    barElement.addEventListener('drop', (e) => {
      e.preventDefault();
      const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
      addAttachments(files.map((f) => f.path).filter(Boolean), { highlight: true });
    });
  }

  textarea.addEventListener('input', autogrow);
  autogrow();

  return { getAttachments: () => attachments.slice(), addAttachments, clear };
}
