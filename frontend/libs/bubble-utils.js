// 对话气泡渲染工具 — 纯函数，无外部依赖

export function formatResultBubbleText(source, text) {
  const raw = String(text || '').trim();
  if (!raw) return '';
  if (source === 'user') return `用户：${raw}`;
  if (source === 'error') return `!错误!:${raw}`;
  return `${raw}`;
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

export function renderMarkdownHtml(text) {
  const fm = (typeof window !== 'undefined') ? window.FaustMarkdown : null;
  const raw = String(text || '');
  if (!fm || !fm.marked || !fm.DOMPurify) {
    return `<pre class="md-fallback">${escapeHtml(raw)}</pre>`;
  }
  const html = fm.marked.parse(raw, { async: false, gfm: true, breaks: true });
  return fm.DOMPurify.sanitize(html);
}

function summarizeToolCall(toolName, args) {
  const name = String(toolName || '').trim();
  const payload = args && typeof args === 'object' ? args : {};
  const pick = (...keys) => keys.map((key) => payload[key]).find((value) => value !== undefined && value !== null && value !== '');

  if (name === 'read') return `读取文件: ${String(pick('uri', 'path', 'file_path') || '-')}`;
  if (name === 'execute') return `执行命令: ${String(pick('code', 'command') || '-')}`;
  if (name === 'write') return `写入目标: ${String(pick('path', 'uri') || '-')}`;
  if (name === 'edit') return `编辑文件: ${String(pick('path', 'uri') || '-')}`;
  if (name === 'search') return `搜索内容: ${String(pick('pattern', 'query') || '-')}`;
  if (name === 'find') {
    const patterns = Array.isArray(payload.patterns) ? payload.patterns.join(', ') : (pick('patterns') || '-');
    return `查找文件: ${String(patterns)}`;
  }

  const keys = Object.keys(payload).slice(0, 2);
  if (!keys.length) return `工具调用:${name}`;
  const values = keys.map((key) => `${key}=${String(payload[key])}`);
  return `工具调用:${name}:${values.join(', ')}`;
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
        '<section class="thinking-card">' +
          '<details class="thinking-details" data-r="' + reasoningIdx + '"' + expandedAttr + '>' +
            '<summary class="thinking-summary">' +
              '<span class="thinking-arrow">&#9654;</span>' +
              '<span class="thinking-divider"></span>' +
              '<span class="thinking-word-count">思考:' + reasoningText.length + '字</span>' +
            '</summary>' +
            '<div class="thinking-body">' +
              '<div class="thinking-content">' + reasoningText + '</div>' +
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
    if (item.type === 'md') {
      blocks.push(`<div class="result-bubble-main md-block">${renderMarkdownHtml(item.text || '')}</div>`);
      continue;
    }
    if (item.type !== 'tool') continue;
    const toolName = escapeHtml(item.toolName ? item.toolName : '未知工具');
    const toolSummary = escapeHtml(summarizeToolCall(item.toolName, Object.prototype.hasOwnProperty.call(item, 'args') ? item.args : {}));
    const argsText = escapeHtml(formatToolBubbleValue(Object.prototype.hasOwnProperty.call(item, 'args') ? item.args : {}));
    const outputText = escapeHtml(formatToolBubbleValue(item.output ? item.output : ''));
    const expandedAttr = item.expanded ? ' open' : '';
    const stateText = item.done ? '完成' : '运行中';
    const callIdAttr = escapeHtml(item.callId || `${toolName}-${blocks.length}`);
    blocks.push(
      '<section class="thinking-card">' +
        '<details class="thinking-details" data-call-id="' + callIdAttr + '"' + expandedAttr + '>' +
          '<summary class="thinking-summary">' +
            '<span class="thinking-arrow">&#9654;</span>' +
            '<span class="thinking-divider"></span>' +
            '<span class="thinking-label">' + toolSummary + '</span>' +
            '<span class="thinking-status">' + stateText + '</span>' +
          '</summary>' +
          '<div class="thinking-body">' +
            '<div class="thinking-section-label">参数</div>' +
            '<pre class="thinking-pre">' + (argsText || '(空)') + '</pre>' +
            '<div class="thinking-section-label">返回值</div>' +
            '<pre class="thinking-pre">' + (outputText || (item.done ? '(空)' : '等待...')) + '</pre>' +
          '</div>' +
        '</details>' +
      '</section>'
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
    if (item.type === 'md') {
      return { type: 'md', text: String(item.text || '') };
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
