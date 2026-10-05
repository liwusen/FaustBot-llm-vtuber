/**
 * Chat composer: 多行自适应、附件(路径制)、剪贴板集成。
 * 由 app.js 调用 initChatComposer() 装配;单实例管理自身 DOM。
 *
 * 另负责输入栏的两个「状态可视化」，语义都集中在这里，避免 app.js 直接改 DOM：
 * - 发送键三态：空输入 / 发送中 → disabled 且淡蓝，发送中额外脉冲；
 * - 中部提示文字：默认提示可临时替换为「发送中…」「网络错误」，出错 4s 后自动还原。
 * @module chat-composer
 */

const MAX_ATTACHMENTS = 10;
const MAX_LINES = 6;
const LINE_HEIGHT = 24;
const CHROME_HEIGHT = 8; // textarea 上下 padding（4px × 2），文本区无边框
const CLIP_DEDUP_MS = 30000;
const ERROR_HINT_TTL_MS = 4000;

function isImagePath(p) {
  return /\.(png|jpe?g|gif|webp|bmp)$/i.test(String(p || ''));
}

export function initChatComposer(opts) {
  const {
    textarea,
    chipContainer,
    barElement,
    pickButton,
    sendButton,
    hintElement,
    getAutoAttachEnabled,
    onHeightChange,
    toast,
  } = opts;

  const attachments = []; // { path, isImage }
  const attachedHashes = new Set(); // 已附加图片的内容哈希(防重复)
  let lastAutoHash = null;          // 最近一次自动附加的图片哈希(30s 内不重复)
  let lastAutoAt = 0;
  const defaultHint = hintElement ? String(hintElement.textContent || '') : '';
  let sending = false;
  let hintTimer = null;

  /* ── autogrow ── */
  function autogrow() {
    textarea.style.height = 'auto';
    const maxH = MAX_LINES * LINE_HEIGHT + CHROME_HEIGHT;
    const next = Math.min(textarea.scrollHeight, maxH);
    textarea.style.height = next + 'px';
    textarea.style.overflowY = textarea.scrollHeight > maxH ? 'auto' : 'hidden';
    if (onHeightChange) onHeightChange();
  }

  /* ── 发送键 / 提示文字状态 ── */
  function syncSendButtonState() {
    if (sendButton) {
      const hasText = !!String(textarea.value || '').trim();
      sendButton.disabled = sending || !hasText;
    }
    if (barElement) barElement.classList.toggle('is-sending', sending);
  }

  function setSending(next) {
    sending = !!next;
    syncSendButtonState();
  }

  // 提示文字三态：
  // - setHint('发送中…', 'busy')：临时替换提示，取消未结束的错误态；
  // - setHint('网络错误', 'error')：红色，ERROR_HINT_TTL_MS 后自动还原默认提示；
  // - setHint(null)：还原默认提示，但**不覆盖尚在生存期内的错误提示**
  //   （否则发送失败后 finally 里的还原会立刻把错误抹掉）。
  function setHint(text, tone) {
    if (!hintElement) return;
    const isError = tone === 'error';
    const isBusy = tone === 'busy';
    if (!isError && !isBusy && hintTimer) return; // 错误提示生存期内不还原
    if (hintTimer) { clearTimeout(hintTimer); hintTimer = null; }
    hintElement.textContent = (text === null || text === undefined) ? defaultHint : String(text);
    if (barElement) barElement.classList.toggle('is-error', isError);
    if (isError) {
      hintTimer = setTimeout(() => {
        hintTimer = null;
        hintElement.textContent = defaultHint;
        if (barElement) barElement.classList.remove('is-error');
      }, ERROR_HINT_TTL_MS);
    }
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
        notify(`附件最多 ${MAX_ATTACHMENTS} 个`);
        break;
      }
      attachments.push({ path: p, isImage: isImagePath(p) });
      added++;
    }
    if (added) renderChips(highlight);
    return added;
  }

  function addImageAttachment(path, hash, { auto = false, highlight = true } = {}) {
    if (hash && attachedHashes.has(hash)) return false; // 同一张图不重复附加
    if (auto && hash && hash === lastAutoHash && Date.now() - lastAutoAt < CLIP_DEDUP_MS) return false;
    if (addAttachments([path], { highlight }) <= 0) return false;
    if (hash) attachedHashes.add(hash);
    if (auto) { lastAutoHash = hash; lastAutoAt = Date.now(); }
    return true;
  }

  /* ── clear ── */
  // 清空输入框与附件并收起自适应高度。只在消息提交时由 app.js 调用（见 clearTextChatInput）
  function clear() {
    textarea.value = '';
    attachments.length = 0;
    attachedHashes.clear();
    renderChips(false);
    autogrow();
    syncSendButtonState();
  }

  /* ── clipboard ── */
  async function readClipboardImageInfo() {
    if (!window.api || !window.api.readClipboardImage) return null;
    return await window.api.readClipboardImage().catch(() => null);
  }

  async function readClipboardFilePaths() {
    if (!window.api || !window.api.readClipboardFilePaths) return [];
    const paths = await window.api.readClipboardFilePaths().catch(() => []);
    return Array.isArray(paths) ? paths : [];
  }

  async function attachClipboardImage({ auto = false } = {}) {
    const img = await readClipboardImageInfo();
    if (!img || !img.path) return false;
    return addImageAttachment(img.path, img.hash, { auto });
  }

  async function attachClipboardFilePaths() {
    return addAttachments(await readClipboardFilePaths(), { highlight: true }) > 0;
  }

  // 剪贴板 → 附件 的统一入口：先试位图（截图 / 复制的图片位图），
  // 再试文件路径（资源管理器里 Ctrl+C 复制的文件，走 CF_HDROP）。
  // 「复制的图片文件」在剪贴板里只有 CF_HDROP、没有位图，必须靠第二条兜住，
  // 否则粘贴事件会被 preventDefault 吞掉却什么也不加。
  // 返回 true 也包含「本来就已经在附件里」——那不是失败，不该提示用户。
  async function attachFromClipboard({ auto = false } = {}) {
    const img = await readClipboardImageInfo();
    if (img && img.path) {
      if (addImageAttachment(img.path, img.hash, { auto })) return true;
      if (img.hash && attachedHashes.has(img.hash)) return true;
    }
    const paths = await readClipboardFilePaths();
    if (paths.length) {
      if (addAttachments(paths, { highlight: true }) > 0) return true;
      if (paths.every((p) => attachments.some((a) => a.path === p))) return true;
    }
    return false;
  }

  // 剪贴板里到底有什么：Chromium 对「复制的图片文件」也会报 image/*，
  // 因此判断依据是「有没有文件项」，而不是「是不是图片」。
  function readPastePayload(e) {
    const dt = e.clipboardData;
    const items = Array.from((dt && dt.items) || []);
    const fileCount = dt && dt.files ? dt.files.length : 0;
    const hasFile = fileCount > 0 || items.some((it) => it.kind === 'file');
    return { hasFile, hasText: !!(dt && String(dt.getData('text/plain') || '')) };
  }

  function isEditableTarget(target) {
    if (!target || target.nodeType !== 1) return false;
    if (target === textarea) return true;
    const tag = String(target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
    return !!target.isContentEditable;
  }

  // 用户可见的反馈：写输入栏里的提示文字（4s 后自动还原）。
  // 不用 toast() 是因为它在本项目里指向 #textChatStatus——该元素当前不存在，
  // 提示会完全静默，用户只会看到「按了没反应」。
  function notify(message) {
    setHint(message, 'error');
    if (toast) toast(message);
  }

  /* ── events ── */
  // 粘贴：图片位图与「复制的文件」都要变成附件；纯文本粘贴照旧交给浏览器默认行为。
  async function handlePasteEvent(e) {
    const { hasFile } = readPastePayload(e);
    if (!hasFile) return false; // 文本走默认粘贴
    e.preventDefault();
    const ok = await attachFromClipboard({ auto: false });
    if (!ok) notify('剪贴板里没有可加入的图片或文件');
    return ok;
  }

  textarea.addEventListener('paste', (e) => {
    handlePasteEvent(e).catch(() => { /* 静默：粘贴失败已有提示，不再抛 */ });
  });

  // 输入框没聚焦时按 Ctrl+V 也应能加附件（复制完截图/文件直接按 Ctrl+V 是最自然的用法）。
  // 只在「粘贴目标不可编辑」且「剪贴板里带文件」时接管：绝不抢别处的文本粘贴。
  document.addEventListener('paste', (e) => {
    if (e.target === textarea) return;                 // 交给上面的处理器
    if (e.defaultPrevented) return;
    if (isEditableTarget(e.target)) return;            // 别抢其它输入框
    if (!barElement || !barElement.isConnected) return;
    if (barElement.style.display === 'none') return;   // 输入栏隐藏（直播模式等）时不劫持
    const { hasFile } = readPastePayload(e);
    if (!hasFile) return;
    e.preventDefault();
    textarea.focus();
    handlePasteEvent(e).catch(() => { /* 同上 */ });
  });

  textarea.addEventListener('focus', async () => {
    try {
      if (typeof getAutoAttachEnabled === 'function' && !getAutoAttachEnabled()) return;
      await attachClipboardImage({ auto: true });
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

  textarea.addEventListener('input', () => {
    autogrow();
    syncSendButtonState();
  });
  autogrow();
  syncSendButtonState();

  return {
    getAttachments: () => attachments.slice(),
    addAttachments,
    clear,
    setSending,
    setHint,
    syncSendButtonState,
  };
}
