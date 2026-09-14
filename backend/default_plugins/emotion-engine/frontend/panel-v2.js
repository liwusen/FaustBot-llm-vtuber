(function(){
  const api = window.pluginUI;
  if (!api) return;
  const baseUrl = api.backendBaseUrl;
  const CONFIG_URL = baseUrl + '/faust/admin/plugins/emotion-engine/config';

  function fetchState(){
    return api.communicate('emotion-engine', { action: 'get_state' });
  }

  async function fetchConfig(){
    const res = await fetch(CONFIG_URL);
    return res.json();
  }

  // 插件配置不重建运行时（apply_runtime=false）：配置变更由后端广播 config_changed 即时生效
  async function saveConfig(values){
    const res = await fetch(CONFIG_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: values, apply_runtime: false, no_initial_chat: true, reset_dialog: false })
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  }

  const EMOTION_ITEMS = [
    ['joy', '愉悦'], ['irritation', '烦躁'], ['pride', '傲慢'], ['curiosity', '好奇'], ['sharpness', '毒舌'], ['boredom', '无聊']
  ];
  const EMOTION_COLORS = ['#ff8fab', '#ff6b6b', '#f4d35e', '#4ecdc4', '#a78bfa', '#8ecae6'];

  // ECharts 由配置中心的本地 bundle（frontend/libs/echarts/echarts-bundle.js）提供，不再走 CDN
  function loadECharts(){
    if (window.echarts) return Promise.resolve(window.echarts);
    return Promise.reject(new Error('ECharts 未加载（缺少 libs/echarts/echarts-bundle.js）'));
  }

  function drawTrend(elm, history){
    if (!elm) return;
    loadECharts().then(function(echarts){
      const chart = echarts.getInstanceByDom(elm) || echarts.init(elm);
      // 后端按 24h 窗口裁剪并做了心跳限频，这里直接用满窗口数据；时间轴用 value 型毫秒时间戳，避免采样不等距导致失真
      const series = EMOTION_ITEMS.map(function(item, index){
        return {
          name: item[1],
          type: 'line',
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2, color: EMOTION_COLORS[index] },
          itemStyle: { color: EMOTION_COLORS[index] },
          data: history.map(function(entry){
            return [Number(entry.ts || 0) * 1000, Number(((entry || {}).vector || {})[item[0]]) || 0];
          }),
        };
      });
      chart.setOption({
        tooltip: {
          trigger: 'axis',
          valueFormatter: function(value){ return Number(value).toFixed(1); }
        },
        legend: { data: EMOTION_ITEMS.map(function(item){ return item[1]; }), textStyle: { color: '#888' } },
        grid: { left: 36, right: 16, top: 36, bottom: 28 },
        xAxis: { type: 'time', axisLabel: { color: '#888', formatter: '{HH}:{mm}' } },
        yAxis: { type: 'value', min: 0, max: 10, axisLabel: { color: '#888' } },
        series: series,
      });
      chart.resize();
    }).catch(function(error){
      elm.textContent = '趋势图加载失败: ' + error.message;
      console.error('emotion trend chart error', error);
    });
  }

  async function setEmotion(key, value){
    try {
      await api.communicate('emotion-engine', { action: 'set_emotion', key: key, value: value });
    } catch (error) {
      console.error('set_emotion failed', error);
    }
  }

  function renderBars(root, vector){
    root.innerHTML = '';
    EMOTION_ITEMS.forEach(function(item){
      const key = item[0];
      const label = item[1];
      let value = Number(vector[key] || 0);
      const row = document.createElement('div');
      row.className = 'emotion-row';
      row.innerHTML = '<span class="emotion-label">' + label + '</span><div class="emotion-bar emotion-bar-editable" title="拖动修改情绪值"><i style="width:' + (value * 10) + '%"></i></div><span class="emotion-value">' + value.toFixed(1) + '</span>';
      const bar = row.querySelector('.emotion-bar');
      const fill = row.querySelector('i');
      const valueEl = row.querySelector('.emotion-value');
      let dragging = false;
      function applyFromEvent(evt){
        const rect = bar.getBoundingClientRect();
        if (!rect.width) return;
        const ratio = Math.min(1, Math.max(0, (evt.clientX - rect.left) / rect.width));
        value = Math.round(ratio * 100) / 10;
        fill.style.width = (value * 10) + '%';
        valueEl.textContent = value.toFixed(1);
      }
      bar.addEventListener('pointerdown', function(evt){
        dragging = true;
        bar.setPointerCapture(evt.pointerId);
        applyFromEvent(evt);
      });
      bar.addEventListener('pointermove', function(evt){
        if (dragging) applyFromEvent(evt);
      });
      bar.addEventListener('pointerup', function(){
        if (!dragging) return;
        dragging = false;
        setEmotion(key, value);
      });
      bar.addEventListener('pointercancel', function(){ dragging = false; });
      root.appendChild(row);
    });
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

  api.addPage({
    id: 'emotion-engine',
    label: '情绪系统',
    desc: '实时情绪、趋势和配置快照',
    plugin: 'emotion-engine',
    render: function(container){
      container.innerHTML = '<article class="card full-span"><h3 class="card-title">Emotion Engine</h3><p class="card-help">显示当前主导情绪、近 24 小时变化与配置状态。拖动情绪条可直接修改情绪值。</p><div id="emotion-metrics" class="data-grid-compact"></div><div id="emotion-bars"></div></article><article class="card full-span"><h3 class="card-title">情绪配置</h3><div class="data-grid-compact"><div class="metric"><span class="metric-label">衰减速度</span><span class="metric-value" id="emotion-decay-state">0.10</span></div></div><div class="toolbar"><label>情绪衰减（每分钟，0 表示不衰减）<input id="emotion-decay" type="number" step="0.05" min="0" max="10" /></label><button id="emotion-save" class="btn btn-primary">保存配置</button><span id="emotion-save-status" class="card-help"></span></div></article><article class="card full-span"><h3 class="card-title">24 小时趋势</h3><div id="emotion-trend" style="width:100%;height:260px;"></div></article>';
      Promise.all([fetchState(), fetchConfig()]).then(function(results){
        const payload = results[0] || {};
        const cfgPayload = (results[1] || {}).config || {};
        const vector = payload.vector || {};
        const history = Array.isArray(payload.history) ? payload.history : [];
        const metrics = document.getElementById('emotion-metrics');
        const bars = document.getElementById('emotion-bars');
        if (metrics) renderMetrics(metrics, payload);
        if (bars) renderBars(bars, vector);
        const values = (cfgPayload.values || {});
        const decay = document.getElementById('emotion-decay');
        const decayState = document.getElementById('emotion-decay-state');
        const status = document.getElementById('emotion-save-status');
        if (decay) decay.value = String(values.DECAY_PER_MINUTE ?? 0.1);
        if (decayState) decayState.textContent = Number(values.DECAY_PER_MINUTE ?? 0.1).toFixed(2);
        const saveBtn = document.getElementById('emotion-save');
        if (saveBtn) {
          saveBtn.onclick = async function(){
            if (status) { status.textContent = '保存中…'; status.style.color = ''; }
            try {
              await saveConfig({ DECAY_PER_MINUTE: Number(decay && decay.value || 0.1) });
              if (status) status.textContent = '已保存，立即生效';
            } catch (error) {
              if (status) { status.textContent = '保存失败：' + error.message; status.style.color = '#e54447'; }
            }
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
