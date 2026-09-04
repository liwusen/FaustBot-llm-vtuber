(function () {
  const api = window.pluginUI;
  if (!api || typeof api.addPage !== 'function') return;

  const PLUGIN_ID = 'dev-debugger';

  let toolsCache = null;
  let selectedName = '';
  let lastResult = '';

  // 调用全局后端 API（debugging 路由）。优先 window.api.configRequest（Electron 通道），
  // 回退到全局 cfgApi（configer 提供），最后走原生 fetch。
  function apiGet(path) {
    if (window.api && typeof window.api.configRequest === 'function') {
      return window.api.configRequest('GET', path);
    }
    if (typeof cfgApi === 'function') {
      return cfgApi('GET', path);
    }
    const base = (window.api && window.api.backendBaseUrl) || api.backendBaseUrl || 'http://127.0.0.1:13900';
    return fetch(base + path).then((r) => r.json());
  }

  // ── UI helpers ──
  function el(tag, className, text) {
    const n = document.createElement(tag);
    if (className) n.className = className;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function typeIsBool(t) { return (t || '').toLowerCase() === 'bool' || (t || '').toLowerCase() === 'boolean'; }
  function typeIsNumber(t) { return ['int', 'float', 'number'].includes((t || '').toLowerCase()); }
  function typeIsList(t) { return (t || '').toLowerCase().startsWith('list') || /^array/.test((t || '').toLowerCase()) || /\[/.test(t || ''); }

  function defaultValueFor(field) {
    if (field.default !== null && field.default !== undefined) return field.default;
    const t = (field.type || 'str').toLowerCase();
    if (typeIsBool(t)) return false;
    if (typeIsNumber(t)) return 0;
    if (typeIsList(t)) return [];
    return '';
  }

  // 把参数值从字符串转回合适类型（用于 invoke）
  function coerceValue(field, rawValue) {
    const t = (field.type || 'str').toLowerCase();
    if (typeIsBool(t)) {
      if (rawValue === true || rawValue === 'true' || rawValue === 'on') return true;
      if (rawValue === false || rawValue === 'false') return false;
      return !!rawValue;
    }
    if (typeIsNumber(t)) {
      const n = Number(rawValue);
      return Number.isNaN(n) ? 0 : n;
    }
    if (typeIsList(t)) {
      if (Array.isArray(rawValue)) return rawValue;
      try { const parsed = JSON.parse(rawValue); if (Array.isArray(parsed)) return parsed; } catch (e) {}
      return String(rawValue || '').split(',').map(s => s.trim()).filter(Boolean);
    }
    return String(rawValue == null ? '' : rawValue);
  }

  // ── Schema-driven form builder ──
  function buildForm(schemaFields, container) {
    container.innerHTML = '';
    const valueFns = {}; // name -> () => value
    if (!schemaFields || !schemaFields.length) {
      container.appendChild(el('div', 'card-help', '该工具无可配置参数。'));
      return valueFns;
    }
    schemaFields.forEach((field) => {
      const wrap = document.createElement('label');
      wrap.style.cssText = 'display:flex;align-items:center;gap:10px;margin-bottom:8px;';
      const label = el('span', 'card-key');
      label.textContent = field.name + ' (' + (field.type || 'str') + (field.required ? ')' : ', 可选)');
      label.style.flex = '0 0 auto';
      wrap.appendChild(label);

      const t = (field.type || 'str').toLowerCase();
      let input;
      if (typeIsBool(t)) {
        input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = !!defaultValueFor(field);
        valueFns[field.name] = (cd) => coerceValue(field, input.checked);
      } else if (typeIsList(t)) {
        input = document.createElement('input');
        input.type = 'text';
        input.className = 'input';
        input.style.flex = '1';
        input.placeholder = 'JSON 数组，如 ["a","b"]';
        input.value = JSON.stringify(defaultValueFor(field));
        valueFns[field.name] = () => coerceValue(field, input.value);
      } else if (typeIsNumber(t)) {
        input = document.createElement('input');
        input.type = 'number';
        input.step = 'any';
        input.className = 'input';
        input.style.flex = '1';
        input.value = String(defaultValueFor(field));
        valueFns[field.name] = () => coerceValue(field, input.value);
      } else {
        input = document.createElement('input');
        input.type = 'text';
        input.className = 'input';
        input.style.flex = '1';
        input.value = String(defaultValueFor(field) || '');
        valueFns[field.name] = () => coerceValue(field, input.value);
      }
      if (input && typeof input.addEventListener === 'function') {
        input.setAttribute('data-debug-field', field.name);
      }
      wrap.appendChild(input);
      container.appendChild(wrap);
    });
    return valueFns;
  }

  function renderToolForm(container, valueFnsRef) {
    container.innerHTML = '';
    const tool = toolsCache && toolsCache.find(t => t.name === selectedName);
    if (!tool) {
      container.appendChild(el('div', 'card-help', '请从左侧选择一个工具。'));
      return;
    }
    const title = el('h3', 'card-title', '调用工具: ' + tool.name);
    const desc = el('div', 'card-help', tool.description || '');
    desc.style.whiteSpace = 'pre-wrap';
    const formHost = el('div');
    const valueFns = buildForm(tool.schema, formHost);

    const resultBox = el('pre', 'textarea code-area');
    resultBox.style.minHeight = '120px';
    resultBox.style.whiteSpace = 'pre-wrap';
    resultBox.textContent = lastResult || '(尚未执行)';

    const runBtn = el('button', 'btn btn-primary', 'Invoke');
    runBtn.addEventListener('click', async () => {
      const args = {};
      Object.keys(valueFns).forEach((k) => { args[k] = valueFns[k](); });
      runBtn.textContent = '…';
      runBtn.disabled = true;
      try {
        const resp = await api.communicate(PLUGIN_ID, { action: 'invoke_tool', name: selectedName, args });
        // 直接以原始文本展示结果（保留真实换行），避免 JSON 序列化把换行转义成 \n 误导
        if (resp && resp.status === 'ok') {
          lastResult = String(resp.result == null ? '' : resp.result);
        } else {
          lastResult = '调用失败: ' + (resp && resp.detail != null ? resp.detail : '未知错误');
        }
      } catch (e) {
        lastResult = '调用异常: ' + (e && e.message ? e.message : String(e));
      }
      resultBox.textContent = lastResult;
      runBtn.textContent = 'Invoke';
      runBtn.disabled = false;
    });

    const actions = el('div', 'toolbar');
    actions.appendChild(runBtn);
    container.append(title, desc, formHost, actions, resultBox);
  }

  function render(container) {
    container.innerHTML = '';
    const root = el('div');
    const queueHost = el('div');
    const listHost = el('div');
    const detailHost = el('div');

    // ── 触发器队列（全局 debugging API，见 routes/debugging.py） ──
    let queueTimer = null;

    function renderQueueRow(item, idx) {
      const row = el('tr');
      row.append(el('td', 'cell-primary', String(item.id || '-')));
      row.append(el('td', '', String(item.type || '-')));
      const extra = { ...item };
      delete extra.id;
      delete extra.type;
      const keys = Object.keys(extra);
      const summary = keys.length
        ? keys.map((k) => {
            let v = extra[k];
            if (v === null || v === undefined) return k + ': -';
            if (typeof v === 'object') v = JSON.stringify(v);
            return k + ': ' + String(v);
          }).join(' · ')
        : '-';
      const sumCell = el('td', '');
      sumCell.style.cssText = 'font-family:monospace;font-size:12px;word-break:break-all;';
      sumCell.textContent = summary;
      row.append(sumCell);
      row.append(el('td', '', String(item.run_background ? '后台' : '前台')));
      row.append(el('td', '', String(idx + 1)));
      return row;
    }

    async function refreshQueue() {
      const countLine = queueCard.querySelector('[data-queue-count]');
      const tableBody = queueCard.querySelector('[data-queue-body]');
      const statusLine = queueCard.querySelector('[data-queue-status]');
      try {
        const resp = await apiGet('/faust/debugging/trigger-queue');
        const items = (resp && Array.isArray(resp.triggers)) ? resp.triggers : [];
        const size = resp && typeof resp.queue_size === 'number' ? resp.queue_size : items.length;
        if (countLine) countLine.textContent = '队列长度: ' + size + '（等待 Agent 消费的触发任务）';
        if (statusLine) statusLine.textContent = '';
        if (tableBody) {
          tableBody.innerHTML = '';
          if (!items.length) {
            const empty = el('tr', '');
            const td = el('td', 'table-empty', '触发器队列为空');
            td.colSpan = 5;
            empty.append(td);
            tableBody.append(empty);
          } else {
            items.forEach((item, i) => tableBody.append(renderQueueRow(item, i)));
          }
        }
      } catch (e) {
        if (statusLine) statusLine.textContent = '获取失败: ' + (e && e.message ? e.message : String(e));
        if (countLine) countLine.textContent = '队列长度: -';
      }
    }

    function startQueuePolling() {
      refreshQueue();
      queueTimer = setInterval(refreshQueue, 3000);
    }

    function stopQueuePolling() {
      if (queueTimer) { clearInterval(queueTimer); queueTimer = null; }
    }

    // ── 工具列表 ──
    async function loadList() {
      listHost.innerHTML = '';
      const resp = await api.communicate(PLUGIN_ID, { action: 'list_tools' });
      if (!resp || resp.status !== 'ok') {
        listHost.appendChild(el('div', 'card-help', '获取工具列表失败: ' + (resp && resp.detail ? resp.detail : '未知')));
        return;
      }
      toolsCache = resp.tools || [];
      const countLine = el('div', 'card-help', '共 ' + (resp.count || toolsCache.length) + ' 个工具');
      listHost.appendChild(countLine);
      const list = el('div');
      toolsCache.forEach((tool) => {
        const row = el('div', 'debug-tool-row');
        const b = el('button', 'btn btn-ghost' + (tool.name === selectedName ? ' debug-active' : ' '), tool.name);
        b.title = tool.description || '';
        b.style.cssText = 'display:block;width:100%;text-align:left;margin:2px 0;font-family:monospace;';
        b.addEventListener('click', () => {
          selectedName = tool.name;
          lastResult = '';
          showDetail();
        });
        row.appendChild(b);
        list.appendChild(row);
      });
      listHost.appendChild(list);
    }

    // 页面卸载/重渲染时停止轮询
    const _origContainerCleanup = container.cleanup || null;
    container.cleanup = () => {
      stopQueuePolling();
      if (typeof _origContainerCleanup === 'function') _origContainerCleanup();
    };

    function showDetail() {
      renderToolForm(detailHost, null);
    }

    // 队列区块
    const queueCard = el('div', 'card');
    const queueTitle = el('h3', 'card-title', '触发器队列');
    const queueHelp = el('p', 'card-help', '当前 trigger_manager 中等待 Agent 消费的触发任务（3s 自动刷新）。');
    const queueCount = el('div', 'card-help', '');
    queueCount.dataset.queueCount = '1';
    const queueStatus = el('div', 'card-help', '');
    queueStatus.style.cssText = 'color:var(--danger,#d33);';
    queueStatus.dataset.queueStatus = '1';
    const qTable = el('table', 'simple-table');
    qTable.innerHTML = '<thead><tr><th>ID</th><th>类型</th><th>内容</th><th>运行</th><th>#</th></tr></thead>';
    const qTbody = el('tbody', '');
    qTbody.dataset.queueBody = '1';
    qTable.append(qTbody);
    queueCard.append(queueTitle, queueHelp, queueCount, queueStatus, qTable);

    root.append(listHost);
    root.appendChild(el('hr'));
    root.appendChild(detailHost);
    root.prepend(queueCard);
    container.appendChild(root);
    loadList().catch((e) => {
      listHost.appendChild(el('div', 'card-help', '加载出错: ' + String(e)));
    });
    startQueuePolling();
  }

  api.addPage({
    plugin: PLUGIN_ID,
    id: 'dev-debugger',
    label: 'Debugger',
    desc: '查看并调用 AI 的激活工具（dev-debugger）',
    render,
  });
})();
