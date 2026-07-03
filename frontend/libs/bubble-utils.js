// 对话气泡渲染工具 — 纯函数，无外部依赖

export function formatResultBubbleText(source, text) {
  const raw = String(text || '').trim();
  if (!raw) return '';
  if (source === 'user') return `用户：${raw}`;
  if (source === 'error') return `!错误!:${raw}`;
  return `AI：${raw}`;
}

export function formatToolBubbleValue(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch (e) {
    return String(value);
  }
}

export function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function renderResultBubbleHtml(source, entries) {
  const blocks = [];
  const items = Array.isArray(entries) ? entries : [];
  let reasoningIdx = 0;
  for (const item of items) {
    if (!item || typeof item !== 'object') continue;
    if (item.type === 'reasoning') {
      const reasoningText = escapeHtml(item.text || '');
      const expandedAttr = item.expanded ? ' open' : '';
      blocks.push(
        '<section class="reasoning-card">' +
          '<details class="reasoning-details" data-r="' + reasoningIdx + '"' + expandedAttr + '>' +
            '<summary class="reasoning-summary">' +
              '<span class="reasoning-icon">&#x1F9E0;</span>' +
              '<span class="reasoning-title">思考过程</span>' +
              '<span class="reasoning-badge">' + reasoningText.length + ' 字</span>' +
            '</summary>' +
            '<div class="reasoning-body">' +
              '<div class="reasoning-content">' + reasoningText + '</div>' +
            '</div>' +
          '</details>' +
        '</section>'
      );
      reasoningIdx++;
      continue;
    }
    if (item.type === 'text') {
      const formatted = formatResultBubbleText(source, item.text || '');
      if (formatted) {
        blocks.push(`<div class="result-bubble-main">${escapeHtml(formatted)}</div>`);
      }
      continue;
    }
    if (item.type !== 'tool') continue;
    const toolName = escapeHtml(item.toolName ? item.toolName : '未知工具');
    const argsText = escapeHtml(formatToolBubbleValue(Object.prototype.hasOwnProperty.call(item, 'args') ? item.args : {}));
    const outputText = escapeHtml(formatToolBubbleValue(item.output ? item.output : ''));
    const expandedAttr = item.expanded ? ' open' : '';
    const stateText = item.done ? '已完成' : '调用中';
    const callIdAttr = escapeHtml(item.callId || `${toolName}-${blocks.length}`);
    blocks.push(
      `<section class="tool-call-card${item.done ? ' is-done' : ' is-running'}">` +
        `<div class="tool-call-divider" aria-hidden="true"></div>` +
        `<details class="tool-call-details" data-call-id="${callIdAttr}"${expandedAttr}>` +
          `<summary class="tool-call-summary">` +
            `<span class="tool-call-title">调用工具:${toolName}</span>` +
            `<span class="tool-call-status">${stateText}</span>` +
          `</summary>` +
          `<div class="tool-call-body">` +
            `<div class="tool-call-section-label">参数</div>` +
            `<pre class="tool-call-pre">${argsText || '(空)'}</pre>` +
            `<div class="tool-call-section-label">返回值</div>` +
            `<pre class="tool-call-pre">${outputText || (item.done ? '(空)' : '等待返回...')}</pre>` +
          `</div>` +
        `</details>` +
      `</section>`
    );
  }
  return blocks.join('');
}

export function cloneBubbleEntries(entries) {
  if (!Array.isArray(entries)) return [];
  return entries.map((item) => {
    if (!item || typeof item !== 'object') return null;
    if (item.type === 'text') {
      return { type: 'text', text: String(item.text || '') };
    }
    if (item.type === 'reasoning') {
      return { type: 'reasoning', text: String(item.text || ''), expanded: !!item.expanded };
    }
    if (item.type === 'tool') {
      return {
        type: 'tool',
        callId: String(item.callId || ''),
        toolName: String(item.toolName || '未知工具'),
        args: Object.prototype.hasOwnProperty.call(item, 'args') ? item.args : {},
        output: String(item.output || ''),
        done: !!item.done,
        expanded: !!item.expanded,
      };
    }
    return null;
  }).filter(Boolean);
}
