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

// 状态展示与排序的唯一来源：摘要栏和详情面板共用，避免两处各写一套中文映射。
const SUBAGENT_STATUS = {
  idle: { label: '空闲', tone: 'idle' },
  pending: { label: '排队中', tone: 'pending' },
  running: { label: '运行中', tone: 'running' },
  stopping: { label: '停止中', tone: 'stopping' },
  stopped: { label: '已停止', tone: 'stopped' },
  error: { label: '错误', tone: 'error' },
  unknown: { label: '未知', tone: 'idle' },
};

const SUBAGENT_STATUS_ORDER = {
  running: 0,
  pending: 1,
  stopping: 2,
  error: 3,
  idle: 4,
  stopped: 5,
  unknown: 6,
};

function normalizeSubagentStatus(raw) {
  return String(raw || '').trim().toLowerCase();
}

function getSubagentStatus(raw) {
  return SUBAGENT_STATUS[normalizeSubagentStatus(raw)] || { label: '未知', tone: 'idle' };
}

export function initSubagentPanel({ getBubbleProps, forceInteractive, statusEndpoint, deleteEndpoint }) {
  const subagentSummaryEl = document.getElementById('subagentSummary');
  const subagentPanel = document.getElementById('subagentPanel');
  const subagentPanelHeader = document.getElementById('subagentPanelHeader');
  const subagentPanelTitle = document.getElementById('subagentPanelTitle');
  const subagentPanelStatus = document.getElementById('subagentPanelStatus');
  const subagentPanelBody = document.getElementById('subagentPanelBody');
  const subagentRemoveBtn = document.getElementById('subagentRemoveBtn');

  let subagentStatuses = [];
  let subagentEventCache = {};
  let selectedSubagentName = '';
  // 当前渲染的气泡条目（与 AsrBubble 同一套结构），toggle 回写展开态用
  let subagentPanelEntries = [];
  // 展开态持久化：重渲染后按 callId / 序号恢复（事件缓存里没有 expanded 字段）
  const toolExpandedState = new Map();
  const reasoningExpandedState = new Map();
  const compactExpandedState = new Map();

  // 最近事件只用于摘要行的 title 提示：优先错误详情，其次把工具事件转成可读短句，
  // JSON 形式的状态事件做有限映射，避免把整段原始 JSON 塞进 tooltip。
  function formatSubagentEventSummary(item){
    if (!item) return '';
    const error = String(item.last_error || '').trim();
    if (error) return error;
    const raw = String(item.last_event_summary || '').trim();
    if (!raw) return '';
    if (raw.startsWith('tool_start:')) return '正在调用 ' + raw.slice('tool_start:'.length).trim();
    if (raw.startsWith('tool_result:')) return '已完成 ' + raw.slice('tool_result:'.length).trim();
    if (raw.startsWith('{')) {
      try {
        const event = JSON.parse(raw);
        const eventType = String((event && event.type) || '');
        if (eventType === 'queued') return '等待执行';
        if (eventType === 'input') return '收到主 Agent 消息';
        if (eventType === 'compact_start') return '正在压缩上下文';
        if (eventType === 'compact_done') return '上下文压缩完成';
        if (eventType === 'error') return '执行出错';
        if (eventType === 'stopping') return '正在停止';
        if (eventType === 'stopped') return '已停止';
        if (eventType === 'final_result') return '已产出最终结果';
        return '';
      } catch (e) {
        return '';
      }
    }
    return raw;
  }

  function setSubagentStatuses(items){
    subagentStatuses = Array.isArray(items) ? items.map((item)=> ({ ...item })) : [];
    renderSubagentSummary();
    if (!selectedSubagentName) return;
    const next = subagentStatuses.find((item)=> String(item.name || '') === selectedSubagentName);
    if (!next) {
      // 子代理已被移除：继续显示旧标题会误导，直接关掉浮窗。
      hideSubagentPanel();
      return;
    }
    // WS 推送的 subagents_summary 是轻量状态（不含 recent_events），
    // 直接用 next 渲染会把事件列表清空为"暂无事件"。
    // 优先合并事件缓存，保留已显示的流式事件。
    renderSubagentPanel({
      ...next,
      recent_events: subagentEventCache[selectedSubagentName] || next.recent_events || [],
    });
  }

  function renderSubagentSummary(){
    if (!subagentSummaryEl) return;
    // 用户可在布景台关闭 Subagents Summary 组件
    if (getBubbleProps().showSubagents === false) {
      subagentSummaryEl.style.display = 'none';
      subagentSummaryEl.innerHTML = '';
      return;
    }
    const visibleItems = Array.isArray(subagentStatuses) ? subagentStatuses : [];
    if (!visibleItems.length){
      subagentSummaryEl.style.display = 'none';
      subagentSummaryEl.innerHTML = '';
      // #asrBubble 的显隐由 asr-bubble.js 统一负责（它观察本节点的变化并重算可见性）
      return;
    }

    let activeCount = 0;
    let pendingCount = 0;
    let errorCount = 0;
    const orderedItems = visibleItems.map((item, index) => {
      const status = normalizeSubagentStatus(item && item.status);
      if (status === 'running' || status === 'stopping') activeCount++;
      if (status === 'pending') pendingCount++;
      if (status === 'error') errorCount++;
      return { item, index, status };
    }).sort((a, b) => {
      const rankA = Object.prototype.hasOwnProperty.call(SUBAGENT_STATUS_ORDER, a.status)
        ? SUBAGENT_STATUS_ORDER[a.status] : 99;
      const rankB = Object.prototype.hasOwnProperty.call(SUBAGENT_STATUS_ORDER, b.status)
        ? SUBAGENT_STATUS_ORDER[b.status] : 99;
      return rankA - rankB || a.index - b.index;
    });

    const countParts = [];
    if (activeCount) countParts.push(`<span class="is-active">${activeCount} 运行中</span>`);
    if (pendingCount) countParts.push(`<span class="is-pending">${pendingCount} 排队中</span>`);
    if (errorCount) countParts.push(`<span class="is-error">${errorCount} 异常</span>`);
    const countHtml = countParts.length
      ? countParts.join('<span class="subagent-summary-count-sep"> · </span>')
      : '<span>全部空闲</span>';

    subagentSummaryEl.style.display = 'flex';
    subagentSummaryEl.innerHTML =
      '<div class="subagent-summary-head">' +
        '<span class="subagent-summary-label">子代理</span>' +
        `<span class="subagent-summary-count">${countHtml}</span>` +
      '</div>' +
      '<div class="subagent-summary-list">' +
        orderedItems.map(({ item })=>{
          const name = escapeHtml(String(item.name || 'Unnamed'));
          const status = getSubagentStatus(item.status);
          const summary = formatSubagentEventSummary(item);
          const title = summary || status.label;
          return `<button type="button" class="subagent-summary-item" data-subagent-name="${name}" data-status="${status.tone}" title="${escapeHtml(title)}" aria-label="查看子代理 ${name}（${status.label}）">` +
            `<span class="subagent-summary-name">${name}</span>` +
            `<span class="subagent-status" data-status="${status.tone}"><i></i>${status.label}</span>` +
          '</button>';
        }).join('') +
      '</div>';
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
    if (eventType === 'queued') return { label: '排队中', body: String(messageContent), tone: 'warn' };
    if (eventType === 'input') return { label: '主 Agent 消息', body: String(messageContent), tone: 'info' };
    if (eventType === 'error') return { label: '错误', body: String(event.content || event.error || ''), tone: 'error' };
    if (eventType === 'stopping') return { label: '停止中', body: '已发送停止请求', tone: 'warn' };
    if (eventType === 'stopped') return { label: '已停止', body: '子代理已停止', tone: 'muted' };
    if (eventType === 'final_result') return { label: '最终结果', body: String(event.content || ''), tone: 'info' };
    const content = String(event.content || event.error || '').trim();
    return { label: '其他事件', body: content || '事件已记录', tone: 'info' };
  }

  // 条目正文：note 行为转义纯文本；其余条目与主 Agent 气泡共用折叠卡片渲染
  function subagentEntryInnerHtml(entry, index, reasoningIdx, compactIdx){
    if (entry && entry.type === 'note') {
      const tone = String(entry.tone || 'info');
      const toneClass = tone === 'error' ? ' is-error'
        : tone === 'warn' ? ' is-warn'
          : tone === 'muted' ? ' is-muted' : '';
      return '<div class="subagent-panel-note">' +
        `<div class="subagent-panel-event-type${toneClass}">${escapeHtml(String(entry.label || 'event'))}</div>` +
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

  function updateSubagentPanelHeader(name, status){
    if (subagentPanelTitle) subagentPanelTitle.textContent = name ? `子代理 · ${name}` : '子代理';
    if (subagentPanelStatus) {
      if (!name) {
        subagentPanelStatus.hidden = true;
      } else {
        subagentPanelStatus.hidden = false;
        subagentPanelStatus.dataset.status = status.tone;
        subagentPanelStatus.innerHTML = '<i></i>' + escapeHtml(status.label);
      }
    }
    if (subagentRemoveBtn) subagentRemoveBtn.disabled = !name;
  }

  function buildSubagentToolsetsHtml(toolsets){
    const list = Array.isArray(toolsets)
      ? toolsets.map((item)=> String(item || '').trim()).filter(Boolean)
      : [];
    if (!list.length) return '<span class="subagent-panel-empty-value">无</span>';
    return '<div class="subagent-toolset-list">' +
      list.map((item)=> `<span class="subagent-toolset">${escapeHtml(item)}</span>`).join('') +
    '</div>';
  }

  function buildSubagentPanelInfoHtml(item){
    const prompt = String(item.system_prompt_summary || '').trim();
    const error = String(item.last_error || '').trim();
    const countValue = Number(item.event_count);
    const eventCount = Number.isFinite(countValue)
      ? countValue
      : (Array.isArray(item.recent_events) ? item.recent_events.length : 0);
    const recentCount = Array.isArray(item.recent_events) ? item.recent_events.length : eventCount;
    const rows = [
      '<div class="subagent-panel-info">',
      '<div class="subagent-panel-meta">',
      '<div class="subagent-panel-meta-key">工具组</div>',
      `<div class="subagent-panel-meta-value">${buildSubagentToolsetsHtml(item.toolsets)}</div>`,
    ];
    if (prompt) {
      rows.push('<div class="subagent-panel-meta-key">系统提示</div>');
      rows.push(`<div class="subagent-panel-meta-value subagent-panel-prompt" title="${escapeHtml(prompt)}">${escapeHtml(prompt)}</div>`);
    }
    if (error) {
      rows.push('<div class="subagent-panel-meta-key is-error">错误</div>');
      rows.push(`<div class="subagent-panel-meta-value is-error">${escapeHtml(error)}</div>`);
    }
    rows.push('</div>');
    const countText = recentCount > 0 ? `最近 ${recentCount} 条` : '暂无';
    rows.push(
      '<div class="subagent-panel-events-head">' +
        '<span>执行记录</span>' +
        `<span class="subagent-panel-events-count">${escapeHtml(countText)}</span>` +
      '</div>'
    );
    rows.push('</div>');
    return rows.join('');
  }

  function applySubagentPanelDiff(item, entries){
    if (!subagentPanelBody) return;
    const infoHtml = buildSubagentPanelInfoHtml(item);
    let eventsEl = subagentPanelBody.querySelector('.subagent-panel-events');
    if (!eventsEl) {
      subagentPanelBody.innerHTML = infoHtml + '<div class="subagent-panel-events"></div>';
      eventsEl = subagentPanelBody.querySelector('.subagent-panel-events');
    } else {
      // 只替换信息容器（保留 events 容器与已渲染事件 DOM）
      const existingInfo = subagentPanelBody.querySelector('.subagent-panel-info');
      if (existingInfo) {
        existingInfo.outerHTML = infoHtml;
      } else {
        const legacyMeta = subagentPanelBody.querySelector('.subagent-panel-meta');
        if (legacyMeta) legacyMeta.remove();
        subagentPanelBody.insertAdjacentHTML('afterbegin', infoHtml);
      }
    }
    applySubagentEntriesDiff(entries, eventsEl);
    if (!entries.length) {
      eventsEl.innerHTML = '<div class="subagent-panel-empty">' +
        '<svg class="ui-icon" width="18" height="18" aria-hidden="true"><use href="#i-doc"/></svg>' +
        '<span>还没有执行记录</span>' +
      '</div>';
    }
  }

  function renderSubagentPanel(item){
    if (!subagentPanel || !subagentPanelBody || !item) return;
    selectedSubagentName = String(item.name || '');
    updateSubagentPanelHeader(selectedSubagentName, getSubagentStatus(item.status));
    subagentPanelEntries = normalizeSubagentPanelEntries(item.recent_events);
    subagentPanel.style.display = 'flex';
    applySubagentPanelDiff(item, subagentPanelEntries);
    forceInteractive();
  }

  function hideSubagentPanel(){
    selectedSubagentName = '';
    updateSubagentPanelHeader('', getSubagentStatus('idle'));
    if (subagentPanel) subagentPanel.style.display = 'none';
  }

  async function openSubagentPanelByName(name){
    const targetName = String(name || '').trim();
    if (!targetName) return;
    if (subagentEventCache[targetName] && subagentEventCache[targetName].length > 0) {
      renderSubagentPanelFromCache(targetName);
      return;
    }
    try {
      const r = await fetch(statusEndpoint);
      const j = await r.json();
      const items = Array.isArray(j.items) ? j.items : [];
      const target = items.find(item => String(item.name || '') === targetName);
      if (target) {
        subagentEventCache[targetName] = Array.isArray(target.recent_events) ? target.recent_events.map(e => ({...e})) : [];
        renderSubagentPanel(target);
      } else {
        renderSubagentPanel({ name: targetName, status: 'unknown', recent_events: [] });
      }
    } catch(e) {
      console.warn('openSubagentPanelByName failed', e);
      renderSubagentPanel({ name: targetName, status: 'unknown', recent_events: [] });
    }
  }

  async function removeSelectedSubagent(){
    const name = selectedSubagentName;
    if (!name) return;
    if (subagentRemoveBtn) subagentRemoveBtn.disabled = true;
    try {
      const r = await fetch(`${deleteEndpoint}/${encodeURIComponent(name)}`, { method: 'DELETE' });
      if (!r.ok){
        const txt = await r.text();
        throw new Error(txt || `HTTP ${r.status}`);
      }
      delete subagentEventCache[name];
      hideSubagentPanel();
      await refreshSubagentStatuses();
    } catch (e) {
      if (subagentRemoveBtn) subagentRemoveBtn.disabled = false;
      throw e;
    }
  }

  async function refreshSubagentStatuses(){
    try{
      const r = await fetch(statusEndpoint);
      const j = await r.json().catch(()=>({}));
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
    removeSelected: removeSelectedSubagent,
    refresh: refreshSubagentStatuses,
  };
}
