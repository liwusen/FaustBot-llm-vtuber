// Subagent 摘要栏 + 详情面板模块 — 承载 #subagentSummary 摘要栏与 #subagentPanel 详情面板。
// 用法: const subagent = initSubagentPanel({ getBubbleProps, forceInteractive, statusEndpoint, deleteEndpoint });
// 说明：状态列表、事件缓存、选中项全部内聚在本模块；
// 气泡属性与鼠标穿透控制器通过注入的 getter/回调读取，模块内不引用 app.js 闭包变量。

import { escapeHtml } from './bubble-utils.js';

export function initSubagentPanel({ getBubbleProps, forceInteractive, statusEndpoint, deleteEndpoint }) {
  const subagentSummaryEl = document.getElementById('subagentSummary');
  const subagentPanel = document.getElementById('subagentPanel');
  const subagentPanelHeader = document.getElementById('subagentPanelHeader');
  const subagentPanelTitle = document.getElementById('subagentPanelTitle');
  const subagentPanelBody = document.getElementById('subagentPanelBody');
  const asrBubbleEl = document.getElementById('asrBubble');
  const asrTextEl = document.getElementById('asrText');

  let subagentStatuses = [];
  let subagentEventCache = {};
  let selectedSubagentName = '';

  function formatSubagentEventSummary(item){
    if (!item) return '';
    return String(item.last_event_summary || item.last_error || '').trim();
  }

  function setSubagentStatuses(items){
    subagentStatuses = Array.isArray(items) ? items.map((item)=> ({ ...item })) : [];
    renderSubagentSummary();
    if (selectedSubagentName) {
      const next = subagentStatuses.find((item)=> String(item.name || '') === selectedSubagentName);
      if (next) {
        // WS 推送的 subagents_summary 是轻量状态（不含 recent_events），
        // 直接用 next 渲染会把事件列表清空为"暂无事件"。
        // 优先合并事件缓存，保留已显示的流式事件。
        renderSubagentPanel({
          ...next,
          recent_events: subagentEventCache[selectedSubagentName] || next.recent_events || [],
        });
      }
    }
  }

  function renderSubagentSummary(){
    if (!subagentSummaryEl) return;
    // 用户可在布景台关闭 Subagents Summary 组件
    if (getBubbleProps().showSubagents === false) {
      subagentSummaryEl.style.display = 'none';
      subagentSummaryEl.innerHTML = '';
      return;
    }
    const STATUS_CN= {
      idle: '空闲', pending: '排队中', running: '运行中',
      stopping: '停止中', stopped: '已停止', error: '错误',
    };
    const visibleItems = Array.isArray(subagentStatuses) ? subagentStatuses : [];
    if (!visibleItems.length){
      subagentSummaryEl.style.display = 'none';
      subagentSummaryEl.innerHTML = '';
      // 如果 asrText 也没有内容，隐藏整个气泡
      if (asrBubbleEl && asrBubbleEl.style.display !== 'none' && asrTextEl && !asrTextEl.textContent.trim()) {
        asrBubbleEl.style.display = 'none';
      }
      return;
    }
    subagentSummaryEl.style.display = 'flex';
    if (asrBubbleEl && asrBubbleEl.style.display === 'none'){
      asrBubbleEl.style.display = 'flex';
    }
    subagentSummaryEl.innerHTML = visibleItems.map((item)=>{
      const name = escapeHtml(String(item.name || 'Unnamed'));
      const rawStatus = String(item.status || 'unknown').trim().toLowerCase();
      const status = escapeHtml(STATUS_CN[rawStatus] || rawStatus);
      const title = escapeHtml(formatSubagentEventSummary(item));
      return `<div class="subagent-summary-item" data-subagent-name="${name}" title="${title}"><span class="subagent-summary-name">${name}:${status}</span></div>`;
    }).join('');
  }

  function renderSubagentPanelFromCache(name){
    const status = subagentStatuses.find(s => String(s.name || '') === name);
    const events = subagentEventCache[name] || [];
    if (status) {
      renderSubagentPanel({ ...status, recent_events: events });
    } else {
      // subagent 已不在状态列表（如已移除/尚未推送 summary），
      // 仍用缓存事件渲染，避免事件被静默丢弃。
      renderSubagentPanel({ name, status: 'unknown', recent_events: events });
    }
  }

  function normalizeSubagentPanelEvents(events){
    const source = Array.isArray(events) ? events : [];
    const normalized = [];
    for (const event of source){
      if (!event || typeof event !== 'object') continue;
      const eventType = String(event.type || '').trim();
      if (!eventType) continue;
      const last = normalized[normalized.length - 1];
      if ((eventType === 'reasoning_delta' || eventType === 'delta') && last && last.type === eventType) {
        last.content = String(last.content || '') + String(event.content || '');
        last.ts = event.ts;
        continue;
      }
      normalized.push({ ...event });
    }
    return normalized;
  }

  function formatSubagentPanelEvent(event){
    const eventType = String(event.type || '').trim();
    if (eventType === 'reasoning_delta') return { label: '思考', body: String(event.content || '') };
    if (eventType === 'delta') return { label: '输出', body: String(event.content || '') };
    if (eventType === 'tool_start') return { label: '调用工具', body: String(event.tool_name || '') };
    if (eventType === 'queued') {
      const content = (((event.message || {}).messages || [])[0] || {}).content || '';
      return { label: '排队中', body: String(content) };
    }
    if (eventType === 'input') {
      const content = (((event.message || {}).messages || [])[0] || {}).content || '';
      return { label: '主Agent消息', body: String(content) };
    }
    if (eventType === 'error') return { label: '错误', body: String(event.content || event.error || '') };
    if (eventType === 'stopping') return { label: '停止中', body: '已发送停止请求' };
    if (eventType === 'stopped') return { label: '已停止', body: 'Subagent 已停止' };
    return { label: eventType || 'event', body: typeof event === 'object' ? JSON.stringify(event, null, 2) : String(event || '') };
  }

  function subagentEventKey(events, index) {
    const event = events[index];
    const eventType = String((event && event.type) || 'event');
    // normalize 已在源头折叠相邻同类型事件（reasoning_delta/delta），索引即稳定 key
    return eventType + ':' + index;
  }

  function subagentEventHash(event) {
    if (!event || typeof event !== 'object') return 'null';
    const eventType = String(event.type || '');
    let body = '';
    if (eventType === 'reasoning_delta' || eventType === 'delta') body = String(event.content || '');
    else if (eventType === 'tool_start') body = String(event.tool_name || '');
    else if (eventType === 'queued' || eventType === 'input') body = JSON.stringify((((event.message || {}).messages || [])[0] || {}).content || '');
    else if (eventType === 'error') body = String(event.content || event.error || '');
    else body = JSON.stringify(event);
    return eventType + ':' + body;
  }

  function applySubagentPanelDiff(item, events){
    if (!subagentPanelBody) return;
    // ── meta 区全量刷新（字段少且低频） ──
    const metaHtml = [
      '<div class="subagent-panel-meta">',
      `<div class="subagent-panel-meta-key">状态</div><div class="subagent-panel-meta-value">${escapeHtml(String(item.status || 'unknown'))}</div>`,
      `<div class="subagent-panel-meta-key">工具组</div><div class="subagent-panel-meta-value">${escapeHtml((item.toolsets || []).join(', ') || '(none)')}</div>`,
      `<div class="subagent-panel-meta-key">Prompt</div><div class="subagent-panel-meta-value">${escapeHtml(String(item.system_prompt_summary || ''))}</div>`,
      `<div class="subagent-panel-meta-key">错误</div><div class="subagent-panel-meta-value">${escapeHtml(String(item.last_error || ''))}</div>`,
      '</div>',
    ].join('');
    let eventsEl = subagentPanelBody.querySelector('.subagent-panel-events');
    if (!eventsEl) {
      subagentPanelBody.innerHTML = metaHtml + '<div class="subagent-panel-events"></div>';
      eventsEl = subagentPanelBody.querySelector('.subagent-panel-events');
    } else {
      // 只替换 meta 容器（保留 events 容器与已渲染事件 DOM）
      const existingMeta = subagentPanelBody.querySelector('.subagent-panel-meta');
      if (existingMeta) existingMeta.outerHTML = metaHtml;
    }
    // ── events 区 diff 更新 ──
    const keys = events.map((e, i) => subagentEventKey(events, i));
    const hashes = events.map((e) => subagentEventHash(e));
    const children = Array.from(eventsEl.children);
    const childCountBefore = children.length;
    for (let i = 0; i < keys.length; i++) {
      const el = children[i];
      const key = keys[i];
      const hash = hashes[i];
      if (!el) {
        const node = document.createElement('div');
        node.className = 'subagent-panel-event';
        node.dataset.eventKey = key;
        node.dataset.eventHash = hash;
        const formatted = formatSubagentPanelEvent(events[i]);
        node.innerHTML = `<div class="subagent-panel-event-type">${escapeHtml(String(formatted.label || 'event'))}</div><div class="subagent-panel-event-body">${escapeHtml(String(formatted.body || ''))}</div>`;
        eventsEl.appendChild(node);
        continue;
      }
      if (el.dataset.eventKey !== key || el.dataset.eventHash !== hash) {
        const node = document.createElement('div');
        node.className = 'subagent-panel-event';
        node.dataset.eventKey = key;
        node.dataset.eventHash = hash;
        const formatted = formatSubagentPanelEvent(events[i]);
        node.innerHTML = `<div class="subagent-panel-event-type">${escapeHtml(String(formatted.label || 'event'))}</div><div class="subagent-panel-event-body">${escapeHtml(String(formatted.body || ''))}</div>`;
        el.replaceWith(node);
      }
    }
    for (let i = keys.length; i < children.length; i++) {
      children[i].remove();
    }
    if (!events.length) {
      eventsEl.innerHTML = '<div class="subagent-panel-event"><div class="subagent-panel-event-body">暂无事件</div></div>';
    }
    return childCountBefore !== keys.length;
  }

  function renderSubagentPanel(item){
    if (!subagentPanel || !subagentPanelBody || !item) return;
    selectedSubagentName = String(item.name || '');
    if (subagentPanelTitle) subagentPanelTitle.textContent = `Subagent: ${selectedSubagentName}`;
    const events = normalizeSubagentPanelEvents(item.recent_events);
    applySubagentPanelDiff(item, events);
    subagentPanel.style.display = 'flex';
    forceInteractive();
  }

  function hideSubagentPanel(){
    if (!subagentPanel) return;
    subagentPanel.style.display = 'none';
  }

  async function openSubagentPanelByName(name){
    if (subagentEventCache[name] && subagentEventCache[name].length > 0) {
      renderSubagentPanelFromCache(name);
      return;
    }
    try {
      const r = await fetch(statusEndpoint);
      const j = await r.json();
      console.info(":subagent status:",j)
      const items = Array.isArray(j.items) ? j.items : [];
      const target = items.find(item => String(item.name || '') === name);
      if (target) {
        subagentEventCache[name] = Array.isArray(target.recent_events) ? target.recent_events.map(e => ({...e})) : [];
        renderSubagentPanel(target);
      }
    } catch(e) {
      console.warn('openSubagentPanelByName failed', e);
    }
  }

  async function stopSelectedSubagent(){
    if (!selectedSubagentName) return;
    const r = await fetch(`${deleteEndpoint}/${encodeURIComponent(selectedSubagentName)}`, { method: 'DELETE' });
    if (!r.ok){
      const txt = await r.text();
      throw new Error(txt || `HTTP ${r.status}`);
    }
    await refreshSubagentStatuses();
    hideSubagentPanel();
  }

  async function refreshSubagentStatuses(){
    try{
      const r = await fetch(statusEndpoint);
      const j = await r.json().catch(()=>({}));
      console.log(":subagent status:",j)
      setSubagentStatuses(Array.isArray(j.items) ? j.items : []);
    }catch(e){
      console.warn('refreshSubagentStatuses failed', e);
    }
  }

  function initSubagentPanelDrag(){
    if (!subagentPanel || !subagentPanelHeader) return;
    let draggingPanel = false;
    let offsetX = 0;
    let offsetY = 0;

    const onMove = (ev)=>{
      if (!draggingPanel) return;
      subagentPanel.style.left = `${Math.max(8, ev.clientX - offsetX)}px`;
      subagentPanel.style.top = `${Math.max(8, ev.clientY - offsetY)}px`;
      subagentPanel.style.right = 'auto';
    };
    const onUp = ()=>{
      draggingPanel = false;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    subagentPanelHeader.addEventListener('mousedown', (ev)=>{
      if (ev.target && ev.target.closest('button')) return;
      const rect = subagentPanel.getBoundingClientRect();
      draggingPanel = true;
      offsetX = ev.clientX - rect.left;
      offsetY = ev.clientY - rect.top;
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    });
  }

  function appendEvent(name, msg){
    if (!subagentEventCache[name]) subagentEventCache[name] = [];
    const cached = subagentEventCache[name];
    const last = cached[cached.length - 1];
    if ((msg.type === 'reasoning_delta' || msg.type === 'delta') && last && last.type === msg.type) {
      last.content = (last.content || '') + (msg.content || '');
      last.ts = msg.ts || Date.now();
    } else {
      cached.push({ ...msg, ts: msg.ts || Date.now() });
    }
    if (cached.length > 500) cached.splice(0, cached.length - 500);
    if (selectedSubagentName === name) {
      renderSubagentPanelFromCache(name);
    }
  }

  function hideSummary(){
    if (subagentSummaryEl) subagentSummaryEl.style.display = 'none';
  }

  return {
    init: initSubagentPanelDrag,
    applySummary: setSubagentStatuses,
    appendEvent,
    renderSummary: renderSubagentSummary,
    renderFromCache: renderSubagentPanelFromCache,
    openByName: openSubagentPanelByName,
    hide: hideSubagentPanel,
    hideSummary,
    stopSelected: stopSelectedSubagent,
    refresh: refreshSubagentStatuses,
  };
}
