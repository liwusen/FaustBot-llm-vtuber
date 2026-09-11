// Toast 与启动更新检查模块 — 自包含全局 Toast 与启动时更新提示
// 用法: import { checkUpdateOnStartup } from './libs/toast.js';

// ── 全局 Toast ──
export function showFaustToast({ title, body, onClick }) {
  const container = document.getElementById('toastContainer');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = 'faust-toast';
  const content = document.createElement('div');
  content.className = 'faust-toast-content';
  if (title) {
    const t = document.createElement('div');
    t.className = 'faust-toast-title';
    t.textContent = title;
    content.append(t);
  }
  const b = document.createElement('div');
  b.className = 'faust-toast-body';
  b.textContent = body;
  content.append(b);
  const close = document.createElement('button');
  close.className = 'faust-toast-close';
  close.textContent = '×';
  close.addEventListener('click', (e) => { e.stopPropagation(); toast.remove(); });
  toast.append(content, close);
  if (onClick) {
    toast.addEventListener('click', () => { onClick(); toast.remove(); });
  }
  container.append(toast);
  setTimeout(() => toast.remove(), 15000);
}

// ── 启动时异步检查更新：仅 release_body 含 CRITICAL 时提示 ──
export async function checkUpdateOnStartup() {
  if (!window.api || typeof window.api.configRequest !== 'function') return;
  try {
    const data = await window.api.configRequest('POST', '/faust/update/check', {});
    if (!data || !data.has_update) return;
    if (!String(data.release_body || '').includes('CRITICAL')) return;
    // 同一个更新只提示一次：已提示过的 tag 持久化在 Electron 本地存储
    const shownTagKey = 'faust_update_toast_shown_tag';
    let shownTag = null;
    try { shownTag = localStorage.getItem(shownTagKey); } catch (e) { /* 存储不可用时按未提示处理 */ }
    if (shownTag && shownTag === data.latest_tag) return;
    const label = `新版本 ${data.latest_tag || ''}`.trim();
    showFaustToast({
      title: '需要更新',
      body: `${label} 包含重要变更 (CRITICAL)，点击打开 Configer 进行更新。`,
      onClick: () => { if (window.api.openConfigWindow) window.api.openConfigWindow(); },
    });
    if (typeof window.api.showNotification === 'function') {
      window.api.showNotification({
        title: 'FaustBot 需要更新',
        body: `${label} 包含重要变更 (CRITICAL)，请在 Configer 中更新。`,
      });
    }
    try { localStorage.setItem(shownTagKey, String(data.latest_tag || '')); } catch (e) { console.warn('persist shown update tag failed', e); }
  } catch (e) {
    console.warn('startup update check failed', e);
  }
}
