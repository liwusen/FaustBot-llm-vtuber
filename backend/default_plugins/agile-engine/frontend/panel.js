(function(){
  const api = window.pluginUI;
  if (!api) return;

  async function communicate(payload){
    return api.communicate('agile-engine', payload || {});
  }

  function statusLabel(m){
    if (m.disabled) return '已禁用';
    if (!m.loaded || m.status === '未加载') return '未加载';
    if (m.status === 'error') return '异常';
    return '已加载';
  }

  function moduleRows(items){
    if (!items.length) {
      return '<tr><td colspan="6">暂无 Agile 模块文件（目录: ~/.faustbot/agile-modules/）</td></tr>';
    }
    return items.map(function(m){
      const keys = (m.storage_keys || []).slice(0, 5).join(', ') || '—';
      const errAttr = m.last_error ? ' title="' + String(m.last_error).replace(/"/g, '&quot;') + '"' : '';
      return '<tr' + errAttr + '>'
        + '<td><button class="btn btn-ghost" data-agile-logs="' + m.name + '">' + m.name + '</button></td>'
        + '<td>' + statusLabel(m) + '</td>'
        + '<td>' + (m.vfs_count || 0) + '</td>'
        + '<td>' + (m.interval_count || 0) + '</td>'
        + '<td>' + keys + '</td>'
        + '<td>' + (m.log_count || 0) + '</td>'
        + '</tr>';
    }).join('');
  }

  function renderPage(container){
    container.innerHTML = `
<article class="card full-span">
  <h3 class="card-title">Agile Module</h3>
  <p class="card-help">只读展示 Agile 模块状态：加载情况、VFS 节点、定时任务、存储键与日志数量。点击模块名查看其最近日志。</p>
  <table class="simple-table">
    <thead><tr><th>模块名</th><th>状态</th><th>VFS 节点</th><th>定时任务</th><th>存储键（前 5）</th><th>日志条数</th></tr></thead>
    <tbody id="agile-module-list"></tbody>
  </table>
</article>
<article class="card full-span">
  <h3 class="card-title">模块日志</h3>
  <pre id="agile-module-logs" style="margin:0;white-space:pre-wrap;font-family:inherit;max-height:320px;overflow:auto;background:#f4f7fb;border:1px solid #e2e8f0;border-radius:8px;padding:10px;">点击上方模块名查看日志...</pre>
</article>`;

    const list = document.getElementById('agile-module-list');
    const logsEl = document.getElementById('agile-module-logs');

    async function showLogs(name){
      if (logsEl) logsEl.textContent = '加载 ' + name + ' 日志...';
      const data = await communicate({ action: 'get_module_logs', name: name });
      const logs = (data && data.logs) || [];
      if (logsEl) logsEl.textContent = logs.length ? logs.join('\n') : '（模块 ' + name + ' 暂无日志）';
    }

    async function refresh(){
      const data = await communicate({ action: 'get_modules' });
      const items = (data && data.items) || [];
      if (list) {
        list.innerHTML = moduleRows(items);
        Array.from(list.querySelectorAll('button[data-agile-logs]')).forEach(function(btn){
          btn.addEventListener('click', function(){
            showLogs(btn.getAttribute('data-agile-logs')).catch(function(){
              if (logsEl) logsEl.textContent = '日志读取失败';
            });
          });
        });
      }
    }

    refresh().catch(function(){
      container.innerHTML = '<article class="card full-span"><h3 class="card-title">Agile Module</h3><p class="card-help">Agile 插件状态读取失败（插件未加载或后端未就绪）</p></article>';
    });
  }

  api.addPage({ id: 'agile-engine', label: 'Agile 模块', desc: 'Agile 模块状态、存储与日志', plugin: 'agile-engine', render: renderPage });
})();
