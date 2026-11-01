/**
 * VNG（视觉小说）对话模式前端模块。
 *
 * 职责：
 * - 管理候选面板的四种状态：input / loading / choices / error；
 * - 在候选态隐藏输入框、展示 3 张编号卡；点选后交给 onSendText 走正常用户消息；
 * - 「自由输入」收起候选并回到输入框；「退出」发送 /vng off；
 * - 收到回合 start 时中止在途请求并收起候选（触发器回合同样适用）。
 *
 * 不负责：模式持久化、Agent 通信、TTS/气泡 —— 这些留在 app.js 与后端。
 *
 * @module vng
 */

const DEFAULT_ENDPOINT = 'http://127.0.0.1:13900/faust/vng/choices';

function isEditableTarget(target) {
  if (!target || target.nodeType !== 1) return false;
  const tag = String(target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
  return !!target.isContentEditable;
}

export function initVng(opts = {}) {
  const endpoint = String(opts.endpoint || DEFAULT_ENDPOINT);
  const inputEl = opts.inputEl || document.getElementById('textChatInput');
  const barEl = opts.barEl || document.getElementById('textChatBar');
  const panelEl = opts.panelEl || document.getElementById('vngPanel');
  const choicesEl = opts.choicesEl || document.getElementById('vngChoices');
  const hintEl = opts.hintEl || document.getElementById('vngHintText');
  const rerollBtn = opts.rerollBtn || document.getElementById('vngRerollBtn');
  const freeBtn = opts.freeBtn || document.getElementById('vngFreeBtn');
  const exitBtn = opts.exitBtn || document.getElementById('vngExitBtn');
  const onSendText = typeof opts.onSendText === 'function' ? opts.onSendText : null;

  if (!panelEl || !choicesEl) {
    throw new Error('VNG: 缺少 #vngPanel / #vngChoices，无法初始化');
  }

  const defaultPlaceholder = inputEl ? String(inputEl.placeholder || '') : '';
  const vngPlaceholder = 'VNG 模式 · 说一句，之后会出现 3 个候选';

  let enabled = false;
  let suppressed = false;
  let phase = 'off'; // off | input | loading | choices | error
  let items = [];
  let abort = null;
  let requestSeq = 0;

  /* ── 小工具 ── */

  function setPlaceholder(text) {
    if (inputEl) inputEl.placeholder = String(text || '');
  }

  function setHint(text) {
    if (hintEl) hintEl.textContent = String(text || '');
  }

  function showCards(show) {
    if (barEl) barEl.classList.toggle('vng-cards', !!show);
  }

  function setPhase(next) {
    phase = next;
    const cards = next === 'loading' || next === 'choices' || next === 'error';
    showCards(cards);
    if (next === 'input' && enabled) {
      setHint('视觉小说模式 · 发送后生成候选');
    }
    if (rerollBtn) {
      rerollBtn.textContent = next === 'error' ? '重试' : '换一批';
      rerollBtn.disabled = next === 'loading';
    }
  }

  function clearChoices() {
    choicesEl.textContent = '';
  }

  function abortRequest() {
    requestSeq += 1;
    if (abort) {
      try { abort.abort(); } catch (_e) { /* ignore */ }
      abort = null;
    }
  }

  function hideCards() {
    items = [];
    clearChoices();
    showCards(false);
    setPhase('input');
  }

  /* ── 渲染 ── */

  function renderSkeleton() {
    clearChoices();
    for (let i = 0; i < 3; i += 1) {
      const card = document.createElement('div');
      card.className = 'vng-card vng-card-skeleton';
      const index = document.createElement('span');
      index.className = 'vng-card-index';
      index.textContent = String(i + 1);
      const text = document.createElement('span');
      text.className = 'vng-card-text';
      text.appendChild(document.createElement('span')).className = 'vng-skeleton-line';
      card.append(index, text);
      choicesEl.appendChild(card);
    }
  }

  function renderError(message) {
    clearChoices();
    const row = document.createElement('div');
    row.className = 'vng-error';
    row.textContent = String(message || '候选生成失败');
    choicesEl.appendChild(row);
  }

  function renderChoices() {
    clearChoices();
    items.forEach((item, i) => {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'vng-card' + (item.is_action ? ' is-action' : '');
      card.title = item.is_action ? '动作 / 沉默' : '台词';
      const index = document.createElement('span');
      index.className = 'vng-card-index';
      index.textContent = String(i + 1);
      const text = document.createElement('span');
      text.className = 'vng-card-text';
      text.textContent = String(item.text || '');
      if (item.is_action) {
        const tag = document.createElement('span');
        tag.className = 'vng-card-tag';
        tag.textContent = '动作';
        text.appendChild(tag);
      }
      card.append(index, text);
      card.addEventListener('click', () => select(i));
      choicesEl.appendChild(card);
    });
  }

  /* ── 请求候选 ── */

  async function requestChoices() {
    if (!enabled || suppressed) return;
    if (inputEl && String(inputEl.value || '').trim()) return; // 有草稿时不抢输入框
    abortRequest();
    const controller = new AbortController();
    abort = controller;
    const seq = ++requestSeq;
    items = [];
    setPhase('loading');
    renderSkeleton();
    setHint('生成候选中…');

    try {
      const resp = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
        signal: controller.signal,
      });
      const data = await resp.json().catch(() => ({}));
      if (seq !== requestSeq) return;
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      if (data.status === 'ok' && Array.isArray(data.items) && data.items.length) {
        items = data.items.slice(0, 3);
        setPhase('choices');
        renderChoices();
        setHint('选择一句，或自由输入');
        return;
      }
      if (data.status === 'disabled') {
        setMode(false);
        return;
      }
      if (data.status === 'blocked') {
        setPhase('error');
        renderError('直播模式下无法生成候选');
        setHint('直播模式下不可用');
        return;
      }
      if (data.status === 'stale') {
        setPhase('error');
        renderError('对话已更新，重新发送一句吧');
        setHint('对话已更新');
        return;
      }
      throw new Error(String(data.error || data.reason || '候选生成失败'));
    } catch (err) {
      if (controller.signal.aborted || seq !== requestSeq) return;
      console.warn('VNG choices failed', err);
      setPhase('error');
      renderError('候选生成失败，可重试或自由输入');
      setHint('候选生成失败');
    } finally {
      if (abort === controller) abort = null;
    }
  }

  /* ── 用户动作 ── */

  function select(index) {
    if (phase !== 'choices') return;
    const item = items[index];
    if (!item) return;
    const text = String(item.text || '');
    hideCards();
    if (!onSendText) return;
    try {
      const result = onSendText(text);
      if (result && typeof result.catch === 'function') {
        result.catch((err) => console.warn('VNG send failed', err));
      }
    } catch (err) {
      console.warn('VNG send failed', err);
    }
  }

  function showInput() {
    abortRequest();
    hideCards();
    if (inputEl) inputEl.focus();
  }

  function exit() {
    if (!enabled) return;
    abortRequest();
    hideCards();
    if (onSendText) {
      try {
        const result = onSendText('/vng off');
        if (result && typeof result.catch === 'function') {
          result.catch((err) => console.warn('VNG exit command failed', err));
        }
      } catch (err) {
        console.warn('VNG exit command failed', err);
      }
    }
  }

  function setMode(next) {
    const value = !!next;
    if (enabled === value) {
      if (value && !suppressed && phase === 'off') setPhase('input');
      return;
    }
    enabled = value;
    if (barEl) barEl.classList.toggle('vng-mode', enabled);
    if (!enabled) {
      abortRequest();
      items = [];
      clearChoices();
      showCards(false);
      setPhase('off');
      setPlaceholder(defaultPlaceholder);
      setHint('');
      return;
    }
    setPhase('input');
    setPlaceholder(vngPlaceholder);
    setHint('视觉小说模式 · 发送后生成候选');
  }

  function onTurnStart(_origin) {
    if (!enabled) return;
    abortRequest();
    hideCards();
    setPlaceholder(vngPlaceholder);
  }

  function onUserTurnDone() {
    if (!enabled || suppressed) return;
    if (inputEl && String(inputEl.value || '').trim()) return;
    requestChoices();
  }

  function onTurnAborted() {
    if (!enabled) return;
    abortRequest();
    hideCards();
  }

  function setSuppressed(value) {
    const next = !!value;
    if (suppressed === next) return;
    suppressed = next;
    if (suppressed) {
      abortRequest();
      items = [];
      clearChoices();
      showCards(false);
      setPhase('input');
    } else if (enabled) {
      setPhase('input');
    }
  }

  /* ── 事件绑定 ── */

  if (rerollBtn) rerollBtn.addEventListener('click', () => { void requestChoices(); });
  if (freeBtn) freeBtn.addEventListener('click', showInput);
  if (exitBtn) exitBtn.addEventListener('click', exit);

  document.addEventListener('keydown', (e) => {
    if (!enabled || phase !== 'choices') return;
    if (e.defaultPrevented || e.isComposing || e.ctrlKey || e.metaKey || e.altKey) return;
    if (isEditableTarget(e.target)) return;
    if (e.key !== '1' && e.key !== '2' && e.key !== '3') return;
    const index = Number(e.key) - 1;
    if (!items[index]) return;
    e.preventDefault();
    select(index);
  });

  setPhase('off');

  return {
    setMode,
    isEnabled: () => enabled,
    onTurnStart,
    onUserTurnDone,
    onTurnAborted,
    setSuppressed,
    requestChoices,
    showInput,
  };
}
