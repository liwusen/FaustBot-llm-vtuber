// Subagent 摘要栏 + 详情面板模块 — 承载 #subagentSummary 摘要栏与 #subagentPanel 详情面板。
// 用法: const subagent = initSubagentPanel({ getBubbleProps, forceInteractive, statusEndpoint, deleteEndpoint });
// 说明：状态列表、事件缓存、选中项全部内聚在本模块；
// 气泡属性与鼠标穿透控制器通过注入的 getter/回调读取，模块内不引用 app.js 闭包变量。
//
// 事件渲染与主 Agent 的 AsrBubble 对齐：事件先归一化为气泡条目
// （reasoning / compact / tool / text），再复用 bubble-utils 的折叠卡片渲染，
// 工具调用因此折叠显示（点击展开参数与返回值），展开态在重渲染后保持。

import {
  escapeHtml,
  renderBubbleEntryHtml,
  patchBubbleEntryNode,
  entryKey,
  entryHash,
} from './bubble-utils.js';

export function initSubagentPanel({ getBubbleProps, forceInteractive, statusEndpoint, deleteEndpoint }) {
  const subagentSummaryEl = document.getElementById('subagentSummary');
  const subagentPanel = document.getElementById('subagentPanel');
  const subagentPanelHeader = document.getElementById('subagentPanelHeader');
  const subagentPanelTitle = document.getElementById('subagentPanelTitle');
  const subagentPanelBody = document.getElementById('subagentPanelBody');

  let subagentStatuses = [];
  let subagentEventCache = {};
  let selectedSubagentName = '';
  // 当前渲染的气泡条目（与 AsrBubble 同一套结构），toggle 回写展开态用
  let subagentPanelEntries = [];
  // 展开态持久化：重渲染后按 callId / 序号恢复（事件缓存里没有 expanded 字段）
  const toolExpandedState = new Map();
  const reasoningExpandedState = new Map();
  const compactExpandedState = new Map();

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
      // #asrBubble 的显隐由 asr-bubble.js 统一负责（它观察本节点的变化并重算可见性）
      return;
    }
    subagentSummaryEl.style.display = 'flex';
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

  // ── 事件 → 气泡条目归一化（与 AsrBubble 共用同一套 item 结构） ──
  // reasoning_delta/delta 折叠相邻同类；compact_start/delta/done 折叠为单个 compact；
  // tool_start + tool_result 按 call_id 合并成一张折叠卡片；其余事件退化为 note 行。
  function normalizeSubagentPanelEntries(events){
    const source = Array.isArray(events) ? events : [];
    const entries = [];
    const toolEntriesById = new Map();
    let reasoningIdx = 0;
    let compactIdx = 0;
    for (const event of source){
      if (!event || typeof event !== 'object') continue;
      const eventType = String(event.type || '').trim();
      if (!eventType) continue;
      const last = entries[entries.length - 1];
      if (eventType === 'reasoning_delta') {
        if (last && last.type === 'reasoning') {
          last.text = String(last.text || '') + String(event.content || '');
          continue;
        }
        entries.push({ type: 'reasoning', text: String(event.content || ''), expanded: reasoningExpandedState.get(reasoningIdx) === true });
        reasoningIdx++;
        continue;
      }
      if (eventType === 'delta') {
        if (last && last.type === 'text') {
          last.text = String(last.text || '') + String(event.content || '');
          continue;
        }
        entries.push({ type: 'text', text: String(event.content || '') });
        continue;
      }
      // 会话压缩：compact_start/compact_delta/compact_done 折叠成单个 compact 条目
      if (eventType === 'compact_start' || eventType === 'compact_delta' || eventType === 'compact_done') {
        if (eventType !== 'compact_start' && last && last.type === 'compact' && !last.done) {
          if (eventType === 'compact_delta') last.text = String(last.text || '') + String(event.content || '');
          else last.done = true;
          continue;
        }
        entries.push({ type: 'compact', text: String(event.content || ''), done: eventType === 'compact_done', expanded: compactExpandedState.get(compactIdx) === true });
        compactIdx++;
        continue;
      }
      if (eventType === 'tool_start') {
        const callId = String(event.call_id || '');
        const entry = {
          type: 'tool',
          callId,
          toolName: String(event.tool_name || '未知工具'),
          args: Object.prototype.hasOwnProperty.call(event, 'args') ? event.args : {},
          output: '',
          done: false,
          expanded: toolExpandedState.get(callId) === true,
        };
        toolEntriesById.set(callId || ('#' + entries.length), entry);
        entries.push(entry);
        continue;
      }
      if (eventType === 'tool_result') {
        const callId = String(event.call_id || '');
        const entry = toolEntriesById.get(callId);
        if (entry) {
          entry.output = String(event.output === undefined || event.output === null ? '' : event.output);
          entry.done = true;
          if (event.tool_name) entry.toolName = String(event.tool_name);
          continue;
        }
        // 没有配对的 tool_start（事件缓存被裁剪）：退化为一张已完成的工具卡片
        entries.push({
          type: 'tool',
          callId,
          toolName: String(event.tool_name || '未知工具'),
          args: {},
          output: String(event.output === undefined || event.output === null ? '' : event.output),
          done: true,
          expanded: toolExpandedState.get(callId) === true,
        });
        continue;
      }
      entries.push({ type: 'note', ...formatSubagentNote(event) });
    }
    return entries;
  }

  // 无法映射到气泡条目的状态类事件：仍以「标签 + 正文」的朴素行展示
  function formatSubagentNote(event){
    const eventType = String(event.type || '').trim();
    const messageContent = (((event.message || {}).messages || [])[0] || {}).content || '';
    if (eventType === 'queued') return { label: '排队中', body: String(messageContent) };
    if (eventType === 'input') return { label: '主Agent消息', body: String(messageContent) };
    if (eventType === 'error') return { label: '错误', body: String(event.content || event.error || '') };
    if (eventType === 'stopping') return { label: '停止中', body: '已发送停止请求' };
    if (eventType === 'stopped') return { label: '已停止', body: 'Subagent 已停止' };
    return { label: eventType || 'event', body: JSON.stringify(event, null, 2) };
  }

  // 条目正文：note 行为转义纯文本；其余条目与主 Agent 气泡共用折叠卡片渲染
  function subagentEntryInnerHtml(entry, index, reasoningIdx, compactIdx){
    if (entry && entry.type === 'note') {
      return '<div class="subagent-panel-note">' +
        `<div class="subagent-panel-event-type">${escapeHtml(String(entry.label || 'event'))}</div>` +
        `<div class="subagent-panel-event-body">${escapeHtml(String(entry.body || ''))}</div>` +
        '</div>';
    }
    return renderBubbleEntryHtml('ai', entry, index, reasoningIdx, compactIdx);
  }

  function subagentEntryHash(entry){
    // note 条目不进 bubble-utils 的 hash 规则（其 pick 为空会恒等）
    if (entry && entry.type === 'note') return 'note:' + String(entry.label || '') + '\u0001' + String(entry.body || '');
    return entryHash(entry, 'ai');
  }

  function buildSubagentEntryNode(key, hash, entry, index, reasoningIdx, compactIdx){
    const node = document.createElement('div');
    node.className = 'subagent-panel-event';
    node.dataset.entryKey = key;
    node.dataset.entryHash = hash;
    node.innerHTML = subagentEntryInnerHtml(entry, index, reasoningIdx, compactIdx);
    return node;
  }

  // 与 asr-bubble 的 applyBubbleEntriesDiff 同构：同键就地打补丁（保留 <details> 与 open 状态）
  function applySubagentEntriesDiff(entries, eventsEl){
    const children = Array.from(eventsEl.children);
    let reasoningIdx = 0;
    let compactIdx = 0;
    for (let i = 0; i < entries.length; i++) {
      const entry = entries[i];
      const key = entryKey('ai', entry, i);
      const hash = subagentEntryHash(entry);
      const isReasoning = !!(entry && entry.type === 'reasoning');
      const entryReasoningIdx = isReasoning ? reasoningIdx++ : reasoningIdx;
      const isCompact = !!(entry && entry.type === 'compact');
      const entryCompactIdx = isCompact ? compactIdx++ : compactIdx;
      let el = children[i];
      if (el && el.dataset.entryKey !== key) {
        for (let j = i; j < children.length; j++) children[j].remove();
        children.length = i;
        el = undefined;
      }
      if (!el) {
        el = buildSubagentEntryNode(key, hash, entry, i, entryReasoningIdx, entryCompactIdx);
        eventsEl.appendChild(el);
        children.push(el);
        continue;
      }
      if (el.dataset.entryHash === hash) continue;
      if (!patchBubbleEntryNode(el, 'ai', entry, i, entryReasoningIdx, entryCompactIdx)) {
        const node = buildSubagentEntryNode(key, hash, entry, i, entryReasoningIdx, entryCompactIdx);
        el.replaceWith(node);
        el = node;
        children[i] = node;
      }
      el.dataset.entryHash = hash;
    }
    for (let i = entries.length; i < children.length; i++) children[i].remove();
  }

  // 展开/收起后写回展开态：否则下一次 patch 会把 open 折回 false
  function handleSubagentPanelToggle(ev){
    const details = ev.target;
    if (!details || !details.classList || !details.dataset) return;
    const dataset = details.dataset;
    if (dataset.callId) {
      const callId = String(dataset.callId);
      toolExpandedState.set(callId, !!details.open);
      for (const entry of subagentPanelEntries) {
        if (entry && entry.type === 'tool' && String(entry.callId || '') === callId) { entry.expanded = details.open; break; }
      }
      return;
    }
    if (dataset.c !== undefined) {
      const cIdx = parseInt(dataset.c, 10);
      if (isNaN(cIdx)) return;
      compactExpandedState.set(cIdx, !!details.open);
      let count = -1;
      for (const entry of subagentPanelEntries) {
        if (entry && entry.type === 'compact') {
          count++;
          if (count === cIdx) { entry.expanded = details.open; break; }
        }
      }
      return;
    }
    if (dataset.r !== undefined) {
      const rIdx = parseInt(dataset.r, 10);
      if (isNaN(rIdx)) return;
      reasoningExpandedState.set(rIdx, !!details.open);
      let count = -1;
      for (const entry of subagentPanelEntries) {
        if (entry && entry.type === 'reasoning') {
          count++;
          if (count === rIdx) { entry.expanded = details.open; break; }
        }
      }
    }
  }

  function applySubagentPanelDiff(item, entries){
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
    applySubagentEntriesDiff(entries, eventsEl);
    if (!entries.length) {
      eventsEl.innerHTML = '<div class="subagent-panel-event"><div class="subagent-panel-note"><div class="subagent-panel-event-body">暂无事件</div></div></div>';
    }
  }

  function renderSubagentPanel(item){
    if (!subagentPanel || !subagentPanelBody || !item) return;
    selectedSubagentName = String(item.name || '');
    if (subagentPanelTitle) subagentPanelTitle.textContent = `Subagent: ${selectedSubagentName}`;
    subagentPanelEntries = normalizeSubagentPanelEntries(item.recent_events);
    applySubagentPanelDiff(item, subagentPanelEntries);
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
    if ((msg.type === 'reasoning_delta' || msg.type === 'delta' || msg.type === 'compact_delta') && last && last.type === msg.type) {
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

  // 折叠卡片展开/收起（事件委托，卡片是流式重建的，不能逐节点绑定）
  if (subagentPanelBody) {
    subagentPanelBody.addEventListener('toggle', handleSubagentPanelToggle, true);
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
