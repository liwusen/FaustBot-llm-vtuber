(function(){
  const api = window.pluginUI;
  if (!api) return;
  const baseUrl = (window.pluginUI && window.pluginUI.backendBaseUrl) || 'http://127.0.0.1:13900';

  async function fetchState(){
    const res = await fetch(baseUrl + '/faust/plugins/emotion-engine/state');
    return res.json();
  }

  async function fetchConfig(){
    const res = await fetch(baseUrl + '/faust/admin/plugins/emotion-engine/config');
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

  api.addPage({
    id: 'emotion-engine',
    label: '情绪系统',
    desc: '实时情绪、趋势和配置快照',
    plugin: 'emotion-engine',
    render: function(container){
      container.innerHTML = '<article class="card full-span"><h3 class="card-title">Emotion Engine</h3><p class="card-help" id="emotion-dominant">加载中...</p><div id="emotion-bars"></div></article><article class="card full-span"><h3 class="card-title">情绪配置</h3><div class="toolbar"><label><input id="emotion-sharp-toggle" type="checkbox" /> 毒舌改写</label><label>衰减 <input id="emotion-decay" type="number" step="0.1" min="0" /></label><label>滤镜 <input id="emotion-overlay" type="number" step="1" min="0" max="100" /></label><button id="emotion-save" class="btn btn-primary">保存配置</button></div></article><article class="card full-span"><h3 class="card-title">24h 趋势</h3><canvas id="emotion-trend" width="640" height="220"></canvas></article><article class="card full-span"><h3 class="card-title">最近事件</h3><ul id="emotion-events" class="emotion-history-v2"></ul></article>';
      Promise.all([fetchState(), fetchConfig()]).then(function(results){
        const payload = results[0] || {};
        const cfgPayload = (results[1] || {}).config || {};
        const vector = payload.vector || {};
        const history = Array.isArray(payload.history) ? payload.history : [];
        const top = Array.isArray(payload.top_emotions) ? payload.top_emotions : [];
        const dominant = document.getElementById('emotion-dominant');
        const bars = document.getElementById('emotion-bars');
        const events = document.getElementById('emotion-events');
        if (dominant) dominant.textContent = '当前主导情绪：' + (payload.dominant_emotion || 'unknown') + ' / ' + top.map(function(item){ return item.label + ' ' + Number(item.value || 0).toFixed(1); }).join(' · ');
        if (bars) renderBars(bars, vector);
        if (events) {
          events.innerHTML = history.slice(-10).reverse().map(function(entry){
            return '<li><span>' + new Date((entry.ts || 0) * 1000).toLocaleTimeString() + '</span><strong>' + (entry.reason || 'event') + '</strong></li>';
          }).join('') || '<li>暂无事件</li>';
        }
        const values = (cfgPayload.values || {});
        const sharpToggle = document.getElementById('emotion-sharp-toggle');
        const decay = document.getElementById('emotion-decay');
        const overlay = document.getElementById('emotion-overlay');
        if (sharpToggle) sharpToggle.checked = !!values.SHARP_TONGUE_REWRITE;
        if (decay) decay.value = String(values.DECAY_PER_MINUTE ?? 0.1);
        if (overlay) overlay.value = String(values.OVERLAY_INTENSITY ?? 50);
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
      }).catch(function(){
        container.innerHTML = '<article class="card full-span"><h3 class="card-title">Emotion Engine</h3><p class="card-help">无法获取情绪状态</p></article>';
      });
    }
  });

  api.addCard('plugins', {
    title: 'Emotion Engine',
    priority: 15,
    plugin: 'emotion-engine',
    render: function(container){
      fetchState().then(function(payload){
        container.innerHTML = '<div class="plugin-mini-card"><p>主导情绪：' + (payload.dominant_emotion || 'unknown') + '</p><p class="plugin-mini-muted">情绪历史样本：' + ((payload.history || []).length) + '</p></div>';
      }).catch(function(){
        container.innerHTML = '<div class="plugin-mini-card">情绪状态不可用</div>';
      });
    }
  });
})();
