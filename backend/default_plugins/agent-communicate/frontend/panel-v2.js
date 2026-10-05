// Agent 通信（agent-communicate）— 配置中心面板
//
// FaustBot 作为 ACP 客户端把编码任务提交给外部 Agent（opencode / omp 等）。
// 同一份资源会同时载入主窗口与配置窗口：主窗口没有 window.pluginUI，
// 这里直接静默返回，绝不抛错（见 docs/agent-communicate-spec.md §14）。
//
// 版面（按"最需要人决策的东西排最前"重排）：
//   ① 总览条：4 个汇总数字 + 一条"现在最该关注什么"的提示
//   ② 待决权限：有待决才出现，倒计时 + 批准/拒绝按钮
//   ③ 任务：进行中在前、历史在后（默认 5 条，可展开），选中行进下方实时输出
//   ④ 外部 Agent：进程 / 会话 / 当前任务 / 队列，命令与配置收进行内展开
//   ⑤⑥⑦ 折叠区（默认收起）：手动提交调试、agents 配置、探测结果
//
// 后端契约（冻结，未改动）：
//   POST /faust/plugins/agent-communicate/communicate   {action, ...} -> {status, ...}
//   GET  /faust/plugins/agent-communicate/sse-communicate?name=&task_id=
//        data: {state, output_tail, event_tail} … 最后一条 {done:true}
(function () {
  'use strict';

  const api = window.pluginUI;
  if (!api || typeof api.addPage !== 'function') return;

  const PLUGIN_ID = 'agent-communicate';
  const REFRESH_MS = 4000;   // 页面可见时每 4 秒刷新一次状态
  const TICK_MS = 1000;      // 权限倒计时每秒走一格
  const HISTORY_PREVIEW = 5; // 历史任务默认显示条数（其余点"展开"）
  const URGENT_MS = 60000;   // 剩余不足 1 分钟时把倒计时标成紧急

  const PHASE_LABELS = { stopped: '未启动', starting: '启动中', ready: '就绪', error: '错误' };
  const PHASE_TAG = {
    stopped: 'tag-chip tag-chip-muted',
    starting: 'tag-chip agent-communicate-tag-info',
    ready: 'tag-chip agent-communicate-tag-ok',
    error: 'tag-chip agent-communicate-tag-warn'
  };

  const TASK_STATUS_LABELS = {
    queued: '排队中',
    starting: '启动中',
    handshaking: '握手中',
    session_pending: '建会话中',
    running: '运行中',
    awaiting_permission: '待授权',
    completed: '已完成',
    cancelled: '已取消',
    timeout: '已超时',
    failed: '失败',
    rejected: '已拒绝（队列满）',
    denied: '已拒绝',
    interrupted: '已中断'
  };

  const TERMINAL_STATUSES = ['completed', 'cancelled', 'timeout', 'failed', 'rejected', 'denied', 'interrupted'];

  // ──────────────────────────── 无状态工具 ────────────────────────────

  function str(value) {
    return value == null ? '' : String(value);
  }

  function esc(value) {
    return str(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function toInt(value, fallback) {
    const parsed = parseInt(str(value).trim(), 10);
    return isFinite(parsed) ? parsed : fallback;
  }

  function toText(value) {
    if (value == null) return '';
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
      return value.map(function (item) {
        return typeof item === 'string' ? item : JSON.stringify(item, null, 2);
      }).join('\n');
    }
    if (typeof value === 'object') {
      try { return JSON.stringify(value, null, 2); } catch (e) { return String(value); }
    }
    return String(value);
  }

  function truncate(value, max) {
    const flat = str(value).replace(/\s+/g, ' ').trim();
    return flat.length > max ? flat.slice(0, max) + '…' : flat;
  }

  function durationText(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    if (total < 60) return total + ' 秒';
    if (total < 3600) return Math.floor(total / 60) + ' 分 ' + (total % 60) + ' 秒';
    return Math.floor(total / 3600) + ' 时 ' + Math.floor((total % 3600) / 60) + ' 分';
  }

  function formatRemaining(ms) {
    const total = Math.max(0, Math.ceil(ms / 1000));
    if (total < 60) return total + ' 秒';
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return minutes + ' 分 ' + (seconds < 10 ? '0' + seconds : seconds) + ' 秒';
  }

  // 时间戳可能是秒或毫秒，也可能是 ISO 字符串；无法识别时返回 null（不猜）
  function toEpochMs(value) {
    if (value == null || value === '') return null;
    const numeric = Number(value);
    if (isFinite(numeric) && numeric > 0) return numeric > 1e11 ? numeric : numeric * 1000;
    const parsed = Date.parse(str(value));
    return isFinite(parsed) ? parsed : null;
  }

  function commandText(command) {
    if (command == null) return '';
    if (Array.isArray(command)) return JSON.stringify(command);
    if (typeof command === 'string') return command;
    try { return JSON.stringify(command); } catch (e) { return String(command); }
  }

  function rowsSignature(rows) {
    try { return JSON.stringify(rows); } catch (e) { return ''; }
  }

  function isTerminal(status) {
    return TERMINAL_STATUSES.indexOf(str(status).toLowerCase()) >= 0;
  }

  function statusLabel(status) {
    const key = str(status).toLowerCase();
    return TASK_STATUS_LABELS[key] || (key || '未知');
  }

  function statusChip(status) {
    const key = str(status).toLowerCase();
    const label = statusLabel(key);
    let cls = 'tag-chip';
    if (key === 'running') cls += ' agent-communicate-tag-info';
    else if (key === 'awaiting_permission') cls += ' agent-communicate-tag-warn';
    else if (key === 'completed') cls += ' agent-communicate-tag-ok';
    else cls += ' tag-chip-muted';
    return '<span class="' + cls + '">' + esc(label) + '</span>';
  }

  function detailOf(res) {
    if (res && typeof res === 'object' && res.detail != null && str(res.detail).trim()) return str(res.detail);
    return '';
  }

  // 后端错误可能是 {status:"error", detail}，也可能是 HTTP 4xx/5xx（err.response.detail）
  function errorText(err) {
    if (!err) return '未知错误';
    const response = err.response;
    if (response && typeof response === 'object' && response.detail != null && str(response.detail).trim()) {
      return str(response.detail);
    }
    return str(err.message || err) || '未知错误';
  }

  function clockNow() {
    return new Date().toTimeString().slice(0, 8);
  }

  // ──────────────────────────── 页面 ────────────────────────────

  function render(container) {
    // 每次重新渲染递增 token：旧实例的定时器与 SSE 靠它自杀，避免重复刷新与连接泄漏
    const token = ++RENDER_TOKEN;

    let agents = [];
    let discover = [];
    let config = {};
    let configAgents = [];
    let configParseError = '';
    let configSource = '';        // 'admin' | 'state' | ''（读不到时禁止保存）
    let configReadOnly = true;
    let agentsDirty = false;
    let renderedConfigSig = null;
    let submitOptionsSig = null;
    let loadFailed = false;
    let errorSource = null;
    let lastRefreshAt = 0;
    let selected = null;   // { name, taskId }
    let stream = null;     // 当前 EventSource
    let historyExpanded = false;
    const expandedAgents = {};   // { [agentName]: true } —— 行内"命令与配置"展开状态
    const renderedHtml = {};     // { [key]: html } —— 内容没变就不重建 DOM（少闪烁、不打断点击）

    container.innerHTML = `
<section class="agent-communicate-error" data-ac="error" hidden></section>

<article class="card full-span agent-communicate-card">
  <div class="agent-communicate-head">
    <div>
      <h3 class="card-title">总览</h3>
      <p class="card-help">FaustBot 作为 ACP 客户端调用本机外部编码 Agent（没有新增工具）。</p>
    </div>
    <div class="toolbar">
      <span class="card-help" data-ac="stamp">尚未读取</span>
      <button class="btn btn-ghost" data-ac-action="refresh">刷新状态</button>
    </div>
  </div>

  <div class="agent-communicate-stats">
    <div class="agent-communicate-stat" data-ac-stat="agents">
      <div class="agent-communicate-stat-label">外部 Agent</div>
      <div class="agent-communicate-stat-value" data-ac="stat-agents">—</div>
      <div class="agent-communicate-stat-sub" data-ac="stat-agents-sub"></div>
    </div>
    <div class="agent-communicate-stat" data-ac-stat="tasks">
      <div class="agent-communicate-stat-label">任务</div>
      <div class="agent-communicate-stat-value" data-ac="stat-tasks">—</div>
      <div class="agent-communicate-stat-sub" data-ac="stat-tasks-sub"></div>
    </div>
    <div class="agent-communicate-stat" data-ac-stat="perms">
      <div class="agent-communicate-stat-label">待决权限</div>
      <div class="agent-communicate-stat-value" data-ac="stat-perms">—</div>
      <div class="agent-communicate-stat-sub" data-ac="stat-perms-sub"></div>
    </div>
    <div class="agent-communicate-stat" data-ac-stat="default">
      <div class="agent-communicate-stat-label">超时默认动作</div>
      <div class="agent-communicate-stat-value" data-ac="stat-default">—</div>
      <div class="agent-communicate-stat-sub">不裁决就按这个结果执行</div>
    </div>
  </div>

  <div class="agent-communicate-alert" data-ac="alert" hidden></div>
  <p class="card-help agent-communicate-notice" data-ac="notice"></p>
</article>

<article class="card full-span agent-communicate-perm-card" data-ac="perm-card" hidden>
  <div class="agent-communicate-head">
    <div>
      <h3 class="card-title">待决权限 <span class="agent-communicate-count-badge" data-ac="perm-count">0</span></h3>
      <p class="card-help">外部 Agent 想动文件或执行命令，正在等你裁决；倒计时结束后按默认动作处理（当前默认：<strong data-ac="permission-default">拒绝</strong>）。</p>
    </div>
  </div>
  <div class="agent-communicate-perm-list" data-ac="permission-rows"></div>
</article>

<article class="card full-span agent-communicate-card">
  <div class="agent-communicate-head">
    <div>
      <h3 class="card-title">任务</h3>
      <p class="card-help">点任意任务行，在下方看它的实时输出（SSE）。「取消」只对排队中或运行中的任务有意义。</p>
    </div>
    <div class="toolbar">
      <span class="card-help" data-ac="task-summary"></span>
    </div>
  </div>

  <div class="agent-communicate-table-wrap">
    <table class="simple-table simple-table-compact agent-communicate-table agent-communicate-task-table">
      <colgroup>
        <col style="width:15%" /><col style="width:13%" /><col style="width:9%" /><col /><col style="width:12%" />
      </colgroup>
      <thead><tr><th>任务</th><th>状态</th><th>耗时</th><th>摘要</th><th>操作</th></tr></thead>
      <tbody data-ac="task-rows"><tr><td colspan="5" class="table-empty">加载中…</td></tr></tbody>
    </table>
  </div>

  <div class="agent-communicate-stream-wrap" data-ac="stream-wrap" hidden>
    <div class="agent-communicate-head">
      <div>
        <h4 class="agent-communicate-subtitle">实时输出</h4>
        <p class="card-help" data-ac="stream-state">未选择任务</p>
      </div>
      <div class="toolbar">
        <span class="card-help" data-ac="stream-title">未选择任务</span>
        <button class="btn btn-ghost" data-ac-action="stream-close">关闭输出流</button>
      </div>
    </div>
    <div class="agent-communicate-split">
      <div>
        <div class="agent-communicate-pane-label">正文</div>
        <pre class="agent-communicate-stream" data-ac="stream-output">（未选择任务）</pre>
      </div>
      <div>
        <div class="agent-communicate-pane-label">事件流（思考 / 工具 / 权限）</div>
        <pre class="agent-communicate-stream" data-ac="stream-events">（未选择任务）</pre>
      </div>
    </div>
  </div>
</article>

<article class="card full-span agent-communicate-card">
  <div class="agent-communicate-head">
    <div>
      <h3 class="card-title">Agent 列表</h3>
      <p class="card-help">进程状态、会话与当前任务。启用开关会写回插件配置的 agents 项；「启动 / 关闭」作用于子进程，「新建 / 关闭会话」只作用于 ACP 会话。</p>
    </div>
  </div>
  <div class="agent-communicate-table-wrap">
    <table class="simple-table simple-table-compact agent-communicate-table agent-communicate-agent-table">
      <colgroup>
        <col style="width:26%" /><col style="width:9%" /><col style="width:13%" /><col style="width:19%" /><col style="width:17%" /><col style="width:16%" />
      </colgroup>
      <thead><tr><th>名称</th><th>启用</th><th>状态</th><th>会话</th><th>当前任务</th><th>操作</th></tr></thead>
      <tbody data-ac="agent-rows"><tr><td colspan="6" class="table-empty">加载中…</td></tr></tbody>
    </table>
  </div>
</article>

<article class="card full-span agent-communicate-fold-card">
  <details class="agent-communicate-fold">
    <summary>
      <span class="agent-communicate-fold-title">手动提交任务（调试）</span>
      <span class="card-help">手动给某个 Agent 派活</span>
    </summary>
    <div class="agent-communicate-fold-body">
      <div class="agent-communicate-submit">
        <div class="toolbar">
          <label class="card-help">提交给
            <select class="select agent-communicate-select" data-ac="submit-agent"></select>
          </label>
          <button class="btn btn-secondary" data-ac-action="task-submit">提交任务</button>
          <span class="card-help">字段：prompt / cwd / session / notify / config / timeout_sec；也可直接填一句纯文本。</span>
        </div>
        <textarea class="textarea" data-ac="submit-envelope" placeholder='{"prompt": "解释 backend/main.py 的启动流程"}'></textarea>
      </div>
    </div>
  </details>
</article>

<article class="card full-span agent-communicate-fold-card">
  <details class="agent-communicate-fold">
    <summary>
      <span class="agent-communicate-fold-title">agents 配置</span>
      <span class="card-help">command 是 JSON 字符串数组，超时单位为秒</span>
    </summary>
    <div class="agent-communicate-fold-body">
      <p class="card-help agent-communicate-error-text" data-ac="config-hint"></p>
      <div class="agent-communicate-table-wrap">
        <table class="simple-table simple-table-compact agent-communicate-config-table">
          <thead><tr><th>名称</th><th>命令（JSON 数组）</th><th>工作目录</th><th>启用</th><th>握手超时</th><th>会话超时</th><th>任务超时</th><th>权限超时</th><th>操作</th></tr></thead>
          <tbody data-ac="config-rows"><tr><td colspan="9" class="table-empty">加载中…</td></tr></tbody>
        </table>
      </div>
      <div class="toolbar">
        <button class="btn btn-ghost" data-ac-action="config-add">新增 Agent</button>
        <button class="btn btn-primary" data-ac-action="config-save">保存 agents 配置</button>
        <span class="card-help agent-communicate-config-status" data-ac="config-status"></span>
      </div>
    </div>
  </details>
</article>

<article class="card full-span agent-communicate-fold-card">
  <details class="agent-communicate-fold">
    <summary>
      <span class="agent-communicate-fold-title">探测结果</span>
      <span class="card-help">PATH 上候选命令的静态探测结论（不做活握手）</span>
    </summary>
    <div class="agent-communicate-fold-body">
      <div class="toolbar">
        <button class="btn btn-ghost" data-ac-action="discover-refresh">重新探测</button>
        <span class="card-help" data-ac="discover-stamp"></span>
      </div>
      <div class="agent-communicate-table-wrap">
        <table class="simple-table simple-table-compact">
          <thead><tr><th>候选</th><th>是否可用</th><th>原因</th><th>解析出的命令</th></tr></thead>
          <tbody data-ac="discover-rows"><tr><td colspan="4" class="table-empty">加载中…</td></tr></tbody>
        </table>
      </div>
    </div>
  </details>
</article>`;

    function $(selector) {
      return container.querySelector(selector);
    }

    // 只在 HTML 真的变化时写 DOM：内容没变就不动，避免 4 秒一次的整块重建
    // （重建会打断悬停与点击，也会让输入框/选择状态抖动）
    function setHtml(el, key, html) {
      if (!el) return;
      if (renderedHtml[key] === html) return;
      renderedHtml[key] = html;
      el.innerHTML = html;
    }

    function invalidateHtml(key) {
      delete renderedHtml[key];
    }

    function nowLabel() {
      return clockNow();
    }

    function setError(message, source) {
      const el = $('[data-ac="error"]');
      if (!el) return;
      const text = str(message).trim();
      el.hidden = !text;
      if (!text) {
        errorSource = null;
        el.textContent = '';
        return;
      }
      errorSource = source || null;
      el.textContent = text.startsWith('[') || !source ? text : '[' + source + '] ' + text;
    }

    function setNotice(message) {
      const el = $('[data-ac="notice"]');
      if (!el) return;
      const text = str(message).trim();
      el.textContent = text;
      el.hidden = !text;
    }

    function setConfigStatus(message, dirty) {
      const el = $('[data-ac="config-status"]');
      if (!el) return;
      el.textContent = str(message);
      el.classList.toggle('is-dirty', !!dirty);
    }

    function agentList() {
      return agents.filter(function (item) { return item && typeof item === 'object'; });
    }

    function defaultActionLabel() {
      const value = str(config && config.permission_default).trim().toLowerCase();
      return value === 'allow' ? '放行' : '拒绝';
    }

    // ── 汇总数字 ──

    function agentStats() {
      const list = agentList();
      let running = 0, ready = 0, errors = 0;
      list.forEach(function (agent) {
        if (agent.running === true) running += 1;
        const phase = str(agent.phase).toLowerCase();
        if (phase === 'ready') ready += 1;
        if (phase === 'error') errors += 1;
      });
      return { total: list.length, running: running, ready: ready, errors: errors };
    }

    function taskStats() {
      const collected = collectTasks();
      let running = 0, queued = 0, elapsedMax = 0;
      collected.active.forEach(function (entry) {
        const status = str(entry.task.status).toLowerCase();
        if (status === 'running' || status === 'awaiting_permission' || status === 'denied') running += 1;
        else queued += 1;
        const elapsed = Number(entry.task.elapsed_sec);
        if (isFinite(elapsed)) elapsedMax = Math.max(elapsedMax, elapsed);
      });
      return { running: running, queued: queued, elapsedMax: elapsedMax, history: collected.history.length };
    }

    function permissionRows() {
      const rows = [];
      agentList().forEach(function (agent) {
        const list = Array.isArray(agent.pending_permissions) ? agent.pending_permissions : [];
        list.forEach(function (item) {
          if (!item || typeof item !== 'object') return;
          rows.push({ agent: str(agent.name), item: item });
        });
      });
      return rows;
    }

    function earliestPermissionMs(rows) {
      let earliest = null;
      (rows || permissionRows()).forEach(function (entry) {
        const deadline = permissionDeadline(entry.item);
        if (!deadline) return;
        if (earliest == null || deadline < earliest) earliest = deadline;
      });
      return earliest;
    }

    async function communicate(payload) {
      return api.communicate(PLUGIN_ID, payload || {});
    }

    // 统一的后端调用：把 detail 原样呈现，绝不静默
    async function call(payload, options) {
      const opts = options || {};
      const action = str(payload && payload.action) || 'communicate';
      const source = opts.source || ('action:' + action);
      try {
        const res = await communicate(payload);
        if (!res || typeof res !== 'object') {
          setError('[' + action + '] 后端未返回 JSON 对象', source);
          return null;
        }
        if (res.status !== 'ok') {
          setError('[' + action + '] ' + (detailOf(res) || ('后端返回 status=' + str(res.status))), source);
          return null;
        }
        setError('', source);
        return res;
      } catch (err) {
        setError('[' + action + '] ' + errorText(err), source);
        return null;
      }
    }

    async function withBusy(button, fn) {
      if (button) button.disabled = true;
      try {
        return await fn();
      } finally {
        if (button && button.isConnected) button.disabled = false;
      }
    }

    function detailSuffix(res) {
      const detail = detailOf(res);
      return detail ? '：' + detail : '';
    }

    // 执行一个动作，成功/失败都刷新一次状态（失败后不显示伪造的状态）
    async function runAction(payload, okText, button) {
      const res = await withBusy(button, function () { return call(payload); });
      if (!res) {
        await refresh();
        return null;
      }
      if (typeof okText === 'function') setNotice(okText(res));
      else if (okText) setNotice(okText + detailSuffix(res));
      await refresh();
      return res;
    }

    // ── 状态读取 ──

    function resolveAgentsConfig(rawConfig) {
      if (Array.isArray(rawConfig)) return { rows: rawConfig.slice(), error: '' };
      if (!rawConfig || typeof rawConfig !== 'object') return { rows: [], error: '' };
      let raw = rawConfig.agents;
      if (typeof raw === 'string') {
        const trimmed = raw.trim();
        if (!trimmed) return { rows: [], error: '' };
        try {
          raw = JSON.parse(trimmed);
        } catch (err) {
          return { rows: [], error: '配置里的 agents 不是合法 JSON：' + errorText(err) };
        }
      }
      if (raw == null) return { rows: [], error: '' };
      if (!Array.isArray(raw)) return { rows: [], error: '配置里的 agents 不是数组（当前是 ' + (typeof raw) + '），无法在此编辑。' };
      return { rows: raw.slice(), error: '' };
    }

    // 插件配置（agents 表 / permission_default）的权威来源是管理接口，
    // get_state 的 config 只作为回退；两者都读不到时**禁止保存**，避免用空表覆盖原配置。
    async function fetchPluginConfig() {
      const base = str(api.backendBaseUrl);
      if (!base) return null;
      const res = await fetch(base + '/faust/admin/plugins/' + encodeURIComponent(PLUGIN_ID) + '/config');
      return res.json();
    }

    function adminConfigValues(payload) {
      if (!payload || typeof payload !== 'object') return null;
      const cfg = payload.config;
      if (!cfg || typeof cfg !== 'object') return null;
      const values = cfg.values;
      return (values && typeof values === 'object') ? values : null;
    }

    async function refresh() {
      const adminPromise = fetchPluginConfig().catch(function () { return null; });
      const res = await call({ action: 'get_state' }, { source: 'refresh' });
      if (!res) {
        loadFailed = true;
        renderAll();
        return;
      }
      const adminPayload = await adminPromise;
      try {
        loadFailed = false;
        agents = Array.isArray(res.agents) ? res.agents : [];
        discover = Array.isArray(res.discover) ? res.discover : [];
        config = (res.config && typeof res.config === 'object') ? Object.assign({}, res.config) : {};

        const values = adminConfigValues(adminPayload);
        if (values && values.permission_default != null) config.permission_default = values.permission_default;

        let resolved;
        if (values && Object.prototype.hasOwnProperty.call(values, 'agents')) {
          resolved = resolveAgentsConfig({ agents: values.agents });
          configSource = 'admin';
        } else if (config && Object.prototype.hasOwnProperty.call(config, 'agents')) {
          resolved = resolveAgentsConfig(config);
          configSource = 'state';
        } else {
          resolved = { rows: [], error: '' };
          configSource = '';
        }
        configParseError = resolved.error;
        configReadOnly = !configSource;
        // 有未保存编辑时绝不覆盖草稿；否则仅在服务端内容变化时重建表格
        if (!agentsDirty && !configParseError && !configReadOnly && rowsSignature(resolved.rows) !== rowsSignature(configAgents)) {
          configAgents = resolved.rows.map(function (row) { return cloneAgentRow(row); });
        }

        lastRefreshAt = Date.now();
        const stamp = $('[data-ac="stamp"]');
        if (stamp) stamp.textContent = '最近更新 ' + nowLabel();
        const discoverStamp = $('[data-ac="discover-stamp"]');
        if (discoverStamp) discoverStamp.textContent = '共 ' + discover.length + ' 个候选';
        const defaultEl = $('[data-ac="permission-default"]');
        if (defaultEl) defaultEl.textContent = defaultActionLabel();
        renderAll();
      } catch (err) {
        setError('状态渲染失败：' + errorText(err), 'refresh');
      }
    }

    function cloneAgentRow(row) {
      if (!row || typeof row !== 'object' || Array.isArray(row)) return {};
      const clone = {};
      Object.keys(row).forEach(function (key) { clone[key] = row[key]; });
      return clone;
    }

    function safeRefresh() {
      return refresh().catch(function (err) { setError('刷新失败：' + errorText(err), 'refresh'); });
    }

    // ── 渲染 ──

    function renderAll() {
      renderOverview();
      renderSubmitAgents();
      renderPermissions();
      renderTasks();
      renderAgents();
      renderConfig();
      renderDiscover();
    }

    function setStat(name, value, sub, tone) {
      const valueEl = $('[data-ac="stat-' + name + '"]');
      if (valueEl) valueEl.textContent = value;
      const subEl = $('[data-ac="stat-' + name + '-sub"]');
      if (subEl) subEl.textContent = sub || '';
      const block = $('[data-ac-stat="' + name + '"]');
      if (block) {
        block.classList.toggle('is-alert', tone === 'alert');
        block.classList.toggle('is-ok', tone === 'ok');
      }
    }

    // 一眼看清"现在最该关注什么"：权限待决 > Agent 报错 > 没配 Agent > 任务在跑
    function renderOverview() {
      const stats = agentStats();
      const tasks = taskStats();
      const perms = permissionRows();
      const alertEl = $('[data-ac="alert"]');

      if (loadFailed) {
        const valueEl = $('[data-ac="stat-agents"]');
        if (valueEl) valueEl.textContent = '—';
        setStat('agents', '—', '状态读取失败', 'alert');
        setStat('tasks', '—', '状态读取失败', '');
        setStat('perms', '—', '状态读取失败', '');
        setStat('default', '—', '状态读取失败', '');
        if (alertEl) { alertEl.hidden = true; }
        setNotice('');
        return;
      }

      // 一个 Agent 都没有时，"0 / 0 就绪"读起来像出了故障，直接说实话。
      // 强调色只留给"需要人决策"的东西；Agent 报错先用文字说明，没有待决权限时
      // 它会自己升级成下面那条红色提示。
      const agentSub = [];
      if (stats.total) {
        agentSub.push(stats.running ? stats.running + ' 个进程在运行' : '当前没有进程在跑');
        if (stats.errors) agentSub.push(stats.errors + ' 个报错');
      } else {
        agentSub.push('还没有可用的外部 Agent');
      }
      setStat(
        'agents',
        stats.total ? stats.ready + ' / ' + stats.total + ' 就绪' : '无 Agent',
        agentSub.join(' · '),
        ''
      );

      const taskValue = tasks.running ? tasks.running + ' 运行中' : (tasks.queued ? tasks.queued + ' 排队中' : '空闲');
      const taskSub = [];
      if (tasks.running && tasks.queued) taskSub.push(tasks.queued + ' 个排队');
      if (tasks.running && tasks.elapsedMax) taskSub.push('最长已跑 ' + durationText(tasks.elapsedMax));
      if (!tasks.running && !tasks.queued) taskSub.push(tasks.history ? '历史 ' + tasks.history + ' 条' : '还没有任务');
      // 只有"需要人决策"的东西上强调色，任务在跑是正常态，不加色
      setStat('tasks', taskValue, taskSub.join(' · '), '');

      const earliest = earliestPermissionMs(perms);
      setStat(
        'perms',
        perms.length ? perms.length + ' 条待裁决' : '无',
        perms.length
          ? (earliest ? '最早 ' + formatRemaining(earliest - Date.now()) + '后自动' + defaultActionLabel() : '未给出超时时间')
          : '外部 Agent 没有在等你决定什么',
        perms.length ? 'alert' : ''
      );
      setStat('default', defaultActionLabel(), '', '');

      let alertText = '';
      let alertTone = '';
      if (perms.length) {
        alertText = '⚠ ' + perms.length + ' 条权限请求待裁决'
          + (earliest ? '，最早 ' + formatRemaining(earliest - Date.now()) + ' 后按默认动作（' + defaultActionLabel() + '）处理' : '')
          + '。往下的「待决权限」里可以直接批准或拒绝。';
        alertTone = 'is-alert';
      } else if (stats.errors) {
        const failed = agentList().filter(function (agent) { return str(agent.phase).toLowerCase() === 'error'; })
          .map(function (agent) { return str(agent.name) + '（' + truncate(agent.last_error || '未给出原因', 80) + '）'; });
        alertText = failed.length + ' 个 Agent 处于错误状态：' + failed.join('；') + '。看「Agent 列表」里的最近错误。';
        alertTone = 'is-alert';
      } else if (!stats.total) {
        alertText = '还没有可用的外部 Agent。展开下方「agents 配置」新增一条，或展开「探测结果」点「重新探测」。';
        alertTone = 'is-alert';
      } else if (tasks.running) {
        alertText = tasks.running + ' 个任务正在跑' + (tasks.elapsedMax ? '，最长已 ' + durationText(tasks.elapsedMax) : '')
          + (tasks.queued ? '；另有 ' + tasks.queued + ' 个排队' : '') + '。点任务行看实时输出。';
      } else {
        alertText = '一切正常：没有待裁决的权限请求，也没有任务在跑。';
        alertTone = 'is-ok';
      }
      if (alertEl) {
        alertEl.hidden = !alertText;
        alertEl.textContent = alertText;
        alertEl.classList.toggle('is-alert', alertTone === 'is-alert');
        alertEl.classList.toggle('is-ok', alertTone === 'is-ok');
      }
    }

    function renderSubmitAgents() {
      const select = $('[data-ac="submit-agent"]');
      if (!select) return;
      const names = agentList().map(function (item) { return str(item.name); }).filter(Boolean);
      const sig = names.join('\u0000');
      if (sig === submitOptionsSig) return;
      const previous = select.value;
      select.innerHTML = names.map(function (name) {
        return '<option value="' + esc(name) + '">' + esc(name) + '</option>';
      }).join('');
      if (previous && names.indexOf(previous) >= 0) select.value = previous;
      submitOptionsSig = sig;
    }

    function switchHtml(options) {
      const opts = options || {};
      const attrs = opts.attrs ? ' ' + opts.attrs : '';
      const label = opts.label ? '<span class="switch-text">' + esc(opts.label) + '</span>' : '';
      const title = opts.title ? ' title="' + esc(opts.title) + '"' : '';
      const cls = opts.compact ? 'switch agent-communicate-switch-sm' : 'switch';
      const rowCls = opts.compact ? 'switch-row agent-communicate-switch-row-sm' : 'switch-row';
      return '<div class="' + rowCls + '">' + label
        + '<label class="' + cls + '"><input type="checkbox"' + attrs
        + (opts.checked === true ? ' checked' : '')
        + (opts.disabled === true ? ' disabled' : '')
        + title + ' /><span class="switch-slider"></span></label></div>';
    }

    // ── Agent 列表 ──

    function agentRowHtml(agent) {
      const name = str(agent.name) || '（未命名）';
      const phaseKnown = str(agent.phase).trim() !== '';
      const phase = str(agent.phase).trim().toLowerCase();
      const phaseLabel = phaseKnown ? (PHASE_LABELS[phase] || phase) : '未知';
      const phaseClass = phaseKnown ? (PHASE_TAG[phase] || 'tag-chip tag-chip-muted') : 'tag-chip tag-chip-muted';
      const runningKnown = typeof agent.running === 'boolean';
      const running = agent.running === true;
      const enabledKnown = typeof agent.enabled === 'boolean';
      const enabled = agent.enabled === true;

      const configRow = configAgents.filter(function (row) { return str(row && row.name) === str(agent.name); })[0] || null;
      const canToggle = !!configRow && !configParseError && !configReadOnly;
      const toggle = switchHtml({
        label: '',
        compact: true,
        checked: enabledKnown ? enabled : false,
        disabled: !canToggle || !enabledKnown,
        attrs: 'data-ac-change="agent-toggle" data-ac-name="' + esc(name) + '"',
        title: (enabledKnown ? (enabled ? '已启用' : '已停用') : '启用状态未知')
          + (canToggle ? '（点击写回 agents 配置）' : '（不在 agents 配置中，无法在这里切换）')
      });

      const description = str(agent.description);
      const session = str(agent.session_id);
      const cwd = str(agent.cwd);
      const errorValue = str(agent.last_error);
      const current = agent.current_task;
      const queueDepth = agent.queue_depth == null ? null : Number(agent.queue_depth);
      const expanded = expandedAgents[name] === true;

      let currentHtml;
      if (current && typeof current === 'object') {
        const currentId = str(current.id);
        currentHtml = currentId
          ? '<code class="agent-communicate-code">' + esc(currentId) + '</code>'
            + (str(current.status) || current.elapsed_sec != null
              ? '<div class="card-help">' + esc(statusLabel(current.status)) + (current.elapsed_sec != null ? ' · ' + esc(durationText(current.elapsed_sec)) : '') + '</div>'
              : '')
          : '<span class="card-help">空闲</span>';
      } else if (str(current)) {
        currentHtml = '<code class="agent-communicate-code">' + esc(str(current)) + '</code>';
      } else {
        currentHtml = '<span class="card-help">空闲</span>';
      }
      if (queueDepth) currentHtml += '<div class="card-help">排队 ' + esc(String(queueDepth)) + ' 个</div>';

      const startDisabled = phaseKnown && (phase === 'ready' || phase === 'starting');
      const stopDisabled = phaseKnown ? phase === 'stopped' : false;
      const rowClass = running ? 'agent-communicate-agent-row is-running' : 'agent-communicate-agent-row';

      return '<tr class="' + rowClass + '">'
        + '<td class="cell-primary">' + esc(name)
        + (description ? '<div class="card-help">' + esc(description) + '</div>' : '')
        + (errorValue
          ? '<div class="card-help agent-communicate-error-text agent-communicate-ellipsis" title="' + esc(errorValue) + '">'
            + esc(truncate(errorValue, 90)) + '</div>'
          : '')
        + '<button class="agent-communicate-link" data-ac-action="agent-detail" data-ac-name="' + esc(name) + '">'
        + (expanded ? '收起命令与配置 ▴' : '命令与配置 ▾') + '</button>'
        + '</td>'
        + '<td>' + toggle + '</td>'
        + '<td class="agent-communicate-nowrap"><span class="' + esc(phaseClass) + '">' + esc(phaseLabel) + '</span>'
        + '<div class="card-help">' + (runningKnown ? (running ? '进程在运行' : '进程未运行') : '进程状态未上报') + '</div></td>'
        + '<td>' + (session
          ? '<code class="agent-communicate-code agent-communicate-ellipsis" title="' + esc(session) + '">' + esc(session) + '</code>'
          : '<span class="card-help">无会话</span>')
        + (cwd ? '<div class="card-help agent-communicate-ellipsis" title="' + esc(cwd) + '">' + esc(cwd) + '</div>' : '') + '</td>'
        + '<td>' + currentHtml + '</td>'
        + '<td><div class="toolbar compact agent-communicate-inline-actions">'
        + (running
          ? '<button class="btn btn-ghost" data-ac-action="agent-stop" data-ac-name="' + esc(name) + '"' + (stopDisabled ? ' disabled' : '') + ' title="关闭该 Agent 的子进程（sessionId 保留）">关闭</button>'
          : '<button class="btn btn-secondary" data-ac-action="agent-start" data-ac-name="' + esc(name) + '"' + (startDisabled ? ' disabled' : '') + ' title="启动该 Agent 的子进程">启动</button>')
        + '<button class="btn btn-ghost" data-ac-action="session-new" data-ac-name="' + esc(name) + '" title="新建一个 ACP 会话">新建会话</button>'
        + '<button class="btn btn-ghost" data-ac-action="session-close" data-ac-name="' + esc(name) + '"' + (session ? '' : ' disabled') + ' title="关闭当前 ACP 会话（不杀进程）">关闭会话</button>'
        + '</div></td>'
        + '</tr>'
        + (expanded ? agentDetailRowHtml(agent) : '');
    }

    function agentDetailRowHtml(agent) {
      const commands = Array.isArray(agent.available_commands) ? agent.available_commands.map(str).filter(Boolean) : [];
      const options = Array.isArray(agent.config_options) ? agent.config_options.filter(function (item) { return item && typeof item === 'object'; }) : [];
      const commandChips = commands.length
        ? commands.map(function (item) { return '<span class="tag-chip agent-communicate-chip">' + esc(item) + '</span>'; }).join('')
        : '<span class="card-help">该 Agent 未上报可用命令</span>';
      const optionRows = options.length
        ? options.map(function (item) {
          const value = item.current_value != null ? item.current_value : item.currentValue;
          const values = Array.isArray(item.options)
            ? item.options.map(function (option) { return str(option && option.value != null ? option.value : option); }).join(' / ')
            : '';
          return '<tr><td class="cell-primary">' + esc(str(item.id)) + '</td>'
            + '<td><code class="agent-communicate-code">' + esc(str(value)) + '</code></td>'
            + '<td class="card-help">' + (values ? esc(truncate(values, 160)) : '—') + '</td></tr>';
        }).join('')
        : '<tr><td colspan="3" class="table-empty">当前会话没有配置项（会话尚未建立，或该 Agent 不支持 configOptions）。</td></tr>';

      return '<tr class="agent-communicate-detail-row"><td colspan="6">'
        + '<div class="agent-communicate-detail">'
        + '<div><div class="agent-communicate-pane-label">可用命令</div><div class="tag-list">' + commandChips + '</div></div>'
        + '<div><div class="agent-communicate-pane-label">会话配置项（config.json 同源）</div>'
        + '<table class="simple-table simple-table-compact"><thead><tr><th>id</th><th>当前值</th><th>合法取值</th></tr></thead>'
        + '<tbody>' + optionRows + '</tbody></table></div>'
        + '</div></td></tr>';
    }

    function renderAgents() {
      const tbody = $('[data-ac="agent-rows"]');
      if (!tbody) return;
      if (loadFailed) {
        setHtml(tbody, 'agents', '<tr><td colspan="6" class="table-empty">状态读取失败，见上方错误信息</td></tr>');
        return;
      }
      const list = agentList();
      if (!list.length) {
        setHtml(tbody, 'agents', '<tr><td colspan="6" class="table-empty">还没有可用的外部 Agent：展开下方「agents 配置」新增，或「探测结果」里点「重新探测」。</td></tr>');
        return;
      }
      setHtml(tbody, 'agents', list.map(agentRowHtml).join(''));
    }

    // ── 任务 ──

    function collectTasks() {
      const active = [];
      const history = [];
      agentList().forEach(function (agent) {
        const name = str(agent.name);
        const tasks = Array.isArray(agent.tasks) ? agent.tasks : [];
        tasks.forEach(function (task) {
          if (!task || typeof task !== 'object' || task.id == null) return;
          const entry = { agent: name, task: task };
          if (isTerminal(task.status)) history.push(entry);
          else active.push(entry);
        });
      });
      history.sort(function (a, b) {
        return (toEpochMs(b.task.created_at) || 0) - (toEpochMs(a.task.created_at) || 0);
      });
      return { active: active, history: history };
    }

    function taskRowHtml(entry, allowCancel) {
      const task = entry.task || {};
      const id = str(task.id);
      const isSelected = !!selected && selected.name === entry.agent && str(selected.taskId) === id;
      const preview = str(task.prompt) || str(task.result_preview);
      const full = str(task.prompt) || str(task.result_preview);
      const queueHint = task.queue_depth == null ? '' : ' title="提交时队列深度 ' + esc(String(task.queue_depth)) + '"';
      const ops = allowCancel
        ? '<button class="btn btn-ghost" data-ac-action="task-cancel" data-ac-name="' + esc(entry.agent) + '" data-ac-task="' + esc(id) + '">取消</button>'
        : '<span class="card-help">—</span>';
      return '<tr class="clickable' + (isSelected ? ' selected' : '') + '"'
        + ' data-ac-action="task-open" data-ac-name="' + esc(entry.agent) + '" data-ac-task="' + esc(id) + '">'
        + '<td class="cell-primary"><code class="agent-communicate-code">' + esc(id) + '</code>'
        + '<div class="card-help">' + esc(entry.agent) + '</div></td>'
        + '<td class="agent-communicate-nowrap"' + queueHint + '>' + statusChip(task.status) + '</td>'
        + '<td class="agent-communicate-nowrap">' + (task.elapsed_sec == null ? '<span class="card-help">—</span>' : esc(durationText(task.elapsed_sec))) + '</td>'
        + '<td><span class="agent-communicate-ellipsis" title="' + esc(full) + '">' + (preview ? esc(truncate(preview, 200)) : '—') + '</span></td>'
        + '<td class="agent-communicate-nowrap">' + ops + '</td>'
        + '</tr>';
    }

    function groupRowHtml(text, colspan, action) {
      const ops = action
        ? '<button class="agent-communicate-link" data-ac-action="' + esc(action.action) + '">' + esc(action.label) + '</button>'
        : '';
      return '<tr class="agent-communicate-group-row"><td colspan="' + colspan + '">'
        + '<span>' + esc(text) + '</span>' + ops + '</td></tr>';
    }

    function renderTasks() {
      const tbody = $('[data-ac="task-rows"]');
      const summaryEl = $('[data-ac="task-summary"]');
      if (!tbody) return;
      if (loadFailed) {
        setHtml(tbody, 'tasks', '<tr><td colspan="5" class="table-empty">状态读取失败，见上方错误信息</td></tr>');
        if (summaryEl) summaryEl.textContent = '';
        return;
      }
      const collected = collectTasks();
      const parts = [];
      parts.push(groupRowHtml('进行中 / 排队（' + collected.active.length + '）', 5, null));
      if (!collected.active.length) {
        parts.push('<tr><td colspan="5" class="table-empty">当前没有排队或运行中的任务。</td></tr>');
      } else {
        collected.active.forEach(function (entry) { parts.push(taskRowHtml(entry, true)); });
      }

      const hiddenCount = Math.max(0, collected.history.length - (historyExpanded ? collected.history.length : HISTORY_PREVIEW));
      const shownHistory = historyExpanded ? collected.history : collected.history.slice(0, HISTORY_PREVIEW);
      parts.push(groupRowHtml(
        '历史（显示 ' + shownHistory.length + ' / 共 ' + collected.history.length + '）',
        5,
        hiddenCount > 0
          ? { action: 'history-more', label: '展开全部 ' + collected.history.length + ' 条 ▾' }
          : (historyExpanded && collected.history.length > HISTORY_PREVIEW ? { action: 'history-less', label: '收起 ▴' } : null)
      ));
      if (!collected.history.length) {
        parts.push('<tr><td colspan="5" class="table-empty">暂无历史任务。</td></tr>');
      } else {
        shownHistory.forEach(function (entry) { parts.push(taskRowHtml(entry, false)); });
      }
      setHtml(tbody, 'tasks', parts.join(''));

      if (summaryEl) {
        const stats = taskStats();
        summaryEl.textContent = stats.running || stats.queued
          ? '运行中 ' + stats.running + ' · 排队 ' + stats.queued
          : '当前空闲';
      }
    }

    // ── 待决权限 ──

    function permissionDeadline(row) {
      const direct = toEpochMs(row && row.deadline_ts);
      if (direct) return direct;
      const remaining = Number(row && row.remaining_sec);
      if (isFinite(remaining) && remaining >= 0) return Date.now() + remaining * 1000;
      const created = toEpochMs(row && row.created_at);
      const timeout = Number(row && row.timeout_sec);
      if (created && isFinite(timeout) && timeout > 0) return created + timeout * 1000;
      return null;
    }

    function renderPermissions() {
      const host = $('[data-ac="permission-rows"]');
      const card = $('[data-ac="perm-card"]');
      const countEl = $('[data-ac="perm-count"]');
      if (!host || !card) return;

      const rows = permissionRows();
      // 读不到状态时既不显示"0 条待裁决"（会是假信息），也不编造请求：
      // 整块隐藏，由顶部错误条如实说明。
      if (loadFailed) {
        if (countEl) countEl.textContent = '—';
        card.hidden = true;
        setHtml(host, 'perms', '');
        return;
      }
      if (countEl) countEl.textContent = String(rows.length);
      // 没有待决权限就整块不出现：首屏把位置留给真正需要决策的东西
      card.hidden = rows.length === 0;
      if (!rows.length) {
        setHtml(host, 'perms', '');
        return;
      }

      setHtml(host, 'perms', rows.map(function (entry) {
        const item = entry.item;
        const requestId = str(item.request_id);
        const deadline = permissionDeadline(item);
        const remaining = deadline ? deadline - Date.now() : null;
        const expired = remaining != null && remaining <= 0;
        const urgent = remaining != null && remaining > 0 && remaining <= URGENT_MS;
        const summary = str(item.summary);
        const created = toEpochMs(item.created_at);
        const meta = [];
        if (str(item.task_id)) meta.push('来源任务 ' + str(item.task_id));
        if (created) meta.push('请求于 ' + new Date(created).toTimeString().slice(0, 8));
        if (requestId) meta.push(requestId);

        const countdown = deadline
          ? '<div class="agent-communicate-countdown-lg' + (expired ? ' is-expired' : (urgent ? ' is-urgent' : ''))
            + '" data-ac-countdown data-ac-deadline="' + String(deadline) + '">'
            + '<span data-ac-countdown-text>' + esc(expired ? '已超时' : formatRemaining(remaining)) + '</span>'
            + '<span class="agent-communicate-countdown-unit">' + (expired ? '已按默认动作处理' : '后自动' + esc(defaultActionLabel())) + '</span></div>'
          : '<div class="agent-communicate-countdown-lg"><span class="agent-communicate-countdown-unit">未给出超时时间</span></div>';

        const ops = !requestId
          ? '<span class="card-help">请求缺少 request_id，无法在此裁决</span>'
          : (expired
            ? '<span class="card-help">已超时，插件按默认动作处理（' + esc(defaultActionLabel()) + '）</span>'
            : '<div class="toolbar compact agent-communicate-inline-actions">'
              + '<button class="btn btn-primary" data-ac-action="permission-answer" data-ac-name="' + esc(entry.agent) + '" data-ac-request="' + esc(requestId) + '" data-ac-outcome="allow" data-ac-scope="once" title="只批准这一次">批准一次</button>'
              + '<button class="btn btn-ghost" data-ac-action="permission-answer" data-ac-name="' + esc(entry.agent) + '" data-ac-request="' + esc(requestId) + '" data-ac-outcome="allow" data-ac-scope="always" title="本会话内一直批准这类请求">始终批准</button>'
              + '<button class="btn btn-ghost" data-ac-action="permission-answer" data-ac-name="' + esc(entry.agent) + '" data-ac-request="' + esc(requestId) + '" data-ac-outcome="deny" data-ac-scope="once" title="拒绝这次请求">拒绝</button>'
              + '</div>');

        return '<div class="agent-communicate-perm' + (urgent || expired ? ' is-urgent' : '') + '" data-ac-perm>'
          + '<div class="agent-communicate-perm-main">'
          + '<div class="agent-communicate-perm-tool">'
          + '<span class="tag-chip tag-chip-muted">' + esc(entry.agent) + '</span>'
          + '<span class="agent-communicate-perm-title" title="' + esc(str(item.tool_title)) + '">' + esc(str(item.tool_title) || '（未命名工具）') + '</span>'
          + '</div>'
          + '<div class="agent-communicate-perm-args" title="' + esc(summary) + '">' + (summary ? esc(truncate(summary, 240)) : '（外部 Agent 未提供参数）') + '</div>'
          + (meta.length ? '<div class="card-help">' + esc(meta.join(' · ')) + '</div>' : '')
          + '</div>'
          + '<div class="agent-communicate-perm-side">'
          + countdown
          + '<div data-ac-perm-ops' + (expired ? ' data-ac-applied="1"' : '') + '>' + ops + '</div>'
          + '</div>'
          + '</div>';
      }).join(''));
      tickCountdowns();
    }

    function tickCountdowns() {
      const cells = container.querySelectorAll('[data-ac-countdown]');
      Array.prototype.forEach.call(cells, function (cell) {
        const raw = cell.getAttribute('data-ac-deadline');
        const deadline = raw ? Number(raw) : NaN;
        if (!deadline || !isFinite(deadline)) return;
        const remain = deadline - Date.now();
        const textEl = cell.querySelector('[data-ac-countdown-text]');
        const unitEl = cell.querySelector('.agent-communicate-countdown-unit');
        if (remain > 0) {
          if (textEl) textEl.textContent = formatRemaining(remain);
          cell.classList.toggle('is-urgent', remain <= URGENT_MS);
          return;
        }
        if (textEl) textEl.textContent = '已超时';
        cell.classList.remove('is-urgent');
        cell.classList.add('is-expired');
        if (unitEl) unitEl.textContent = '已按默认动作处理';
        const perm = cell.closest('[data-ac-perm]');
        const ops = perm ? perm.querySelector('[data-ac-perm-ops]') : null;
        if (ops && ops.getAttribute('data-ac-applied') !== '1') {
          ops.setAttribute('data-ac-applied', '1');
          ops.innerHTML = '<span class="card-help">已超时，插件按默认动作处理（' + esc(defaultActionLabel()) + '）</span>';
        }
      });
    }

    // ── agents 配置表 ──

    function timeoutValue(row, key, fallback) {
      const value = row ? row[key] : null;
      if (value == null || value === '') return fallback;
      return toInt(value, fallback);
    }

    function renderConfig() {
      const tbody = $('[data-ac="config-rows"]');
      const hint = $('[data-ac="config-hint"]');
      const saveBtn = $('[data-ac-action="config-save"]');
      const addBtn = $('[data-ac-action="config-add"]');
      const blocked = !!configParseError || configReadOnly;
      if (hint) {
        hint.textContent = configParseError
          || (configReadOnly ? '读不到插件配置（get_state 未返回 agents，管理接口也不可用），为避免用空表覆盖原有 agents 已停用编辑与保存。' : '');
      }
      if (saveBtn) saveBtn.disabled = blocked;
      if (addBtn) addBtn.disabled = blocked;
      if (!tbody) return;
      if (loadFailed) {
        tbody.innerHTML = '<tr><td colspan="9" class="table-empty">状态读取失败，见上方错误信息</td></tr>';
        renderedConfigSig = null;
        return;
      }
      if (configReadOnly) {
        tbody.innerHTML = '<tr><td colspan="9" class="table-empty">配置不可读，已停止编辑以免覆盖原值。</td></tr>';
        renderedConfigSig = null;
        return;
      }
      if (configParseError) {
        tbody.innerHTML = '<tr><td colspan="9" class="table-empty">agents 配置不可解析，已停止编辑与保存以免覆盖原值。</td></tr>';
        renderedConfigSig = null;
        return;
      }
      const sig = rowsSignature(configAgents);
      // 有未保存编辑时绝不重建表格（否则会吃掉输入框内容与光标）
      if (agentsDirty && renderedConfigSig !== null) return;
      if (renderedConfigSig === sig) return;
      renderConfigTableBody(tbody);
      renderedConfigSig = sig;
      if (!agentsDirty) setConfigStatus('', false);
    }

    function renderConfigTableBody(tbody) {
      if (!configAgents.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="table-empty">配置里还没有 Agent：点「新增 Agent」，或先「重新探测」。</td></tr>';
        return;
      }
      tbody.innerHTML = configAgents.map(function (row, index) {
        const enabledKnown = typeof row.enabled === 'boolean';
        const toggle = switchHtml({
          label: '',
          compact: true,
          checked: enabledKnown ? row.enabled : true,
          attrs: 'data-ac-field="enabled" data-ac-index="' + index + '"'
        });
        return '<tr>'
          + '<td><input class="input agent-communicate-cell-input" type="text" data-ac-field="name" data-ac-index="' + index + '" value="' + esc(str(row.name)) + '" placeholder="opencode" /></td>'
          + '<td><input class="input agent-communicate-cell-input agent-communicate-cell-wide" type="text" data-ac-field="command" data-ac-index="' + index + '" value="' + esc(commandText(row.command)) + '" placeholder="[&quot;opencode.exe&quot;,&quot;acp&quot;]" /></td>'
          + '<td><input class="input agent-communicate-cell-input agent-communicate-cell-wide" type="text" data-ac-field="cwd" data-ac-index="' + index + '" value="' + esc(str(row.cwd)) + '" placeholder="D:/dev/project" /></td>'
          + '<td>' + toggle + '</td>'
          + '<td><input class="input agent-communicate-cell-input agent-communicate-cell-num" type="number" min="1" data-ac-field="handshake_timeout_sec" data-ac-index="' + index + '" value="' + esc(String(timeoutValue(row, 'handshake_timeout_sec', 30))) + '" /></td>'
          + '<td><input class="input agent-communicate-cell-input agent-communicate-cell-num" type="number" min="1" data-ac-field="session_timeout_sec" data-ac-index="' + index + '" value="' + esc(String(timeoutValue(row, 'session_timeout_sec', 60))) + '" /></td>'
          + '<td><input class="input agent-communicate-cell-input agent-communicate-cell-num" type="number" min="1" data-ac-field="task_timeout_sec" data-ac-index="' + index + '" value="' + esc(String(timeoutValue(row, 'task_timeout_sec', 1800))) + '" /></td>'
          + '<td><input class="input agent-communicate-cell-input agent-communicate-cell-num" type="number" min="1" data-ac-field="permission_timeout_sec" data-ac-index="' + index + '" value="' + esc(String(timeoutValue(row, 'permission_timeout_sec', 300))) + '" /></td>'
          + '<td><button class="btn btn-ghost" data-ac-action="config-delete" data-ac-index="' + index + '">删除</button></td>'
          + '</tr>';
      }).join('');
    }

    function buildAgentsPayload() {
      const rows = [];
      for (let index = 0; index < configAgents.length; index += 1) {
        const source = configAgents[index];
        const row = cloneAgentRow(source);
        const name = str(row.name).trim();
        if (!name) return { error: '第 ' + (index + 1) + ' 行：名称不能为空' };
        const rawCommand = row.command;
        let command;
        if (Array.isArray(rawCommand)) {
          command = rawCommand.map(str);
        } else {
          const raw = str(rawCommand).trim();
          if (!raw) return { error: '第 ' + (index + 1) + ' 行（' + name + '）：command 不能为空' };
          try {
            command = JSON.parse(raw);
          } catch (err) {
            return { error: '第 ' + (index + 1) + ' 行（' + name + '）：command 不是合法 JSON，应为字符串数组，如 ["opencode.exe","acp"]' };
          }
          if (!Array.isArray(command)) return { error: '第 ' + (index + 1) + ' 行（' + name + '）：command 必须是 JSON 数组' };
          command = command.map(str);
        }
        row.name = name;
        row.command = command;
        row.cwd = str(row.cwd).trim();
        row.enabled = row.enabled !== false && row.enabled !== 'false';
        row.handshake_timeout_sec = toInt(row.handshake_timeout_sec, 30);
        row.session_timeout_sec = toInt(row.session_timeout_sec, 60);
        row.task_timeout_sec = toInt(row.task_timeout_sec, 1800);
        row.permission_timeout_sec = toInt(row.permission_timeout_sec, 300);
        rows.push(row);
      }
      return { rows: rows };
    }

    // ── 探测结果 ──

    function renderDiscover() {
      const tbody = $('[data-ac="discover-rows"]');
      if (!tbody) return;
      if (loadFailed) {
        setHtml(tbody, 'discover', '<tr><td colspan="4" class="table-empty">探测结果读取失败，见上方错误信息</td></tr>');
        return;
      }
      if (!discover.length) {
        setHtml(tbody, 'discover', '<tr><td colspan="4" class="table-empty">暂无探测结果：点「重新探测」，或确认插件配置里 discover_enabled 是否开启。</td></tr>');
        return;
      }
      setHtml(tbody, 'discover', discover.map(function (row) {
        const item = (row && typeof row === 'object') ? row : {};
        const available = item.available === true;
        const command = commandText(item.command);
        return '<tr>'
          + '<td class="cell-primary">' + esc(str(item.name) || '（未命名候选）') + '</td>'
          + '<td class="agent-communicate-nowrap">' + (available
            ? '<span class="tag-chip agent-communicate-tag-ok">可用</span>'
            : '<span class="tag-chip tag-chip-muted">不可用</span>') + '</td>'
          + '<td>' + (str(item.reason) ? esc(str(item.reason)) : '<span class="card-help">未给出原因</span>') + '</td>'
          + '<td>' + (command
            ? '<code class="agent-communicate-code agent-communicate-ellipsis" title="' + esc(command) + '">' + esc(truncate(command, 200)) + '</code>'
            : '<span class="card-help">—</span>') + '</td>'
          + '</tr>';
      }).join(''));
    }

    // ── SSE 实时输出 ──

    // SSE state 帧：契约里是“任务状态对象”，实际后端给 {name, task_id, status, status_label, elapsed_sec, error}；
    // 两种形状都尽量读出来，读不到的字段不编造
    function streamStateText(state) {
      if (state == null) return '';
      if (typeof state === 'string') return state;
      if (typeof state !== 'object') return String(state);
      const parts = [];
      const taskId = state.task_id != null ? state.task_id : state.id;
      if (taskId != null) parts.push('任务 ' + str(taskId));
      if (state.name != null) parts.push('Agent ' + str(state.name));
      if (state.status != null) {
        parts.push('状态 ' + (str(state.status_label) || statusLabel(state.status)));
      } else if (state.status_label != null) {
        parts.push('状态 ' + str(state.status_label));
      }
      if (state.phase != null) parts.push('阶段 ' + (PHASE_LABELS[str(state.phase).toLowerCase()] || str(state.phase)));
      if (state.elapsed_sec != null) parts.push('已运行 ' + durationText(state.elapsed_sec));
      if (state.queue_depth != null) parts.push('队列 ' + str(state.queue_depth));
      const errorValue = str(state.error) || str(state.last_error);
      if (errorValue) parts.push('错误 ' + errorValue);
      if (!parts.length) return toText(state);
      return parts.join(' · ');
    }

    function closeStream(showNote) {
      if (stream) {
        try { stream.close(); } catch (e) { /* 已关闭 */ }
        stream = null;
      }
      const stateEl = $('[data-ac="stream-state"]');
      const titleEl = $('[data-ac="stream-title"]');
      if (showNote && stateEl) stateEl.textContent = '输出流已关闭（重新点击任务行可重连）。';
      if (showNote && titleEl) titleEl.textContent = '实时输出（已关闭）';
    }

    function openStream(name, taskId) {
      const wrap = $('[data-ac="stream-wrap"]');
      const output = $('[data-ac="stream-output"]');
      const events = $('[data-ac="stream-events"]');
      const stateEl = $('[data-ac="stream-state"]');
      const titleEl = $('[data-ac="stream-title"]');

      closeStream(false);
      if (!name || !taskId) {
        setError('无法打开输出流：缺少 Agent 名称或 task_id', 'action:task-open');
        return;
      }
      if (wrap) wrap.hidden = false;
      selected = { name: name, taskId: str(taskId) };
      renderTasks();
      if (titleEl) titleEl.textContent = '任务 ' + str(taskId) + ' @ ' + name;
      if (stateEl) stateEl.textContent = '正在连接输出流…';
      if (events) events.textContent = '（等待事件）';

      if (typeof api.communicateSSE !== 'function') {
        if (output) output.textContent = '当前窗口的 pluginUI 不支持 communicateSSE，无法显示实时输出。';
        if (stateEl) stateEl.textContent = '';
        return;
      }
      if (output) output.textContent = '（等待输出）';

      let es;
      try {
        es = api.communicateSSE(PLUGIN_ID, { name: name, task_id: str(taskId) });
      } catch (err) {
        if (output) output.textContent = '建立输出流失败：' + errorText(err);
        return;
      }
      stream = es;

      es.onmessage = function (event) {
        if (token !== RENDER_TOKEN) { try { es.close(); } catch (e) { /* ignore */ } return; }
        let data = null;
        try { data = JSON.parse(event.data); } catch (err) { return; }
        if (!data || typeof data !== 'object') return;
        // 先渲染本帧内容，再处理结束标记：后端把 done 与最后一帧内容放在同一条消息里
        if (data.state != null) {
          const text = streamStateText(data.state);
          if (stateEl) stateEl.textContent = text;
        }
        if (data.output_tail != null && output) output.textContent = toText(data.output_tail) || '（暂无输出）';
        if (data.event_tail != null && events) events.textContent = toText(data.event_tail) || '（暂无事件）';
        if (data.done === true) {
          closeStream(false);
          if (titleEl) titleEl.textContent = '任务 ' + str(taskId) + ' @ ' + name + '（已结束）';
          if (stateEl) stateEl.textContent = (stateEl.textContent ? stateEl.textContent + ' · ' : '') + '任务已进入终态，输出流已结束。';
          safeRefresh();
          return;
        }
        if (data.state && typeof data.state === 'object' && isTerminal(data.state.status)) safeRefresh();
      };
      es.onerror = function () {
        // 后端结束时也会触发 error；直接关闭，避免 EventSource 无休止重连
        if (stream === es) {
          try { es.close(); } catch (e) { /* ignore */ }
          stream = null;
          const el = $('[data-ac="stream-state"]');
          if (el) el.textContent = '输出流已断开（插件重载或后端结束连接）。任务仍有运行时，重新点击任务行即可重连。';
        }
      };
    }

    // ── 动作 ──

    async function submitTask(button) {
      const select = $('[data-ac="submit-agent"]');
      const area = $('[data-ac="submit-envelope"]');
      const name = select ? str(select.value) : '';
      if (!name) { setError('请先选择要提交的 Agent', 'action:task-submit'); return; }
      const raw = area ? str(area.value) : '';
      if (!raw.trim()) { setError('信封内容不能为空', 'action:task-submit'); return; }
      let envelope = raw;
      const trimmed = raw.trim();
      if (trimmed.charAt(0) === '{') {
        try {
          const parsed = JSON.parse(trimmed);
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) envelope = parsed;
        } catch (err) {
          setError('信封不是合法 JSON：' + errorText(err), 'action:task-submit');
          return;
        }
      }
      const res = await withBusy(button, function () {
        return call({ action: 'task_submit', name: name, envelope: envelope });
      });
      if (!res) { await refresh(); return; }
      const taskId = res.task_id != null ? str(res.task_id) : '';
      setNotice('已提交任务 ' + (taskId || '（后端未返回 task_id）') + detailSuffix(res));
      if (area) area.value = '';
      await refresh();
      if (!taskId) return;
      // 并发提交时后端返回的 task_id 可能不是本次那一条（store.recent(1) 是全局最近任务）：
      // 发现归属不符时如实说明，不打开别人的输出流
      const owner = agentList().filter(function (agent) {
        const tasks = Array.isArray(agent.tasks) ? agent.tasks : [];
        return tasks.some(function (task) { return task && str(task.id) === taskId; });
      })[0];
      const ownerName = owner ? str(owner.name) : '';
      if (ownerName && ownerName !== name) {
        setNotice('后端返回的 task_id ' + taskId + ' 属于 Agent「' + ownerName + '」，未自动打开输出流；请在任务列表点击对应任务。');
        return;
      }
      openStream(name, taskId);
    }

    async function toggleAgentEnabled(name, enabled) {
      if (!name) return;
      if (configReadOnly || configParseError) {
        setError('读不到当前 agents 配置，无法在这里切换「' + name + '」', 'action:agent-toggle');
        await refresh();
        return;
      }
      if (!configAgents.length) {
        setError('无法切换「' + name + '」：agents 配置为空', 'action:agent-toggle');
        await refresh();
        return;
      }
      // 与「保存」走同一套校验/归一化：输入框里的 command 文本在这里才变成数组
      const built = buildAgentsPayload();
      if (built.error) {
        setError(built.error, 'action:agent-toggle');
        await refresh();
        return;
      }
      const rows = built.rows;
      const target = rows.filter(function (row) { return str(row.name) === name; })[0];
      if (!target) {
        setError('无法切换「' + name + '」：agents 配置里找不到这个名字', 'action:agent-toggle');
        await refresh();
        return;
      }
      target.enabled = !!enabled;
      const res = await call({ action: 'agents_save', agents: rows });
      if (!res) { await refresh(); return; }
      configAgents = rows;
      agentsDirty = false;
      renderedConfigSig = rowsSignature(rows);
      setNotice('Agent「' + name + '」已' + (enabled ? '启用' : '停用') + detailSuffix(res));
      await refresh();
    }

    async function saveAgents(button) {
      if (configReadOnly || configParseError) {
        setError('读不到当前 agents 配置，已拒绝保存以免覆盖原值', 'action:config-save');
        setConfigStatus('已拒绝保存（配置不可读）', true);
        return;
      }
      const built = buildAgentsPayload();
      if (built.error) {
        setError(built.error, 'action:config-save');
        setConfigStatus('校验失败：' + built.error, true);
        return;
      }
      setConfigStatus('保存中…', true);
      const res = await withBusy(button, function () {
        return call({ action: 'agents_save', agents: built.rows });
      });
      if (!res) {
        setConfigStatus('保存失败（见上方错误信息）', true);
        await refresh();
        return;
      }
      configAgents = built.rows;
      agentsDirty = false;
      renderedConfigSig = rowsSignature(built.rows);
      setConfigStatus('已保存', false);
      setNotice('agents 配置已保存' + detailSuffix(res));
      await refresh();
    }

    async function handleAction(action, el) {
      const name = el.getAttribute('data-ac-name') || '';
      switch (action) {
        case 'refresh':
          await withBusy(el, refresh);
          break;
        case 'agent-start':
          await runAction({ action: 'agent_start', name: name }, 'Agent「' + name + '」已启动', el);
          break;
        case 'agent-stop':
          await runAction({ action: 'agent_stop', name: name }, 'Agent「' + name + '」已关闭', el);
          break;
        case 'agent-detail': {
          if (expandedAgents[name]) delete expandedAgents[name];
          else expandedAgents[name] = true;
          renderAgents();
          break;
        }
        case 'session-new':
          await runAction({ action: 'session_new', name: name }, function (res) {
            return '已新建会话' + (str(res.session_id) ? '：' + str(res.session_id) : '（后端未返回 session_id）') + detailSuffix(res);
          }, el);
          break;
        case 'session-close':
          await runAction({ action: 'session_close', name: name }, '已关闭会话', el);
          break;
        case 'task-open':
          openStream(name, el.getAttribute('data-ac-task') || '');
          break;
        case 'task-cancel':
          await runAction({
            action: 'task_cancel',
            name: name,
            task_id: el.getAttribute('data-ac-task') || ''
          }, '已提交取消请求', el);
          break;
        case 'history-more':
          historyExpanded = true;
          renderTasks();
          break;
        case 'history-less':
          historyExpanded = false;
          renderTasks();
          break;
        case 'task-submit':
          await submitTask(el);
          break;
        case 'permission-answer': {
          const outcome = el.getAttribute('data-ac-outcome') === 'allow' ? 'allow' : 'deny';
          const scope = el.getAttribute('data-ac-scope') === 'always' ? 'always' : 'once';
          const requestId = el.getAttribute('data-ac-request') || '';
          const label = outcome === 'allow' ? (scope === 'always' ? '已始终批准' : '已批准一次') : '已拒绝';
          await runAction({
            action: 'permission_answer',
            name: name,
            request_id: requestId,
            outcome: outcome,
            scope: scope
          }, label + '（请求 ' + requestId + '）', el);
          break;
        }
        case 'config-add':
          configAgents.push({
            name: '',
            command: [],
            cwd: '',
            enabled: true,
            handshake_timeout_sec: 30,
            session_timeout_sec: 60,
            task_timeout_sec: 1800,
            permission_timeout_sec: 300
          });
          agentsDirty = true;
          renderedConfigSig = null;
          renderConfig();
          setConfigStatus('有未保存的修改', true);
          break;
        case 'config-delete': {
          const index = toInt(el.getAttribute('data-ac-index'), -1);
          if (index < 0 || index >= configAgents.length) break;
          configAgents.splice(index, 1);
          agentsDirty = true;
          renderedConfigSig = null;
          renderConfig();
          setConfigStatus('有未保存的修改', true);
          break;
        }
        case 'config-save':
          await saveAgents(el);
          break;
        case 'discover-refresh': {
          const res = await withBusy(el, function () {
            return call({ action: 'discover_refresh' });
          });
          if (!res) { await refresh(); break; }
          if (Array.isArray(res.discover)) discover = res.discover;
          setNotice('重新探测完成' + detailSuffix(res));
          await refresh();
          break;
        }
        case 'stream-close':
          closeStream(true);
          break;
        default:
          break;
      }
    }

    function onFieldInput(event) {
      const el = event.target;
      if (!el || typeof el.getAttribute !== 'function') return;
      const field = el.getAttribute('data-ac-field');
      if (!field) return;
      const index = toInt(el.getAttribute('data-ac-index'), -1);
      if (index < 0 || index >= configAgents.length) return;
      const row = configAgents[index];
      if (field === 'enabled') row.enabled = !!el.checked;
      else if (field === 'name' || field === 'command' || field === 'cwd') row[field] = el.value;
      else row[field] = el.value;
      agentsDirty = true;
      setConfigStatus('有未保存的修改', true);
    }

    function onToggleChange(event) {
      const el = event.target;
      if (!el || typeof el.getAttribute !== 'function') return;
      if (el.getAttribute('data-ac-change') !== 'agent-toggle') return;
      toggleAgentEnabled(el.getAttribute('data-ac-name') || '', !!el.checked);
    }

    container.addEventListener('click', function (event) {
      const target = event.target && event.target.closest ? event.target.closest('[data-ac-action]') : null;
      if (!target || !container.contains(target)) return;
      const action = target.getAttribute('data-ac-action') || '';
      if (action === 'task-open' && event.target.closest('button')) return;   // 行内按钮优先
      Promise.resolve(handleAction(action, target)).catch(function (err) {
        setError('[' + action + '] ' + errorText(err), 'action:' + action);
      });
    });

    container.addEventListener('input', onFieldInput);
    container.addEventListener('change', function (event) {
      onFieldInput(event);
      onToggleChange(event);
    });

    function isAlive() {
      return token === RENDER_TOKEN
        && container.isConnected
        && container.childElementCount > 0
        && container.style.display !== 'none';
    }

    const timer = setInterval(function () {
      if (!isAlive()) {
        clearInterval(timer);
        if (stream) { try { stream.close(); } catch (e) { /* ignore */ } stream = null; }
        return;
      }
      tickCountdowns();
      if (Date.now() - lastRefreshAt >= REFRESH_MS) {
        refresh().catch(function (err) { setError('刷新失败：' + errorText(err), 'refresh'); });
      }
    }, TICK_MS);

    lastRefreshAt = Date.now();
    refresh().catch(function (err) { setError('刷新失败：' + errorText(err), 'refresh'); });
  }

  // 当前活动实例：重新渲染时旧实例的定时器/SSE 会自行退出
  let RENDER_TOKEN = 0;

  api.addPage({
    id: PLUGIN_ID,
    label: 'Agent 通信',
    desc: '通过 ACP 调用外部编码 Agent：权限裁决、任务与实时输出、进程与会话、agents 配置与探测',
    plugin: PLUGIN_ID,
    render: render
  });

  api.addCard('plugins', {
    title: 'Agent 通信（ACP）',
    priority: 18,
    plugin: PLUGIN_ID,
    render: function (container) {
      function show(lines) {
        container.innerHTML = '<div class="plugin-mini-card"><p>' + esc(lines[0]) + '</p>'
          + '<p class="plugin-mini-muted">' + esc(lines[1]) + '</p></div>';
      }
      api.communicate(PLUGIN_ID, { action: 'get_state' }).then(function (res) {
        if (!res || res.status !== 'ok') {
          show(['Agent 通信状态不可用', detailOf(res) || ('后端返回 status=' + str(res && res.status))]);
          return;
        }
        const list = Array.isArray(res.agents) ? res.agents : [];
        const running = list.filter(function (item) { return item && item.running === true; }).length;
        const ready = list.filter(function (item) { return item && str(item.phase) === 'ready'; }).length;
        const pending = list.reduce(function (sum, item) {
          return sum + ((item && Array.isArray(item.pending_permissions)) ? item.pending_permissions.length : 0);
        }, 0);
        const active = list.reduce(function (sum, item) {
          const tasks = (item && Array.isArray(item.tasks)) ? item.tasks : [];
          return sum + tasks.filter(function (task) { return task && !isTerminal(task.status); }).length;
        }, 0);
        show([
          '已注册 ' + list.length + ' 个外部 Agent，运行中 ' + running + '，就绪 ' + ready + (active ? '，任务在跑 ' + active : ''),
          pending ? '⚠ 待裁决权限 ' + pending + ' 条，请到「Agent 通信」页处理' : '没有待裁决的权限请求；任务与实时输出见「Agent 通信」页'
        ]);
      }).catch(function (err) {
        show(['Agent 通信状态不可用', errorText(err)]);
      });
    }
  });
})();
