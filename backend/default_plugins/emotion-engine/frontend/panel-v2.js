(function(){
  const api = window.pluginUI;
  if (!api) return;
  baseUrl = api.backendBaseUrl;

  function formatBool(value) {
    return value ? '是' : '否';
  }

  async function fetchState(){
    return api.communicate('emotion-engine', { action: 'get_state' });
    console.log('fetchState:', payload);
  }

  async function fetchConfig(){
    const res = await fetch(baseUrl + '/faust/admin/plugins/emotion-engine/config');
    console.log('fetchConfig:', res);
    return res.json();
    
  }

  async function saveConfig(values){
    await fetch(baseUrl + '/faust/admin/plugins/emotion-engine/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: values, apply_runtime: true, no_initial_chat: true, reset_dialog: false })
    });
  }

  function drawTrend(canvas, history){
    if (!canvas || !canvas.getContext) return;
    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = 'rgba(255,255,255,0.03)';
    ctx.fillRect(0, 0, width, height);
    const keys = ['joy', 'irritation', 'pride', 'curiosity', 'sharpness', 'boredom'];
    const colors = ['#ff8fab', '#ff6b6b', '#f4d35e', '#4ecdc4', '#a78bfa', '#8ecae6'];
    keys.forEach(function(key, index){
      ctx.beginPath();
      ctx.strokeStyle = colors[index];
      ctx.lineWidth = 2;
      history.slice(-24).forEach(function(entry, pointIndex, arr){
        const x = arr.length <= 1 ? 0 : (pointIndex / (arr.length - 1)) * width;
        const value = Number((((entry || {}).vector || {})[key]) || 0);
        const y = height - (value / 10) * (height - 8) - 4;
        if (pointIndex === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
    });
  }

  function renderBars(root, vector){
    const items = [
      ['joy', '愉悦'], ['irritation', '烦躁'], ['pride', '傲慢'], ['curiosity', '好奇'], ['sharpness', '毒舌'], ['boredom', '无聊']
    ];
    root.innerHTML = items.map(function(item){
      const key = item[0];
      const label = item[1];
      const value = Number(vector[key] || 0);
      return '<div class="emotion-row"><span class="emotion-label">' + label + '</span><div class="emotion-bar"><i style="width:' + (value * 10) + '%"></i></div><span class="emotion-value">' + value.toFixed(1) + '</span></div>';
    }).join('');
  }

  function renderMetrics(root, payload) {
    const top = Array.isArray(payload.top_emotions) ? payload.top_emotions : [];
    const strongest = top[0] || { label: '未知', value: 0 };
    root.innerHTML = [
      '<div class="metric"><span class="metric-label">当前主导情绪</span><span class="metric-value">' + strongest.label + '</span></div>',
      '<div class="metric"><span class="metric-label">情绪强度</span><span class="metric-value">' + Number(strongest.value || 0).toFixed(1) + '</span></div>',
      '<div class="metric"><span class="metric-label">24 小时记录</span><span class="metric-value">' + ((payload.history || []).length || 0) + '</span></div>'
    ].join('');
  }

  function renderEvents(root, history) {
    if (!root) return;
    const rows = history.slice(-10).reverse();
    if (!rows.length) {
      root.innerHTML = '<tr><td colspan="2">暂无事件</td></tr>';
      return;
    }
    root.innerHTML = rows.map(function(entry){
      const timeText = new Date((entry.ts || 0) * 1000).toLocaleTimeString();
      const reason = entry.reason || '事件';
      return '<tr><td>' + timeText + '</td><td>' + reason + '</td></tr>';
    }).join('');
  }

  api.addPage({
    id: 'emotion-engine',
    label: '情绪系统',
    desc: '实时情绪、趋势和配置快照',
    plugin: 'emotion-engine',
    render: function(container){
      container.innerHTML = '<article class="card full-span"><h3 class="card-title">Emotion Engine</h3><p class="card-help">显示当前主导情绪、近 24 小时变化与配置状态。</p><div id="emotion-metrics" class="data-grid-compact"></div><div id="emotion-bars"></div></article><article class="card full-span"><h3 class="card-title">情绪配置</h3><div class="data-grid-compact"><div class="metric"><span class="metric-label">毒舌改写</span><span class="metric-value" id="emotion-sharp-state">否</span></div><div class="metric"><span class="metric-label">衰减速度</span><span class="metric-value" id="emotion-decay-state">0.10</span></div><div class="metric"><span class="metric-label">滤镜强度</span><span class="metric-value" id="emotion-overlay-state">50</span></div></div><div class="toolbar"><label><input id="emotion-sharp-toggle" type="checkbox" /> 启用毒舌改写</label><label>情绪衰减 <input id="emotion-decay" type="number" step="0.1" min="0" /></label><label>滤镜强度 <input id="emotion-overlay" type="number" step="1" min="0" max="100" /></label><button id="emotion-save" class="btn btn-primary">保存配置</button></div></article><article class="card full-span"><h3 class="card-title">24 小时趋势</h3><canvas id="emotion-trend" width="640" height="220"></canvas></article><article class="card full-span"><h3 class="card-title">最近事件</h3><table class="simple-table"><thead><tr><th>时间</th><th>事件内容</th></tr></thead><tbody id="emotion-events"></tbody></table></article>';
      Promise.all([fetchState(), fetchConfig()]).then(function(results){
        const payload = results[0] || {};
        const cfgPayload = (results[1] || {}).config || {};
        const vector = payload.vector || {};
        const history = Array.isArray(payload.history) ? payload.history : [];
        const metrics = document.getElementById('emotion-metrics');
        const bars = document.getElementById('emotion-bars');
        const events = document.getElementById('emotion-events');
        if (metrics) renderMetrics(metrics, payload);
        if (bars) renderBars(bars, vector);
        renderEvents(events, history);
        const values = (cfgPayload.values || {});
        const sharpToggle = document.getElementById('emotion-sharp-toggle');
        const decay = document.getElementById('emotion-decay');
        const overlay = document.getElementById('emotion-overlay');
        const sharpState = document.getElementById('emotion-sharp-state');
        const decayState = document.getElementById('emotion-decay-state');
        const overlayState = document.getElementById('emotion-overlay-state');
        if (sharpToggle) sharpToggle.checked = !!values.SHARP_TONGUE_REWRITE;
        if (decay) decay.value = String(values.DECAY_PER_MINUTE ?? 0.1);
        if (overlay) overlay.value = String(values.OVERLAY_INTENSITY ?? 50);
        if (sharpState) sharpState.textContent = formatBool(!!values.SHARP_TONGUE_REWRITE);
        if (decayState) decayState.textContent = Number(values.DECAY_PER_MINUTE ?? 0.1).toFixed(2);
        if (overlayState) overlayState.textContent = String(values.OVERLAY_INTENSITY ?? 50);
        const saveBtn = document.getElementById('emotion-save');
        if (saveBtn) {
          saveBtn.onclick = async function(){
            await saveConfig({
              SHARP_TONGUE_REWRITE: !!(sharpToggle && sharpToggle.checked),
              DECAY_PER_MINUTE: Number(decay && decay.value || 0.1),
              OVERLAY_INTENSITY: Number(overlay && overlay.value || 50)
            });
          };
        }
        drawTrend(document.getElementById('emotion-trend'), history);
      }).catch(function(error){
        container.innerHTML = '<article class="card full-span"><h3 class="card-title">Emotion Engine</h3><p class="card-help">无法获取情绪状态</p></article>';
        console.error("Failed to fetch emotion engine state or config", error);
      });
    }
  });

  api.addCard('plugins', {
    title: 'Emotion Engine',
    priority: 15,
    plugin: 'emotion-engine',
    render: function(container){
      fetchState().then(function(payload){
        const top = Array.isArray(payload.top_emotions) ? payload.top_emotions : [];
        const dominant = top[0] || { label: '未知', value: 0 };
        container.innerHTML = '<div class="plugin-mini-card"><p>主导情绪：' + dominant.label + '</p><p class="plugin-mini-muted">当前强度 ' + Number(dominant.value || 0).toFixed(1) + '，最近记录 ' + ((payload.history || []).length) + ' 条</p></div>';
      }).catch(function(){
        container.innerHTML = '<div class="plugin-mini-card">情绪状态不可用</div>';
      });
    }
  });
})();
